"""The SRD 5.2.1 rules-text ledger (spec 02a, acceptance criterion 4).

``audit/srd_5_2_1_ledger.json`` holds one entry per audited corpus record. The hermetic
tests check its shape, that no audited record lacks an entry, and that the corpus still
holds the values the ledger says it was compared against. The PDF test rebuilds the
ledger from the pinned PDF (``tools/srd_audit/ledger.py``, without the raw Foundry
sources) and checks it against the committed one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tools.srd_audit import ledger as ledger_tool
from tools.srd_audit import spells, stat_blocks

ROOT = Path(__file__).resolve().parents[1]
LEDGER = json.loads((ROOT / "audit" / "srd_5_2_1_ledger.json").read_text(encoding="utf-8"))
DIVERGENCES = json.loads(
    (ROOT / "audit" / "srd_5_2_1_divergences.json").read_text(encoding="utf-8")
)
INDEX = json.loads((ROOT / "srd" / "index.json").read_text(encoding="utf-8"))
CANONICAL = ROOT / "src" / "dnd5e_srd_data" / "canonical"
CATEGORIES = ("monsters", "spell_lists", "spells")
ISSUE = re.compile(r"^https://github\.com/n1ffl3r/[\w.-]+/issues/\d+$")
ENTRY_KEYS = {
    "name",
    "srd_page",
    "status",
    "compared",
    "read_by_hand",
    "not_compared",
    "changes",
    "differences",
    "divergence",
}


def _records(category: str) -> dict[str, dict[str, Any]]:
    return ledger_tool.canonical_records(category)


def test_ledger_names_its_source_and_categories() -> None:
    assert LEDGER["source"] == INDEX["source"]
    assert LEDGER["audited_categories"] == list(CATEGORIES)
    assert sorted(LEDGER["records"]) == list(CATEGORIES)


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_audited_record_has_one_entry(category: str) -> None:
    """No corpus record of an audited category lacks an entry, and no entry names a record
    the corpus lacks."""
    assert sorted(LEDGER["records"][category]) == sorted(_records(category))


def test_spell_entries_carry_the_index_page() -> None:
    pages = {s["name"]: s["page"] for s in INDEX["categories"]["spells"]}
    for slug, entry in LEDGER["records"]["spells"].items():
        assert entry["srd_page"] == pages[entry["name"]], slug


def test_monster_entries_carry_their_stat_block_page() -> None:
    """A monster's page is its stat block's in the index, or for a variant its parent's;
    a helper with no stat block records its parent's page in the divergences file."""
    pages = {s["name"]: s["page"] for s in INDEX["categories"]["stat_blocks"]}
    aliases = json.loads((ROOT / "srd" / "aliases.json").read_text(encoding="utf-8"))
    parents = {a["name"]: a["of"] for a in aliases["aliases"] if a["corpus_category"] == "monsters"}
    for slug, entry in LEDGER["records"]["monsters"].items():
        parent = parents.get(entry["name"], entry["name"])
        if parent in pages:
            assert entry["srd_page"] == pages[parent], slug
        else:
            assert entry["srd_page"] == DIVERGENCES["monsters"][slug]["srd_page"], slug


@pytest.mark.parametrize("category", CATEGORIES)
def test_entry_schema(category: str) -> None:
    for slug, entry in LEDGER["records"][category].items():
        assert set(entry) <= ENTRY_KEYS, slug
        assert {"name", "srd_page", "status", "compared"} <= set(entry), slug
        assert isinstance(entry["srd_page"], int) and entry["srd_page"] > 0, slug
        assert entry["status"] in ledger_tool.STATUSES, slug
        # A helper with no stat block compares nothing and is a divergence.
        assert entry["compared"] or "stat_block" in entry.get("differences", {}), slug
        assert ("changes" in entry) == (entry["status"] == "corrected") or (
            entry["status"] == "divergence"
        ), slug
        for change in entry.get("changes", []):
            assert set(change) == {"field", "from", "to", "srd"}, slug
            assert change["from"] != change["to"] and change["srd"], slug
        if entry["status"] == "divergence":
            assert sorted(entry["differences"]) == sorted(entry["divergence"]["fields"]), slug
            assert entry["divergence"] == DIVERGENCES[category][slug], slug
        else:
            assert "differences" not in entry and "divergence" not in entry, slug


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_divergence_links_an_issue(category: str) -> None:
    """Each recorded divergence names the issue where the owner approves it, and is a
    record the ledger marks as a divergence."""
    for slug, divergence in DIVERGENCES[category].items():
        assert ISSUE.match(divergence["issue"]), slug
        assert divergence["reason"], slug
        assert LEDGER["records"][category][slug]["status"] == "divergence", slug


def test_the_corpus_holds_what_the_ledger_compared() -> None:
    """For each spell, the corpus's fields equal the SRD values the ledger compared them
    against, apart from a divergence's recorded differences, and a correction's ``to``
    is the corpus value now."""
    records = _records("spells")
    classes = ledger_tool.spell_classes(_records("spell_lists"))
    for slug, entry in LEDGER["records"]["spells"].items():
        corpus = spells.corpus_facts(records[slug], classes.get(slug, []))
        for field in entry.get("not_compared", {}):
            corpus.pop(field, None)
        differences = entry.get("differences", {})
        for field, srd_value in entry["compared"].items():
            if field in differences:
                assert differences[field]["srd"] == srd_value, (slug, field)
                assert differences[field]["corpus"] == corpus.get(field), (slug, field)
            else:
                assert corpus.get(field) == srd_value, (slug, field)
        for change in entry.get("changes", []):
            assert change["to"] == corpus.get(change["field"]), (slug, change["field"])


def test_the_corpus_holds_what_the_ledger_compared_for_monsters() -> None:
    """As for spells: compared with the SRD values the ledger records, each monster differs
    only in a divergence's recorded fields, and a correction's ``to`` is the corpus value."""
    records = _records("monsters")
    for slug, entry in LEDGER["records"]["monsters"].items():
        corpus = stat_blocks.corpus_facts(records[slug])
        srd = {k: v for k, v in entry["compared"].items() if v is not None}
        held = {k: corpus[k] for k in entry["compared"] if k in corpus}
        differences = stat_blocks.compare(srd, held)
        assert sorted(differences) == sorted(entry.get("differences", {})) or (
            "stat_block" in entry.get("differences", {})
        ), slug
        for change in entry.get("changes", []):
            assert change["to"] == corpus.get(change["field"]), (slug, change["field"])


def test_status_counts() -> None:
    """Every spell and spell list is reviewed; the counts change only with the audit."""
    counts = {
        category: {
            status: sum(e["status"] == status for e in LEDGER["records"][category].values())
            for status in ledger_tool.STATUSES
        }
        for category in CATEGORIES
    }
    assert sum(counts["spells"].values()) == 339
    assert sum(counts["monsters"].values()) == 344
    assert counts["monsters"]["divergence"] == len(DIVERGENCES["monsters"])
    assert counts["spell_lists"] == {"verified": 5, "corrected": 0, "divergence": 3}
    assert counts["spells"]["divergence"] == len(DIVERGENCES["spells"])


@pytest.fixture(scope="module")
def rebuilt() -> dict[str, Any]:
    if not ledger_tool.pdf_text.srd_index.pinned_pdf()[0].is_file():
        pytest.skip("the PDF ships in the repository, not in the sdist")
    return ledger_tool.build(with_changes=False)


def test_the_ledger_matches_a_rebuild_from_the_pdf(rebuilt: dict[str, Any]) -> None:
    """Rebuilt from the pinned PDF and the corpus, the ledger is the committed one, except
    that without the raw Foundry sources a corrected record reads as verified."""
    committed = json.loads(json.dumps(LEDGER))
    for entries in committed["records"].values():
        for entry in entries.values():
            entry.pop("changes", None)
            if entry["status"] == "corrected":
                entry["status"] = "verified"
    assert rebuilt == committed
