"""A spell cast outside combat, and the casting-time gate on a combat turn
(spec 02a, acceptance criterion 15; issue #41).

SRD 5.2.1 "Casting Time": "Most spells have a casting time of an action, but
some ... take 1 minute or even longer". A turn lasts about six seconds, so a
spell that takes a minute or more is cast between fights, and contract 03 lets
the Game Master declare ``cast_spell`` in a narrative turn.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dnd5e_engine import (
    PlayerIntent,
    cast_outside_combat,
    cell_id,
    derive_spell_slots,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.combat_state import CombatState, CombatStep
from dnd5e_engine.events import (
    CastFailed,
    ConcentrationDropped,
    EffectApplied,
    HealingApplied,
    SpellCast,
    TurnEnded,
)
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec

CASTER = "pc:1"
ALLY = "pc:2"


def _cleric(**fields: Any) -> PartyMemberSpec:
    base: dict[str, Any] = {
        "entity_id": CASTER,
        "name": "Cleric",
        "initiative": 15,
        "hp_current": 40,
        "hp_max": 40,
        "wisdom": 18,
        "zone_id": cell_id(1, 0),  # ignored without a grid_scene
        "class_slug": "cleric",
        "character_level": 9,
        "spell_slots": derive_spell_slots("cleric", "full", 9),
        "spells_known": ["cure-wounds", "bless", "raise-dead", "teleportation-circle", "identify"],
    }
    return PartyMemberSpec(**(base | fields))


def _ally(hp: int = 5) -> PartyMemberSpec:
    return PartyMemberSpec(
        entity_id=ALLY, name="Ally", initiative=10, hp_current=hp, hp_max=30, zone_id=cell_id(0, 0)
    )


def _cast(spell: str, level: int, seed: int = 3, **aim: Any) -> CombatStep:
    return asyncio.run(
        cast_outside_combat(
            session_id="narrative",
            caster_id=CASTER,
            intent=PlayerIntent(intent_type="cast_spell", spell_id=spell, slot_level=level, **aim),
            party=[_cleric(), _ally()],
            seed=seed,
        )
    )


def _of[T](step: CombatStep, kind: type[T]) -> list[T]:
    return [e for e in step.events if isinstance(e, kind)]


def _slots(state: CombatState, level: int) -> int:
    return state.spell_slots[CASTER][level]


def test_a_healing_spell_heals_and_spends_its_slot() -> None:
    before = derive_spell_slots("cleric", "full", 9)
    step = _cast("cure-wounds", 2, target_id=ALLY)
    assert [e.spell_id for e in _of(step, SpellCast)] == ["cure-wounds"]
    (healed,) = _of(step, HealingApplied)
    assert healed.target_id == ALLY
    assert step.state.hp[ALLY] == 5 + healed.amount
    assert _slots(step.state, 2) == before[2] - 1


def test_a_minute_long_spell_casts_outside_combat() -> None:
    """Teleportation Circle: "Casting Time: 1 minute"."""
    step = _cast("teleportation-circle", 5)
    assert _of(step, SpellCast)
    assert not _of(step, CastFailed)
    assert _slots(step.state, 5) == derive_spell_slots("cleric", "full", 9)[5] - 1


def test_an_hour_long_spell_casts_outside_combat() -> None:
    """Raise Dead: "Casting Time: 1 hour"."""
    step = _cast("raise-dead", 5, target_id=ALLY)
    assert _of(step, SpellCast)
    assert not _of(step, CastFailed)


def test_a_concentration_spell_begins_and_the_state_keeps_it() -> None:
    step = _cast("bless", 1, target_ids=(CASTER, ALLY))
    assert step.state.concentration[CASTER]
    assert {e.effect.target_id for e in _of(step, EffectApplied)} == {CASTER, ALLY}
    assert CombatState.model_validate_json(step.state.model_dump_json()) == step.state


def test_no_turn_passes_and_the_caster_keeps_its_turn() -> None:
    step = _cast("cure-wounds", 1, target_id=ALLY)
    assert step.state.round == 1
    assert step.state.active_combatant == CASTER
    assert not _of(step, TurnEnded)
    # No duration ran: a concentration spell is as long as it was cast.
    bless = _cast("bless", 1, target_ids=(CASTER,))
    assert not _of(bless, ConcentrationDropped)


def test_the_same_seed_gives_the_same_cast() -> None:
    a = _cast("cure-wounds", 2, seed=9, target_id=ALLY)
    b = _cast("cure-wounds", 2, seed=9, target_id=ALLY)
    assert a.model_dump_json() == b.model_dump_json()
    healed = {_cast("cure-wounds", 2, seed=s, target_id=ALLY).state.hp[ALLY] for s in range(1, 12)}
    assert len(healed) > 1


def test_an_out_of_slots_cast_fails_without_spending_anything() -> None:
    step = asyncio.run(
        cast_outside_combat(
            session_id="narrative",
            caster_id=CASTER,
            intent=PlayerIntent(
                intent_type="cast_spell", spell_id="cure-wounds", slot_level=1, target_id=ALLY
            ),
            party=[_cleric(spell_slots={1: 0}), _ally()],
            seed=1,
        )
    )
    assert [e.reason for e in _of(step, CastFailed)] == ["no_slot"]
    assert not _of(step, HealingApplied)


def test_a_cast_can_name_a_creature_that_is_not_on_the_casters_side() -> None:
    step = asyncio.run(
        cast_outside_combat(
            session_id="narrative",
            caster_id=CASTER,
            intent=PlayerIntent(
                intent_type="cast_spell", spell_id="sacred-flame", target_id="cre:1"
            ),
            party=[_cleric(spells_known=["sacred-flame"])],
            others=[
                EncounterMemberSpec(
                    entity_id="cre:1",
                    entity_type="Monster",
                    name="Bandit",
                    initiative=0,
                    hp_current=11,
                    hp_max=11,
                    ac=12,
                    monster_template_slug="bandit",
                    zone_id=cell_id(0, 0),
                )
            ],
            seed=2,
        )
    )
    assert _of(step, SpellCast)


def test_a_declaration_that_is_not_a_cast_is_refused() -> None:
    with pytest.raises(ValueError, match="only a cast_spell"):
        asyncio.run(
            cast_outside_combat(
                session_id="narrative",
                caster_id=CASTER,
                intent=PlayerIntent(intent_type="pass"),
                party=[_cleric()],
                seed=1,
            )
        )
    with pytest.raises(ValueError, match="not in the party"):
        asyncio.run(
            cast_outside_combat(
                session_id="narrative",
                caster_id="pc:9",
                intent=PlayerIntent(intent_type="cast_spell", spell_id="bless", slot_level=1),
                party=[_cleric()],
                seed=1,
            )
        )


# ── a spell that takes a minute or more is not cast on a combat turn ────────


def _combat_cast(spell: str, level: int, **aim: Any) -> tuple[list[Any], int]:
    async def _run() -> tuple[list[Any], int]:
        started = await start_combat(
            session_id="combat",
            party=[_cleric(initiative=20), _ally()],
            encounter=[
                EncounterMemberSpec(
                    entity_id="cre:1",
                    entity_type="Monster",
                    name="Foe",
                    initiative=1,
                    hp_current=10,
                    hp_max=10,
                    ac=10,
                    zone_id=cell_id(5, 5),
                )
            ],
            grid_scene=GridScene(width=8, height=8),
            rng_seed=1,
        )
        drain_pending_events(started.handle)
        await submit_player_intent(
            started.handle,
            actor_id=CASTER,
            intent=PlayerIntent(intent_type="cast_spell", spell_id=spell, slot_level=level, **aim),
        )
        events = drain_pending_events(started.handle)
        from dnd5e_engine.orchestrator import _get_live

        live = _get_live(started.handle)
        return events, live.spell_slots_by_entity[CASTER][level]

    return asyncio.run(_run())


@pytest.mark.parametrize(
    ("spell", "level"),
    [("teleportation-circle", 5), ("raise-dead", 5), ("scrying", 5)],
)
def test_a_long_spell_is_refused_on_a_combat_turn_before_its_slot_is_spent(
    spell: str, level: int
) -> None:
    events, slots_left = _combat_cast(spell, level, target_id=ALLY)
    assert [e.reason for e in events if isinstance(e, CastFailed)] == ["casting_time"]
    assert not [e for e in events if isinstance(e, SpellCast)]
    assert slots_left == derive_spell_slots("cleric", "full", 9)[level]


def test_an_action_spell_is_still_cast_on_a_combat_turn() -> None:
    events, _ = _combat_cast("cure-wounds", 1, target_id=ALLY)
    assert [e for e in events if isinstance(e, SpellCast)]
