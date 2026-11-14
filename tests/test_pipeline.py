"""Test suite: happy path, edge cases, and the four hard rules.

Tier note: these tests drive the APPLICATION tier through its facade
(service.py), which is why no test contains SQL.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from ingest import service

REFERENCE = [
    {"customer_id": "C001", "customer_name": "Acme", "segment": "retail"},
    {"customer_id": "C002", "customer_name": "Borealis", "segment": "wholesale"},
]

HEADER = ["op", "order_id", "customer_id", "amount", "status", "updated_at"]


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture()
def warehouse(tmp_path):
    db_path = tmp_path / "warehouse.db"
    reference = write_csv(
        tmp_path / "customers.csv",
        ["customer_id", "customer_name", "segment"],
        [[r["customer_id"], r["customer_name"], r["segment"]] for r in REFERENCE],
    )
    service.load_customer_reference(db_path, reference)
    return db_path


def run_batch(warehouse, tmp_path, name, rows, **kwargs):
    source = write_csv(tmp_path / (name + ".csv"), HEADER, rows)
    return service.run_ingestion(warehouse, source, source_name="orders_feed", **kwargs)


def test_happy_path_accepts_and_enriches(warehouse, tmp_path):
    result = run_batch(warehouse, tmp_path, "b1", [
        ["upsert", "O1", "C001", "100.00", "new", "2026-11-01T09:00:00Z"],
        ["upsert", "O2", "C002", "50.00", "paid", "2026-11-01T09:10:00Z"],
    ])
    assert result["status"] == "success"
    assert (result["input_rows"], result["applied_rows"], result["rejected_rows"]) == (2, 2, 0)
    assert result["watermark_after"] == "2026-11-01T09:10:00Z"
    assert service.reconcile(warehouse, result["run_id"])["reconciled"] is True


def test_empty_batch_is_a_valid_no_op(warehouse, tmp_path):
    result = run_batch(warehouse, tmp_path, "empty", [])
    assert result["status"] == "success"
    assert result["input_rows"] == 0
    assert result["watermark_after"] is None
    assert service.reconcile(warehouse, result["run_id"])["reconciled"] is True


def test_missing_required_field_is_quarantined(warehouse, tmp_path):
    result = run_batch(warehouse, tmp_path, "nulls", [
        ["upsert", "O1", "", "10.00", "new", "2026-11-01T09:00:00Z"],
    ])
    assert result["rejected_rows"] == 1
    assert result["rejects_by_reason"] == {"missing_required_field": 1}


def test_malformed_record_with_extra_values(warehouse, tmp_path):
    result = run_batch(warehouse, tmp_path, "malformed", [
        ["upsert", "O1", "C001", "10.00", "new", "2026-11-01T09:00:00Z", "EXTRA"],
    ])
    assert result["rejects_by_reason"] == {"malformed_record": 1}


def test_invalid_reference_key_is_rejected(warehouse, tmp_path):
    result = run_batch(warehouse, tmp_path, "badref", [
        ["upsert", "O1", "C999", "10.00", "new", "2026-11-01T09:00:00Z"],
    ])
    assert result["rejects_by_reason"] == {"invalid_reference_key": 1}


def test_unexpected_schema_change_fails_the_run(warehouse, tmp_path):
    source = write_csv(
        tmp_path / "renamed.csv",
        ["op", "order_key", "customer_id", "amount", "status", "updated_at"],
        [["upsert", "O1", "C001", "10.00", "new", "2026-11-01T09:00:00Z"]],
    )
    result = service.run_ingestion(warehouse, source, source_name="orders_feed")
    assert result["status"] == "failed"
    assert "order_id" in result["failure_reason"]
    assert result["watermark_after"] is None


def test_invalid_amount_and_status_are_rejected(warehouse, tmp_path):
    result = run_batch(warehouse, tmp_path, "badvalues", [
        ["upsert", "O1", "C001", "abc", "new", "2026-11-01T09:00:00Z"],
        ["upsert", "O2", "C001", "10.00", "pending", "2026-11-01T09:01:00Z"],
    ])
    assert result["rejects_by_reason"] == {"invalid_amount": 1, "invalid_status": 1}


def test_duplicate_event_in_batch_applied_once(warehouse, tmp_path):
    row = ["upsert", "O1", "C001", "10.00", "new", "2026-11-01T09:00:00Z"]
    result = run_batch(warehouse, tmp_path, "dupe", [row, list(row)])
    assert result["applied_rows"] == 1
    assert result["stale_skipped_rows"] == 1
    assert service.reconcile(warehouse, result["run_id"])["duplicate_history_keys"] == 0


def test_rerun_of_same_input_creates_no_duplicates(warehouse, tmp_path):
    rows = [
        ["upsert", "O1", "C001", "10.00", "new", "2026-11-01T09:00:00Z"],
        ["upsert", "O2", "C002", "20.00", "paid", "2026-11-01T09:05:00Z"],
    ]
    first = run_batch(warehouse, tmp_path, "rerun", rows)
    second = run_batch(warehouse, tmp_path, "rerun", rows)

    assert first["run_id"] == second["run_id"]
    assert second["applied_rows"] == 0
    assert second["stale_skipped_rows"] == 2
    summary = service.reconcile(warehouse, second["run_id"])
    assert summary["reconciled"] is True
    assert summary["duplicate_history_keys"] == 0
    assert summary["target_orders_total"] == 2


def test_late_arriving_record_is_applied_not_dropped(warehouse, tmp_path):
    run_batch(warehouse, tmp_path, "on_time", [
        ["upsert", "O1", "C001", "10.00", "new", "2026-11-01T12:00:00Z"],
    ])
    late = run_batch(warehouse, tmp_path, "late", [
        ["upsert", "O2", "C002", "20.00", "paid", "2026-11-01T08:00:00Z"],
    ])
    assert late["late_rows"] == 1
    assert late["applied_rows"] == 1
    assert service.get_watermark(warehouse) == "2026-11-01T12:00:00Z"


def test_out_of_order_arrival_keeps_newest_version(warehouse, tmp_path):
    run_batch(warehouse, tmp_path, "v2", [
        ["upsert", "O1", "C001", "10.00", "shipped", "2026-11-01T12:00:00Z"],
    ])
    stale = run_batch(warehouse, tmp_path, "v1_late", [
        ["upsert", "O1", "C001", "10.00", "new", "2026-11-01T09:00:00Z"],
    ])
    assert stale["stale_skipped_rows"] == 1
    overview = service.get_overview(warehouse)
    target = {r["order_id"]: r for r in overview["target_orders"]}
    assert target["O1"]["status"] == "shipped"


def test_tombstone_soft_deletes_and_blocks_resurrection(warehouse, tmp_path):
    run_batch(warehouse, tmp_path, "create", [
        ["upsert", "O1", "C001", "10.00", "paid", "2026-11-01T09:00:00Z"],
    ])
    run_batch(warehouse, tmp_path, "delete", [
        ["delete", "O1", "C001", "", "cancelled", "2026-11-02T09:00:00Z"],
    ])
    overview = service.get_overview(warehouse)
    target = {r["order_id"]: r for r in overview["target_orders"]}
    assert target["O1"]["is_deleted"] == 1

    resurrect = run_batch(warehouse, tmp_path, "resurrect", [
        ["upsert", "O1", "C001", "10.00", "paid", "2026-11-01T10:00:00Z"],
    ])
    assert resurrect["stale_skipped_rows"] == 1
    overview = service.get_overview(warehouse)
    assert {r["order_id"]: r for r in overview["target_orders"]}["O1"]["is_deleted"] == 1


def test_delete_of_unknown_key_is_rejected(warehouse, tmp_path):
    result = run_batch(warehouse, tmp_path, "del_unknown", [
        ["delete", "O404", "C001", "", "cancelled", "2026-11-02T09:00:00Z"],
    ])
    assert result["rejects_by_reason"] == {"delete_unknown_key": 1}


def test_partial_failure_rolls_back_and_retry_is_clean(warehouse, tmp_path):
    run_batch(warehouse, tmp_path, "base", [
        ["upsert", "O1", "C001", "10.00", "paid", "2026-11-01T09:00:00Z"],
    ])
    watermark_before = service.get_watermark(warehouse)

    batch = [
        ["upsert", "O2", "C001", "20.00", "new", "2026-11-02T09:00:00Z"],
        ["upsert", "O3", "C002", "30.00", "new", "2026-11-02T09:05:00Z"],
        ["upsert", "O4", "C002", "40.00", "new", "2026-11-02T09:10:00Z"],
    ]
    failed = run_batch(warehouse, tmp_path, "batch", batch, fail_after=2)

    assert failed["status"] == "failed"
    assert "Injected failure" in failed["failure_reason"]
    assert service.get_watermark(warehouse) == watermark_before
    assert service.get_overview(warehouse)["counts"]["target_orders"] == 1
    assert service.reconcile(warehouse, failed["run_id"])["reconciled"] is True

    retried = run_batch(warehouse, tmp_path, "batch", batch)
    assert retried["status"] == "success"
    assert retried["applied_rows"] == 3
    summary = service.reconcile(warehouse, retried["run_id"])
    assert summary["reconciled"] is True
    assert summary["duplicate_history_keys"] == 0
    assert summary["target_orders_total"] == 4


def test_missing_source_file_fails_loudly(warehouse, tmp_path):
    result = service.run_ingestion(warehouse, tmp_path / "does_not_exist.csv")
    assert result["status"] == "failed"
    assert "FileNotFoundError" in result["failure_reason"]

def test_boundary_zero_amount_is_rejected(warehouse, tmp_path):
    """Boundary: amount must be strictly greater than zero."""
    result = run_batch(warehouse, tmp_path, "zero_amount", [
        ["upsert", "O1", "C001", "0", "new", "2026-11-01T09:00:00Z"],
    ])
    assert result["rejects_by_reason"] == {"invalid_amount": 1}


def test_boundary_large_amount_is_accepted(warehouse, tmp_path):
    """Boundary: a large but valid amount must pass unchanged."""
    result = run_batch(warehouse, tmp_path, "large_amount", [
        ["upsert", "O1", "C001", "999999.99", "paid", "2026-11-01T09:00:00Z"],
    ])
    assert result["applied_rows"] == 1
    overview = service.get_overview(warehouse)
    assert {r["order_id"]: r for r in overview["target_orders"]}["O1"]["amount"] == 999999.99


def test_boundary_equal_timestamp_is_not_reapplied(warehouse, tmp_path):
    """Boundary: an event exactly equal to the stored timestamp is not newer,
    so it must be skipped rather than applied twice."""
    run_batch(warehouse, tmp_path, "boundary_a", [
        ["upsert", "O1", "C001", "10.00", "new", "2026-11-01T09:00:00Z"],
    ])
    same_instant = run_batch(warehouse, tmp_path, "boundary_b", [
        ["upsert", "O1", "C001", "99.00", "paid", "2026-11-01T09:00:00Z"],
    ])
    assert same_instant["stale_skipped_rows"] == 1
    assert same_instant["applied_rows"] == 0
    overview = service.get_overview(warehouse)
    target = {r["order_id"]: r for r in overview["target_orders"]}["O1"]
    assert target["amount"] == 10.0          # original value survives
    assert target["status"] == "new"
