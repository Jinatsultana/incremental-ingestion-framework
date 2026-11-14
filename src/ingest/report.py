"""APPLICATION TIER - validation report generator.

Composes a human-readable Markdown report from the warehouse: run history,
reconciliation, quarantine breakdown and the duplicate-key guarantee.

Run standalone:
    python -m ingest.report
    python -m ingest.report --out outputs/validation_report.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from . import db, service

DEFAULT_DB = Path("outputs/warehouse.db")
DEFAULT_OUT = Path("outputs/validation_report.md")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def build_report(db_path: Path, limit: int = 10) -> str:
    overview = service.get_overview(db_path)
    runs = overview["runs"][:limit]
    counts = overview["counts"]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out: list[str] = []
    out.append("# Validation Report")
    out.append("")
    out.append("Generated: " + generated)
    out.append("Warehouse: " + str(db_path))
    out.append("Current watermark: " + str(overview["watermark"] or "none"))
    out.append("")

    out.append("## Table counts")
    out.append("")
    out.append(_table(["Table", "Rows"], [[k, v] for k, v in counts.items()]))
    out.append("")

    out.append("## Run history (most recent first)")
    out.append("")
    if runs:
        out.append(_table(
            ["run_id", "source_file", "status", "input", "applied", "deleted",
             "late", "stale", "rejected", "duration_ms"],
            [[r["run_id"], r["source_file"], r["status"], r["input_rows"],
              r["applied_rows"], r["deleted_rows"], r["late_rows"],
              r["stale_skipped_rows"], r["rejected_rows"], r["duration_ms"]] for r in runs],
        ))
    else:
        out.append("_No runs recorded._")
    out.append("")

    out.append("## Reconciliation - latest run")
    out.append("")
    verdicts: list[str] = []
    if overview["latest_run"]:
        run_id = overview["latest_run"]["run_id"]
        summary = service.reconcile(db_path, run_id)
        out.append("Run: " + run_id + " (status: " + str(summary["status"]) + ")")
        out.append("")
        out.append(_table(
            ["Measure", "Value"],
            [["input_rows", summary["input_rows"]],
             ["applied_rows", summary["applied_rows"]],
             ["deleted_rows", summary["deleted_rows"]],
             ["stale_skipped_rows", summary["stale_skipped_rows"]],
             ["rejected_rows", summary["rejected_rows"]],
             ["accounted_rows", summary["accounted_rows"]],
             ["late_rows", summary["late_rows"]],
             ["rolled_back", summary["rolled_back"]]],
        ))
        out.append("")
        out.append("Rule: input_rows = applied + deleted + stale_skipped + rejected.")
        out.append("")
        verdicts.append("- Reconciliation: " + ("PASS" if summary["reconciled"] else "FAIL"))
    else:
        out.append("_No runs recorded._")
        verdicts.append("- Reconciliation: NO DATA")

    verdicts.append(
        "- Duplicate change events: " + ("PASS" if overview["duplicate_history_keys"] == 0 else "FAIL")
        + " (" + str(overview["duplicate_history_keys"]) + " duplicate keys)"
    )
    verdicts.append("- Quarantined rows retained: " + str(counts["rejected_records"]))
    out.append("## Verdict")
    out.append("")
    out.extend(verdicts)
    out.append("")

    out.append("## Quarantine breakdown - latest run")
    out.append("")
    if overview["rejects"]:
        out.append(_table(["reason_code", "rows"],
                          [[r, n] for r, n in sorted(overview["rejects"].items())]))
    else:
        out.append("_No rejected rows in the latest run._")
    out.append("")

    out.append("## Target state sample")
    out.append("")
    target = overview["target_orders"][:15]
    if target:
        out.append(_table(
            ["order_id", "customer_id", "amount", "status", "updated_at", "is_deleted"],
            [[t["order_id"], t["customer_id"], t["amount"], t["status"],
              t["updated_at"], t["is_deleted"]] for t in target],
        ))
    else:
        out.append("_Target is empty._")
    out.append("")
    return "\n".join(out)


def write_report(db_path: Path, out_path: Path) -> Path:
    conn = db.connect(db_path)
    conn.close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(db_path), encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest.report", description="Generate a Markdown validation report."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    path = write_report(args.db, args.out)
    print("wrote " + str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
