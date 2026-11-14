"""DATA TIER.

The only module in the project that knows SQL exists. Everything above this
layer (pipeline, service, CLI, UI) talks to plain Python dicts and lists.
Swapping SQLite for Postgres means rewriting this file and schema.sql only.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


# ---------------------------------------------------------------------------
# Connection & schema
# ---------------------------------------------------------------------------
def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with explicit transaction control."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One atomic unit of work.

    Crash or exception before COMMIT -> ROLLBACK -> target tables AND the
    watermark stay exactly as they were. That is what makes retry safe.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------
def get_watermark(conn: sqlite3.Connection, source_name: str) -> str | None:
    row = conn.execute(
        "SELECT last_updated_at FROM watermark WHERE source_name = ?", (source_name,)
    ).fetchone()
    return row["last_updated_at"] if row else None


def set_watermark(
    conn: sqlite3.Connection,
    *,
    source_name: str,
    last_updated_at: str | None,
    run_id: str,
    advanced_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO watermark (source_name, last_updated_at, last_run_id, advanced_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_name) DO UPDATE SET
            last_updated_at = excluded.last_updated_at,
            last_run_id     = excluded.last_run_id,
            advanced_at     = excluded.advanced_at
        """,
        (source_name, last_updated_at, run_id, advanced_at),
    )


# ---------------------------------------------------------------------------
# Target state reads
# ---------------------------------------------------------------------------
def load_target_snapshot(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Preload current target state once per run.

    PERFORMANCE DECISION: one read instead of one lookup per input row, so the
    merge decision is O(1) per row in memory. This is the "one optimization".
    """
    rows = conn.execute(
        "SELECT order_id, updated_at, is_deleted FROM target_orders"
    ).fetchall()
    return {
        r["order_id"]: {"updated_at": r["updated_at"], "is_deleted": r["is_deleted"]}
        for r in rows
    }


def load_customer_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["customer_id"] for r in conn.execute("SELECT customer_id FROM customer_ref")}


# ---------------------------------------------------------------------------
# Target state writes
# ---------------------------------------------------------------------------
def apply_upsert(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    customer_id: str,
    amount: float,
    status: str,
    updated_at: str,
    run_id: str,
    ingested_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO target_orders
            (order_id, customer_id, amount, status, updated_at,
             is_deleted, deleted_at, last_run_id, ingested_at)
        VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            customer_id = excluded.customer_id,
            amount      = excluded.amount,
            status      = excluded.status,
            updated_at  = excluded.updated_at,
            is_deleted  = 0,
            deleted_at  = NULL,
            last_run_id = excluded.last_run_id,
            ingested_at = excluded.ingested_at
        """,
        (order_id, customer_id, amount, status, updated_at, run_id, ingested_at),
    )


def apply_tombstone(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    updated_at: str,
    run_id: str,
    deleted_at: str,
) -> None:
    """Soft delete: keep the row so a later stale upsert cannot resurrect it."""
    conn.execute(
        """
        UPDATE target_orders
           SET is_deleted  = 1,
               deleted_at  = ?,
               updated_at  = ?,
               last_run_id = ?,
               ingested_at = ?
         WHERE order_id = ?
        """,
        (deleted_at, updated_at, run_id, deleted_at, order_id),
    )


def insert_history(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    op: str,
    customer_id: str,
    amount: float | None,
    status: str | None,
    updated_at: str,
    run_id: str,
    applied_at: str,
) -> bool:
    """Append one change event. Returns False when it was already recorded."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO order_history
            (order_id, op, customer_id, amount, status, updated_at, run_id, applied_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (order_id, op, customer_id, amount, status, updated_at, run_id, applied_at),
    )
    return cur.rowcount == 1


def insert_reject(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_file: str,
    source_line: int,
    order_id: str | None,
    reason_code: str,
    reason_detail: str,
    raw_record: str,
    logged_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO rejected_records
            (run_id, source_file, source_line, order_id, reason_code,
             reason_detail, raw_record, logged_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, source_file, source_line, order_id, reason_code,
         reason_detail, raw_record, logged_at),
    )


# ---------------------------------------------------------------------------
# Run history (observability)
# ---------------------------------------------------------------------------
def start_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_name: str,
    source_file: str,
    watermark_before: str | None,
    started_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO run_history
            (run_id, source_name, source_file, watermark_before, started_at,
             ended_at, status)
        VALUES (?, ?, ?, ?, ?, ?, 'running')
        ON CONFLICT(run_id) DO NOTHING
        """,
        (run_id, source_name, source_file, watermark_before, started_at, started_at),
    )


def finish_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    ended_at: str,
    watermark_after: str | None,
    input_rows: int,
    applied_rows: int,
    deleted_rows: int,
    late_rows: int,
    stale_skipped_rows: int,
    rejected_rows: int,
    duration_ms: float,
    failure_reason: str | None,
) -> None:
    conn.execute(
        """
        UPDATE run_history
           SET status = ?, ended_at = ?, watermark_after = ?,
               input_rows = ?, applied_rows = ?, deleted_rows = ?,
               late_rows = ?, stale_skipped_rows = ?, rejected_rows = ?,
               duration_ms = ?, failure_reason = ?
         WHERE run_id = ?
        """,
        (status, ended_at, watermark_after, input_rows, applied_rows,
         deleted_rows, late_rows, stale_skipped_rows, rejected_rows,
         duration_ms, failure_reason, run_id),
    )


# ---------------------------------------------------------------------------
# Reconciliation helpers (read-only)
# ---------------------------------------------------------------------------
def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM run_history WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def count(conn: sqlite3.Connection, table: str) -> int:
    if table not in {"target_orders", "order_history", "run_history", "rejected_records"}:
        raise ValueError(f"Unknown table: {table}")
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def count_history_for_run(conn: sqlite3.Connection, run_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM order_history WHERE run_id = ?", (run_id,)
    ).fetchone()["n"]


def count_rejects_for_run(conn: sqlite3.Connection, run_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM rejected_records WHERE run_id = ?", (run_id,)
    ).fetchone()["n"]


def duplicate_history_keys(conn: sqlite3.Connection) -> int:
    """Must always be 0: proves no change event was applied twice."""
    return conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
            SELECT order_id, updated_at FROM order_history
             GROUP BY order_id, updated_at HAVING COUNT(*) > 1
        )
        """
    ).fetchone()["n"]


def rejects_by_reason(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT reason_code, COUNT(*) AS n FROM rejected_records WHERE run_id = ? GROUP BY reason_code",
        (run_id,),
    ).fetchall()
    return {r["reason_code"]: r["n"] for r in rows}


def recent_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Most recently written runs first.

    rowid DESC breaks ties when several runs start within the same second, so
    "latest" always means the run that was actually written last.
    """
    rows = conn.execute(
        "SELECT * FROM run_history ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def current_target_rows(conn: sqlite3.Connection, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM target_orders ORDER BY order_id LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Reference data & run artefact lifecycle
# ---------------------------------------------------------------------------
def clear_customer_ref(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM customer_ref")


def upsert_customer_ref(
    conn: sqlite3.Connection,
    *,
    customer_id: str,
    customer_name: str,
    segment: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO customer_ref (customer_id, customer_name, segment)
        VALUES (?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET
            customer_name = excluded.customer_name,
            segment       = excluded.segment
        """,
        (customer_id, customer_name, segment),
    )


def clear_run_artifacts(conn: sqlite3.Connection, run_id: str) -> None:
    """Remove the previous attempt for a deterministic run_id.

    order_history is deliberately NOT cleared: it is a change log keyed by
    (order_id, updated_at), and one change event legitimately exists once.
    """
    conn.execute("DELETE FROM rejected_records WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM run_history WHERE run_id = ?", (run_id,))
