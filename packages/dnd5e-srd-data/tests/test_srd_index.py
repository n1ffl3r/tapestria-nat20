"""The pinned official SRD 5.2.1 PDF and the index built from it (spec 02a, AC 1).

These tests are hermetic: they read the committed ``srd/index.json`` and ``srd/aliases.json``.
Rebuilding the index from the PDF (``make srd-index``) is the maintainer flow, and it also
checks every alias's evidence phrase against the PDF's text.
"""

import hashlib
import json
from pathlib import Path

import pytest

from tools.srd_index import CATEGORIES, fix_case, heading_before

ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((ROOT / "raw_sources" / "PINS.json").read_text(encoding="utf-8"))
INDEX = json.loads((ROOT / "srd" / "index.json").read_text(encoding="utf-8"))
ALIASES = json.loads((ROOT / "srd" / "aliases.json").read_text(encoding="utf-8"))["aliases"]
SRD_PDF_SHA256 = "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"


def test_pins_record_the_official_pdf() -> None:
    pin = PINS["srd_pdf"]
    assert pin["version"] == "5.2.1"
    assert pin["sha256"] == SRD_PDF_SHA256


def test_pinned_pdf_matches_its_sha256() -> None:
    pdf = ROOT / PINS["srd_pdf"]["path"]
    if not pdf.is_file():
        pytest.skip("the PDF ships in the repository, not in the sdist")
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == SRD_PDF_SHA256


def test_index_was_built_from_the_pinned_pdf() -> None:
    assert INDEX["source"] == {
        "path": PINS["srd_pdf"]["path"],
        "sha256": SRD_PDF_SHA256,
        "version": "5.2.1",
    }


def test_index_has_every_category() -> None:
    assert sorted(INDEX["categories"]) == sorted(CATEGORIES)


@pytest.mark.parametrize(
    ("category", "count"),
    [
        ("spells", 339),
        ("classes", 12),
        ("subclasses", 12),
        ("species", 9),
        ("backgrounds", 4),
        ("feats", 17),
        ("conditions", 15),
    ],
)
def test_index_counts_match_the_srd(category: str, count: int) -> None:
    assert len(INDEX["categories"][category]) == count


def test_index_spells_carry_level_and_classes() -> None:
    ice_knife = next(s for s in INDEX["categories"]["spells"] if s["name"] == "Ice Knife")
    assert ice_knife["level"] == 1
    assert ice_knife["classes"] == ["druid", "sorcerer", "wizard"]
    assert all(s["name"] != "Arcane Vigor" for s in INDEX["categories"]["spells"])


def test_index_lists_the_stat_blocks_the_corpus_lacked() -> None:
    names = {s["name"] for s in INDEX["categories"]["stat_blocks"]}
    assert {"Giant Centipede", "Giant Wasp", "Werewolf"} <= names


def test_index_entries_are_sorted_and_unique() -> None:
    for category, entries in INDEX["categories"].items():
        names = [e["name"] for e in entries]
        assert names == sorted(set(names)), category
        assert all(isinstance(e["page"], int) and e["page"] > 0 for e in entries), category


def test_every_alias_names_a_parent_the_index_lists() -> None:
    for alias in ALIASES:
        if alias["of_category"] == "text":
            continue
        parents = {e["name"] for e in INDEX["categories"][alias["of_category"]]}
        assert alias["of"] in parents, alias["name"]


def test_every_alias_is_explained() -> None:
    for alias in ALIASES:
        assert alias["kind"] in ("variant", "helper"), alias["name"]
        assert alias["reason"] and alias["evidence"], alias["name"]


def test_fix_case_undoes_small_capitals() -> None:
    assert fix_case("Acid SplASh") == "Acid Splash"
    assert fix_case("Acid Splash") == "Acid Splash"


def test_a_wrapped_heading_is_joined() -> None:
    lines = [(1, "Amulet of Proof against Detection"), (1, "and Location"), (1, "Wondrous Item")]
    assert heading_before(lines, 2) == "Amulet of Proof against Detection and Location"
