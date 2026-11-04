"""Pins the SRD 5.2.1 rules of the Spatial and Spells rows of
``docs/srd-5.2.1-rules-audit.md``. Each test quotes the decisive SRD sentence in
its docstring. Where the engine applies a rule differently, the test states the
SRD's outcome and is a strict ``xfail`` that names its ``BACKLOG.md`` entry;
fixing the engine turns it into an unexpected pass, which fails the suite until
the marker is removed.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Iterator
from itertools import pairwise
from typing import Any

import pytest
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine import (
    EncounterMemberSpec,
    GridScene,
    PartyMemberSpec,
    PlayerIntent,
    advance_monster_turn,
    cell_id,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.activities.passive_stats import CombatantSenses
from dnd5e_engine.events import (
    ActorMoved,
    AreaTargeted,
    AttackRolled,
    CastFailed,
    CombatantMoved,
    CreatureFell,
    DamageApplied,
    EffectApplied,
    EffectExpired,
    MoveFailed,
    SaveRolled,
    SpellCast,
)
from dnd5e_engine.lib_loader import get_lib_loader, set_lib_loader_for_tests
from dnd5e_engine.orchestrator import CombatHandle, _get_live, _LiveCombat, _occupied_cells
from dnd5e_engine.spatial import GridTopology, cover_bonus
from dnd5e_engine.spellcasting import spell_component_metadata

HERO = "pc:hero"
FOE = "mon:foe"


@pytest.fixture(autouse=True)
def _bundled_loader() -> Iterator[None]:
    set_lib_loader_for_tests(BundledAssetLoader())
    yield
    set_lib_loader_for_tests(None)


# ── helpers ─────────────────────────────────────────────────────────────────


class _Dice(random.Random):
    """A stand-in for the seeded dice: every d20 lands on ``d20`` and every
    other die on ``pips``. Swapped in after ``start_combat`` has rolled
    initiative."""

    def __init__(self, *, d20: int = 15, pips: int = 1) -> None:
        super().__init__()
        self._d20 = d20
        self._pips = pips

    def randint(self, a: int, b: int) -> int:
        return self._d20 if (a, b) == (1, 20) else self._pips


def _hero(at: tuple[int, int] = (0, 0), **fields: Any) -> PartyMemberSpec:
    base: dict[str, Any] = {
        "entity_id": HERO,
        "name": "Hero",
        "initiative": 20,
        "hp_current": 100,
        "hp_max": 100,
        "zone_id": cell_id(*at),
    }
    return PartyMemberSpec(**(base | fields))


def _foe(at: tuple[int, int] = (4, 0), entity_id: str = FOE, **fields: Any) -> EncounterMemberSpec:
    base: dict[str, Any] = {
        "entity_id": entity_id,
        "entity_type": "Monster",
        "name": entity_id,
        "initiative": 1,
        "hp_current": 500,
        "hp_max": 500,
        "ac": 5,
        "base_speed": 0,
        "zone_id": cell_id(*at),
    }
    return EncounterMemberSpec(**(base | fields))


def _caster(spells: list[str], slots: dict[int, int], **fields: Any) -> PartyMemberSpec:
    base: dict[str, Any] = {
        "class_slug": "wizard",
        "character_level": 9,
        "intelligence": 16,
        "spells_known": spells,
        "spell_slots": slots,
    }
    return _hero(**(base | fields))


async def _open(
    party: list[PartyMemberSpec],
    encounter: list[EncounterMemberSpec],
    scene: GridScene | None = None,
    *,
    seed: int = 3,
) -> tuple[CombatHandle, _LiveCombat]:
    started = await start_combat(
        session_id="srd-audit",
        party=party,
        encounter=encounter,
        grid_scene=scene or GridScene(width=20, height=8),
        rng_seed=seed,
    )
    drain_pending_events(started.handle)
    return started.handle, _get_live(started.handle)


async def _do(handle: CombatHandle, actor: str = HERO, **intent: Any) -> list[Any]:
    await submit_player_intent(handle, actor_id=actor, intent=PlayerIntent(**intent))
    return drain_pending_events(handle)


def _cast(spell: str, level: int | None = None, **aim: Any) -> dict[str, Any]:
    return {"intent_type": "cast_spell", "spell_id": spell, "slot_level": level, **aim}


def _of[T](events: list[Any], kind: type[T], target: str | None = None) -> list[T]:
    return [
        e
        for e in events
        if isinstance(e, kind) and (target is None or getattr(e, "target_id", None) == target)
    ]


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _grid(**scene: Any) -> GridTopology:
    scene.setdefault("width", 10)
    scene.setdefault("height", 10)
    return GridTopology(GridScene(**scene))


# ═══════════════════════════ Spatial ════════════════════════════════════════


# Playing on a Grid
def test_a_diagonal_step_costs_one_square_and_ranges_count_the_shortest_route() -> None:
    """SRD 5.2.1 "Playing on a Grid": "It costs 1 square of movement to enter an
    unoccupied square that's adjacent to your space (orthogonally or diagonally
    adjacent)." and "Count by the shortest route.\""""
    g = _grid()
    assert g.edge_distance("0,0", "1,1") == 5  # no 5-10-5 diagonal surcharge
    assert g.distance_ft("0,0", "3,7") == 35  # seven squares by the shortest route
    assert g.within_range("0,0", "3,7", 35)
    assert not g.within_range("0,0", "3,7", 30)


# Playing on a Grid
def test_a_square_is_five_feet_by_default_and_the_size_is_configuration() -> None:
    """SRD 5.2.1 "Playing on a Grid": "Squares. Each square represents 5 feet."
    The engine reads the size from the scene, so a host can change it."""
    assert _grid().cell_size_ft == 5
    wide = _grid(cell_size_ft=10)
    assert wide.distance_ft("0,0", "2,1") == 20
    assert wide.edge_distance("0,0", "1,1") == 10


# Difficult Terrain
def test_difficult_terrain_costs_two_squares_to_enter_and_nothing_to_leave() -> None:
    """SRD 5.2.1 "Difficult Terrain" (Playing on a Grid, Entering a Square): "A
    square of Difficult Terrain costs 2 squares to enter." The cost belongs to
    the square entered, so leaving it costs the ordinary 5 feet."""
    g = _grid(width=5, height=1, difficult_terrain_cells=["1,0"])
    assert g.edge_distance("0,0", "1,0") == 10
    assert g.edge_distance("1,0", "2,0") == 5
    assert g.reachable_cells("0,0", 15)["2,0"][0] == 15


# Playing on a Grid
def test_a_diagonal_step_cannot_cross_either_blocked_corner() -> None:
    """SRD 5.2.1 "Playing on a Grid": "Corners. Diagonal movement can't cross the
    corner of a wall, a large tree, or another terrain feature that fills its
    space." Either orthogonal neighbour filling its space refuses the diagonal;
    the orthogonal steps stay legal, and a feature that is only diagonal to the
    step does not matter."""
    east_filled = _grid(width=3, height=3, blocked_cells=["1,0"])
    south_filled = _grid(width=3, height=3, blocked_cells=["0,1"])
    assert east_filled.edge_distance("0,0", "1,1") is None
    assert south_filled.edge_distance("0,0", "1,1") is None
    assert east_filled.edge_distance("0,0", "0,1") == 5
    assert south_filled.edge_distance("0,0", "1,0") == 5
    # The feature sits on the far diagonal: nothing is cut, so the step is fine.
    assert _grid(width=3, height=3, blocked_cells=["1,1"]).edge_distance("0,0", "1,0") == 5


# Moving around Other Creatures
def test_an_enemy_space_is_impassable_rather_than_difficult_terrain() -> None:
    """SRD 5.2.1 "Moving around Other Creatures": "Another creature's space is
    Difficult Terrain for you unless that creature is Tiny or your ally." The
    engine does not model this: an enemy's space is impassable, so a move that
    the SRD allows at double cost is refused as unreachable. Pins the documented
    gap."""

    async def go() -> list[Any]:
        handle, _ = await _open(
            [_hero(base_speed=30)], [_foe((1, 0))], GridScene(width=3, height=1)
        )
        return await _do(handle, intent_type="move", target_zone_id=cell_id(2, 0))

    events = _run(go())
    assert [e.reason for e in _of(events, MoveFailed)] == ["unreachable"]
    assert not _of(events, ActorMoved)


# Wall and blocked-cell sight
def test_a_blocked_cell_blocks_sight_only_between_the_two_cells() -> None:
    """SRD 5.2.1 "Total Cover": "An object that covers the whole target" gives
    Total Cover, and "To block a line, an obstruction must provide Total Cover"
    ("Areas of Effect"). A blocked cell blocks the line through it and nothing
    beside it."""
    g = _grid(blocked_cells=["2,0"])
    assert g.cover_between("0,0", "4,0") == "total"
    assert g.has_line_of_sight("0,0", "4,0") is False
    assert g.has_line_of_sight("0,0", "1,0") is True  # the obstruction is not between
    assert g.has_line_of_sight("0,1", "4,1") is True  # a parallel row is clear


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Line of sight and cover are not symmetric",
)
def test_line_of_sight_and_cover_do_not_depend_on_which_end_looks() -> None:
    """SRD 5.2.1 "Total Cover": "To block a line, an obstruction must provide
    Total Cover." Whether an obstruction lies between two points does not depend
    on which of them is the attacker. The engine's Bresenham walk breaks ties by
    direction, so with ``blocked_cells=['1,1']`` sight from 0,0 to 1,2 is blocked
    and from 1,2 to 0,0 is not."""
    g = _grid(width=6, height=6, blocked_cells=["1,1"])
    assert g.has_line_of_sight("0,0", "1,2") == g.has_line_of_sight("1,2", "0,0")
    assert g.cover_between("0,0", "1,2") == g.cover_between("1,2", "0,0")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: A `total` cover tag does not block line of sight",
)
def test_a_total_cover_tagged_cell_blocks_sight_like_a_blocked_cell() -> None:
    """SRD 5.2.1 "Total Cover": "Total: An object that covers the whole target,
    can't be targeted directly." and "Blindsight": "Within that range, you can
    see anything that isn't behind Total Cover". A ``cover_cells`` entry tagged
    ``total`` is Total Cover, so sight does not pass through it, even for a
    creature with Blindsight."""
    g = _grid(width=5, height=1, cover_cells={"1,0": "total"})
    assert g.cover_between("0,0", "2,0") == "total"
    assert g.has_line_of_sight("0,0", "2,0") is False
    assert g.can_see("0,0", "2,0", CombatantSenses(blindsight=60)) is False


# Cover
def test_only_the_most_protective_degree_of_cover_applies() -> None:
    """SRD 5.2.1 "Cover": "If a target is behind multiple sources of cover, only
    the most protective degree of cover applies; the degrees aren't added
    together. For example, if a target is behind a creature that gives Half
    Cover and a tree trunk that gives Three-Quarters Cover, the target has
    Three-Quarters Cover." The bonus is +5, not +7."""
    g = _grid(cover_cells={"2,0": "three_quarters"})
    degree = g.cover_between("0,0", "4,0", occupied_cells={"1,0"})  # a creature at 1,0
    assert degree == "three_quarters"
    assert cover_bonus(degree) == 5


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Cover from creatures ignores size",
)
def test_a_tiny_creature_in_the_way_gives_no_half_cover() -> None:
    """SRD 5.2.1 "Cover": Half Cover comes from "Another creature or an object
    that covers at least half of the target". A Tiny creature (2½ by 2½ feet)
    does not cover half of a target. The engine gives Half Cover for any creature
    on the line, so an attack that meets AC 10 misses against AC 12."""

    async def shoot(*blockers: EncounterMemberSpec) -> AttackRolled:
        handle, live = await _open(
            [_hero(attack_bonus=5)],
            [_foe((4, 0), ac=10), *blockers],
            GridScene(width=6, height=3),
        )
        live.rng = _Dice(d20=5)  # 5 + 5 meets AC 10 exactly
        events = await _do(handle, intent_type="attack", weapon_id="longbow", target_id=FOE)
        (rolled,) = _of(events, AttackRolled)
        assert rolled.roll_total == 10
        return rolled

    assert get_lib_loader().get_monster("rat").creature_size == "tiny"  # type: ignore[union-attr]
    assert _run(shoot()).is_hit is True  # control: nothing in the way
    rat = _foe((2, 0), "mon:rat", monster_template_slug="rat")
    assert _run(shoot(rat)).is_hit is True


# Areas of Effect
def test_a_sphere_and_a_cylinder_include_their_point_of_origin() -> None:
    """SRD 5.2.1 "Sphere [Area of Effect]": "A Sphere's point of origin is
    included in the Sphere's area of effect." and "Cylinder [Area of Effect]":
    "A Cylinder's point of origin is included in the area of effect." """
    g = _grid(width=11, height=11)
    for shape in ("sphere", "cylinder"):
        cells = g.cells_in_template("5,5", shape, 5)
        assert "5,5" in cells
        assert len(cells) == 9


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: The Cone is wider than the SRD's",
)
def test_a_cone_is_as_wide_as_its_distance_from_the_point_of_origin() -> None:
    """SRD 5.2.1 "Cone [Area of Effect]": "A Cone's width at any point along its
    length is equal to that point's distance from the point of origin. For
    example, a Cone is 15 feet wide at a point along its length that is 15 feet
    from the point of origin." A 15 foot Cone aimed east is 3 squares wide at 3
    squares out. The engine keeps a 90 degree wedge, 7 squares wide there."""
    g = _grid(width=21, height=21)
    cells = set(g.cells_in_template("10,10", "cone", 15, direction=(1, 0)))
    assert {c for c in cells if c.startswith("13,")} == {"13,9", "13,10", "13,11"}


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: An area's aim snaps to eight directions",
)
def test_a_line_aimed_at_a_target_off_the_grid_directions_reaches_it() -> None:
    """SRD 5.2.1 "Line [Area of Effect]": "A Line is an area of effect that
    extends from a point of origin in a straight path along its length." Aimed at
    a creature 6 squares east and 1 south, a Lightning Bolt reaches it. The
    engine aims along the sign of the offset on each axis, a diagonal, and
    misses."""

    async def go() -> list[Any]:
        handle, _ = await _open(
            [_caster(["lightning-bolt"], {3: 2})], [_foe((6, 1))], GridScene(width=20, height=8)
        )
        return await _do(handle, **_cast("lightning-bolt", 3, target_id=FOE))

    (targeted,) = _of(_run(go()), AreaTargeted)
    assert FOE in targeted.affected_ids


# Breaking Up Your Move
def test_a_move_may_pass_through_an_ally_but_not_end_in_its_space() -> None:
    """SRD 5.2.1 "Moving around Other Creatures": "You can't willingly end a move
    in a space occupied by another creature." and "During your move, you can pass
    through the space of an ally". Ending the move in the ally's square is
    refused and costs nothing; moving on past the ally is allowed."""

    async def go() -> tuple[list[Any], list[Any]]:
        ally = PartyMemberSpec(
            entity_id="pc:ally", name="Ally", initiative=19, hp_current=20, hp_max=20,
            zone_id=cell_id(1, 0),
        )  # fmt: skip
        handle, _ = await _open(
            [_hero(base_speed=30), ally], [_foe((9, 0))], GridScene(width=10, height=1)
        )
        first = await _do(handle, intent_type="move", target_zone_id=cell_id(1, 0))
        second = await _do(handle, intent_type="move", target_zone_id=cell_id(2, 0))
        return first, second

    first, second = _run(go())
    assert [e.reason for e in _of(first, MoveFailed)] == ["occupied"]
    (moved,) = _of(second, ActorMoved)
    assert (moved.to_zone, moved.distance_ft) == (cell_id(2, 0), 10)


# Falling
def test_a_fall_deals_1d6_for_every_full_10_feet() -> None:
    """SRD 5.2.1 "Falling [Hazard]": "A creature that falls takes 1d6 Bludgeoning
    damage at the end of the fall for every 10 feet it fell, to a maximum of
    20d6." With every die forced to 6, a 25 foot fall deals 2d6 = 12 and a 19
    foot fall deals 1d6 = 6: the count is the floor of the feet over 10."""

    async def fall(drop_ft: int) -> tuple[list[Any], list[Any]]:
        scene = GridScene(
            width=8,
            height=3,
            elevation_cells={cell_id(0, 0): drop_ft, cell_id(1, 0): drop_ft},
        )
        brute = _hero(strength=18, character_level=5)
        target = PartyMemberSpec(
            entity_id="pc:target", name="Target", initiative=10, hp_current=200, hp_max=200,
            strength=1, dexterity=1, zone_id=cell_id(1, 0),
        )  # fmt: skip
        handle, live = await _open([brute, target], [_foe((7, 2))], scene)
        live.rng = _Dice(d20=15, pips=6)  # the Shove save (DC 15) fails
        events = await _do(handle, intent_type="shove", target_id="pc:target", shove_push=True)
        return _of(events, CreatureFell), [
            e for e in _of(events, DamageApplied) if e.source_id == "hazard:falling"
        ]

    fell25, damage25 = _run(fall(25))
    fell19, damage19 = _run(fall(19))
    assert [e.distance_ft for e in fell25] == [25]
    assert [(e.amount, e.damage_type) for e in damage25] == [(12, "bludgeoning")]
    assert [e.distance_ft for e in fell19] == [19]
    assert [e.amount for e in damage19] == [6]


# Creature Size
def test_a_large_creature_still_occupies_one_cell() -> None:
    """SRD 5.2.1 "Creature Size" (Creature Size and Space table): "Large | 10 by
    10 feet | 4 squares (2 by 2)". The engine does not model footprints: an Ogre
    (Large) holds a single cell, so it blocks one square and a creature may walk
    to the square beside it. Pins the documented gap; a footprint change must
    rewrite this test to expect 4 squares."""

    async def go() -> tuple[set[str], list[Any]]:
        handle, live = await _open(
            [_hero(base_speed=30)],
            [_foe((3, 0), monster_template_slug="ogre")],
            GridScene(width=8, height=4),
        )
        assert get_lib_loader().get_monster("ogre").creature_size == "large"  # type: ignore[union-attr]
        occupied = _occupied_cells(live, exclude=(HERO,))
        return occupied, await _do(handle, intent_type="move", target_zone_id=cell_id(2, 1))

    occupied, events = _run(go())
    assert occupied == {cell_id(3, 0)}  # one cell, not four
    assert not _of(events, MoveFailed)
    assert [e.to_zone for e in _of(events, ActorMoved)] == [cell_id(2, 1)]


# Size
def test_a_huge_creature_is_not_a_nine_square_space() -> None:
    """SRD 5.2.1 "Size" (Creature Size and Space table): "Huge | 15 by 15 feet | 9
    squares (3 by 3)". The engine gives a Huge creature one cell. Pins the
    documented gap."""
    assert get_lib_loader().get_monster("hill-giant").creature_size == "huge"  # type: ignore[union-attr]

    async def go() -> set[str]:
        _, live = await _open(
            [_hero()],
            [_foe((3, 0), monster_template_slug="hill-giant")],
            GridScene(width=8, height=4),
        )
        return _occupied_cells(live, exclude=(HERO,))

    assert _run(go()) == {cell_id(3, 0)}


# Difficult Terrain
def test_the_shortest_path_minimises_squares_not_cost() -> None:
    """SRD 5.2.1 "Difficult Terrain" (Playing on a Grid): "A square of Difficult
    Terrain costs 2 squares to enter." ``shortest_path`` is fewest squares, with
    ties broken by neighbour order, so it wades through three Difficult Terrain
    squares (35 feet) where a route of equal length along the clear row costs 20.
    ``reachable_cells`` prices the cheapest route. Documented in BACKLOG.md as
    cost-blind pathfinding."""
    g = _grid(width=5, height=3, difficult_terrain_cells=["1,0", "2,0", "3,0"])
    path = g.shortest_path("0,1", "4,1")
    assert path == ["0,1", "1,0", "2,0", "3,0", "4,1"]
    assert sum(g.edge_distance(a, b) or 0 for a, b in pairwise(path)) == 35
    assert g.reachable_cells("0,1", 100)["4,1"][0] == 20


# Playing on a Grid
def test_a_move_is_refused_when_only_the_cost_blind_route_exceeds_the_budget() -> None:
    """SRD 5.2.1 "Playing on a Grid": "To enter a square, you must have enough
    movement left to pay for entering." A hero with Speed 20 can reach 4,1 along
    the clear row for 20 feet, but the engine walks the fewest-squares route
    through Difficult Terrain (35 feet) and refuses the move. Pins the user
    visible side of the cost-blind pathfinding gap."""

    async def go() -> list[Any]:
        scene = GridScene(width=5, height=3, difficult_terrain_cells=["1,0", "2,0", "3,0"])
        handle, _ = await _open([_hero((0, 1), base_speed=20)], [_foe((4, 2))], scene)
        return await _do(handle, intent_type="move", target_zone_id=cell_id(4, 1))

    events = _run(go())
    assert [e.reason for e in _of(events, MoveFailed)] == ["insufficient_movement"]


# Thunderwave
def test_thunderwave_pushes_ten_feet_directly_away_from_the_caster() -> None:
    """SRD 5.2.1 Thunderwave: "On a failed save, a creature takes 2d8 Thunder
    damage and is pushed 10 feet away from you." A foe diagonally adjacent at 1,1
    ends 2 squares further along the diagonal, at 3,3."""

    async def go() -> list[Any]:
        handle, live = await _open(
            [_caster(["thunderwave"], {1: 2})], [_foe((1, 1))], GridScene(width=8, height=8)
        )
        live.rng = _Dice(d20=1, pips=1)  # the Constitution save fails
        return await _do(handle, **_cast("thunderwave", 1, target_id=FOE))

    events = _run(go())
    assert [(e.actor_id, e.to_zone, e.distance_ft) for e in _of(events, CombatantMoved)] == [
        (FOE, cell_id(3, 3), 10)
    ]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Several spell pushes are not wired",
)
def test_gust_of_wind_pushes_a_creature_that_fails_its_save_fifteen_feet() -> None:
    """SRD 5.2.1 Gust of Wind: "Each creature in the Line must succeed on a
    Strength saving throw or be pushed 15 feet away from you in a direction
    following the Line." A foe 3 squares east that fails ends 6 squares east. The
    engine resolves the save and applies no push."""

    async def go() -> list[Any]:
        handle, live = await _open(
            [_caster(["gust-of-wind"], {2: 2})], [_foe((3, 0))], GridScene(width=20, height=6)
        )
        live.rng = _Dice(d20=1, pips=1)  # the Strength save fails
        events = await _do(handle, **_cast("gust-of-wind", 2, target_id=FOE))
        assert [e.succeeded for e in _of(events, SaveRolled, FOE)] == [False]
        return events

    moved = _of(_run(go()), CombatantMoved)
    assert [(e.actor_id, e.to_zone) for e in moved] == [(FOE, cell_id(6, 0))]


# Darkvision
def test_darkvision_sees_darkness_only_within_its_range() -> None:
    """SRD 5.2.1 "Darkvision": "If you have Darkvision, you can see in Dim Light
    within a specified range as if it were Bright Light and in Darkness within
    that range as if it were Dim Light." A creature with Darkvision 20 sees a
    dark square 20 feet away and one with Darkvision 15 does not."""
    g = _grid(width=10, height=1, lighting={"4,0": "dark"})
    assert g.can_see("0,0", "4,0", CombatantSenses(darkvision=20)) is True
    assert g.can_see("0,0", "4,0", CombatantSenses(darkvision=15)) is False
    assert g.can_see("0,0", "4,0", CombatantSenses()) is False


# ═══════════════════════════ Spells ═════════════════════════════════════════


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: A slot below the spell's level is accepted",
)
def test_a_slot_below_the_spells_level_is_refused() -> None:
    """SRD 5.2.1 "Spell Slots": "When you cast a spell, you expend a slot of that
    spell's level or higher." Fireball is level 3, so a level 1 slot cannot cast
    it: the cast fails, no damage is dealt and the slot stays. The engine resolves
    the Fireball at full power and spends the level 1 slot."""

    async def go() -> tuple[list[Any], dict[int, int]]:
        handle, live = await _open(
            [_caster(["fireball"], {1: 2, 3: 1})], [_foe((8, 0))], GridScene(width=20, height=8)
        )
        events = await _do(handle, **_cast("fireball", 1, target_id=FOE))
        return events, dict(live.spell_slots_by_entity[HERO])

    events, slots = _run(go())
    assert _of(events, CastFailed)
    assert not _of(events, SpellCast)
    assert not _of(events, DamageApplied)
    assert slots == {1: 2, 3: 1}


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: One spell with a slot per turn is not enforced",
)
def test_a_second_spell_with_a_slot_in_the_same_turn_is_refused() -> None:
    """SRD 5.2.1 "One Spell with a Spell Slot per Turn": "On a turn, you can
    expend only one spell slot to cast a spell. This rule means you can't, for
    example, cast a spell with a spell slot using the Magic action and another one
    using a Bonus Action on the same turn." Shield of Faith (Bonus Action) then
    Magic Missile (Magic action): the second cast is refused and only one slot is
    spent. The engine resolves both."""

    async def go() -> tuple[list[Any], dict[int, int]]:
        handle, live = await _open(
            [_caster(["shield-of-faith", "magic-missile"], {1: 3})], [_foe((4, 0))]
        )
        await _do(handle, **_cast("shield-of-faith", 1, target_id=HERO))
        second = await _do(handle, **_cast("magic-missile", 1, target_id=FOE))
        return second, dict(live.spell_slots_by_entity[HERO])

    second, slots = _run(go())
    assert _of(second, CastFailed)
    assert not _of(second, DamageApplied)
    assert slots == {1: 2}


# Saving Throws, Attack Rolls, Proficiency Bonus table
_CASTERS = [
    # class, level, ability, score, Proficiency Bonus, ability modifier
    ("wizard", 5, "intelligence", 16, 3, 3),
    ("wizard", 9, "intelligence", 20, 4, 5),
    ("wizard", 17, "intelligence", 20, 6, 5),
    ("cleric", 5, "wisdom", 18, 3, 4),
    ("sorcerer", 1, "charisma", 18, 2, 4),
    ("warlock", 17, "charisma", 14, 6, 2),
]


async def _spell_events(spell: str, level: int | None, **caster: Any) -> list[Any]:
    handle, _ = await _open(
        [_caster([spell], {1: 4, 2: 3, 3: 3}, **caster)],
        [_foe((2, 0), ac=30)],
        GridScene(width=10, height=4),
    )
    return await _do(handle, **_cast(spell, level, target_id=FOE))


@pytest.mark.parametrize(("cls", "level", "ability", "score", "pb", "mod"), _CASTERS)
def test_spell_attack_modifier_is_the_ability_modifier_plus_proficiency(
    cls: str, level: int, ability: str, score: int, pb: int, mod: int
) -> None:
    """SRD 5.2.1 "Attack Rolls" (Spells, Proficiency Bonus table): "Spell attack
    modifier = your spellcasting ability modifier + your Proficiency Bonus." The
    Proficiency Bonus is 2 + (level - 1) // 4."""
    assert pb == 2 + (level - 1) // 4
    events = _run(
        _spell_events("fire-bolt", None, class_slug=cls, character_level=level, **{ability: score})
    )
    (rolled,) = _of(events, AttackRolled)
    assert rolled.modifier == pb + mod


@pytest.mark.parametrize(("cls", "level", "ability", "score", "pb", "mod"), _CASTERS)
def test_spell_save_dc_is_8_plus_the_ability_modifier_plus_proficiency(
    cls: str, level: int, ability: str, score: int, pb: int, mod: int
) -> None:
    """SRD 5.2.1 "Saving Throws" (Spells, Proficiency Bonus table): "Spell save DC
    = 8 + spellcasting ability modifier + Proficiency Bonus." Across classes and
    levels: Wizard 5 INT 16 is DC 14, Wizard 9 INT 20 is 17, Wizard 17 INT 20 is
    19, Cleric 5 WIS 18 is 15, Sorcerer 1 CHA 18 is 14, Warlock 17 CHA 14 is 16."""
    events = _run(
        _spell_events("burning-hands", 1, class_slug=cls, character_level=level, **{ability: score})
    )
    (save,) = _of(events, SaveRolled)
    assert save.dc == 8 + pb + mod


def test_a_pinned_attack_bonus_replaces_the_spell_attack_modifier_deliberately() -> None:
    """SRD 5.2.1 "Attack Rolls": "Spell attack modifier = your spellcasting
    ability modifier + your Proficiency Bonus." The engine lets a host pin the
    attack total with ``attack_bonus`` (a flat override, by design): a Wizard 5
    with INT 16 rolls +6 derived and +9 pinned, while the save DC stays 14 either
    way."""

    async def go() -> tuple[int, int, int]:
        derived = await _spell_events("fire-bolt", None, character_level=5)
        pinned = await _spell_events("fire-bolt", None, character_level=5, attack_bonus=9)
        dc = await _spell_events("burning-hands", 1, character_level=5, attack_bonus=9)
        (a,), (b,), (s,) = (
            _of(derived, AttackRolled),
            _of(pinned, AttackRolled),
            _of(dc, SaveRolled),
        )
        assert (a.modifier, b.modifier) != (None, None)
        return a.modifier or 0, b.modifier or 0, s.dc

    assert _run(go()) == (6, 9, 14)


# Duration
async def _to_hero(handle: CombatHandle, live: _LiveCombat) -> list[Any]:
    """Run every other turn until the hero's next turn starts."""
    events: list[Any] = []
    while live.current_actor_id != HERO:
        await advance_monster_turn(handle)
        events += drain_pending_events(handle)
    return events


def test_spirit_guardians_makes_a_creature_save_only_once_per_turn() -> None:
    """SRD 5.2.1 "Spirit Guardians", Duration: Concentration, up to 10 minutes
    (Spell entries: Weird, Acid Arrow, Spirit Guardians, Hunter's Mark, Hex,
    Haste, Delayed Blast Fireball, Stinking Cloud): "whenever a creature enters
    the Emanation or ends its turn there, the creature must make a Wisdom saving
    throw ... A creature makes this save only once per turn." A foe that ends its
    turn in the Emanation saves once in that turn, and once more in the next
    round: two saves over two rounds, never two in a turn."""

    async def go() -> tuple[list[Any], list[Any], list[Any]]:
        spare = _foe((3, 0), base_speed=30, entity_id=FOE)
        handle, live = await _open(
            [_caster(["spirit-guardians"], {3: 2}, class_slug="cleric", wisdom=16)], [spare]
        )
        cast = await _do(handle, **_cast("spirit-guardians", 3))
        first = await _to_hero(handle, live)
        await _do(handle, intent_type="pass")
        return cast, first, await _to_hero(handle, live)

    cast, first, second = _run(go())
    assert not _of(cast, SaveRolled, FOE)  # nothing at the cast
    assert len(_of(first, SaveRolled, FOE)) == 1
    assert len(_of(second, SaveRolled, FOE)) == 1  # a fresh save in the next turn


def test_hunters_mark_adds_1d6_force_to_a_hit_and_the_cast_deals_nothing() -> None:
    """SRD 5.2.1 "Hunter's Mark", Duration: Concentration, up to 1 hour (Spell
    entries: Weird, Acid Arrow, Spirit Guardians, Hunter's Mark, Hex, Haste,
    Delayed Blast Fireball, Stinking Cloud): "Until the spell ends, you deal an
    extra 1d6 Force damage to the target whenever you hit it with an attack
    roll." The cast deals no damage; a hit on a later action adds 1d6 Force."""

    async def go() -> tuple[list[Any], list[Any]]:
        hunter = _caster(
            ["hunters-mark"], {1: 2}, class_slug="ranger", strength=20, equipment=("longsword",),
            zone_id=cell_id(3, 0),
        )  # fmt: skip
        handle, live = await _open([hunter], [_foe((4, 0), ac=5)])
        cast = await _do(handle, **_cast("hunters-mark", 1, target_id=FOE))
        live.rng = _Dice(d20=15, pips=3)  # a hit, every damage die 3
        swing = await _do(handle, intent_type="attack", target_id=FOE, weapon_id="longsword")
        return cast, swing

    cast, swing = _run(go())
    assert not _of(cast, DamageApplied, FOE)
    force = [e for e in _of(swing, DamageApplied, FOE) if e.damage_type == "force"]
    assert [e.amount for e in force] == [3]  # one d6, forced to 3


# Dispel Magic
async def _dispel_scenario() -> tuple[list[Any], list[Any], dict[int, int]]:
    handle, live = await _open(
        [_caster(["hold-person", "dispel-magic"], {2: 2, 3: 2})], [_foe((4, 0))]
    )
    hold = await _do(handle, **_cast("hold-person", 2, target_id=FOE))
    await _to_hero(handle, live)
    dispel = await _do(handle, **_cast("dispel-magic", 3, target_id=FOE))
    return hold, dispel, dict(live.spell_slots_by_entity[HERO])


def test_dispel_magic_spends_its_slot_and_ends_nothing_today() -> None:
    """SRD 5.2.1 "Dispel Magic": "Choose one creature, object, or magical effect
    within range. Any ongoing spell of level 3 or lower on the target ends." The
    engine casts the spell as a narrative no-op: the level 3 slot is spent, the
    cast is recorded, and the Hold Person (level 2) on the target stays. Pins the
    no-op until Dispel Magic is modelled (the capability matrix tripwire)."""
    hold, dispel, slots = _run(_dispel_scenario())
    assert [e.effect.id for e in _of(hold, EffectApplied)] == ["effect:paralyzed"]
    assert [e.spell_id for e in _of(dispel, SpellCast)] == ["dispel-magic"]
    assert slots == {2: 1, 3: 1}
    assert not _of(dispel, EffectExpired)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Dispel Magic spends the slot and ends nothing",
)
def test_dispel_magic_ends_an_ongoing_spell_of_level_3_or_lower() -> None:
    """SRD 5.2.1 "Dispel Magic": "Any ongoing spell of level 3 or lower on the
    target ends." Hold Person (level 2) on the foe ends when a Wizard casts Dispel
    Magic at it. The engine ends nothing."""
    hold, dispel, _ = _run(_dispel_scenario())
    held = [e.effect.id for e in _of(hold, EffectApplied)]
    assert held == ["effect:paralyzed"]
    assert [e.effect_id for e in _of(dispel, EffectExpired, FOE)] == held


# Components
@pytest.mark.parametrize(
    ("slug", "consumed", "cost_gp"),
    [("find-familiar", True, 10), ("identify", False, 100), ("fireball", False, 0)],
)
def test_material_cost_and_consumption_follow_the_spell_text(
    slug: str, consumed: bool, cost_gp: int
) -> None:
    """SRD 5.2.1 "Components": Find Familiar needs "M (burning incense worth 10+ GP,
    which the spell consumes)", Identify "M (a pearl worth 100+ GP and an owlet
    feather)" without consuming it, and Fireball's "M (a ball of bat guano and
    sulfur)" has no cost. The component metadata carries all three facts."""
    spell = get_lib_loader().get_spell(slug)
    assert spell is not None
    components, material, material_consumed, material_cost = spell_component_metadata(spell)
    assert components == ("V", "S", "M")
    assert material is not None
    assert (material_consumed, material_cost) == (consumed, cost_gp)
