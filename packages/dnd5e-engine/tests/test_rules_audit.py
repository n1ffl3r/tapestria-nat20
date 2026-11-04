"""Keeps ``docs/srd-5.2.1-rules-audit.md`` honest (spec 02a, workstream C,
acceptance criterion 16).

The audit lists every mechanic row of ``docs/capabilities.md`` with the SRD
5.2.1 section that governs it, a verdict, and the tests that pin it. This test
checks the audit against both: every capability row has an audit row with the
same status, and every audit row cites at least one test that exists and
whose docstring or comments name one of the row's SRD sections. A row whose
verdict is a divergence names the ``BACKLOG.md`` entry that records it, and
that entry exists.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AUDIT_MD = ROOT / "docs" / "srd-5.2.1-rules-audit.md"
CAPABILITIES_MD = ROOT / "docs" / "capabilities.md"
BACKLOG_MD = ROOT / "BACKLOG.md"
ENGINE = ROOT / "packages" / "dnd5e-engine"

#: Capability sections whose rows are mechanics (the others hold counts or an
#: event list).
MECHANIC_SECTIONS = ("Combat loop", "Spatial", "Spells", "Monsters", "Characters", "Reactions")
STATUS = re.compile(r"(✅|⚠️|❌)")
#: The header rows of the capability tables.
HEADER_CELLS = {"Mechanic", "Spell mechanic", "Reaction", "Count", ""}


@dataclass(frozen=True)
class Row:
    section: str
    name: str
    status: str
    cells: list[str]


def _clean(name: str) -> str:
    return re.sub(r"[*`]", "", name).strip()


def _table_rows(path: Path, sections: tuple[str, ...] | None) -> list[Row]:
    rows: list[Row] = []
    section = ""
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if sections is not None and section not in sections:
            continue
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = _clean(cells[0])
        if name in HEADER_CELLS or len(cells) < 2:
            continue
        match = STATUS.search(cells[1])
        if match is None:
            continue  # a count row, not a mechanic
        rows.append(Row(section, name, match.group(1), cells))
    return rows


CAPABILITY_ROWS = _table_rows(CAPABILITIES_MD, MECHANIC_SECTIONS)
AUDIT_ROWS = _table_rows(AUDIT_MD, MECHANIC_SECTIONS) if AUDIT_MD.exists() else []

# An audit row's cells: Mechanic | Status | SRD 5.2.1 sections | Verdict | Tests.
SECTIONS_CELL, VERDICT_CELL, TESTS_CELL = 2, 3, 4


def _tests_of(row: Row) -> list[str]:
    return re.findall(r"`([^`]+::[^`]+)`", row.cells[TESTS_CELL])


def _sections_of(row: Row) -> list[str]:
    return [s.strip() for s in re.split(r";|<br>", row.cells[SECTIONS_CELL]) if s.strip()]


def _test_text(test_id: str) -> str:
    """A test's source with the comment lines just above it: its docstring and
    comments are where it cites the SRD."""
    rel, _, name = test_id.partition("::")
    path = ENGINE / rel
    assert path.exists(), f"{test_id}: {rel} does not exist"
    source = path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and lines[start - 1].lstrip().startswith(("#", "@")):
                start -= 1
            text = "\n".join(lines[start : node.end_lineno])
            module_doc = ast.get_docstring(tree) or ""
            return text + "\n" + module_doc
    raise AssertionError(f"{test_id}: no test function {name!r} in {rel}")


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("“", '"')).lower()


def test_the_audit_exists_and_lists_rows() -> None:
    assert AUDIT_MD.exists(), "docs/srd-5.2.1-rules-audit.md is missing"
    assert AUDIT_ROWS


def test_every_capability_row_has_an_audit_row() -> None:
    audited = {(r.section, r.name) for r in AUDIT_ROWS}
    missing = [f"{r.section}: {r.name}" for r in CAPABILITY_ROWS if (r.section, r.name) not in audited]
    assert not missing, "capabilities.md rows with no audit row: " + "; ".join(missing)


def test_the_audit_lists_no_row_capabilities_does_not() -> None:
    known = {(r.section, r.name) for r in CAPABILITY_ROWS}
    extra = [f"{r.section}: {r.name}" for r in AUDIT_ROWS if (r.section, r.name) not in known]
    assert not extra, "audit rows with no capabilities.md row: " + "; ".join(extra)


def test_each_audit_row_carries_its_capability_status() -> None:
    status = {(r.section, r.name): r.status for r in CAPABILITY_ROWS}
    wrong = [
        f"{r.section}: {r.name} (audit {r.status}, capabilities {status[(r.section, r.name)]})"
        for r in AUDIT_ROWS
        if (r.section, r.name) in status and status[(r.section, r.name)] != r.status
    ]
    assert not wrong, "status differs: " + "; ".join(wrong)


@pytest.mark.parametrize("row", AUDIT_ROWS, ids=lambda r: f"{r.section}:{r.name}")
def test_each_audit_row_cites_an_srd_section_and_a_test_that_names_it(row: Row) -> None:
    sections = _sections_of(row)
    assert sections, f"{row.name}: no SRD section"
    tests = _tests_of(row)
    assert tests, f"{row.name}: no test"
    wanted = [_normal(s) for s in sections]
    texts = [_normal(_test_text(t)) for t in tests]
    assert any(s in text for s in wanted for text in texts), (
        f"{row.name}: none of its tests {tests} names any of its SRD sections {sections}"
    )


def test_a_divergence_names_a_backlog_entry_that_exists() -> None:
    backlog = _normal(BACKLOG_MD.read_text())
    for row in AUDIT_ROWS:
        verdict = row.cells[VERDICT_CELL]
        for title in re.findall(r"BACKLOG: ([^;|]+?)(?:;|\||$)", verdict):
            assert _normal(title.strip(" .*`")) in backlog, (
                f"{row.name}: BACKLOG.md has no entry titled {title!r}"
            )
