"""PRESENTATION TIER — command line interface.

Deliberately thin: argument parsing and printing only. Every action is a call
into service.py. No SQL, no business rules.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import service

DEFAULT_DB = Path("outputs/warehouse.db")
DEFAULT_REFERENCE = Path("data/reference/customers.csv")
DEFAULT_SOURCE = Path("data/source/orders_batch_01.csv")
DEFAULT_REPORT = Path("outputs/run_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Incremental ingestion framework: watermarks, tombstones, idempotent reruns.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="warehouse database path")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the schema and load the customer reference")
    init.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)

    run = sub.add_parser("run", help="ingest one incremental batch")
    run.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    run.add_argument("--source-name", default="orders_feed")
    run.add_argument(
        "--fail-after",
        type=int,
        default=None,
        metavar="N",
        help="inject a crash after N rows to demonstrate rollback + a safe retry",
    )
    run.add_argument("--report", type=Path, default=DEFAULT_REPORT)

    check = sub.add_parser("reconcile", help="assert the run's row counts add up")
    check.add_argument("--run-id", required=False)
    check.add_argument("--latest", action="store_true", help="reconcile the most recent run")

    sub.add_parser("watermark", help="show the current watermark")
    sub.add_parser("runs", help="show recent runs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "init":
        loaded = service.load_customer_reference(args.db, args.reference)
        print(json.dumps({"database": str(args.db), "customers_loaded": loaded}, indent=2))
        return 0

    if args.command == "run":
        result = service.run_ingestion(
            args.db, args.source, source_name=args.source_name, fail_after=args.fail_after
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        if result["status"] != "success":
            print(f"\nRUN FAILED (exit 1): {result['failure_reason']}", file=sys.stderr)
            return 1
        return 0

    if args.command == "reconcile":
        run_id = args.run_id
        if args.latest and not run_id:
            latest = service.get_overview(args.db)["latest_run"]
            if latest is None:
                raise SystemExit("No runs found yet.")
            run_id = latest["run_id"]
        if not run_id:
            raise SystemExit("Pass --run-id or --latest")
        result = service.reconcile(args.db, run_id)
        print(json.dumps(result, indent=2))
        if not result["reconciled"]:
            print("\nRECONCILIATION FAILED (exit 2)", file=sys.stderr)
            return 2
        return 0

    if args.command == "watermark":
        print(json.dumps({"watermark": service.get_watermark(args.db)}, indent=2))
        return 0

    if args.command == "runs":
        overview = service.get_overview(args.db)
        print(json.dumps(overview["runs"], indent=2, default=str))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())