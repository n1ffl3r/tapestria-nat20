"""The SRD gate decides from the official SRD 5.2.1 PDF's index (spec 02a, AC 2).

``gate_decision`` admits a name when ``srd/index.json`` lists it for the corpus category, or
an alias in ``srd/aliases.json`` ties it to a listed record, and quarantines anything else.
"""

import pytest

from tools.translators.srd_gate import (
    INDEX_CATEGORIES,
    NOT_IN_SRD,
    SrdIndex,
    gate_decision,
    load_index,
    name_keys,
)


def _index(**names: list[str]) -> SrdIndex:
    """A small index: category -> names, with one alias of a listed magic item."""
    return SrdIndex.from_documents(
        {"categories": {c: [{"name": n, "page": 1} for n in ns] for c, ns in names.items()}},
        {
            "aliases": [
                {
                    "name": "Ioun Stone of Agility",
                    "corpus_category": "items",
                    "of": "Ioun Stone",
                    "of_category": "magic_items",
                },
                {
                    "name": "Orphan Variant",
                    "corpus_category": "items",
                    "of": "Missing Parent",
                    "of_category": "magic_items",
                },
            ]
        },
    )


def test_listed_name_is_admitted() -> None:
    index = _index(spells=["Ice Knife"])
    assert gate_decision("Ice Knife", "spells", index).is_srd is True


def test_unlisted_name_is_quarantined() -> None:
    index = _index(spells=["Ice Knife"])
    decision = gate_decision("Arcane Vigor", "spells", index)
    assert decision.is_srd is False
    assert decision.quarantine_reason == NOT_IN_SRD


def test_a_name_listed_in_another_category_is_quarantined() -> None:
    index = _index(spells=["Shield"])
    assert gate_decision("Shield", "items", index).is_srd is False


def test_names_match_without_case_or_punctuation() -> None:
    index = _index(magic_items=["Devil's Sight Amulet"])
    assert gate_decision("devils sight amulet", "items", index).is_srd is True


def test_a_comma_name_also_matches_reordered() -> None:
    index = _index(equipment=["Lantern, Bullseye"])
    assert gate_decision("Bullseye Lantern", "items", index).is_srd is True
    assert gate_decision("Lantern, Bullseye", "items", index).is_srd is True
    assert name_keys("Lantern, Bullseye") == {"lanternbullseye", "bullseyelantern"}


def test_an_alias_of_a_listed_record_is_admitted() -> None:
    index = _index(magic_items=["Ioun Stone"])
    assert gate_decision("Ioun Stone of Agility", "items", index).is_srd is True


def test_an_alias_whose_parent_is_not_listed_is_quarantined() -> None:
    index = _index(magic_items=["Ioun Stone"])
    assert gate_decision("Orphan Variant", "items", index).is_srd is False


def test_an_unknown_corpus_category_is_an_error() -> None:
    with pytest.raises(ValueError, match="no SRD index categories"):
        gate_decision("Anything", "vehicles", _index())


def test_the_real_index_admits_the_records_the_old_gate_dropped() -> None:
    """Foundry's license tags left these out; the PDF lists them."""
    index = load_index()
    assert gate_decision("Ice Knife", "spells", index).is_srd is True
    for name in ("Giant Centipede", "Giant Wasp", "Werewolf"):
        assert gate_decision(name, "monsters", index).is_srd is True


def test_the_real_index_quarantines_arcane_vigor() -> None:
    """Foundry tags Arcane Vigor CC-BY-4.0; SRD 5.2.1 does not contain it."""
    assert gate_decision("Arcane Vigor", "spells", load_index()).is_srd is False


def test_every_corpus_category_maps_to_index_categories_the_index_has() -> None:
    index = load_index()
    for categories in INDEX_CATEGORIES.values():
        for category in categories:
            assert category in index.names, category
