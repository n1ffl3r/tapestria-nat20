"""A monster built from a stat block attacks with that stat block's numbers
(spec 02a, acceptance criterion 14; issue #40).

SRD 5.2.1 stat blocks give each attack a bonus ("Attack Roll: +3") and damage
("1d6 + 1"): the creature's ability modifier plus its Proficiency Bonus to
hit, and the modifier on the damage. A host that sets no ``attack_bonus`` on
its ``EncounterMemberSpec`` leaves the creature to those numbers; one that sets
it keeps the uniform model it always had.
"""

from __future__ import annotations

import asyncio

import pytest

from dnd5e_engine import (
    advance_monster_turn,
    cell_id,
    drain_pending_events,
    start_combat,
)
from dnd5e_engine.events import AttackRolled, DamageApplied
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec


def _first_turn(slug: str, **foe: object) -> tuple[list[AttackRolled], list[DamageApplied]]:
    async def _run() -> list[object]:
        hero = PartyMemberSpec(
            entity_id="pc:1",
            name="Hero",
            initiative=1,
            hp_current=900,
            hp_max=900,
            ac=10,
            zone_id=cell_id(1, 0),
        )
        monster = EncounterMemberSpec(
            entity_id="cre:1",
            entity_type="Monster",
            name="Foe",
            initiative=20,
            hp_current=50,
            hp_max=50,
            ac=12,
            zone_id=cell_id(0, 0),
            monster_template_slug=slug,
            **foe,  # type: ignore[arg-type]
        )
        started = await start_combat(
            session_id="stat-block",
            party=[hero],
            encounter=[monster],
            grid_scene=GridScene(width=6, height=6),
            rng_seed=4,
        )
        await advance_monster_turn(started.handle)
        return drain_pending_events(started.handle)

    events = asyncio.run(_run())
    return (
        [e for e in events if isinstance(e, AttackRolled)],
        [e for e in events if isinstance(e, DamageApplied)],
    )


@pytest.mark.parametrize(
    ("slug", "to_hit"),
    [
        ("bandit", 3),  # Scimitar: Dexterity +1, Proficiency Bonus +2
        ("goblin-warrior", 4),  # Scimitar: Dexterity +2, Proficiency Bonus +2
        ("tough", 4),  # Mace: Strength +2, Proficiency Bonus +2
        ("ogre", 6),  # Greatclub: Strength +4, Proficiency Bonus +2
        ("priest", 5),  # Mace: Strength +3, Proficiency Bonus +2
        ("owlbear", 7),  # Rend: Strength +5, Proficiency Bonus +2
    ],
)
def test_a_monster_attacks_at_its_stat_blocks_bonus(slug: str, to_hit: int) -> None:
    rolled, _ = _first_turn(slug)
    assert rolled
    assert {e.modifier for e in rolled} == {to_hit}
    assert all(e.roll_total == e.natural + to_hit for e in rolled)


def test_a_monsters_damage_adds_its_ability_modifier() -> None:
    """Ogre Greatclub: "13 (2d8 + 4) Bludgeoning damage"; a hit rolls 2d8 and
    adds the Strength modifier of +4."""
    rolled, damage = _first_turn("ogre")
    assert [e.is_hit for e in rolled] == [True]
    (hit,) = damage
    assert 2 * 1 + 4 <= hit.amount <= 2 * 8 + 4
    assert hit.damage_type == "bludgeoning"


def test_the_reported_bandit_attacks_at_plus_3() -> None:
    """Issue #40: a bandit with no ``attack_bonus`` set attacked at +0."""
    (rolled, *_), _ = _first_turn("bandit")
    assert rolled.modifier == 3


def test_a_host_set_attack_bonus_wins() -> None:
    (rolled, *_), _ = _first_turn("bandit", attack_bonus=7, damage_dice="1d4")
    assert rolled.modifier == 7
