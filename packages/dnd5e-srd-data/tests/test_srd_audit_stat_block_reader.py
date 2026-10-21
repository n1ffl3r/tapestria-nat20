"""The stat block audit's readers (``tools/srd_audit/stat_blocks.py``), on stat block text
written as the SRD 5.2.1 PDF prints it. The ledger tests run them on the real PDF."""

from __future__ import annotations

import pytest

from tools.srd_audit import stat_blocks as sb
from tools.srd_audit.pdf_text import RecordText

GOBLIN = """Goblin Warrior
Small Fey (Goblinoid), Chaotic Neutral
AC 15  Initiative +2 (12)
HP 10 (3d6)
Speed 30 ft., Climb 20 ft., 40 ft. (wolf form only)
MOD SAVE MOD SAVE MOD SAVE
Str 8 −1 −1 Dex 15 +2 +2 Con 10 +0 +0
Int 10 +0 +0 WIS 8 −1 +1 Cha 8 −1 −1
Skills Stealth +6, Sleight of Hand +4
Resistances Fire
Immunities Poison; Charmed (except from its master), Poisoned
Senses Darkvision 60 ft.; Passive Perception 9
Languages Common, Goblin
CR 1/4 (XP 7 ,200; PB +2)
Traits
Legendary Resistance (3/Day). If the goblin fails a saving throw, it succeeds instead.
Actions
Scimitar. Melee Attack Roll: +4, reach 5 ft. Hit: 5 (1d6
+ 2) Slashing damage, plus 2 (1d4) Slashing damage if
the attack roll had Advantage.
Shortbow. Ranged Attack Roll: +4, range 80/320 ft.
Hit: 1 Piercing damage plus 3 (1d6) Poison damage.
Spit (Recharge 5–6). Dexterity Saving Throw: DC 12, each creature in a 15-foot Cone.
Failure: 7 (2d6) Acid damage, and the target must succeed on a DC 10 Constitution
saving throw or its Hit Point maximum decreases by 3 (1d6).
Hand Crossbow (Humanoid or Hybrid Form
Only). Ranged Attack Roll: +4, range 30/120 ft. Hit: 5 (1d6 + 2) Piercing damage.
Bonus Actions
Nimble Escape (2/Day). The goblin takes the Disengage or
Hide action, moving through Difficult
Terrain. It takes no damage.
Legendary Actions
Legendary Action Uses: 3. Immediately after another creature's turn."""


def _record(text: str) -> RecordText:
    lines = tuple(text.split("\n"))
    return RecordText(name=lines[0], page=1, lines=lines)


def test_header_fields() -> None:
    facts = sb.pdf_facts(_record(GOBLIN))
    assert (facts["size"], facts["type"], facts["alignment"]) == ("small", "fey", "chaotic neutral")
    assert (facts["ac"], facts["hp"], facts["hp_dice"]) == (15, 10, "3d6")
    assert facts["speed"] == {
        "walk": 30,
        "burrow": None,
        "climb": 20,
        "fly": None,
        "swim": None,
        "hover": False,
    }
    assert facts["abilities"] == {"str": 8, "dex": 15, "con": 10, "int": 10, "wis": 8, "cha": 8}
    assert facts["saves"] == {"wis": 1}
    assert facts["skills"] == {"stealth": 6, "sleight_of_hand": 4}
    assert facts["damage_resistances"] == ["fire"]
    assert facts["damage_immunities"] == ["poison"]
    assert facts["condition_immunities"] == ["charmed", "poisoned"]
    assert facts["senses"]["darkvision"] == 60
    assert facts["senses"]["passive_perception"] == 9
    assert (facts["cr"], facts["proficiency_bonus"]) == (0.25, 2)
    assert facts["legendary_resistance_uses"] == 3
    assert facts["legendary_action_uses"] == 3


def test_sections_and_action_numbers() -> None:
    facts = sb.pdf_facts(_record(GOBLIN))
    assert facts["traits"] == ["legendary-resistance"]
    assert facts["actions"] == ["hand-crossbow", "scimitar", "shortbow", "spit"]
    assert facts["bonus_actions"] == ["nimble-escape"]
    assert "traits.legendary-resistance" not in facts
    assert facts["actions.scimitar"] == {
        "attack_bonus": 4,
        "distance": 5,
        "damage": ["1d4+0 slashing", "1d6+2 slashing"],
    }
    assert facts["actions.shortbow"] == {
        "attack_bonus": 4,
        "distance": 80,
        "damage": ["1 piercing", "1d6+0 poison"],
    }
    assert facts["actions.spit"] == {
        "recharge": "5-6",
        "save": ["con 10", "dex 12"],
        "damage": ["1d6+0 untyped", "2d6+0 acid"],
        "area": ["cone 15"],
    }
    assert facts["actions.hand-crossbow"]["distance"] == 30
    assert facts["bonus_actions.nimble-escape"] == {"uses_per_day": 2}


def test_unread_blocks_fail_loudly() -> None:
    with pytest.raises(ValueError, match="unread type line"):
        sb.pdf_facts(_record("X\nA strange creature\nAC 10"))
    with pytest.raises(ValueError, match="unread ability scores"):
        sb.pdf_facts(_record("X\nSmall Fey, Neutral\nAC 10\nStr 8 −1 −1"))


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("Bite", True),
        ("Breath of the Dragon", True),
        ("Piercing damage", False),
        ("AC to 10", False),
    ],
)
def test_entry_names(name: str, ok: bool) -> None:
    assert sb._entry_name(name) is ok


def _monster(**fields: object) -> dict[str, object]:
    record: dict[str, object] = {
        "creature_size": "small",
        "creature_type": "fey",
        "alignment": "Chaotic Neutral",
        "ac": 15,
        "hp": 10,
        "hp_dice": "3d6",
        "movement": {"walk": 30, "hover": False},
        "ability_scores": {"str": 8, "dex": 15, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"wis": 1, "str": None},
        "skills": {"stealth": 6, "arcana": None},
        "senses": {"darkvision": 60, "passive_perception": 9},
        "cr": 0.25,
        "proficiency_bonus": 2,
        "actions": [],
        "special_abilities": [],
        "legendary_actions": [],
        "spellcasting_ability": None,
    }
    record.update(fields)
    return record


def _part(number: int | None, denomination: int, bonus: str, types: list[str]) -> dict:
    return {"number": number, "denomination": denomination, "bonus": bonus, "types": types}


def test_corpus_reader_derives_the_engines_numbers() -> None:
    scimitar = {
        "slug": "scimitar",
        "kind": "action",
        "recharge": None,
        "uses_per_day": None,
        "activities": [
            {
                "kind": "attack",
                "attack": {
                    "ability": "dex",
                    "bonus": "",
                    "flat": False,
                    "type": {"value": "melee"},
                },
                "range": {"value": "5"},
                "damage": {"include_base": True, "parts": [_part(1, 6, "", ["slashing"])]},
                "target": {"template": {}},
            },
            {
                "kind": "attack",
                "attack": {"ability": "", "bonus": "2", "flat": True, "type": {"value": "ranged"}},
                "range": {"value": "80"},
                "damage": {"include_base": True, "parts": [_part(1, 4, "@mod", ["fire"])]},
                "target": {"template": {}},
            },
        ],
    }
    breath = {
        "slug": "breath",
        "kind": "bonus_action",
        "recharge": "5-6",
        "uses_per_day": None,
        "activities": [
            {
                "kind": "save",
                "save": {"ability": ["dex"], "dc": {"calculation": "con", "formula": ""}},
                "damage": {
                    "parts": [
                        _part(2, 6, "@mod", ["acid"]),
                        _part(None, 0, "@abilities.dex.mod", []),
                    ]
                },
                "target": {"template": {"type": "cone", "size": "15"}},
            }
        ],
    }
    facts = sb.corpus_facts(_monster(actions=[scimitar, breath]))
    assert facts["actions"] == ["scimitar"]
    assert facts["bonus_actions"] == ["breath"]
    assert facts["actions.scimitar"] == {
        "attack_bonus": 2,
        "distance": 80,
        "damage": ["1d4+2 fire", "1d6+2 slashing"],
    }
    assert facts["bonus_actions.breath"] == {
        "recharge": "5-6",
        "save": ["dex 10"],
        "damage": ["2 untyped", "2d6+0 acid"],
        "area": ["cone 15"],
    }
    assert facts["saves"] == {"wis": 1}
    assert facts["skills"] == {"stealth": 6}


def test_compare_skips_an_area_the_srd_does_not_state() -> None:
    srd = {"actions.grab": {"save": ["str 12"]}}
    corpus = {
        "actions.grab": {"save": ["str 12"], "area": ["emanation 10"]},
        "actions.aura": {"area": ["x"]},
    }
    assert sb.compare(srd, corpus) == {}


def test_stat_block_texts_cut_at_the_next_block_and_at_a_paragraph() -> None:
    lines = [
        (109, "Animated Object"),
        (109, "Huge or Smaller Construct, Unaligned"),
        (109, "AC 15"),
        (109, " Ending the Effect. A paragraph of the spell around it."),
        (300, "Wolf"),
        (300, "Medium Beast, Unaligned"),
        (300, "AC 12"),
        (300, "Rat"),
        (300, "Tiny Beast, Unaligned"),
    ]
    texts = sb.stat_block_texts(lines)
    assert texts["Animated Object"].lines == (
        "Animated Object",
        "Huge or Smaller Construct, Unaligned",
        "AC 15",
    )
    assert texts["Wolf"].lines == ("Wolf", "Medium Beast, Unaligned", "AC 12")
    assert texts["Rat"].page == 300


def test_hand_readings_replace_or_drop_a_field() -> None:
    facts, read = sb.audited_facts(
        "sphinx-of-valor", {"actions.first-roar": {"save": ["wis 20"]}, "actions": []}
    )
    assert "actions.first-roar" not in facts
    assert facts["actions"] == ["claw", "multiattack", "roar", "spellcasting"]
    assert read["actions"].startswith("Roar (3/Day)")
