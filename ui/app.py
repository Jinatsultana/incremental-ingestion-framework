"""PRESENTATION TIER — Streamlit dashboard.

Imports service.py and nothing below it: no SQL, no sqlite3, no business rules.
That is what keeps the three tiers separately replaceable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ingest import service  # noqa: E402

DB_PATH = ROOT / "outputs" / "warehouse.db"
SOURCE_DIR = ROOT / "data" / "source"
REFERENCE_PATH = ROOT / "data" / "reference" / "customers.csv"

st.set_page_config(page_title="Incremental Ingestion Console", page_icon="🔄", layout="wide")
st.title("🔄 Incremental Ingestion Console")
st.caption(
    "Presentation tier -> service.py (application) -> db.py + SQLite (data). "
    "Watermark-driven ingest with tombstones, late-arriving records and idempotent reruns."
)

# ---------------------------------------------------------------- sidebar ---
with st.sidebar:
    st.header("Run a batch")
    batch_files = sorted(p.name for p in SOURCE_DIR.glob("*.csv"))
    if not batch_files:
        st.error("No CSVs found in data/source.")
        st.stop()

    chosen = st.selectbox("Source file", batch_files, index=0)
    inject = st.checkbox("Simulate partial failure (crash before COMMIT)")
    fail_after = st.number_input("Fail after N rows", min_value=1, value=2, step=1) if inject else None

    if st.button("Run ingestion", type="primary", use_container_width=True):
        result = service.run_ingestion(
            DB_PATH, SOURCE_DIR / chosen, source_name="orders_feed", fail_after=fail_after
        )
        if result["status"] == "success":
            st.success("Committed run " + result["run_id"])
        else:
            st.error("Run FAILED and was rolled back: " + str(result["failure_reason"]))
        st.rerun()

    st.divider()
    st.subheader("Reference data")
    if st.button("Reload customer reference", use_container_width=True):
        loaded = service.load_customer_reference(DB_PATH, REFERENCE_PATH)
        st.info("Loaded " + str(loaded) + " customers")
        st.rerun()

    st.divider()
    st.caption(
        "Demo: load orders_batch_01.csv, then tick 'Simulate partial failure' on "
        "orders_batch_03.csv, then run orders_batch_03.csv again."
    )

# ---------------------------------------------------------------- overview ---
overview = service.get_overview(DB_PATH)
counts = overview["counts"]
latest = overview["latest_run"]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Watermark", overview["watermark"] or "none")
c2.metric("Target orders", counts["target_orders"])
c3.metric("History rows", counts["order_history"])
c4.metric("Quarantined", counts["rejected_records"])
c5.metric("Duplicate keys", overview["duplicate_history_keys"], help="Must stay 0: nothing applied twice")

if overview["duplicate_history_keys"] != 0:
    st.error("Duplicate change events detected: idempotency broken.")
else:
    st.success("No duplicate change events: retries and reruns did not double-apply anything.")

st.divider()

runs_tab, target_tab, quarantine_tab, how_tab = st.tabs(
    ["Run history", "Target state", "Quarantine", "How it works"]
)

with runs_tab:
    if not overview["runs"]:
        st.info("No runs yet: use the sidebar to ingest a batch.")
    else:
        st.dataframe(overview["runs"], use_container_width=True, hide_index=True)

    if latest:
        summary = service.reconcile(DB_PATH, latest["run_id"])
        a, b, c = st.columns(3)
        a.metric("Input rows", summary["input_rows"])
        b.metric("Accounted rows", summary["accounted_rows"])
        c.metric("Reconciled", "yes" if summary["reconciled"] else "NO")
        st.caption(
            "input = applied(" + str(summary["applied_rows"]) + ")"
            " + deleted(" + str(summary["deleted_rows"]) + ")"
            " + stale_skipped(" + str(summary["stale_skipped_rows"]) + ")"
            " + rejected(" + str(summary["rejected_rows"]) + ")"
            + ("  |  run rolled back, nothing committed" if summary["rolled_back"] else "")
        )
        st.json(summary, expanded=False)

with target_tab:
    st.caption("Current state: one row per order_id. is_deleted = 1 means a tombstone was applied.")
    st.dataframe(overview["target_orders"], use_container_width=True, hide_index=True)

with quarantine_tab:
    st.caption("Reject reasons for run " + str(overview["selected_run_id"]) + ": every row is traceable to its source line.")
    if overview["rejects"]:
        st.bar_chart(overview["rejects"])
        st.table(overview["rejects"])
    else:
        st.info("No rejected rows for the selected run.")

with how_tab:
    st.markdown("**The four rules that make this pipeline correct**")
    st.markdown(
        "**1. Watermark gate** - rows older than the committed watermark are late-arriving. "
        "They are counted as late_rows but STILL APPLIED. The watermark is a hint, never a filter."
    )
    st.markdown(
        "**2. Per-key ordering** - a change applies only if its updated_at is newer than the target "
        "row's. Older or equal events are skipped as stale_or_duplicate_version. This makes the result "
        "independent of arrival order."
    )
    st.markdown(
        "**3. Tombstones are not deletes** - op=delete sets is_deleted=1 and keeps the row, so a late "
        "older update can never resurrect a deleted order."
    )
    st.markdown(
        "**4. One transaction per batch** - data and the watermark commit together. A crash before "
        "COMMIT changes nothing, so retry is safe, and UNIQUE(order_id, updated_at) makes a "
        "double-apply impossible."
    )
    st.markdown("**Three-tier architecture**")
    st.code(
        "ui/app.py                (presentation - no SQL)\n"
        "    |\n"
        "    v\n"
        "service.py -> pipeline.py -> merge.py / validation.py   (application)\n"
        "    |\n"
        "    v\n"
        "db.py + schema.sql -> SQLite   (data - the only place SQL lives)",
        language="text",
    )