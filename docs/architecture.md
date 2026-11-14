# Architecture

## Three tiers, one-way dependencies

| Tier | Responsibility | Modules | May depend on |
|---|---|---|---|
| 1 Presentation | Operator interface: trigger a run, read results | ui/app.py, cli.py | the service facade only |
| 2 Application | Validation, merge rules, watermark, orchestration | service.py, pipeline.py, merge.py, validation.py | lower application modules, data tier |
| 3 Data | DDL, connections, transactions, persistence | db.py, schema.sql, data/ | SQLite and files |

Dependency direction (never upward):

    ui/app.py (Streamlit)      cli.py (argparse)
              \                  /
               v                v
              service.py (facade, application tier)
                       |
                       v
              pipeline.py (orchestration)
                /            \
               v              v
     merge.py / validation.py  db.py  <-- the only module with SQL
               |                 |
               +--------+--------+
                        v
              schema.sql  ->  SQLite warehouse
                        ^
                        |
              data/source/*.csv  (change feed)
## How one run crosses the tiers

1. Presentation parses arguments or button state and calls service.run_ingestion(...).
2. Application reads the CSV, checks the schema, asks the data tier for the watermark and a target
   snapshot, then for each row calls validation.validate_record and merge.decide. It opens one
   transaction and asks the data tier to write.
3. Data owns every statement: upsert, tombstone, history, quarantine, watermark, run history.
   It commits or rolls back.
4. Control returns upward as plain dictionaries. No cursor, no row object and no SQL crosses a
   boundary.

## Enforced by tests, not by convention

tests/test_architecture.py parses the source with ast and fails if a boundary is crossed:

| Rule | Enforcing test |
|---|---|
| Presentation contains no SQL and no sqlite3 import | test_presentation_tier_contains_no_sql |
| Presentation may call service.py only | test_presentation_reaches_application_only_through_the_facade |
| Application imports no driver and no UI library | test_application_tier_imports_no_driver_and_no_ui |
| Rule modules are pure (no db, no I/O) | test_rules_are_pure |
| Exactly one module imports sqlite3 | test_only_the_data_tier_knows_sqlite |
| DDL ships beside the code | test_schema_lives_beside_the_code |

## What this buys

- Swappable store: SQLite to Postgres touches db.py and schema.sql only.
- Swappable interface: Streamlit to REST, or CLI to a scheduler, touches the presentation tier only.
- Testable rules: merge.decide and validation.validate_record are pure functions, so domain edge
  cases are unit-tested in milliseconds with no database.
- Honest seams: quarantine, run metrics and the watermark are data-tier operations, so
  observability is a schema concern rather than scattered prints.
