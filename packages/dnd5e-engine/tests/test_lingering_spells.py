"""A spell's later activities: held back at the cast, fired at their
triggers (spec 02a B3b, issue #37).

SRD 5.2.1 sentences, by spell:

- Weird: "A Frightened target makes a Wisdom saving throw at the end of each of
  its turns. On a failed save, it takes 5d10 Psychic damage. On a successful
  save, the spell ends on that target."
- Acid Arrow: "On a hit, the target takes 4d4 Acid damage and 2d4 Acid damage
  at the end of its next turn."
- Vitriolic Sphere: "On a failed save, a creature takes 10d4 Acid damage and
  another 5d4 Acid damage at the end of its next turn."
- Delayed Blast Fireball: "The spell's base damage is 12d6, and the damage
  increases by 1d6 whenever your turn ends and the spell hasn't ended."; "When
  the spell ends, the bead explodes".
- Tsunami: "At the start of each of your turns after the wall appears ... must
  succeed on a Strength saving throw or take 5d10 Bludgeoning damage"; "the
  damage the wall deals on later rounds is reduced by 1d10".
- Stinking Cloud: "Each creature that starts its turn in the Sphere must
  succeed on a Constitution saving throw or have the Poisoned condition".
- Spirit Guardians: "whenever a creature enters the Emanation or ends its turn
  there, the creature must make a Wisdom saving throw."
- Storm of Vengeance: "At the start of each of your later turns, the storm
  produces different effects"; "Turn 2. Acidic rain falls. Each creature and
  object under the cloud takes 4d6 Acid damage."
- Haste: "When the spell ends, the target is Incapacitated and has a Speed of 0
  until the end of its next turn".
- Hunter's Mark: "you deal an extra 1d6 Force damage to the target whenever you
  hit it with an attack roll."
"""

from __future__ import annotations

import asyncio
from typing import Any

from dnd5e_engine import (
    PlayerIntent,
    advance_monster_turn,
    cell_id,
    derive_spell_slots,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.activities.timing import SPELL_ACTIVITY_TIMINGS, cast_time_activities
from dnd5e_engine.combat_state import CombatState, begin_combat, resolve_intent
from dnd5e_engine.events import (
    AttackRolled,
    ConditionApplied,
    DamageApplied,
    EffectApplied,
    EffectExpired,
    SaveRolled,
)
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.lingering import LATER_RULES
from dnd5e_engine.orchestrator import CombatHandle, _get_live
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec

CASTER = "pc:1"
ALLY = "pc:2"
FOE = "cre:1"


def _caster(spell: str, cls: str = "wizard", **fields: Any) -> PartyMemberSpec:
    base: dict[str, Any] = {
        "entity_id": CASTER,
        "name": "Caster",
        "initiative": 20,
        "hp_current": 300,
        "hp_max": 300,
        "ac": 30,
        "zone_id": cell_id(2, 3),
        "spell_slots": derive_spell_slots(cls, "full" if cls != "ranger" else "half", 20),
        "spells_known": [spell],
        "class_slug": cls,
        "character_level": 20,
        "intelligence": 20,
        "wisdom": 20,
        "charisma": 20,
        "constitution": 30,
        "equipment": ("longsword",),
    }
    return PartyMemberSpec(**(base | fields))


def _foe(cell: tuple[int, int] = (5, 3), entity_id: str = FOE) -> EncounterMemberSpec:
    """A foe that never moves and so never reaches the caster."""
    return EncounterMemberSpec(
        entity_id=entity_id,
        entity_type="Monster",
        name="Foe",
        initiative=1,
        hp_current=500,
        hp_max=500,
        ac=5,
        zone_id=cell_id(*cell),
        base_speed=0,
    )


async def _open(caster: PartyMemberSpec, *extra: PartyMemberSpec, seed: int = 5) -> CombatHandle:
    started = await start_combat(
        session_id="lingering",
        party=[caster, *extra],
        encounter=[_foe()],
        grid_scene=GridScene(width=20, height=12),
        rng_seed=seed,
    )
    drain_pending_events(started.handle)
    return started.handle


async def _cast(handle: CombatHandle, spell: str, level: int, **aim: Any) -> list[Any]:
    await submit_player_intent(
        handle,
        actor_id=CASTER,
        intent=PlayerIntent(intent_type="cast_spell", spell_id=spell, slot_level=level, **aim),
    )
    return drain_pending_events(handle)


async def _to_caster(handle: CombatHandle) -> list[Any]:
    """Run every other turn until the caster's next turn starts."""
    events: list[Any] = []
    live = _get_live(handle)
    while live.current_actor_id != CASTER:
        if live.current_actor_id == ALLY:
            await submit_player_intent(
                handle, actor_id=ALLY, intent=PlayerIntent(intent_type="pass")
            )
        else:
            await advance_monster_turn(handle)
        events += drain_pending_events(handle)
    return events


async def _pass(handle: CombatHandle) -> list[Any]:
    await submit_player_intent(handle, actor_id=CASTER, intent=PlayerIntent(intent_type="pass"))
    return drain_pending_events(handle)


def _of[T](events: list[Any], kind: type[T], target: str | None = None) -> list[T]:
    return [
        e
        for e in events
        if isinstance(e, kind) and (target is None or getattr(e, "target_id", None) == target)
    ]


# ── the table ───────────────────────────────────────────────────────────────


def test_every_table_entry_names_an_activity_of_its_spell() -> None:
    loader = get_lib_loader()
    for table in (SPELL_ACTIVITY_TIMINGS, LATER_RULES):
        for slug, entries in table.items():
            spell = loader.get_spell(slug)
            assert spell is not None, slug
            ids = {a.id for a in spell.activities}
            assert set(entries) <= ids, (slug, set(entries) - ids)


def test_every_fired_rule_is_a_later_activity_with_that_trigger() -> None:
    for slug, rules in LATER_RULES.items():
        for activity_id, rule in rules.items():
            timing = SPELL_ACTIVITY_TIMINGS[slug][activity_id]
            assert timing.timing == "later"
            assert rule.trigger in (timing.trigger, *timing.also), (slug, activity_id)


def test_a_cast_resolves_one_of_each_set_of_alternatives() -> None:
    spell = get_lib_loader().get_spell("wall-of-fire")
    assert spell is not None
    kept = cast_time_activities(spell.slug, spell.activities)
    names = [a.name for a in kept]
    assert names == ["Create Wall"]
    ring = next(a for a in spell.activities if a.name == "Create Ring")
    assert [a.name for a in cast_time_activities(spell.slug, spell.activities, ring.id)] == [
        "Create Ring"
    ]


# ── turn-boundary triggers ──────────────────────────────────────────────────


def test_weird_saves_at_the_end_of_each_frightened_turn_and_ends_on_a_success() -> None:
    async def _run() -> tuple[list[Any], list[Any]]:
        handle = await _open(_caster("weird"))
        cast = await _cast(handle, "weird", 9, target_id=FOE)
        return cast, await _to_caster(handle)

    cast, later = asyncio.run(_run())
    # One save at the cast; the end-of-turn save is not rolled with it.
    assert len(_of(cast, SaveRolled, FOE)) == 1
    saves = _of(later, SaveRolled, FOE)
    assert len(saves) == 1  # the lingering save replaces the effect's own repeat save
    if saves[0].succeeded:
        assert any(e.reason == "spell_ended" for e in _of(later, EffectExpired, FOE))
    else:
        assert _of(later, DamageApplied, FOE)


def test_acid_arrow_deals_its_2d4_at_the_end_of_the_targets_next_turn_only() -> None:
    async def _run() -> tuple[list[Any], list[Any], list[Any]]:
        for seed in range(1, 30):
            handle = await _open(_caster("acid-arrow"), seed=seed)
            cast = await _cast(handle, "acid-arrow", 2, target_id=FOE)
            if any(e.is_hit for e in _of(cast, AttackRolled, FOE)):
                first = await _to_caster(handle)
                await _pass(handle)
                return cast, first, await _to_caster(handle)
        raise AssertionError("Acid Arrow never hit")

    cast, first, second = asyncio.run(_run())
    assert len(_of(cast, DamageApplied, FOE)) == 1  # 4d4 at the cast, nothing more
    (later,) = _of(first, DamageApplied, FOE)
    assert later.damage_type == "acid"
    assert 2 <= later.amount <= 8
    assert not _of(second, DamageApplied, FOE)  # once


def test_vitriolic_sphere_waits_for_each_failing_creatures_next_turn() -> None:
    async def _run() -> tuple[list[Any], list[Any]]:
        for seed in range(1, 30):
            handle = await _open(_caster("vitriolic-sphere"), seed=seed)
            cast = await _cast(handle, "vitriolic-sphere", 4, target_zone_id=cell_id(6, 3))
            if any(not e.succeeded for e in _of(cast, SaveRolled, FOE)):
                return cast, await _to_caster(handle)
        raise AssertionError("the foe never failed its save")

    cast, later = asyncio.run(_run())
    assert len(_of(cast, DamageApplied, FOE)) == 1
    (extra,) = _of(later, DamageApplied, FOE)
    assert 5 <= extra.amount <= 20


def test_tsunami_casts_and_its_wave_dwindles_each_round() -> None:
    async def _run() -> list[list[Any]]:
        handle = await _open(_caster("tsunami", "druid"))
        rounds = [await _cast(handle, "tsunami", 8, target_id=FOE)]
        for _ in range(3):
            rounds.append(await _to_caster(handle))
            await _pass(handle)
        return rounds

    cast, *later = asyncio.run(_run())
    assert len(_of(cast, SaveRolled, FOE)) == 1
    for dice, events in zip((5, 4, 3), later, strict=True):
        assert len(_of(events, SaveRolled, FOE)) == 1
        for damage in _of(events, DamageApplied, FOE):
            assert damage.amount <= dice * 10


def test_stinking_cloud_saves_each_creature_starting_its_turn_inside() -> None:
    async def _run() -> list[Any]:
        handle = await _open(_caster("stinking-cloud"))
        cast = await _cast(handle, "stinking-cloud", 3, target_zone_id=cell_id(9, 3))
        return cast + await _to_caster(handle)

    events = asyncio.run(_run())
    assert len(_of(events, SaveRolled, FOE)) == 1  # the foe's own turn start
    assert not _of(events, SaveRolled, CASTER)  # outside the cloud


def test_spirit_guardians_saves_an_enemy_that_ends_its_turn_in_the_emanation() -> None:
    async def _run() -> tuple[list[Any], list[Any]]:
        ally = PartyMemberSpec(
            entity_id=ALLY,
            name="Ally",
            initiative=10,
            hp_current=30,
            hp_max=30,
            zone_id=cell_id(3, 3),
        )
        handle = await _open(_caster("spirit-guardians", "cleric"), ally)
        cast = await _cast(handle, "spirit-guardians", 3)
        return cast, await _to_caster(handle)

    cast, later = asyncio.run(_run())
    assert not _of(cast, SaveRolled, FOE)  # no save at the cast
    assert len(_of(later, SaveRolled, FOE)) == 1
    assert not _of(later, SaveRolled, ALLY)  # an ally is spared


def test_storm_of_vengeance_rains_acid_on_the_casters_second_turn() -> None:
    async def _run() -> list[Any]:
        handle = await _open(_caster("storm-of-vengeance", "druid"))
        await _cast(handle, "storm-of-vengeance", 9, target_zone_id=cell_id(15, 3))
        return await _to_caster(handle)

    later = asyncio.run(_run())
    acid = [e for e in _of(later, DamageApplied, FOE) if e.damage_type == "acid"]
    assert len(acid) == 1
    assert 4 <= acid[0].amount <= 24


# ── when the spell ends ─────────────────────────────────────────────────────


def test_delayed_blast_fireball_explodes_with_its_extra_dice_when_the_spell_ends() -> None:
    async def _run() -> tuple[list[Any], list[Any], list[Any]]:
        handle = await _open(_caster("delayed-blast-fireball"))
        cast = await _cast(handle, "delayed-blast-fireball", 7, target_zone_id=cell_id(6, 3))
        await _to_caster(handle)
        await _pass(handle)
        await _to_caster(handle)
        assert _get_live(handle).lingering_spells[0].uses == 2  # two of its turns ended
        await submit_player_intent(
            handle, actor_id=CASTER, intent=PlayerIntent(intent_type="drop_concentration")
        )
        return cast, drain_pending_events(handle), _get_live(handle).lingering_spells

    cast, ended, left = asyncio.run(_run())
    assert not _of(cast, SaveRolled)  # no explosion at the cast
    assert [e.ability for e in _of(ended, SaveRolled, FOE)] == ["dex"]
    fire = [e for e in _of(ended, DamageApplied, FOE) if e.damage_type == "fire"]
    assert len(fire) == 1
    assert fire[0].amount <= 14 * 6  # 12d6 + 2d6, halved on a success
    assert left == []


def test_haste_leaves_its_target_lethargic_when_the_spell_ends() -> None:
    async def _run() -> tuple[list[Any], list[Any]]:
        ally = PartyMemberSpec(
            entity_id=ALLY,
            name="Ally",
            initiative=10,
            hp_current=30,
            hp_max=30,
            zone_id=cell_id(3, 3),
        )
        handle = await _open(_caster("haste"), ally)
        cast = await _cast(handle, "haste", 3, target_id=ALLY)
        await _to_caster(handle)
        await submit_player_intent(
            handle, actor_id=CASTER, intent=PlayerIntent(intent_type="drop_concentration")
        )
        return cast, drain_pending_events(handle)

    cast, ended = asyncio.run(_run())
    cast_effects = [e.effect.name for e in _of(cast, EffectApplied) if e.effect.target_id == ALLY]
    lethargy = [e.effect.name for e in _of(ended, EffectApplied) if e.effect.target_id == ALLY]
    assert cast_effects
    assert lethargy
    assert all(name not in lethargy for name in cast_effects)


# ── on a hit ────────────────────────────────────────────────────────────────


def test_hunters_mark_adds_1d6_force_to_each_hit_not_to_the_cast() -> None:
    async def _run() -> tuple[list[Any], list[Any]]:
        for seed in range(1, 40):
            handle = await _open(
                _caster("hunters-mark", "ranger", zone_id=cell_id(4, 3), strength=20), seed=seed
            )
            cast = await _cast(handle, "hunters-mark", 1, target_id=FOE)
            await submit_player_intent(
                handle,
                actor_id=CASTER,
                intent=PlayerIntent(intent_type="attack", target_id=FOE, weapon_id="longsword"),
            )
            swing = drain_pending_events(handle)
            if any(e.is_hit for e in _of(swing, AttackRolled, FOE)):
                return cast, swing
        raise AssertionError("the swing never hit")

    cast, swing = asyncio.run(_run())
    assert not _of(cast, DamageApplied, FOE)
    force = [e for e in _of(swing, DamageApplied, FOE) if e.damage_type == "force"]
    assert len(force) == 1
    assert 1 <= force[0].amount <= 12  # 1d6, doubled on a Critical Hit


# ── the state carries it ────────────────────────────────────────────────────


def test_a_lingering_spell_round_trips_through_the_combat_state() -> None:
    async def _run() -> CombatState:
        opened = await begin_combat(
            session_id="lingering-state",
            party=[_caster("delayed-blast-fireball")],
            encounter=[_foe()],
            grid_scene=GridScene(width=20, height=12),
            seed=1,
        )
        step = await resolve_intent(
            opened.state,
            actor_id=CASTER,
            intent=PlayerIntent(
                intent_type="cast_spell",
                spell_id="delayed-blast-fireball",
                slot_level=7,
                target_zone_id=cell_id(6, 3),
            ),
            seed=2,
        )
        return step.state

    state = asyncio.run(_run())
    (lingering,) = state.lingering_spells
    assert (lingering.spell_id, lingering.origin, lingering.uses) == (
        "delayed-blast-fireball",
        cell_id(6, 3),
        1,
    )
    assert CombatState.model_validate_json(state.model_dump_json()) == state


def test_poisoned_by_stinking_cloud_is_a_condition() -> None:
    async def _run() -> list[Any]:
        handle = await _open(_caster("stinking-cloud"), seed=9)
        cast = await _cast(handle, "stinking-cloud", 3, target_zone_id=cell_id(9, 3))
        return cast + await _to_caster(handle)

    events = asyncio.run(_run())
    (save,) = _of(events, SaveRolled, FOE)
    poisoned = [e for e in _of(events, ConditionApplied, FOE) if e.condition == "poisoned"]
    assert bool(poisoned) == (not save.succeeded)
