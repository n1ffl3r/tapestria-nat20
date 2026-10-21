"""The spell audit's readers (``tools/srd_audit``), on spell text written as the SRD 5.2.1
PDF prints it. The ledger tests run them on the real PDF."""

from __future__ import annotations

import pytest

from tools.srd_audit import pdf_text, spells
from tools.srd_audit.pdf_text import RecordText, column_lines, flatten, gutter

FIREBALL = """Fireball
Level 3 Evocation (Sorcerer, Wizard)
Casting Time: Action
Range: 150 feet
Components: V, S, M (a ball of bat guano and sulfur)
Duration: Instantaneous
A bright streak flashes from you to a point you
choose within range and then blossoms with a
low roar into a fiery explosion. Each creature in a
20-foot-radius Sphere centered on that point makes
a Dexterity saving throw, taking 8d6 Fire damage
on a failed save or half as much damage on a suc -
cessful one.
Using a Higher-Level Spell Slot. The damage in  -
creases by 1d6 for each spell slot level above 3."""


def _record(text: str) -> RecordText:
    lines = tuple(text.split("\n"))
    return RecordText(name=lines[0], page=1, lines=lines)


def test_flatten_rejoins_soft_hyphens_and_keeps_real_ones() -> None:
    assert flatten("a suc -\ncessful one") == "a successful one"
    assert flatten("in  -\ncreases") == "increases"
    assert flatten("10-foot Ema-\nnation") == "10-foot Emanation"
    assert flatten("a 10-\nfoot cube") == "a 10-foot cube"
    assert flatten("10-foot-\nradius") == "10-foot-radius"
    assert flatten("Three-\nQuarters") == "Three-Quarters"


def test_fireball_reads_every_field() -> None:
    facts = spells.pdf_facts(_record(FIREBALL), ["wizard", "sorcerer"])
    assert facts == {
        "level": 3,
        "school": "evo",
        "classes": ["sorcerer", "wizard"],
        "casting_time": {"unit": "action", "value": 1},
        "ritual": False,
        "range": {"units": "ft", "value": 150},
        "components": ["M", "S", "V"],
        "material_cost": 0,
        "material_consumed": False,
        "duration": {"units": "inst", "value": None},
        "concentration": False,
        "save": ["dex"],
        "damage": ["8d6 fire"],
        "area": ["sphere 20"],
        "scaling": ["1d6"],
    }


def test_headers_wrap_and_tolerate_spill_from_a_stat_block() -> None:
    text = """Raise Dead
Level 5 Necromancy (Bard, Cleric,
Paladin)
10 +0 +0Casting Time: 1 hour or Ritual
Range: Touch
Components: V, S, M (a diamond worth 500+ GP, which
the spell consumes)
Duration: Concentration, up to 8 hours
You regain 2d8 Hit Points."""
    facts = spells.pdf_facts(_record(text), ["bard", "cleric", "paladin"])
    assert facts["casting_time"] == {"unit": "hour", "value": 1}
    assert facts["ritual"] is True
    assert facts["range"] == {"units": "touch", "value": None}
    assert (facts["material_cost"], facts["material_consumed"]) == (500, True)
    assert facts["duration"] == {"units": "hour", "value": 8}
    assert facts["concentration"] is True


@pytest.mark.parametrize(
    ("header", "field", "value"),
    [
        ("Range: Self (15-foot Cone)", "range", {"units": "self", "value": None}),
        ("Range: Sight", "range", {"units": "spec", "value": None}),
        ("Range: Unlimited", "range", {"units": "any", "value": None}),
        ("Range: 1 mile", "range", {"units": "mi", "value": 1}),
        ("Duration: Until dispelled or triggered", "duration", {"units": "dstr", "value": None}),
        ("Duration: Up to 1 hour", "duration", {"units": "hour", "value": 1}),
        ("Duration: Special", "duration", {"units": "spec", "value": None}),
        ("Casting Time: Reaction, which you take when", "casting_time", None),
    ],
)
def test_header_values(header: str, field: str, value: object) -> None:
    lines = ["Casting Time: Action", "Range: Self", "Components: V", "Duration: 1 round"]
    key = header.split(":")[0]
    lines = [header if line.startswith(key) else line for line in lines]
    facts = spells.pdf_facts(_record("X\nLevel 1 Abjuration (Wizard)\n" + "\n".join(lines)), [])
    if value is None:
        assert facts[field] == {"unit": "reaction", "value": 1}
    else:
        assert facts[field] == value


@pytest.mark.parametrize(
    "header",
    ["Casting Time: Whenever", "Range: Nearby", "Duration: Forever and a day"],
)
def test_unread_headers_fail_loudly(header: str) -> None:
    lines = ["Casting Time: Action", "Range: Self", "Components: V", "Duration: 1 round"]
    key = header.split(":")[0]
    lines = [header if line.startswith(key) else line for line in lines]
    with pytest.raises(ValueError, match="unread"):
        spells.pdf_facts(_record("X\nLevel 1 Abjuration (Wizard)\n" + "\n".join(lines)), [])


def test_unread_level_line_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unread level line"):
        spells.pdf_facts(_record("X\nA Wondrous Item\nDuration: 1 round"), [])


@pytest.mark.parametrize(
    ("body", "field", "value"),
    [
        ("you take 3d8 Radiant or Necrotic damage", "damage", ["3d8 necrotic", "3d8 radiant"]),
        (
            "takes 1d8 damage of a type you choose: Acid, Cold, or Fire.",
            "damage",
            ["1d8 acid", "1d8 cold", "1d8 fire"],
        ),
        (
            "deals an extra 2d8 damage when you hit. This damage is Acid or Cold (your choice).",
            "damage",
            ["2d8 acid", "2d8 cold"],
        ),
        ("the target takes Fire damage equal to 3d6 plus", "damage", ["3d6 fire"]),
        ("the target takes 1d4 damage", "damage", ["1d4 untyped"]),
        ("A target regains Hit Points equal to 2d8 plus", "healing", ["2d8"]),
        ("You gain 2d4 + 4 Temporary Hit Points.", "healing", ["2d4"]),
        ("Make a ranged spell attack against the target.", "attack", ["ranged"]),
        ("has Advantage on Wisdom saving throws", "save", None),
        ("takes a -2 penalty to AC and Dexterity saving throws", "save", None),
        ("must succeed on a Charisma saving throw", "save", ["cha"]),
        ("a 100-foot-long, 5-foot-wide Line blasts", "area", ["line 100"]),
        ("a 5-foot-wide, 60-foot-long Line", "area", ["line 60"]),
        ("A Line of strong wind 60 feet long and 10 feet wide", "area", ["line 60"]),
        ("a 50-foot-radius, 100-foot high Cylinder", "area", ["cylinder 50"]),
        ("a Cylinder that is 10 feet tall with a 60-foot radius", "area", ["cylinder 60"]),
        ("an area that is a Cube up to 100 feet on a side", "area", ["cube 100"]),
        ("in a 15-foot Emanation", "area", ["emanation 15"]),
    ],
)
def test_effect_readings(body: str, field: str, value: object) -> None:
    text = "X\nLevel 2 Evocation (Wizard)\nCasting Time: Action\nRange: Self\n"
    text += "Components: V\nDuration: Instantaneous\n" + body
    assert spells.pdf_facts(_record(text), []).get(field) == value


def test_scaling_reads_every_increase_and_skips_cantrips() -> None:
    text = "X\nLevel 7 Evocation (Wizard)\nCasting Time: Action\nRange: Self\n"
    text += "Components: V\nDuration: Instantaneous\nIt deals 10d6 Cold damage.\n"
    text += "Using a Higher-Level Spell Slot. The wall's damage increases by 2d6 and the air's"
    text += " damage increases by 1d6 for each spell slot level above 6."
    assert spells.pdf_facts(_record(text), [])["scaling"] == ["1d6", "2d6"]
    cantrip = text.replace("Level 7 Evocation", "Evocation Cantrip").replace(
        "Using a Higher-Level Spell Slot", "Cantrip Upgrade"
    )
    assert "scaling" not in spells.pdf_facts(_record(cantrip), [])


def _activity(**fields: object) -> dict[str, object]:
    activity: dict[str, object] = {"kind": "utility", "target": {"template": {}}}
    activity.update(fields)
    return activity


def _corpus(**fields: object) -> dict[str, object]:
    record: dict[str, object] = {
        "level": 4,
        "school": "evo",
        "casting_time": {"unit": "action", "value": 1},
        "ritual": False,
        "range": {"units": "self", "value": None},
        "components": ["V"],
        "materials": {"cost": 0, "consumed": False},
        "duration": {"units": "inst", "value": None},
        "concentration": False,
        "activities": [],
        "passive_effects": [],
    }
    record.update(fields)
    return record


def test_corpus_reader() -> None:
    part = {
        "number": 2,
        "denomination": 6,
        "types": ["fire", "cold"],
        "scaling": {"mode": "whole", "number": 1},
    }
    record = _corpus(
        activities=[
            _activity(
                kind="save",
                save={"ability": ["dex"]},
                damage={"parts": [part]},
                target={"template": {"type": "sphere", "size": "@item.level * 5 - 10"}},
            ),
            _activity(kind="attack", attack={"type": {"value": "melee"}}),
            _activity(
                kind="heal",
                healing={"number": None, "bonus": "70", "scaling": {"formula": "10"}},
            ),
            _activity(
                kind="heal",
                healing={
                    "custom": {"enabled": True, "formula": "4d8 + @mod"},
                    "types": ["temphp"],
                },
            ),
            _activity(target={"template": {"type": "wall", "size": "60"}}),
            _activity(target={"template": {"type": "cube", "size": "@details.level"}}),
        ],
        passive_effects=[
            {
                "changes": [
                    {"key": "system.bonuses.mwak.damage", "value": "1d4[radiant]"},
                    {"key": "system.bonuses.rwak.damage", "value": "- 1d4"},
                ]
            }
        ],
    )
    facts = spells.corpus_facts(record, ["wizard"])
    assert facts["damage"] == ["1d4 radiant", "2d6 cold", "2d6 fire"]
    assert facts["save"] == ["dex"]
    assert facts["attack"] == ["melee"]
    assert facts["healing"] == ["4d8"]
    assert facts["area"] == ["sphere 10"]
    assert facts["scaling"] == ["10", "1d6"]


def test_compare_skips_an_area_the_srd_does_not_state() -> None:
    srd = {"level": 1, "damage": ["1d6 fire"]}
    corpus = {"level": 2, "area": ["sphere 10"], "damage": ["1d6 fire"]}
    assert spells.compare(srd, corpus) == {"level": {"srd": 1, "corpus": 2}}
    assert spells.compared_fields(srd, corpus) == ["damage", "level"]


def test_hand_readings_replace_or_drop_a_field() -> None:
    facts, read = spells.audited_facts("web", {"area": ["cube 20", "cube 5"]})
    assert facts["area"] == ["cube 20"] and "web" in pdf_text.flatten(read["area"]).lower()
    facts, _ = spells.audited_facts("silent-image", {"area": ["cube 15"], "level": 1})
    assert facts == {"level": 1}


def test_gutter_splits_two_columns() -> None:
    rows = ["left text        right text", "more left        more right", "", "12"]
    assert gutter(rows) in range(9, 17)
    assert column_lines("\n".join(rows)) == ["left text", "more left", "right text", "more right"]
    assert gutter(["ab"]) == 2


def test_spell_texts_cut_at_the_next_heading_and_a_stat_block() -> None:
    lines = tuple(
        (1, line)
        for line in (
            "Intro",
            "Find Steed",
            "Level 2 Conjuration (Paladin)",
            "Casting Time: Action",
            "Range: 30 feet",
            "Otherworldly Steed",
            "Large Celestial, Fey, or Fiend (Your Choice), Neutral",
            "AC 10",
            "Fireball",
            "Level 3 Evocation (Sorcerer, Wizard)",
            "Casting Time: Action",
            "Rules Glossary",
            "Abbreviations",
        )
    )
    texts = pdf_text.spell_texts(lines)
    assert texts["Find Steed"].lines == (
        "Find Steed",
        "Level 2 Conjuration (Paladin)",
        "Casting Time: Action",
        "Range: 30 feet",
    )
    assert texts["Fireball"].lines[-1] == "Casting Time: Action"
