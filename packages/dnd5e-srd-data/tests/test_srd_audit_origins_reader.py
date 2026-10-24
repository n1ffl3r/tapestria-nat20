"""The origins audit's readers (``tools/srd_audit/origins.py``), on text written as the SRD
5.2.1 PDF prints it. The ledger tests run them on the real PDF."""

from __future__ import annotations

import pytest

from tools.srd_audit import origins

SPECIES = """Dragonborn
Creature Type: Humanoid
Size: Medium (about 5–7 feet tall)
Speed: 30 feet
As a Dragonborn, you have these special traits.
 Breath Weapon. When you take the Attack action
on your turn, you can replace one of your attacks.
 Darkvision. You have Darkvision with a range of
60 feet.
 Draconic Flight. When you reach character level
5, you can channel draconic magic.
Human
Creature Type: Humanoid
Size: Medium (about 4–7 feet tall) or Small (about 2–4
feet tall), chosen when you select this species
Speed: 30 feet
As a Human, you have these special traits.
 Skillful.  You gain proficiency in one skill.
Feat Descriptions
Feats"""


def _species() -> dict[str, origins.SpeciesText]:
    return origins.species_texts(tuple((84, line) for line in SPECIES.split("\n")))


def test_species_facts() -> None:
    facts = origins.species_facts(_species()["Dragonborn"])
    assert facts["creature_type"] == "humanoid"
    assert (facts["sizes"], facts["speed"], facts["darkvision"]) == (["medium"], 30, 60)
    assert facts["traits"] == {"Breath Weapon": 0, "Darkvision": 0, "Draconic Flight": 5}


def test_a_species_with_two_sizes_has_both() -> None:
    assert origins.species_facts(_species()["Human"])["sizes"] == ["medium", "small"]


def test_the_last_species_ends_before_the_feats() -> None:
    assert "Feats" not in _species()["Human"].text


def test_traits_the_corpus_holds_as_fields_are_not_traits() -> None:
    facts, readings = origins.species_with_readings(
        "dragonborn", origins.species_facts(_species()["Dragonborn"])
    )
    assert set(facts["traits"]) == {"Breath Weapon", "Draconic Flight"}
    assert facts["scale"]["Breath Weapon"]["5"] == "2d10"
    assert facts["resistance_choices"] == ["acid", "cold", "fire", "lightning", "poison"]
    assert "scale.Breath Weapon" in readings


def test_a_lineage_reading_overrides_its_species() -> None:
    facts, _ = origins.species_with_readings("elf-wood", {"traits": {}, "speed": 30})
    assert facts["speed"] == 35
    assert facts["spells"]["3"] == ["Longstrider"]


BACKGROUND = """Acolyte
Ability Scores: Intelligence, Wisdom, Charisma
Feat: Magic Initiate (Cleric) (see "Feats")
Skill Proficiencies:  Insight and Religion
Tool Proficiency: Calligrapher's Supplies
Equipment: Choose A or B: (A) Calligrapher's Supplies,
Book (prayers), Robe, 8 GP; or (B) 50 GP
Soldier
Ability Scores: Strength, Dexterity, Constitution
Feat: Savage Attacker (see "Feats")
Skill Proficiencies:  Athletics and Intimidation
Tool Proficiency: Choose one kind of  Gaming Set (see
"Equipment")
Equipment: Choose A or B: (A) Spear, Healer's Kit,
14 GP; or (B) 50 GP
Species"""


def test_background_facts() -> None:
    texts = origins.background_texts(tuple((83, line) for line in BACKGROUND.split("\n")))
    acolyte = origins.background_facts(texts["Acolyte"])
    assert acolyte["ability_options"] == ["cha", "int", "wis"]
    assert (acolyte["feat"], acolyte["feat_list"]) == ("Magic Initiate", "Cleric")
    assert acolyte["skills"] == ["Insight", "Religion"]
    assert (acolyte["tool"], acolyte["equipment_b_gold"]) == ("Calligrapher's Supplies", 50)
    soldier = origins.background_facts(texts["Soldier"])
    assert soldier["tool"] == "Choose one kind of Gaming Set"
    assert "Species" not in texts["Soldier"].text


FEATS = """Origin Feats
Alert
Origin Feat
You gain the following benefits.
Magic Initiate
Origin Feat
You gain the following benefits.
 Repeatable. You can take this feat more than once.
Grappler
General Feat (Prerequisite: Level 4+, Strength or
Dexterity 13+)
You gain the following benefits.
Archery
Fighting Style Feat (Prerequisite: Fighting Style
Feature)
You gain the following benefits.
Boon of Spell Recall
Epic Boon Feat (Prerequisite: Level 19+, Spellcasting
Feature)
You gain the following benefits."""


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Alert", ("origin", None, "", False, False)),
        ("Magic Initiate", ("origin", None, "", False, True)),
        ("Grappler", ("general", 4, "Strength or Dexterity 13+", False, False)),
        ("Archery", ("fighting_style", None, "", True, False)),
        ("Boon of Spell Recall", ("epic_boon", 19, "Spellcasting Feature", False, False)),
    ],
)
def test_feat_facts(name: str, expected: tuple[object, ...]) -> None:
    texts = origins.feat_texts(tuple((87, line) for line in FEATS.split("\n")))
    facts = origins.feat_facts(texts[name])
    assert tuple(facts.values()) == expected


def test_an_ability_name_pypdf_split_is_rejoined() -> None:
    assert origins._squash("Strength or Dexterit y 13+") == "Strength or Dexterity 13+"


def test_condition_effects_are_the_corpus_vocabulary() -> None:
    record = {
        "implies": ["incapacitated"],
        "effects": [
            {"kind": "auto_fail_save", "abilities": ["str", "dex"], "value": None},
            {"kind": "auto_crit_within_5ft", "abilities": [], "value": 5},
            {"kind": "speed_zero", "abilities": [], "value": None},
        ],
    }
    assert origins.corpus_condition(record) == {
        "implies": ["incapacitated"],
        "effects": ["auto_crit_within_5ft=5", "auto_fail_save/dex,str", "speed_zero"],
    }


def test_every_condition_has_a_reading_with_quotes() -> None:
    assert len(origins.CONDITION_READINGS) == 15
    for reading in origins.CONDITION_READINGS.values():
        assert isinstance(reading.srd, tuple) and reading.srd
