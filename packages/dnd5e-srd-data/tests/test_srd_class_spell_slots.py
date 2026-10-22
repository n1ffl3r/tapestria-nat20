"""The engine's spell slot tables against the SRD 5.2.1 Features tables.

The corpus holds each class's spellcasting progression, not slot counts, so the counts a
character gets are the engine's (``dnd5e_engine.spellcasting``). The ledger cannot compare
them; this test reads each caster class's table from the PDF and checks the engine row for
row (spec 02a: the audit covers spell slots).
"""

from __future__ import annotations

import pytest

from tools.srd_audit import classes, pdf_text

spellcasting = pytest.importorskip("dnd5e_engine.spellcasting")

CASTERS = ("Bard", "Cleric", "Druid", "Sorcerer", "Wizard", "Paladin", "Ranger", "Warlock")


@pytest.fixture(scope="module")
def texts() -> dict[str, classes.ClassText]:
    if not pdf_text.srd_index.pinned_pdf()[0].is_file():
        pytest.skip("the PDF ships in the repository, not in the sdist")
    return classes.class_texts()


@pytest.mark.parametrize("name", CASTERS)
def test_the_engine_slots_match_the_srd_table(
    name: str, texts: dict[str, classes.ClassText]
) -> None:
    text = texts[name]
    progression = classes.progression(text)
    for level, srd in classes.slot_rows(text).items():
        if progression == "pact":
            engine = spellcasting.derive_pact_slots(level)
        else:
            engine = spellcasting.derive_spell_slots(name.lower(), progression, level)
        assert engine == srd, (name, level)


@pytest.mark.parametrize("name", CASTERS)
def test_a_class_progression_is_what_its_table_shows(
    name: str, texts: dict[str, classes.ClassText]
) -> None:
    """The Features table's slot columns (nine spell levels, five, or Pact Magic) give each
    class's progression; the corpus agrees (the ledger compares it)."""
    expected = {"Paladin": "half", "Ranger": "half", "Warlock": "pact"}.get(name, "full")
    assert classes.progression(texts[name]) == expected


def test_the_proficiency_bonus_column_is_the_standard_one(
    texts: dict[str, classes.ClassText],
) -> None:
    for text in texts.values():
        for level, row in text.rows.items():
            assert int(row.split()[1][1:]) == classes.PROFICIENCY_BONUS[level], (text.name, level)
