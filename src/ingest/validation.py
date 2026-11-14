"""APPLICATION TIER — validation.

Pure functions only: rows in, decisions out. No database, no I/O, no printing.
This is why the rules are unit-testable without a warehouse.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

# amount is required for an upsert but may be empty on a tombstone.
REQUIRED_FIELDS = ("op", "order_id", "customer_id", "status", "updated_at")
VALID_OPS = {"upsert", "delete"}
VALID_STATUSES = {"new", "paid", "shipped", "cancelled"}


def normalize_timestamp(value: Any) -> str | None:
    """Parse ISO-8601 and normalise to UTC 'YYYY-MM-DDTHH:MM:SSZ'.

    Normalising matters: comparing timestamps as strings is only a valid
    ordering when every value shares one format and one timezone.
    Naive timestamps are assumed UTC (documented assumption).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def raw_json(row: dict[str, Any]) -> str:
    clean = {k: v for k, v in row.items() if not k.startswith("_")}
    return json.dumps(clean, sort_keys=True, default=str)


def validate_record(
    row: dict[str, Any],
    customer_ids: set[str],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Validate one source row.

    Returns (clean_record, reason_code, reason_detail).
    clean_record is None whenever reason_code is set.
    """
    # 1. Structural: more values than the header.
    if row.get("_extra_values"):
        return None, "malformed_record", "Row has more values than the CSV header"

    # 2. Missing required values.
    missing = [
        f for f in REQUIRED_FIELDS
        if row.get(f) is None or not str(row.get(f)).strip()
    ]
    if missing:
        return None, "missing_required_field", f"Missing required field(s): {', '.join(sorted(missing))}"

    op = str(row["op"]).strip().lower()
    order_id = str(row["order_id"]).strip()
    customer_id = str(row["customer_id"]).strip()
    status = str(row["status"]).strip().lower()

    # 3. Operation must be a known change type.
    if op not in VALID_OPS:
        return None, "invalid_operation", f"op must be one of {sorted(VALID_OPS)}, got {op!r}"

    # 4. Timestamp must parse.
    updated_at = normalize_timestamp(row["updated_at"])
    if updated_at is None:
        return None, "invalid_timestamp", "updated_at must be ISO-8601 (e.g. 2026-11-01T10:00:00Z)"

    # 5. Referential integrity against the customer reference set.
    if customer_ids and customer_id not in customer_ids:
        return None, "invalid_reference_key", f"customer_id {customer_id!r} not found in customer reference"

    # 6. Status must be a known state.
    if status not in VALID_STATUSES:
        return None, "invalid_status", f"status must be one of {sorted(VALID_STATUSES)}, got {status!r}"

    # 7. Amount rules differ by operation: a tombstone carries no money.
    amount: float | None = None
    raw_amount = str(row.get("amount", "")).strip()
    if op == "upsert":
        if not raw_amount:
            return None, "missing_required_field", "Missing required field(s): amount"
        try:
            amount = float(raw_amount)
        except ValueError:
            return None, "invalid_amount", f"amount must be numeric, got {raw_amount!r}"
        if amount <= 0:
            return None, "invalid_amount", "amount must be greater than 0 for an upsert"
    else:
        try:
            amount = float(raw_amount) if raw_amount else None
        except ValueError:
            amount = None

    return (
        {
            "op": op,
            "order_id": order_id,
            "customer_id": customer_id,
            "amount": amount,
            "status": status,
            "updated_at": updated_at,
            "source_line": int(row.get("_line_number", 0)),
        },
        None,
        None,
    )