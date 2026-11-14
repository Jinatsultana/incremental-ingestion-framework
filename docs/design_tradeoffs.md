# Design and Trade-off Note

## Key decisions

1. **The watermark is bookkeeping, not a filter.** Correctness comes from comparing each event to
   the target row's updated_at. Filtering on the watermark is the classic bug that silently drops
   late-arriving data, so the watermark is used only for statistics and for advancing the bookmark.
2. **Deterministic run_id** (hash of input bytes plus source name) instead of a timestamp. A
   timestamp makes every retry a new run, which destroys idempotency and replay history.
3. **Soft tombstones** (is_deleted = 1) instead of row deletion, so a late older update cannot
   resurrect a deleted record.
4. **One transaction per batch**, with the watermark updated inside it. This gives atomic
   commit-or-nothing semantics, so recovery needs no compensating logic.
5. **UNIQUE(order_id, updated_at) on history** as the database-level guarantee against double-apply,
   independent of application logic.
6. **Quarantine instead of discard**: every reject keeps file, line, raw row and a reason code, so it
   is traceable and fixable.
7. **Three tiers with one-way dependencies**: all SQL in the data tier, so the store can be replaced
   without touching rules, and the rules can be tested without a store.

## Trade-offs

- Batch held in memory: easy to reason about, but bounded by memory on very large files. Next step is
  chunked streaming plus executemany inserts.
- SQLite: zero setup and file based, but single-writer. Confined to db.py by design.
- Failed-run evidence is run-level: a rollback leaves no partial trace, which is correct for the
  target but means per-row debug detail for a failed batch is not preserved.
- Retry replaces the run row for the same deterministic run_id. The final state is what matters; full
  attempt history would need an attempt counter in the key.
- A delete for an unknown key is rejected rather than stored: there is no row to tombstone and no
  business rule supplied for delete-before-insert.
- Unknown additive columns are tolerated, but removing a required column fails the run.

## What I would do next

Streaming plus batched inserts; a quarantine re-processing command; reference snapshots with
checksums and approval metadata; replacement-code mapping for deprecations; partition by source date;
and alerting on run rows stuck in status running.
