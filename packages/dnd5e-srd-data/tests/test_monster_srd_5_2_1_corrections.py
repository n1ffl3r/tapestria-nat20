"""The stat block audit's corrections (spec 02a, acceptance criterion 4).

The pinned Foundry pack keeps an attack's reach and Finesse property on the item, which the
activity alone loses, and gets some stat blocks wrong outright. The translator restores
the first (``_with_item_reach_and_finesse``) and fixes the second with
``_MONSTER_SRD_CORRECTIONS``, whose entries each quote the stat block's SRD 5.2.1 text.
These tests pin the corrected corpus; ``audit/srd_5_2_1_ledger.json`` records every change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dnd5e_srd_data import BundledAssetLoader
from dnd5e_srd_data.schema.common import AttackActivity, SaveActivity
from dnd5e_srd_data.schema.monster import Monster, MonsterAction
from tools.translators.foundry import (
    _MONSTER_SRD_CORRECTIONS,
    _apply_monster_srd_corrections,
    _correction_parent,
    srd_rules_disabled,
    translate_monster_yaml,
)

LOADER = BundledAssetLoader()
ROOT = Path(__file__).resolve().parents[1]
FOUNDRY = ROOT / "raw_sources" / "foundry" / "packs" / "_source" / "actors24"


def _monster(slug: str) -> Monster:
    monster = LOADER.get_monster(slug)
    assert monster is not None, slug
    return monster


def _action(monster: Monster, slug: str) -> MonsterAction:
    lists = (
        monster.actions,
        monster.special_abilities,
        monster.legendary_actions,
        monster.lair_actions,
    )
    return next(a for actions in lists for a in actions if a.slug == slug)


def _attack(monster: str, action: str) -> AttackActivity:
    return next(
        a for a in _action(_monster(monster), action).activities if isinstance(a, AttackActivity)
    )


@pytest.mark.parametrize(
    ("slug", "path", "value"),
    [
        (slug, correction.path, correction.value)
        for slug, corrections in sorted(_MONSTER_SRD_CORRECTIONS.items())
        for correction in corrections
        if not correction.path.endswith(".kind")
    ],
)
def test_every_correction_is_in_the_corpus(slug: str, path: str, value: object) -> None:
    blob = _monster(slug).model_dump(mode="json")
    parent, key = _correction_parent(blob, path)
    assert parent[key] == value


@pytest.mark.parametrize(
    ("slug", "action", "kind"),
    [
        (slug, c.path.split("[")[1].split("]")[0], c.value)
        for slug, corrections in sorted(_MONSTER_SRD_CORRECTIONS.items())
        for c in corrections
        if c.path.endswith(".kind")
    ],
)
def test_every_entry_is_in_its_srd_section(slug: str, action: str, kind: str) -> None:
    """A ``kind`` correction moves the entry to the list its SRD section belongs in."""
    monster = _monster(slug)
    entry = _action(monster, action)
    assert entry.kind == kind
    expected = {"special": monster.special_abilities}.get(kind, monster.actions)
    assert entry in expected


def test_a_dragons_rend_keeps_its_reach() -> None:
    """SRD 5.2.1 Adult Red Dragon, Rend: "Melee Attack Roll: +14, reach 10 ft." Foundry
    keeps the 10 on the item (``system.range.reach``)."""
    rend = _attack("adult-red-dragon", "rend")
    assert (rend.range.units, rend.range.value) == ("ft", "10")


def test_a_melee_attack_without_a_reach_has_5_feet() -> None:
    """SRD 5.2.1 "Reach": "A creature has a reach of 5 feet unless a rule says otherwise."
    Wolf, Bite: "Melee Attack Roll: +4, reach 5 ft." """
    assert _attack("wolf", "bite").range.value == "5"


def test_a_thrown_spear_reaches_5_feet() -> None:
    """SRD 5.2.1 Guard, Spear: "Melee or Ranged Attack Roll: +3, reach 5 ft. or range
    20/60 ft." Foundry's activity inherits the throwing range."""
    assert _attack("guard", "spear").range.value == "5"


def test_a_finesse_weapon_uses_dexterity() -> None:
    """SRD 5.2.1 Goblin Warrior (Str 8, Dex 15), Scimitar: "Melee Attack Roll: +4"."""
    assert _attack("goblin-warrior", "scimitar").attack.ability == "dex"


def test_defenses() -> None:
    """Death Dog: "Immunities Blinded, Charmed, Deafened, Frightened, Stunned,
    Unconscious"; Badger: "Resistances Poison"; Elk: "AC 10"."""
    assert _monster("death-dog").condition_immunities == [
        "blinded",
        "charmed",
        "deafened",
        "frightened",
        "stunned",
        "unconscious",
    ]
    assert _monster("badger").damage_resistances == ["poison"]
    assert _monster("elk").ac == 10


def test_lycanthrope_curse_saves_have_a_dc() -> None:
    """Werewolf, Bite: "Constitution Saving Throw: DC 12." Foundry's DC formula reads an
    ``@mod`` with no ability behind it."""
    save = next(
        a for a in _action(_monster("werewolf"), "bite").activities if isinstance(a, SaveActivity)
    )
    assert (save.save.dc.calculation, save.save.dc.formula) == ("", "12")


def test_pit_fiend_bite_save_names_its_ability() -> None:
    """Pit Fiend, Bite: "Constitution Saving Throw: DC 21." Foundry ships no ability, which
    the engine refuses (``save.ability is empty``)."""
    save = next(
        a for a in _action(_monster("pit-fiend"), "bite").activities if isinstance(a, SaveActivity)
    )
    assert save.save.ability == ["con"]


def test_a_trait_filed_as_an_action_moves() -> None:
    """Aboleth: Eldritch Restoration is a trait in the SRD's Traits section."""
    aboleth = _monster("aboleth")
    assert "eldritch-restoration" in {t.slug for t in aboleth.special_abilities}
    assert "eldritch-restoration" not in {a.slug for a in aboleth.actions}


def test_a_stale_correction_fails_the_regen() -> None:
    """A Foundry pin that fixes the Elk's AC upstream makes the correction stale."""
    with pytest.raises(ValueError, match="stale"):
        _apply_monster_srd_corrections(_monster("elk"))


def test_without_the_srd_rules_the_pinned_pack_shows() -> None:
    """``srd_rules_disabled`` gives the record as Foundry has it, which the ledger diffs."""
    path = FOUNDRY / "monstrosity" / "death-dog.yml"
    if not path.is_file():
        pytest.skip("the raw Foundry sources are not checked out (make refresh-upstream)")
    from datetime import date

    with srd_rules_disabled():
        raw = translate_monster_yaml(path, ingest_date=date(2000, 1, 1), ingest_version="t")
    assert raw.condition_immunities == []
    assert (
        translate_monster_yaml(
            path, ingest_date=date(2000, 1, 1), ingest_version="t"
        ).condition_immunities
        == _monster("death-dog").condition_immunities
    )
