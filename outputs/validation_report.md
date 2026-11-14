# Validation Report

Generated: 2026-11-14T10:17:12Z
Warehouse: outputs\warehouse.db
Current watermark: 2026-11-03T08:20:00Z

## Table counts

| Table | Rows |
|---|---|
| target_orders | 41557 |
| order_history | 87516 |
| rejected_records | 8995 |
| run_history | 3 |

## Run history (most recent first)

| run_id | source_file | status | input | applied | deleted | late | stale | rejected | duration_ms |
|---|---|---|---|---|---|---|---|---|---|
| 90d5d91880e1f649 | orders_generated.csv | success | 100100 | 85121 | 2386 | 93276 | 3605 | 8988 | 7927.005 |
| 47a1817a77295186 | orders_batch_03.csv | success | 5 | 3 | 2 | 0 | 0 | 0 | 4.361 |
| 61aa572c12df2112 | orders_batch_01.csv | success | 11 | 0 | 0 | 4 | 4 | 7 | 5.154 |

## Reconciliation - latest run

Run: 90d5d91880e1f649 (status: success)

| Measure | Value |
|---|---|
| input_rows | 100100 |
| applied_rows | 85121 |
| deleted_rows | 2386 |
| stale_skipped_rows | 3605 |
| rejected_rows | 8988 |
| accounted_rows | 100100 |
| late_rows | 93276 |
| rolled_back | False |

Rule: input_rows = applied + deleted + stale_skipped + rejected.

## Verdict

- Reconciliation: PASS
- Duplicate change events: PASS (0 duplicate keys)
- Quarantined rows retained: 8995

## Quarantine breakdown - latest run

| reason_code | rows |
|---|---|
| delete_unknown_key | 2164 |
| invalid_amount | 1838 |
| invalid_reference_key | 1061 |
| invalid_status | 976 |
| invalid_timestamp | 1967 |
| missing_required_field | 982 |

## Target state sample

| order_id | customer_id | amount | status | updated_at | is_deleted |
|---|---|---|---|---|---|
| O000001 | C003 | 778.41 | new | 2026-11-01T19:27:50Z | 0 |
| O000002 | C002 | 35.79 | cancelled | 2026-11-02T01:31:59Z | 0 |
| O000003 | C001 | 91.69 | paid | 2026-11-01T08:12:20Z | 1 |
| O000004 | C001 | 369.62 | shipped | 2026-11-02T00:59:45Z | 0 |
| O000006 | C003 | 304.78 | cancelled | 2026-11-01T16:53:05Z | 0 |
| O000007 | C002 | 535.82 | new | 2026-11-01T11:30:04Z | 0 |
| O000008 | C002 | 114.66 | shipped | 2026-11-01T20:05:14Z | 0 |
| O000009 | C003 | 853.05 | cancelled | 2026-11-02T00:48:54Z | 0 |
| O000010 | C002 | 834.38 | paid | 2026-11-01T04:10:21Z | 0 |
| O000011 | C001 | 610.82 | cancelled | 2026-11-01T23:10:14Z | 0 |
| O000012 | C001 | 141.03 | shipped | 2026-11-01T05:55:18Z | 0 |
| O000013 | C002 | 708.01 | cancelled | 2026-11-02T02:21:31Z | 0 |
| O000014 | C002 | 124.85 | new | 2026-11-02T01:50:01Z | 0 |
| O000017 | C001 | 390.45 | new | 2026-11-01T15:58:24Z | 0 |
| O000018 | C002 | 156.69 | paid | 2026-11-01T19:48:56Z | 0 |
