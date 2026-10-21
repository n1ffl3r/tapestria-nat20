"""Elevation, climbing and falling on the grid, and Feather Fall at its trigger.

SRD 5.2.1 "Climbing": "While you're climbing, each foot of movement costs 1 extra
foot (2 extra feet in Difficult Terrain). You ignore this extra cost if you have
a Climb Speed and use it to climb."

SRD 5.2.1 "Falling [Hazard]": "A creature that falls takes 1d6 Bludgeoning
damage at the end of the fall for every 10 feet it fell, to a maximum of 20d6.
When the creature lands, it has the Prone condition unless it avoids taking any
damage from the fall."

SRD 5.2.1 "Flying": "While flying, you fall if you have the Incapacitated or
Prone condition or your Fly Speed is reduced to 0. You can stay aloft in those
circumstances if you can hover."

SRD 5.2.1 Feather Fall: "Casting Time: Reaction, which you take when you or a
creature you can see within 60 feet of you falls"; "A falling creature's rate of
descent slows to 60 feet per round until the spell ends. If a creature lands
before the spell ends, the creature takes no damage from the fall".
"""

from __future__ import annotations

import asyncio
from typing import Any

from dnd5e_engine import PlayerIntent, cell_id, start_combat
from dnd5e_engine.activities.passive_stats import CombatantMovementModes
from dnd5e_engine.combat_state import (
    CombatState,
    CombatStep,
    begin_combat,
    resolve_intent,
    resume,
)
from dnd5e_engine.events import (
    ActorMoved,
    CombatantMoved,
    ConditionApplied,
    CreatureFell,
    DamageApplied,
    MoveFailed,
    ReactionTriggered,
    SaveRolled,
    SpellCast,
)
from dnd5e_engine.orchestrator import (
    _find_combatant,
    _get_live,
    _resolution_events,
    _stays_aloft,
    push_combatant,
)
from dnd5e_engine.reactions import ReactionPrompt
from dnd5e_engine.spatial import GridTopology
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec
from dnd5e_engine.types.conditions import ActiveCondition

BRUTE = "pc:brute"
TARGET = "pc:target"
WIZARD = "pc:wizard"
FOE = "cre:foe"

# Seed 2 fails the target's Shove save (DC 15).
_FAILS = 2


def _ledge(drop_ft: int, **scene: Any) -> GridScene:
    """Columns 0 and 1 stand ``drop_ft`` above the rest of the grid."""
    return GridScene(
        width=8,
        height=3,
        elevation_cells={cell_id(0, 0): drop_ft, cell_id(1, 0): drop_ft},
        **scene,
    )


def _brute() -> PartyMemberSpec:
    # Level 5, Strength 18: a Shove save DC of 8 + 4 + 3 = 15.
    return PartyMemberSpec(
        entity_id=BRUTE,
        name="Brute",
        initiative=20,
        hp_current=30,
        hp_max=30,
        strength=18,
        character_level=5,
        zone_id=cell_id(0, 0),
    )


def _target(**fields: Any) -> PartyMemberSpec:
    base: dict[str, Any] = {
        "entity_id": TARGET,
        "name": "Target",
        "initiative": 10,
        "hp_current": 200,
        "hp_max": 200,
        "strength": 1,
        "dexterity": 1,
        "zone_id": cell_id(1, 0),
    }
    return PartyMemberSpec(**(base | fields))


def _wizard(**fields: Any) -> PartyMemberSpec:
    base: dict[str, Any] = {
        "entity_id": WIZARD,
        "name": "Wizard",
        "initiative": 5,
        "hp_current": 20,
        "hp_max": 20,
        "intelligence": 16,
        "zone_id": cell_id(4, 2),
        "spell_slots": {1: 2},
        "spells_known": ["feather-fall"],
        "class_slug": "wizard",
        "character_level": 1,
    }
    return PartyMemberSpec(**(base | fields))


def _foe() -> EncounterMemberSpec:
    return EncounterMemberSpec(
        entity_id=FOE,
        entity_type="Monster",
        name="Foe",
        initiative=1,
        hp_current=10,
        hp_max=10,
        ac=10,
        zone_id=cell_id(7, 2),
    )


def _opened(scene: GridScene, *party: PartyMemberSpec) -> CombatState:
    step = asyncio.run(
        begin_combat(
            session_id="falling",
            party=[_brute(), *party],
            encounter=[_foe()],
            grid_scene=scene,
            seed=1,
        )
    )
    return step.state


def _shove(state: CombatState, seed: int = _FAILS, **kwargs: Any) -> CombatStep:
    intent = PlayerIntent(intent_type="shove", target_id=TARGET, shove_push=True)
    return asyncio.run(resolve_intent(state, actor_id=BRUTE, intent=intent, seed=seed, **kwargs))


def _of[T](step: CombatStep, kind: type[T]) -> list[T]:
    return [e for e in step.events if isinstance(e, kind)]


def _fall_damage(step: CombatStep) -> list[DamageApplied]:
    return [e for e in _of(step, DamageApplied) if e.source_id == "hazard:falling"]


def _prone(step: CombatStep, entity_id: str = TARGET) -> bool:
    return any(
        e.target_id == entity_id and e.condition == "prone" for e in _of(step, ConditionApplied)
    )


def _condition(name: str) -> ActiveCondition:
    return ActiveCondition(condition=name, source_entity_id="implied:scenario", scope="combat")


def _suspend(_prompt: ReactionPrompt) -> str:
    return "suspend"


# ── climbing ────────────────────────────────────────────────────────────────


def test_a_flat_step_costs_what_it_did() -> None:
    grid = GridTopology(GridScene(width=3, height=1, difficult_terrain_cells=[cell_id(2, 0)]))
    assert grid.edge_distance(cell_id(0, 0), cell_id(1, 0)) == 5
    assert grid.edge_distance(cell_id(1, 0), cell_id(2, 0)) == 10


def test_each_foot_climbed_costs_one_extra_foot_or_two_in_difficult_terrain() -> None:
    grid = GridTopology(
        GridScene(
            width=3,
            height=1,
            elevation_cells={cell_id(1, 0): 10, cell_id(2, 0): 20},
            difficult_terrain_cells=[cell_id(2, 0)],
        )
    )
    up, down = (cell_id(0, 0), cell_id(1, 0)), (cell_id(1, 0), cell_id(0, 0))
    # 5 ft across plus 10 ft climbed at 2 ft a foot, up or down.
    assert grid.edge_distance(*up) == 25
    assert grid.edge_distance(*down) == 25
    # Into difficult terrain: 10 ft across, then 10 ft climbed at 3 ft a foot.
    assert grid.edge_distance(cell_id(1, 0), cell_id(2, 0)) == 40


def test_a_climber_ignores_the_extra_cost() -> None:
    grid = GridTopology(
        GridScene(
            width=3,
            height=1,
            elevation_cells={cell_id(1, 0): 10, cell_id(2, 0): 20},
            difficult_terrain_cells=[cell_id(2, 0)],
        )
    )
    assert grid.edge_distance(cell_id(0, 0), cell_id(1, 0), climber=True) == 15
    # Difficult terrain still costs its own extra foot.
    assert grid.edge_distance(cell_id(1, 0), cell_id(2, 0), climber=True) == 30


def test_reachable_cells_prices_the_climb() -> None:
    grid = GridTopology(GridScene(width=3, height=1, elevation_cells={cell_id(1, 0): 15}))
    assert cell_id(1, 0) not in grid.reachable_cells(cell_id(0, 0), 30)
    assert grid.reachable_cells(cell_id(0, 0), 30, climber=True)[cell_id(1, 0)] == (
        20,
        cell_id(0, 0),
    )


def _climb(**brute: Any) -> CombatStep:
    scene = GridScene(width=4, height=1, elevation_cells={cell_id(1, 0): 15})
    spec = _brute().model_copy(update=brute)
    opened = asyncio.run(
        begin_combat(
            session_id="climb",
            party=[spec],
            encounter=[_foe().model_copy(update={"zone_id": cell_id(3, 0)})],
            grid_scene=scene,
            seed=1,
        )
    )
    intent = PlayerIntent(intent_type="move", target_zone_id=cell_id(1, 0))
    return asyncio.run(resolve_intent(opened.state, actor_id=BRUTE, intent=intent, seed=1))


def test_a_walk_up_a_ledge_pays_for_the_climb() -> None:
    """5 ft across and 15 ft climbed at 2 ft a foot is 35 ft: more than a 30 ft
    Speed."""
    step = _climb()
    assert [e.reason for e in _of(step, MoveFailed)] == ["insufficient_movement"]


def test_a_climb_speed_climbs_the_ledge_at_the_ordinary_cost() -> None:
    step = _climb(movement_modes=CombatantMovementModes(climb=30))
    moved = _of(step, ActorMoved)
    assert [(m.to_zone, m.distance_ft) for m in moved] == [(cell_id(1, 0), 20)]
    assert not _of(step, CreatureFell)


# ── forced movement off an edge ─────────────────────────────────────────────


def test_a_push_stops_on_the_lower_cell() -> None:
    grid = GridTopology(_ledge(30))
    path = grid.push_path(cell_id(0, 0), cell_id(1, 0), 20)
    assert path == [cell_id(2, 0)]


def test_a_push_never_climbs() -> None:
    grid = GridTopology(GridScene(width=4, height=1, elevation_cells={cell_id(2, 0): 5}))
    assert grid.push_path(cell_id(0, 0), cell_id(1, 0), 10) == []


def test_a_creature_shoved_off_a_ledge_falls_and_lands_prone() -> None:
    step = _shove(_opened(_ledge(30), _target()))
    assert not next(e for e in _of(step, SaveRolled) if e.target_id == TARGET).succeeded
    assert [(e.actor_id, e.to_zone) for e in _of(step, CombatantMoved)] == [(TARGET, cell_id(2, 0))]
    assert _of(step, CreatureFell) == [
        CreatureFell(target_id=TARGET, zone=cell_id(2, 0), distance_ft=30)
    ]
    (damage,) = _fall_damage(step)
    assert damage.target_id == TARGET
    assert damage.damage_type == "bludgeoning"
    assert 3 <= damage.amount <= 18  # 3d6
    assert _prone(step)
    target = next(c for c in step.state.combatants if c.entity_id == TARGET)
    assert target.hp_current == 200 - damage.amount


def test_a_drop_under_10_feet_deals_no_damage_and_leaves_the_creature_standing() -> None:
    step = _shove(_opened(_ledge(5), _target()))
    assert [e.distance_ft for e in _of(step, CreatureFell)] == [5]
    assert not _fall_damage(step)
    assert not _prone(step)


def test_the_damage_stops_at_20d6() -> None:
    step = _shove(_opened(_ledge(400), _target()))
    (damage,) = _fall_damage(step)
    assert 20 <= damage.amount <= 120


def test_resistance_applies_and_no_damage_means_no_prone() -> None:
    step = _shove(_opened(_ledge(30), _target(damage_immunities=["bludgeoning"])))
    (damage,) = _fall_damage(step)
    assert damage.amount == 0
    assert not _prone(step)


def test_a_flier_stays_aloft() -> None:
    step = _shove(_opened(_ledge(30), _target(movement_modes=CombatantMovementModes(fly=30))))
    assert _of(step, CombatantMoved)
    assert not _of(step, CreatureFell)
    assert not _fall_damage(step)


def test_a_prone_or_incapacitated_flier_falls_unless_it_can_hover() -> None:
    async def _run() -> tuple[bool, bool, bool, bool]:
        start = await start_combat(
            session_id="aloft",
            party=[_brute(), _target(movement_modes=CombatantMovementModes(fly=30))],
            encounter=[_foe()],
            grid_scene=_ledge(30),
            rng_seed=1,
        )
        live = _get_live(start.handle)
        flier = _find_combatant(live, TARGET)
        assert flier is not None
        prone = flier.model_copy(update={"conditions": [_condition("prone")]})
        stunned = flier.model_copy(update={"conditions": [_condition("stunned")]})
        hovering = prone.model_copy(
            update={"movement_modes": CombatantMovementModes(fly=30, hover=True)}
        )
        return (
            _stays_aloft(live, flier),
            _stays_aloft(live, prone),
            _stays_aloft(live, stunned),
            _stays_aloft(live, hovering),
        )

    assert asyncio.run(_run()) == (True, False, False, True)


def test_the_fall_is_not_the_pushers_damage() -> None:
    """The fall is a nested span: the resolution that pushed the creature does
    not count the fall's damage (so it offers no Hellish Rebuke against the
    pusher)."""

    async def _run() -> tuple[int, int]:
        start = await start_combat(
            session_id="nested",
            party=[_brute(), _target()],
            encounter=[_foe()],
            grid_scene=_ledge(30),
            rng_seed=1,
        )
        live = _get_live(start.handle)
        before = len(live.event_log)
        push_combatant(live, TARGET, cell_id(0, 0), 10)
        outer = _resolution_events(live, before)
        return len(outer), sum(isinstance(e, DamageApplied) for e in live.event_log[before:])

    outer, damage = asyncio.run(_run())
    assert damage == 1
    assert outer == 1  # the CombatantMoved alone


def test_a_templated_monster_keeps_its_stat_block_speeds() -> None:
    step = asyncio.run(
        begin_combat(
            session_id="speeds",
            party=[_brute()],
            encounter=[_foe().model_copy(update={"monster_template_slug": "ghost"})],
            grid_scene=GridScene(width=8, height=3),
            seed=1,
        )
    )
    ghost = next(c for c in step.state.combatants if c.entity_id == FOE)
    assert ghost.movement_modes.fly == 40
    assert ghost.movement_modes.hover is True


# ── Feather Fall ────────────────────────────────────────────────────────────


def test_a_fall_offers_feather_fall_to_a_creature_that_sees_it() -> None:
    state = _opened(_ledge(30), _target(), _wizard())
    step = _shove(state, reactions=_suspend)
    assert step.suspension is not None
    prompt = step.suspension.prompt
    assert (prompt.reactor_id, prompt.reaction_key) == (WIZARD, "feather-fall")
    assert prompt.triggering_actor_id == TARGET
    assert "falls 30 feet" in prompt.trigger


def test_feather_fall_prevents_the_damage_and_the_prone_condition() -> None:
    state = _opened(_ledge(30), _target(), _wizard())
    paused = _shove(state, reactions=_suspend)
    assert paused.suspension is not None
    step = asyncio.run(resume(state, paused.suspension, "use", 9))
    assert [e.actor_id for e in _of(step, ReactionTriggered)] == [WIZARD]
    assert [(e.actor_id, e.spell_id) for e in _of(step, SpellCast)] == [(WIZARD, "feather-fall")]
    assert _of(step, CreatureFell)
    assert not _fall_damage(step)
    assert not _prone(step)
    wizard = next(c for c in step.state.combatants if c.entity_id == WIZARD)
    assert wizard.reaction_available is False


def test_passing_on_feather_fall_lets_the_creature_fall() -> None:
    step = _shove(_opened(_ledge(30), _target(), _wizard()))  # the default policy passes
    assert not _of(step, SpellCast)
    assert len(_fall_damage(step)) == 1
    assert _prone(step)


def test_feather_fall_lasts_600_feet_of_a_longer_fall() -> None:
    """Ten rounds at 60 feet a round: a 650 ft fall still deals 5d6 for the
    last 50 feet."""
    step = _shove(_opened(_ledge(650), _target(), _wizard()), reactions=lambda _p: "use")
    assert _of(step, SpellCast)
    (damage,) = _fall_damage(step)
    assert 5 <= damage.amount <= 30
    assert _prone(step)


def test_the_falling_creature_can_cast_it_on_itself() -> None:
    target = _target(
        spell_slots={1: 1},
        spells_known=["feather-fall"],
        class_slug="wizard",
        character_level=1,
        intelligence=16,
    )
    step = _shove(_opened(_ledge(30), target), reactions=lambda _p: "use")
    assert [(e.actor_id, e.spell_id) for e in _of(step, SpellCast)] == [(TARGET, "feather-fall")]
    assert not _fall_damage(step)


def test_feather_fall_is_not_offered_out_of_range_or_for_a_harmless_drop() -> None:
    wide = GridScene(width=20, height=3, elevation_cells={cell_id(0, 0): 30, cell_id(1, 0): 30})
    # 17 squares (85 feet) from the landing cell.
    out_of_range = _opened(wide, _target(), _wizard(zone_id=cell_id(19, 2)))
    assert _shove(out_of_range, reactions=_suspend).suspension is None
    harmless = _opened(_ledge(5), _target(), _wizard())
    assert _shove(harmless, reactions=_suspend).suspension is None
