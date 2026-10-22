"""The class audit's readers (``tools/srd_audit/classes.py``), on chapter text written as the
SRD 5.2.1 PDF prints it. The ledger tests run them on the real PDF."""

from __future__ import annotations

import pytest

from tools.srd_audit import classes

FIGHTER_CORE = """Core Fighter Traits
Primary Ability Strength or Dexterity
Hit Point Die D10 per Fighter level
Saving Throw
Proficiencies
Strength and Constitution
Skill Proficiencies Choose 2: Acrobatics, Animal
Handling, Athletics
Becoming a Fighter …
Level 1: Second Wind
You have a limited well of stamina.
Fighter Features
Level
Proficiency
Bonus Class Features
Second
Wind
Weapon
Mastery"""
FIGHTER_ROWS = {
    1: "Fighting Style, Second Wind, Weapon Mastery 2 3",
    2: "Action Surge (one use), Tactical Mind 2 3",
    3: "Fighter Subclass 2 3",
    4: "Ability Score Improvement 3 4",
    9: "Indomitable (one use), Tactical Master 3 4",
    13: "Indomitable (two uses), Studied Attacks 4 5",
    17: "Action Surge (two uses), Indomitable (three uses) 4 6",
    19: "Epic Boon 4 6",
}
AFTER = """Level 2: Action Surge
You can push yourself.
Fighter Subclass: Champion
Level 3: Improved Critical
Your attack rolls score a Critical Hit on a 19 or 20.
Level 7: Remarkable Athlete
You have Advantage on Initiative."""


def _fighter() -> classes.ClassText:
    rows = []
    for level in range(1, 21):
        body = FIGHTER_ROWS.get(level, "Subclass feature 4 5")
        rows.append(f"{level} +{2 + (level - 1) // 4} {body}")
    # A row wrapped over two lines is rejoined.
    rows[0] = "1 +2 Fighting Style, Second Wind,\nWeapon Mastery 2 3"
    text = "\n".join([FIGHTER_CORE, *rows, AFTER])
    lines = tuple((47, line) for line in text.split("\n"))
    return classes.class_texts(lines)["Fighter"]


def test_core_traits() -> None:
    core = _fighter().core
    assert core["Primary Ability"] == "Strength or Dexterity"
    assert core["Hit Point Die"] == "D10 per Fighter level"
    assert core["Saving Throw Proficiencies"] == "Strength and Constitution"
    assert core["Skill Proficiencies"] == "Choose 2: Acrobatics, Animal Handling, Athletics"


def test_facts() -> None:
    facts = classes.pdf_facts(_fighter())
    assert facts["hit_die"] == "d10"
    assert facts["primary_ability"] == {"all": False, "value": ["dex", "str"]}
    assert facts["saving_throws"] == ["con", "str"]
    assert facts["progression"] == "none"
    assert facts["subclass_level"] == 3
    assert facts["asi_levels"] == [4, 19]
    assert facts["feature_levels"] == {"Second Wind": [1]} | {"Action Surge": [2]}


def test_a_qualifier_is_a_scale() -> None:
    """ "Action Surge (one use)" at level 2 and "(two uses)" at 17 is a scale."""
    scale = classes.pdf_facts(_fighter())["scale"]
    assert scale["Action Surge"]["1"] is None
    assert [scale["Action Surge"][str(n)] for n in (2, 16, 17)] == [1, 1, 2]
    assert [scale["Indomitable"][str(n)] for n in (8, 9, 13, 17)] == [None, 1, 2, 3]


def test_table_columns_are_read_from_the_right() -> None:
    scale = classes.pdf_facts(_fighter())["scale"]
    assert (scale["Second Wind"]["1"], scale["Weapon Masteries Known"]["1"]) == (2, 3)
    assert scale["Weapon Masteries Known"]["4"] == 4


def test_a_subclass_has_its_own_headings() -> None:
    text = _fighter()
    assert [(h.level, h.name) for h in text.subclasses["Champion"]] == [
        (3, "Improved Critical"),
        (7, "Remarkable Athlete"),
    ]
    assert classes.subclass_facts(text.subclasses["Champion"]) == {
        "feature_levels": {"Improved Critical": [3], "Remarkable Athlete": [7]}
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("—", None),
        ("3", 3),
        ("+2", 2),
        ("D6", "d6"),
        ("1d6", "d6"),
        ("2d8", "2d8"),
        ("+10ft.", 10),
        ("1/4", 0.25),
    ],
)
def test_table_values(value: str, expected: object) -> None:
    assert classes.number(value) == expected


def test_names_have_one_spelling() -> None:
    assert classes.canon("Lay On Hands") == classes.canon("Lay on Hands")
    assert classes.canon("Monk’s Focus") == "Monk's Focus"
    assert classes.canon("Improved Brutal Strike (2)") == "Improved Brutal Strike"


def test_a_feature_printed_at_two_levels_has_both() -> None:
    found = classes.levels_by_name(
        [("Improved Brutal Strike", 13), ("Improved Brutal Strike (2)", 17)]
    )
    assert found == {"Improved Brutal Strike": [13, 17]}


def test_a_corpus_scale_carries_forward() -> None:
    config = {
        "type": "dice",
        "scale": {"1": {"faces": 6, "number": None}, "5": {"faces": 8, "number": 2}},
    }
    scale = classes._corpus_scale(config)
    assert (scale["1"], scale["4"], scale["5"], scale["20"]) == ("d6", "d6", "2d8", "2d8")


def test_every_reading_has_quotes_in_a_tuple() -> None:
    """A single quote without its comma would be a string, read letter by letter."""
    for readings in classes.SCALE_READINGS.values():
        for reading in readings.values():
            assert isinstance(reading.srd, tuple) and reading.srd
            assert all(isinstance(quote, str) and len(quote) > 1 for quote in reading.srd)
