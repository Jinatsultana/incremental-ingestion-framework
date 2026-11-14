"""APPLICATION TIER — orchestration.

One function, one batch, one transaction. The pipeline knows nothing about
SQL (it calls the data tier) and nothing about Streamlit or argparse.
"""
from __future__ import annotations

import csv
import hashlib
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db, merge, validation

SOURCE_FIELDS = ("op", "order_id", "customer_id", "amount", "status", "updated_at")


class SchemaError(ValueError):
    """Raised when the source file's structure is unusable (not a row problem)."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_run_id(source_path: Path, source_name: str) -> str:
    """Deterministic: same bytes + same source => same run_id. Never a timestamp."""
    digest = hashlib.sha256()
    if source_path.exists():
        digest.update(source_path.read_bytes())
    else:
        digest.update(f"MISSING:{source_path.resolve()}".encode("utf-8"))
    digest.update(b"\0")
    digest.update(source_name.encode("utf-8"))
    return digest.hexdigest()[:16]


def read_source_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Read the change feed, preserving malformed rows so they can be quarantined."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows: list[dict[str, Any]] = []
        for line_number, row in enumerate(reader, start=2):
            row["_line_number"] = line_number
            if None in row:                      # extra values beyond the header
                row["_extra_values"] = row.pop(None)
            rows.append(row)
        return fields, rows


def run_ingestion(
    conn,
    *,
    source_path: str | Path,
    source_name: str = "orders_feed",
    customer_ids: set[str] | None = None,
    fail_after: int | None = None,
) -> dict[str, Any]:
    """Ingest one batch incrementally and idempotently.

    Lifecycle:
      1. record the attempt as 'running'  (its own committed transaction)
      2. apply the batch in ONE transaction -> data + watermark move together
      3. record the outcome               (its own transaction)

    A crash in phase 2 rolls back phase 2 only, so the watermark never advances
    and the run row stays visibly 'running' for an operator to spot.
    """
    source_path = Path(source_path)
    started_at = utc_now()
    clock = time.perf_counter()
    run_id = stable_run_id(source_path, source_name)
    watermark_before = db.get_watermark(conn, source_name)

    # Phase 0: make reruns of the same input replace that run's artefacts.
    db.clear_run_artifacts(conn, run_id)
    db.start_run(
        conn,
        run_id=run_id,
        source_name=source_name,
        source_file=source_path.name,
        watermark_before=watermark_before,
        started_at=started_at,
    )

    stats: dict[str, Any] = {
        "input_rows": 0,
        "applied_rows": 0,
        "deleted_rows": 0,
        "late_rows": 0,
        "stale_skipped_rows": 0,
        "rejected_rows": 0,
    }
    watermark_after = watermark_before
    rejects: Counter[str] = Counter()
    status = "success"
    failure_reason: str | None = None

    try:
        fields, rows = read_source_csv(source_path)
        missing = [f for f in SOURCE_FIELDS if f not in fields]
        if missing:
            raise SchemaError(f"Source schema missing required column(s): {', '.join(sorted(missing))}")

        customer_ids = customer_ids or set()
        snapshot = db.load_target_snapshot(conn)     # one read, then O(1) per row
        stats["input_rows"] = len(rows)

        applied_timestamps: list[str] = []

        with db.transaction(conn):
            processed = 0

            for row in rows:
                processed += 1
                line = int(row.get("_line_number", 0))
                raw_order_id = (str(row.get("order_id")).strip() if row.get("order_id") else None) or None

                clean, reason, detail = validation.validate_record(row, customer_ids)
                action: str | None = None

                if reason is None:
                    # Rule 1: "late" is an observation, never a filter.
                    if merge.is_late_arriving(clean["updated_at"], watermark_before):
                        stats["late_rows"] += 1
                    action, why = merge.decide(
                        clean["op"], clean["updated_at"], snapshot.get(clean["order_id"])
                    )
                    if action == merge.REJECT:
                        reason, detail = why, (
                            f"Cannot apply {clean['op']} for order_id {clean['order_id']!r}: "
                            "no such key in the target"
                        )
                    elif action == merge.SKIP_STALE:
                        stats["stale_skipped_rows"] += 1

                if reason is not None:
                    stats["rejected_rows"] += 1
                    rejects[reason] += 1
                    db.insert_reject(
                        conn,
                        run_id=run_id,
                        source_file=source_path.name,
                        source_line=line,
                        order_id=clean["order_id"] if clean else raw_order_id,
                        reason_code=reason,
                        reason_detail=detail or "",
                        raw_record=validation.raw_json(row),
                        logged_at=utc_now(),
                    )
                elif action == merge.APPLY:
                    if clean["op"] == "upsert":
                        db.apply_upsert(
                            conn,
                            order_id=clean["order_id"],
                            customer_id=clean["customer_id"],
                            amount=clean["amount"],
                            status=clean["status"],
                            updated_at=clean["updated_at"],
                            run_id=run_id,
                            ingested_at=utc_now(),
                        )
                        stats["applied_rows"] += 1
                    else:
                        db.apply_tombstone(
                            conn,
                            order_id=clean["order_id"],
                            updated_at=clean["updated_at"],
                            run_id=run_id,
                            deleted_at=utc_now(),
                        )
                        stats["deleted_rows"] += 1
                    db.insert_history(
                        conn,
                        order_id=clean["order_id"],
                        op=clean["op"],
                        customer_id=clean["customer_id"],
                        amount=clean["amount"],
                        status=clean["status"],
                        updated_at=clean["updated_at"],
                        run_id=run_id,
                        applied_at=utc_now(),
                    )
                    applied_timestamps.append(clean["updated_at"])
                    # Keep the in-memory snapshot current so two events for the
                    # same key inside one batch are also ordered correctly.
                    snapshot[clean["order_id"]] = {
                        "updated_at": clean["updated_at"],
                        "is_deleted": 1 if clean["op"] == "delete" else 0,
                    }

                if fail_after is not None and processed >= fail_after:
                    raise RuntimeError(
                        f"Injected failure after {processed} row(s): simulated crash before COMMIT"
                    )

            watermark_after = merge.next_watermark(watermark_before, applied_timestamps)
            db.set_watermark(
                conn,
                source_name=source_name,
                last_updated_at=watermark_after,
                run_id=run_id,
                advanced_at=utc_now(),
            )

    except Exception as exc:                      # noqa: BLE001 - recorded, never swallowed
        status = "failed"
        failure_reason = f"{type(exc).__name__}: {exc}"
        watermark_after = watermark_before        # the watermark must not move
        # The transaction rolled back, so nothing was committed: report zeros
        # rather than counting work that never reached the warehouse.
        for key in ("applied_rows", "deleted_rows", "late_rows",
                    "stale_skipped_rows", "rejected_rows"):
            stats[key] = 0
        rejects.clear()

    duration_ms = round((time.perf_counter() - clock) * 1000, 3)
    ended_at = utc_now()

    db.finish_run(
        conn,
        run_id=run_id,
        status=status,
        ended_at=ended_at,
        watermark_after=watermark_after,
        input_rows=stats["input_rows"],
        applied_rows=stats["applied_rows"],
        deleted_rows=stats["deleted_rows"],
        late_rows=stats["late_rows"],
        stale_skipped_rows=stats["stale_skipped_rows"],
        rejected_rows=stats["rejected_rows"],
        duration_ms=duration_ms,
        failure_reason=failure_reason,
    )

    return {
        "run_id": run_id,
        "source_name": source_name,
        "source_file": source_path.name,
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "watermark_before": watermark_before,
        "watermark_after": watermark_after,
        "duration_ms": duration_ms,
        "failure_reason": failure_reason,
        "rejects_by_reason": dict(rejects),
        **stats,
    }