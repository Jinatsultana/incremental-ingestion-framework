"""APPLICATION TIER — service facade.

The ONLY entry point the presentation tier is allowed to use. The CLI and the
Streamlit UI import this module and nothing below it, which is what keeps the
three tiers honestly separated.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from . import db, pipeline


def load_customer_reference(db_path: str | Path, reference_path: str | Path) -> int:
    """Load the customer reference CSV into the warehouse. Returns row count."""
    reference_path = Path(reference_path)
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        with reference_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        with db.transaction(conn):
            db.clear_customer_ref(conn)
            for row in rows:
                customer_id = (row.get("customer_id") or "").strip()
                if not customer_id:
                    continue
                db.upsert_customer_ref(
                    conn,
                    customer_id=customer_id,
                    customer_name=(row.get("customer_name") or "").strip(),
                    segment=(row.get("segment") or "").strip() or None,
                )
        return len(rows)
    finally:
        conn.close()


def run_ingestion(
    db_path: str | Path,
    source_path: str | Path,
    *,
    source_name: str = "orders_feed",
    fail_after: int | None = None,
) -> dict[str, Any]:
    """Run one incremental ingestion batch."""
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        customer_ids = db.load_customer_ids(conn)
        return pipeline.run_ingestion(
            conn,
            source_path=source_path,
            source_name=source_name,
            customer_ids=customer_ids,
            fail_after=fail_after,
        )
    finally:
        conn.close()


def reconcile(db_path: str | Path, run_id: str) -> dict[str, Any]:
    """Prove the run adds up.

    A successful run must account for every input row:
        input = applied + deleted + stale_skipped + rejected
    A failed run must have committed NOTHING for its run_id (the transaction
    rolled back), so its evidence is an empty result set, not a partial one.
    """
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        run = db.get_run(conn, run_id)
        if run is None:
            raise ValueError(f"Unknown run_id: {run_id}")

        accounted = (
            run["applied_rows"]
            + run["deleted_rows"]
            + run["stale_skipped_rows"]
            + run["rejected_rows"]
        )
        rolled_back = run["status"] == "failed"
        persisted_rejects = db.count_rejects_for_run(conn, run_id)

        if rolled_back:
            reconciled = (
                run["applied_rows"] == 0
                and run["rejected_rows"] == 0
                and persisted_rejects == 0
            )
        else:
            reconciled = (
                accounted == run["input_rows"]
                and run["rejected_rows"] == persisted_rejects
            )

        return {
            "run_id": run_id,
            "status": run["status"],
            "input_rows": run["input_rows"],
            "applied_rows": run["applied_rows"],
            "deleted_rows": run["deleted_rows"],
            "late_rows": run["late_rows"],
            "stale_skipped_rows": run["stale_skipped_rows"],
            "rejected_rows": run["rejected_rows"],
            "accounted_rows": accounted,
            "rolled_back": rolled_back,
            "reconciled": reconciled,
            "duplicate_history_keys": db.duplicate_history_keys(conn),
            "target_orders_total": db.count(conn, "target_orders"),
            "history_total": db.count(conn, "order_history"),
        }
    finally:
        conn.close()


def get_watermark(db_path: str | Path, source_name: str = "orders_feed") -> str | None:
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        return db.get_watermark(conn, source_name)
    finally:
        conn.close()


def get_overview(db_path: str | Path, run_id: str | None = None) -> dict[str, Any]:
    """Everything the dashboard needs, in one call."""
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        runs = db.recent_runs(conn, limit=20)
        latest = runs[0] if runs else None
        target_run = run_id or (latest["run_id"] if latest else None)
        return {
            "watermark": db.get_watermark(conn, "orders_feed"),
            "runs": runs,
            "latest_run": latest,
            "target_orders": db.current_target_rows(conn, limit=500),
            "rejects": db.rejects_by_reason(conn, target_run) if target_run else {},
            "counts": {
                "target_orders": db.count(conn, "target_orders"),
                "order_history": db.count(conn, "order_history"),
                "rejected_records": db.count(conn, "rejected_records"),
                "run_history": db.count(conn, "run_history"),
            },
            "duplicate_history_keys": db.duplicate_history_keys(conn),
            "selected_run_id": target_run,
        }
    finally:
        conn.close()