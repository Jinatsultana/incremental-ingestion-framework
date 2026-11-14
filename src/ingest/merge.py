"""APPLICATION TIER — merge decision logic (the heart of the pipeline).

Pure function: it needs only the event and the current target state for that
key, so it is fully unit-testable and order-independent.
"""
from __future__ import annotations

from typing import Any

APPLY = "apply"
SKIP_STALE = "skip_stale"
REJECT = "reject"


def decide(op: str, updated_at: str, existing: dict[str, Any] | None) -> tuple[str, str | None]:
    """Rule 2 + Rule 3.

    - unseen key + upsert          -> apply
    - unseen key + delete          -> reject (cannot tombstone a key never seen)
    - incoming newer               -> apply (resurrect allowed: is_deleted resets)
    - incoming older or equal      -> skip as stale  <- this is what kills duplicates

    Ordering is on the SOURCE timestamp, never on arrival order, so a shuffled
    file produces exactly the same target as a sorted one.
    """
    if existing is None:
        if op == "delete":
            return REJECT, "delete_unknown_key"
        return APPLY, None

    if updated_at > existing["updated_at"]:
        return APPLY, None

    return SKIP_STALE, "stale_or_duplicate_version"


def is_late_arriving(updated_at: str, watermark: str | None) -> bool:
    """Rule 1: 'late' means not newer than the last committed watermark.

    Late records are STILL APPLIED. This flag is observability only — it must
    never be used to filter data out.
    """
    if watermark is None:
        return False
    return updated_at <= watermark


def next_watermark(current: str | None, applied_timestamps: list[str]) -> str | None:
    """Rule 4: the watermark only moves forward, and only on success."""
    if not applied_timestamps:
        return current
    candidate = max(applied_timestamps)
    if current is None:
        return candidate
    return max(current, candidate)