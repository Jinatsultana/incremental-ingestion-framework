"""Reproducible synthetic change-feed generator.

Deterministic: the same --seed always produces byte-identical output, so a
reviewer can regenerate the exact dataset used in the report.

Plants every defect class the pipeline must handle: late-arriving rows,
out-of-order rows, tombstones, unknown customers, bad timestamps, bad amounts,
unexpected statuses, missing fields and duplicate change events.

Usage:
    python scripts/generate_feed.py --rows 100000 --seed 42 \
        --output data/source/orders_generated.csv
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("new", "paid", "shipped", "cancelled")
BASE_TS = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
HEADER = ("op", "order_id", "customer_id", "amount", "status", "updated_at")


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_rows(rows: int, seed: int, customers: list[str]) -> list[dict[str, str]]:
    rnd = random.Random(seed)
    order_pool = max(1, rows // 2)
    out: list[dict[str, str]] = []

    for i in range(rows):
        order_id = f"O{rnd.randint(1, order_pool):06d}"
        customer_id = rnd.choice(customers)
        ts = BASE_TS + timedelta(seconds=i)
        # ~8% of rows simulate a late-arriving / out-of-order event.
        if rnd.random() < 0.08:
            ts = ts - timedelta(seconds=rnd.randint(3600, 86400 * 3))
        op = "delete" if rnd.random() < 0.05 else "upsert"
        amount = f"{rnd.uniform(5, 900):.2f}"
        status = rnd.choice(STATUSES)
        ts_text = iso(ts)

        roll = rnd.random()
        if roll < 0.010:
            status = "PENDING"                      # invalid_status
        elif roll < 0.020:
            amount = "-12.50"                       # invalid_amount
        elif roll < 0.030:
            ts_text = "2026-13-45T99:99:99Z"        # invalid_timestamp
        elif roll < 0.040:
            customer_id = "C999"                    # invalid_reference_key
        elif roll < 0.050:
            customer_id = ""                        # missing_required_field
        elif roll < 0.060:
            amount = "abc"                          # invalid_amount (non numeric)
        elif roll < 0.070:
            ts_text = "not-a-timestamp"             # invalid_timestamp

        out.append(
            {
                "op": op,
                "order_id": order_id,
                "customer_id": customer_id,
                "amount": "" if op == "delete" else amount,
                "status": status,
                "updated_at": ts_text,
            }
        )

    # Plant a handful of exact duplicate change events (same key + timestamp).
    for _ in range(max(1, rows // 1000)):
        if out:
            out.append(dict(rnd.choice(out)))

    return out


def write_feed(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a reproducible synthetic change feed.")
    parser.add_argument("--rows", type=int, default=100_000, help="number of change events")
    parser.add_argument("--seed", type=int, default=42, help="fixed random seed")
    parser.add_argument("--output", type=Path, default=Path("data/source/orders_generated.csv"))
    parser.add_argument(
        "--customers",
        type=Path,
        default=Path("data/reference/customers.csv"),
        help="reference file used to pick valid customer ids",
    )
    args = parser.parse_args()

    with args.customers.open("r", encoding="utf-8-sig", newline="") as handle:
        customers = [
            (r.get("customer_id") or "").strip()
            for r in csv.DictReader(handle)
            if (r.get("customer_id") or "").strip()
        ]
    if not customers:
        raise SystemExit("No customer ids found in the reference file.")

    rows = build_rows(args.rows, args.seed, customers)
    write_feed(args.output, rows)
    print(f"wrote {len(rows)} rows to {args.output} (seed={args.seed})")


if __name__ == "__main__":
    main()