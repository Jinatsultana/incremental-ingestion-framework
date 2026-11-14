-- ============================================================================
-- DATA TIER: warehouse schema (the ONLY place raw SQL is written)
-- ============================================================================

PRAGMA foreign_keys = ON;

-- Current state of each order: one row per business key.
CREATE TABLE IF NOT EXISTS target_orders (
    order_id     TEXT PRIMARY KEY,
    customer_id  TEXT    NOT NULL,
    amount       REAL    NOT NULL,
    status       TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    is_deleted   INTEGER NOT NULL DEFAULT 0,
    deleted_at   TEXT,
    last_run_id  TEXT    NOT NULL,
    ingested_at  TEXT    NOT NULL
);

-- Append-only history. UNIQUE(order_id, updated_at) = retries cannot duplicate.
CREATE TABLE IF NOT EXISTS order_history (
    history_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT    NOT NULL,
    op           TEXT    NOT NULL CHECK (op IN ('upsert','delete')),
    customer_id  TEXT    NOT NULL,
    amount       REAL,
    status       TEXT,
    updated_at   TEXT    NOT NULL,
    run_id       TEXT    NOT NULL,
    applied_at   TEXT    NOT NULL,
    UNIQUE (order_id, updated_at)
);

-- Incremental bookmark: highest source timestamp successfully committed.
CREATE TABLE IF NOT EXISTS watermark (
    source_name      TEXT PRIMARY KEY,
    last_updated_at  TEXT,
    last_run_id      TEXT,
    advanced_at      TEXT
);

-- One row per execution -> observability + recovery evidence.
CREATE TABLE IF NOT EXISTS run_history (
    run_id             TEXT PRIMARY KEY,
    source_name        TEXT    NOT NULL,
    source_file        TEXT    NOT NULL,
    watermark_before   TEXT,
    watermark_after    TEXT,
    started_at         TEXT    NOT NULL,
    ended_at           TEXT    NOT NULL,
    status             TEXT    NOT NULL,
    input_rows         INTEGER NOT NULL DEFAULT 0,
    applied_rows       INTEGER NOT NULL DEFAULT 0,
    deleted_rows       INTEGER NOT NULL DEFAULT 0,
    late_rows          INTEGER NOT NULL DEFAULT 0,
    stale_skipped_rows INTEGER NOT NULL DEFAULT 0,
    rejected_rows      INTEGER NOT NULL DEFAULT 0,
    duration_ms        REAL    NOT NULL DEFAULT 0,
    failure_reason     TEXT
);

-- Quarantine: every rejected row traceable to its source line.
CREATE TABLE IF NOT EXISTS rejected_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT    NOT NULL,
    source_file   TEXT    NOT NULL,
    source_line   INTEGER NOT NULL,
    order_id      TEXT,
    reason_code   TEXT    NOT NULL,
    reason_detail TEXT    NOT NULL,
    raw_record    TEXT    NOT NULL,
    logged_at     TEXT    NOT NULL
);

-- Reference data for referential-integrity checks.
CREATE TABLE IF NOT EXISTS customer_ref (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    segment       TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_updated_at ON target_orders(updated_at);
CREATE INDEX IF NOT EXISTS idx_history_order     ON order_history(order_id);
CREATE INDEX IF NOT EXISTS idx_rejected_run      ON rejected_records(run_id);
CREATE INDEX IF NOT EXISTS idx_run_status        ON run_history(status);