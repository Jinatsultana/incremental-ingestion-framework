"""Architecture tests: the three tiers are enforced, not just described.

These tests parse the source with ast and assert the dependency direction:

    presentation (ui/app.py, cli.py) -> application (service, pipeline, merge, validation)
                                     -> data (db.py, schema.sql)

If someone later writes SQL in the UI, or lets a rule import the database, this
suite fails. That is what makes three-tier a property, not a claim.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "ingest"

PRESENTATION = [ROOT / "ui" / "app.py", PKG / "cli.py"]
APPLICATION = [PKG / "service.py", PKG / "pipeline.py", PKG / "merge.py", PKG / "validation.py"]
PURE_RULES = [PKG / "merge.py", PKG / "validation.py"]

# Upper-case only: real SQL here is upper-case, so prose containing the word
# "update" will not trigger a false positive.
SQL_KEYWORDS = re.compile(r"\b(SELECT |INSERT INTO|UPDATE \w+ SET|DELETE FROM|CREATE TABLE|PRAGMA )")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def package_dependencies(path: Path) -> set[str]:
    """Which modules of this package the file imports (. and ingest. forms)."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    deps: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if node.level > 0:
            if module:
                deps.add(module.split(".")[0])
            else:
                deps.update(alias.name for alias in node.names)
        elif module == "ingest":
            deps.update(alias.name for alias in node.names)
        elif module.startswith("ingest."):
            deps.add(module.split(".")[1])
    return deps

@pytest.mark.parametrize("path", PRESENTATION, ids=lambda p: p.name)
def test_presentation_tier_contains_no_sql(path: Path):
    """Tier 1 must not know that SQL exists."""
    assert "sqlite3" not in imported_modules(path), f"{path.name} imports sqlite3"
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if SQL_KEYWORDS.search(line):
            pytest.fail(f"SQL found in presentation tier {path.name}:{number}: {line.strip()}")


@pytest.mark.parametrize("path", PRESENTATION, ids=lambda p: p.name)
def test_presentation_reaches_application_only_through_the_facade(path: Path):
    """Tier 1 may call service.py and nothing below it."""
    forbidden = package_dependencies(path) & {"pipeline", "merge", "validation", "db"}
    assert not forbidden, f"{path.name} bypasses the service facade: {sorted(forbidden)}"


@pytest.mark.parametrize("path", APPLICATION, ids=lambda p: p.name)
def test_application_tier_imports_no_driver_and_no_ui(path: Path):
    """Tier 2 must not depend on a database driver or a presentation library."""
    names = imported_modules(path) | package_dependencies(path)
    forbidden = names & {"sqlite3", "streamlit", "argparse"}
    assert not forbidden, f"{path.name} imports layer-breaking module(s): {sorted(forbidden)}"


@pytest.mark.parametrize("path", PURE_RULES, ids=lambda p: p.name)
def test_rules_are_pure(path: Path):
    """The rules must be testable without a warehouse: no db, no I/O modules."""
    names = imported_modules(path) | package_dependencies(path)
    forbidden = names & {"db", "sqlite3", "csv", "pathlib", "streamlit"}
    assert not forbidden, f"{path.name} is not pure: {sorted(forbidden)}"

def test_only_the_data_tier_knows_sqlite():
    """Exactly one module may import sqlite3: the data tier."""
    importers = {p.name for p in PKG.glob("*.py") if "sqlite3" in imported_modules(p)}
    assert importers == {"db.py"}, f"sqlite3 imported by {sorted(importers)}"


def test_schema_lives_beside_the_code():
    """The DDL is a data-tier artifact and must ship with the package."""
    ddl = (PKG / "schema.sql").read_text(encoding="utf-8-sig")
    for table in ("target_orders", "order_history", "watermark", "run_history", "rejected_records"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl

