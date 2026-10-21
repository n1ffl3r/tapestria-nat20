"""The item audit's readers (``tools/srd_audit/items.py``), on Equipment and Magic Items
text written as the SRD 5.2.1 PDF prints it. The ledger tests run them on the real PDF."""

from __future__ import annotations

from tools.srd_audit import items
from tools.srd_audit.pdf_text import RecordText

EQUIPMENT = """Simple Melee Weapons
Dagger 1d4 Piercing Finesse, Light, Thrown (Range 20/60) Nick 1 lb. 2 GP
Martial Ranged Weapons
Light Crossbow 1d8 Piercing Ammunition (Range 80/320; Bolt), Loading,
Two-Handed
Slow 5 lb. 25 GP
Armor Armor Class (AC) Strength Stealth Weight Cost
Light Armor (1 Minute to Don or Doff)
Leather Armor 11 + Dex modifier — — 10 lb. 10 GP
Medium Armor (5 Minutes to Don or Doff)
Chain Shirt 13 + Dex modifier (max 2) — — 20 lb. 50 GP
Heavy Armor (10 Minutes to Don or Doff)
Chain Mail 16 Str 13 Disadvantage 55 lb. 75 GP
Shield (Utilize Action to Don or Doff)
Shield +2 — — 6 lb. 10 GP
Herbalism Kit (5 GP)
Ability: Intelligence Weight: 3 lb.
Musical Instrument (Varies)
Ability: Charisma  Weight: Varies
Variants: Bagpipes (30 GP, 6 lb.), drum (6 GP, 3 lb.), pan flute (12
GP, 2 lb.)
Navigator's Tools (25 GP)
Caltrops (1 GP)
A creature that enters the area must succeed on a DC 15 Dex-
terity saving throw or take 1 Piercing damage and have its Speed
Item Weight Cost
Arcane Focus Varies Varies
Clothes, Fine 6 lb. 15 GP
Waterskin 5 lb. (full) 2 SP
Mirror 1/2 lb. 5 GP
Entertainer's Pack 58½ lb. 40 GP
Type Amount Storage Weight Cost
Arrows 20 Quiver 1 lb. 1 GP
Focus Weight Cost
Staff (also a Quarterstaff) 4 lb. 5 GP"""


def _equipment() -> dict[str, tuple[int, dict[str, object]]]:
    return items.equipment_facts(tuple((90, line) for line in EQUIPMENT.split("\n")))


def test_weapon_rows() -> None:
    found = _equipment()
    assert found["Dagger"][1] == {
        "category": "simple_melee",
        "damage": "1d4 piercing",
        "properties": ["finesse", "light", "thrown"],
        "mastery": "nick",
        "weight": 1.0,
        "cost": 2.0,
        "range": [20, 60],
    }
    # A wrapped row is rejoined.
    crossbow = found["Light Crossbow"][1]
    assert crossbow["properties"] == ["ammunition", "loading", "two_handed"]
    assert (crossbow["mastery"], crossbow["cost"]) == ("slow", 25.0)


def test_armor_rows() -> None:
    found = _equipment()
    assert found["Leather Armor"][1]["dex_bonus_max"] is None
    assert found["Chain Shirt"][1]["dex_bonus_max"] == 2
    mail = found["Chain Mail"][1]
    assert (mail["base_ac"], mail["strength_min"], mail["stealth_disadvantage"]) == (16, 13, True)
    # A Shield states no Dexterity cap.
    assert "dex_bonus_max" not in found["Shield"][1]


def test_tools_and_variants() -> None:
    found = _equipment()
    assert found["Herbalism Kit"][1] == {"cost": 5.0, "weight": 3.0}
    assert found["Bagpipes"][1] == {"cost": 30.0, "weight": 6.0}
    assert found["Pan flute"][1] == {"cost": 12.0, "weight": 2.0}
    # A cost that varies is no one price; a weight that varies is not stated.
    assert found["Musical Instrument"][1] == {"cost": None}


def test_prose_naming_damage_is_no_weapon_row() -> None:
    """ "take 1 Piercing damage" in the Caltrops text does not start a wrapped row."""
    assert "Navigator's Tools" in _equipment()
    assert _equipment()["Mirror"][1] == {"weight": 0.5, "cost": 5.0}


def test_gear_rows() -> None:
    found = _equipment()
    assert found["Arcane Focus"][1] == {"cost": None}
    assert found["Waterskin"][1] == {"weight": 5.0, "cost": 0.2}
    assert found["Entertainer's Pack"][1]["weight"] == 58.5
    assert found["Arrows"][1] == {"weight": 1.0, "cost": 1.0}
    assert found["Staff"][1] == {"weight": 4.0, "cost": 5.0}


def test_an_inverted_name_finds_its_row() -> None:
    name, (_, facts) = items.equipment_entry(_equipment(), "Fine Clothes") or ("", (0, {}))
    assert (name, facts["cost"]) == ("Clothes, Fine", 15.0)
    assert items.equipment_entry(_equipment(), "Pan Flute") is not None


def _magic(text: str) -> dict[str, object]:
    lines = tuple(text.split("\n"))
    return items.magic_item_facts(RecordText(name=lines[0], page=1, lines=lines))


def test_magic_item_kind_line() -> None:
    assert _magic(
        "Wand of Magic Missiles\nWand, Uncommon\nThis wand has 7 charges. While holding it"
    ) == {"category": "magic_item", "attunement": False, "rarity": "uncommon", "charges": 7}
    assert _magic(
        "Holy Avenger\nWeapon (Any Simple or Martial), Legendary (Requires\n"
        "Attunement by a Paladin)\nYou gain a +3 bonus"
    ) == {
        "category": "weapon",
        "attunement": True,
        "rarity": "legendary",
        "attunement_by": "a Paladin",
    }


def test_a_rarity_by_form_states_none() -> None:
    facts = _magic(
        "Wand of the War Mage, +1, +2, or +3\nWand, Uncommon (+1), Rare (+2), or Very Rare\n"
        "(+3) (Requires Attunement by a Spellcaster)\nWhile holding this wand"
    )
    assert "rarity" not in facts
    assert facts["attunement_by"] == "a Spellcaster"
    assert "rarity" not in _magic("Feather Token\nWondrous Item, Rarity Varies\nThis object")


def test_a_staff_is_a_weapon_and_ammunition_an_item() -> None:
    assert _magic("Staff of Fire\nStaff, Very Rare\nYou have")["category"] == "weapon"
    assert (
        _magic("Rod of Lordly Might\nRod, Legendary\nIt functions as a magic Mace that")["category"]
        == "weapon"
    )
    assert (
        _magic("Arrow of Slaying\nWeapon (Any Ammunition), Very Rare\nAn")["category"]
        == "magic_item"
    )


def test_a_forms_charges_are_not_the_items() -> None:
    assert "charges" not in _magic(
        "Figurine\nWondrous Item, Rarity Varies\nThe goat. It has 24 charges, and each hour"
    )


def test_corpus_charges_on_the_one_activity_that_has_them() -> None:
    record = {
        "item_kind": "weapon",
        "rarity": "legendary",
        "uses": None,
        "activities": [{"uses": {"max": ""}}, {"uses": {"max": "5"}}],
    }
    assert items.corpus_facts(record, magic=True)["charges"] == 5


def test_corpus_attunement_constraint_drops_its_by() -> None:
    record = {"item_kind": "magic_item", "attunement_constraint": "by a Wizard"}
    assert items.corpus_facts(record, magic=True)["attunement_by"] == "a Wizard"


def test_a_variant_compares_its_kind_line_fields() -> None:
    srd = {"category": "magic_item", "attunement": False, "charges": 3}
    corpus = {"category": "magic_item", "attunement": False, "charges": 7, "rarity": "rare"}
    compared, held, not_compared = items.audited(srd, corpus, True, "Horn of Valhalla")
    assert compared == {"attunement": False, "category": "magic_item"}
    assert held == compared
    assert {"charges", "rarity", "cost", "weight"} <= set(not_compared)
