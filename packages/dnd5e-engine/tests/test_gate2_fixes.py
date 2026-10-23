"""Spell fixes for spec 02a's gate 2 (issue #37): a movement token in an
effect, Mystic Arcanum, and a Sphere centred on a chosen point.

SRD 5.2.1 Spider Climb: the target "gains the ability to move up, down, and
across vertical surfaces and along ceilings, while leaving its hands free. It
also gains a Climb Speed equal to its Speed."

SRD 5.2.1 Freedom of Movement: "the target also has a Swim Speed equal to its
Speed."

SRD 5.2.1 Warlock, Mystic Arcanum: "Choose one level 6 Warlock spell as this
arcanum. You can cast your arcanum spell once without expending a spell slot,
and you must finish a Long Rest before you can cast it in this way again. As
shown in the Warlock Features table, you gain another Warlock spell of your
choice that can be cast in this way when you reach Warlock levels 13 (level 7
spell), 15 (level 8 spell), and 17 (level 9 spell)."

SRD 5.2.1 Fireball: "A bright streak flashes from you to a point you choose
within range and then blossoms with a low roar into a fiery explosion. Each
creature in a 20-foot-radius Sphere centered on that point makes a Dexterity
saving throw."
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dnd5e_engine import (
    PlayerIntent,
    advance_monster_turn,
    cell_id,
    derive_pact_slots,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.events import AreaTargeted, CastFailed, EffectApplied, SaveRolled, SpellCast
from dnd5e_engine.orchestrator import _climbs_freely, _find_combatant, _get_live
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec

CASTER = "pc:1"
ALLY = "pc:2"


def _caster(**fields: Any) -> PartyMemberSpec:
    base: dict[str, Any] = {
        "entity_id": CASTER,
        "name": "Caster",
        "initiative": 20,
        "hp_current": 100,
        "hp_max": 100,
        "zone_id": cell_id(2, 3),
        "intelligence": 20,
        "class_slug": "wizard",
        "character_level": 20,
        "spell_slots": {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1},
    }
    return PartyMemberSpec(**(base | fields))


def _warlock(level: int, **fields: Any) -> PartyMemberSpec:
    return _caster(
        class_slug="warlock",
        character_level=level,
        charisma=20,
        spell_slots={},
        pact_slots=derive_pact_slots(level),
        **fields,
    )


def _ally() -> PartyMemberSpec:
    return PartyMemberSpec(
        entity_id=ALLY,
        name="Ally",
        initiative=19,
        hp_current=30,
        hp_max=30,
        base_speed=25,
        zone_id=cell_id(2, 4),
    )


def _ogre(entity_id: str, cell: tuple[int, int]) -> EncounterMemberSpec:
    return EncounterMemberSpec(
        entity_id=entity_id,
        entity_type="Monster",
        name="Ogre",
        initiative=1,
        hp_current=300,
        hp_max=300,
        ac=11,
        zone_id=cell_id(*cell),
        monster_template_slug="ogre",
    )


def _cast(
    caster: PartyMemberSpec,
    *,
    foes: dict[str, tuple[int, int]] | None = None,
    scene: GridScene | None = None,
    **intent: Any,
) -> tuple[Any, list[Any]]:
    async def _run() -> tuple[Any, list[Any]]:
        started = await start_combat(
            session_id="gate2-fixes",
            party=[caster, _ally()],
            encounter=[_ogre(i, c) for i, c in (foes or {"cre:1": (8, 3)}).items()],
            grid_scene=scene or GridScene(width=40, height=12),
            rng_seed=3,
        )
        drain_pending_events(started.handle)
        await submit_player_intent(
            started.handle,
            actor_id=CASTER,
            intent=PlayerIntent(intent_type="cast_spell", **intent),
        )
        return started.handle, drain_pending_events(started.handle)

    return asyncio.run(_run())


def _of[T](events: list[Any], kind: type[T]) -> list[T]:
    return [e for e in events if isinstance(e, kind)]


# ── a Speed in an effect reads the creature it lands on ─────────────────────


def _granted(events: list[Any], key: str) -> list[str]:
    return [
        str(change.value)
        for applied in _of(events, EffectApplied)
        if applied.effect.target_id == ALLY
        for change in applied.effect.changes
        if change.key == key
    ]


def test_spider_climb_gives_a_climb_speed_equal_to_the_targets_speed() -> None:
    handle, events = _cast(
        _caster(spells_known=["spider-climb"]),
        spell_id="spider-climb",
        slot_level=2,
        target_id=ALLY,
    )
    assert _of(events, SpellCast)
    assert _granted(events, "system.attributes.movement.climb") == ["25"]
    live = _get_live(handle)
    ally = _find_combatant(live, ALLY)
    assert ally is not None
    assert _climbs_freely(live, ally)


def test_freedom_of_movement_gives_a_swim_speed_equal_to_the_targets_speed() -> None:
    _, events = _cast(
        _caster(spells_known=["freedom-of-movement"]),
        spell_id="freedom-of-movement",
        slot_level=4,
        target_id=ALLY,
    )
    assert _of(events, SpellCast)
    assert _granted(events, "system.attributes.movement.swim") == ["25"]


# ── Mystic Arcanum ──────────────────────────────────────────────────────────


def _arcanum_cast(caster: PartyMemberSpec, slug: str, level: int, times: int = 1) -> list[Any]:
    async def _run() -> list[Any]:
        started = await start_combat(
            session_id="arcanum",
            party=[caster],
            encounter=[_ogre("cre:1", (4, 3))],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
        handle = started.handle
        drain_pending_events(handle)
        out: list[Any] = []
        for turn in range(times):
            if turn:
                await advance_monster_turn(handle)  # the ogre's turn, back to the caster
                drain_pending_events(handle)
            await submit_player_intent(
                handle,
                actor_id=CASTER,
                intent=PlayerIntent(
                    intent_type="cast_spell", spell_id=slug, slot_level=level, target_id="cre:1"
                ),
            )
            out.append(drain_pending_events(handle))
            out.append(_get_live(handle).custom_counters_by_entity.get(CASTER, {}))
        return out

    return asyncio.run(_run())


def test_a_level_11_warlock_casts_its_level_6_arcanum_once_without_a_slot() -> None:
    first, counters, second, _ = _arcanum_cast(
        _warlock(11, spells_known=["eyebite"]), "eyebite", 6, times=2
    )
    assert [e.spell_id for e in _of(first, SpellCast)] == ["eyebite"]
    assert counters["slotless_cast:eyebite"] == {"spent": 1}
    assert [e.reason for e in _of(second, CastFailed)] == ["no_slot"]


def test_an_arcanum_arrives_at_its_warlock_level() -> None:
    (events, _) = _arcanum_cast(
        _warlock(12, spells_known=["finger-of-death"]), "finger-of-death", 7
    )
    assert [e.reason for e in _of(events, CastFailed)] == ["no_slot"]
    (events, _) = _arcanum_cast(
        _warlock(13, spells_known=["finger-of-death"]), "finger-of-death", 7
    )
    assert _of(events, SpellCast)


def test_a_level_10_warlock_has_no_arcanum() -> None:
    (events, _) = _arcanum_cast(_warlock(10, spells_known=["eyebite"]), "eyebite", 6)
    assert [e.reason for e in _of(events, CastFailed)] == ["no_slot"]


def test_an_arcanum_casts_only_at_its_own_level() -> None:
    (events, _) = _arcanum_cast(_warlock(20, spells_known=["eyebite"]), "eyebite", 7)
    assert [e.reason for e in _of(events, CastFailed)] == ["no_slot"]


def test_mystic_arcanum_names_the_arcanum_when_spells_known_leaves_it_open() -> None:
    known = ["eyebite", "circle-of-death"]
    with pytest.raises(ValueError, match="one level 6 arcanum"):
        _arcanum_cast(_warlock(11, spells_known=known), "eyebite", 6)
    (events, _) = _arcanum_cast(
        _warlock(11, spells_known=known, mystic_arcanum=("circle-of-death",)),
        "circle-of-death",
        6,
    )
    assert _of(events, SpellCast)


@pytest.mark.parametrize(
    ("arcanum", "message"),
    [
        (("finger-of-death",), "Warlock level 13, not 11"),
        (("fireball",), "not a level 6 to 9 Warlock spell"),
        (("heal",), "not a level 6 to 9 Warlock spell"),
        (("eyebite", "circle-of-death"), "one level 6 arcanum"),
    ],
)
def test_an_arcanum_the_warlock_cannot_have_is_refused(
    arcanum: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _arcanum_cast(_warlock(11, mystic_arcanum=arcanum), arcanum[0], 6)


# ── a Sphere at a chosen point ──────────────────────────────────────────────


def _fireball(**aim: Any) -> tuple[Any, list[Any]]:
    return _cast(
        _caster(spells_known=["fireball"]),
        foes={"cre:1": (8, 3), "cre:2": (12, 3)},
        spell_id="fireball",
        slot_level=3,
        **aim,
    )


def test_fireball_centred_on_an_empty_point_catches_the_creatures_around_it() -> None:
    handle, events = _fireball(target_zone_id=cell_id(10, 3))
    (area,) = _of(events, AreaTargeted)
    assert area.origin == cell_id(10, 3)
    assert {e.target_id for e in _of(events, SaveRolled)} == {"cre:1", "cre:2"}
    assert _get_live(handle).spell_slots_by_entity[CASTER][3] == 2


def test_a_point_beyond_the_range_is_refused_before_the_slot() -> None:
    # Fireball's range is 150 feet; (33, 3) is 31 squares (155 feet) away.
    handle, events = _fireball(target_zone_id=cell_id(33, 3))
    assert [e.reason for e in _of(events, CastFailed)] == ["out_of_range"]
    assert not _of(events, SpellCast)
    assert _get_live(handle).spell_slots_by_entity[CASTER][3] == 3


def test_a_point_behind_total_cover_or_off_the_map_is_refused() -> None:
    walled = GridScene(width=40, height=12, blocked_cells=[cell_id(5, 3)])
    _, events = _cast(
        _caster(spells_known=["fireball"]),
        scene=walled,
        spell_id="fireball",
        slot_level=3,
        target_zone_id=cell_id(8, 3),
    )
    assert [e.reason for e in _of(events, CastFailed)] == ["out_of_range"]
    _, events = _fireball(target_zone_id=cell_id(50, 3))
    assert [e.reason for e in _of(events, CastFailed)] == ["out_of_range"]


def test_a_named_creature_still_centres_the_sphere() -> None:
    _, events = _fireball(target_id="cre:1")
    (area,) = _of(events, AreaTargeted)
    assert area.origin == cell_id(8, 3)
