@'
![ci](https://github.com/Jinatsultana/incremental-ingestion-framework/actions/workflows/ci.yml/badge.svg)

# Incremental Ingestion Framework

Incremental ingestion of a change feed using a **watermark**, with support for
**late-arriving records**, **deletes/tombstones**, **safe reruns**, and **recovery after partial
failure without duplicating target data**. Python standard library + SQLite, in three tiers.

## How to run

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt

    python -m ingest.cli init
    python -m ingest.cli run --source data/source/orders_batch_01.csv
    python -m ingest.cli run --source data/source/orders_batch_02.csv
    python -m ingest.cli reconcile --latest
    python -m ingest.cli watermark

Dashboard: `python -m streamlit run ui/app.py`
Validation report: `python -m ingest.report`
Tests: `python -m pytest -q` (27 passing)

## One-command demo

    python scripts/demo.py

Runs the entire story against a throwaway database: bootstrap, initial load, rerun idempotency,
late-arriving plus tombstone batch, injected partial failure (watermark provably unchanged), retry,
reconciliation, and a generated validation report.

## Three-tier architecture

| Tier | Responsibility | Files |
|---|---|---|
| Presentation | CLI + Streamlit console, no SQL | `ui/app.py`, `src/ingest/cli.py` |
| Application | validation, merge rules, watermark, orchestration | `validation.py`, `merge.py`, `pipeline.py`, `service.py` |
| Data | DDL, connections, transactions, all SQL | `schema.sql`, `db.py` |

Dependencies point one way: ui/cli -> service -> pipeline -> merge/validation -> db -> SQLite.

The boundaries are **enforced by tests, not convention**: `tests/test_architecture.py` parses every
module with `ast` and fails the build if presentation contains SQL, if presentation reaches past the
service facade, if the application tier imports a driver or a UI library, if a rule module is not
pure, or if any module other than `db.py` imports `sqlite3`. See `docs/architecture.md`.

## The four rules that make it correct

1. Watermark gate is efficiency only: rows older than the watermark are COUNTED as late but STILL APPLIED.
2. Per-key ordering: an event applies only if its updated_at is newer than the target row's, so
   arrival order never matters.
3. Tombstones are not deletes: op=delete sets is_deleted=1 and keeps the row, so a late older
   update cannot resurrect it.
4. One transaction per batch: data and watermark commit together, so a crash before COMMIT leaves
   nothing behind and the retry is safe. UNIQUE(order_id, updated_at) blocks a double apply.

## Data dictionary

Source feed columns: op (upsert|delete), order_id, customer_id, amount, status, updated_at (ISO-8601).
Natural key of a change event: (order_id, updated_at).

Target tables: target_orders (current state, PK order_id), order_history (append-only, UNIQUE
order_id+updated_at), watermark (last committed updated_at per source), run_history (status, counts,
timing, failure reason), rejected_records (quarantine with source line and reason code),
customer_ref (referential integrity).

## Reject codes

malformed_record, missing_required_field, invalid_operation, invalid_timestamp, invalid_amount,
invalid_status, invalid_reference_key, delete_unknown_key, stale_or_duplicate_version, pipeline_failure.

## Reconciliation

    input_rows = applied_rows + deleted_rows + stale_skipped_rows + rejected_rows

Asserted against run_history and re-counted from the persisted quarantine rows by
`python -m ingest.cli reconcile --latest`, and rendered as PASS/FAIL verdicts in
`python -m ingest.report`.

## Test suite (27 tests)

Pipeline behaviour: happy path, empty batch, nulls, malformed row, unexpected schema change,
duplicate event, invalid reference key, invalid amount and status, rerun idempotency, late-arriving
data, out-of-order arrival, tombstone plus resurrection attempt, delete of an unknown key, partial
failure with clean retry, and a missing input file. Architecture: twelve tier-boundary assertions.

## Measured results

100,000 seeded change events: 85,121 applied, 2,386 deleted, 8,988 rejected, 93,276 late-arriving
events all applied. 100,100 input rows reconciled with duplicate_history_keys = 0.
Evidence: `examples/validation_report.md`, `examples/perf_100k.json`.

## Optimisation implemented

Target keys are preloaded once per run, so each merge decision is an O(1) in-memory comparison
instead of a per-row database lookup. Next step: chunked streaming plus executemany batch inserts.

## Assumptions and limitations

updated_at must be ISO-8601 (naive treated as UTC); a delete for a never-seen key is rejected;
SQLite is single-writer; order_history is a change log, not a run log; no scheduler, API, alerting
or cloud deployment by design.

## Documentation map

| Document | What it answers |
|---|---|
| `README.md` | how to run it, data dictionary, rules, limitations |
| `docs/architecture.md` | the three tiers, dependency direction, enforcing tests |
| `docs/design_tradeoffs.md` | decisions, trade-offs, what I would do next |
| `examples/validation_report.md` | generated reconciliation verdicts |

## AI use disclosure

AI tools were used as a build assistant; the implementation was run, tested and inspected locally
by the candidate, who is responsible for the design and its limitations.
'@ | Set-Content -Path README.md -Encoding utf8