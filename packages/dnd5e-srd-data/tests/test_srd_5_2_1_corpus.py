"""The shipped corpus holds SRD 5.2.1 content only (spec 02a, AC 3).

Every record in ``canonical/`` passes the PDF-backed SRD gate, the records the old gate
dropped are present, and the one record SRD 5.2.1 does not contain is gone.
"""

import json
from pathlib import Path

import pytest

from tools.translators.srd_gate import INDEX_CATEGORIES, gate_decision, load_index

CANONICAL = Path(__file__).resolve().parents[1] / "src" / "dnd5e_srd_data" / "canonical"


def _names(category: str) -> list[str]:
    return sorted(
        json.loads(path.read_text(encoding="utf-8"))["name"]
        for path in (CANONICAL / category).glob("*.json")
    )


@pytest.mark.parametrize("category", sorted(INDEX_CATEGORIES))
def test_every_record_is_srd_5_2_1(category: str) -> None:
    index = load_index()
    outside = [n for n in _names(category) if not gate_decision(n, category, index).is_srd]
    assert outside == []


def test_arcane_vigor_is_gone() -> None:
    assert "Arcane Vigor" not in _names("spells")
    assert not (CANONICAL / "spells" / "arcane-vigor.json").exists()


def test_ice_knife_is_present() -> None:
    assert "Ice Knife" in _names("spells")


@pytest.mark.parametrize("name", ["Giant Centipede", "Giant Wasp", "Werewolf"])
def test_missing_stat_blocks_are_present(name: str) -> None:
    assert name in _names("monsters")


@pytest.mark.parametrize(
    ("slug", "ac", "hp", "cr"),
    [
        # "Small Beast, Unaligned / AC 14 ... HP 9 (2d6 + 2)", CR 1/4
        ("giant-centipede", 14, 9, 0.25),
        # "Medium Beast, Unaligned / AC 13 ... HP 22 (5d8)", CR 1/2
        ("giant-wasp", 13, 22, 0.5),
        # "Large Beast, Unaligned / AC 14 ... HP 26 (4d10 + 4)", CR 1
        ("giant-spider", 14, 26, 1.0),
        # "Medium or Small Monstrosity (Lycanthrope) / AC 15 ... HP 71 (11d8 + 22)", CR 3
        ("werewolf", 15, 71, 3.0),
    ],
)
def test_stat_blocks_match_the_srd(slug: str, ac: int, hp: int, cr: float) -> None:
    record = json.loads((CANONICAL / "monsters" / f"{slug}.json").read_text(encoding="utf-8"))
    assert (record["ac"], record["hp"], record["cr"]) == (ac, hp, cr)


def test_beasts_are_not_their_giant_insect_summon_forms() -> None:
    """The Giant Insect spell's summon forms share these slugs; the beasts are kept."""
    for slug in ("giant-centipede", "giant-spider", "giant-wasp"):
        record = json.loads((CANONICAL / "monsters" / f"{slug}.json").read_text(encoding="utf-8"))
        assert "/actors24/beast/" in record["provenance"]["source_url"], slug


def test_werewolf_is_a_monstrosity() -> None:
    record = json.loads((CANONICAL / "monsters" / "werewolf.json").read_text(encoding="utf-8"))
    assert record["creature_type"] == "monstrosity"
