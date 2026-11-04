"""Pins the SRD 5.2.1 rules of the Monsters and Characters rows of
``docs/srd-5.2.1-rules-audit.md``: each test quotes the decisive SRD sentence
and passes today. Where the engine differs from the SRD, a strict ``xfail`` test
states the SRD outcome and names its ``BACKLOG.md`` entry; fixing the engine
turns it into an unexpected pass, which fails the suite until the marker is
removed.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from dnd5e_srd_data import MemoryAssetLoader, Provenance, ReviewState
from dnd5e_srd_data.loader import BundledAssetLoader
from dnd5e_srd_data.schema.common import CastActivity, CastSpellBlock, Movement, Senses
from dnd5e_srd_data.schema.monster import (
    AbilityScores,
    CreatureSize,
    CreatureType,
    Monster,
    MonsterAction,
    MonsterActionKind,
    SavingThrowProficiencies,
    SkillProficiencies,
)

from dnd5e_engine import (
    ActiveEffect,
    EncounterMemberSpec,
    GridScene,
    IntentRejectedError,
    PartyMemberSpec,
    PlayerIntent,
    advance_monster_turn,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.activities.monster_actions import expand_action_to_parts, rank_monster_actions
from dnd5e_engine.activities.scale import resolve_scale_value
from dnd5e_engine.build_spec import CharacterBuildSpec, DerivedSheet, derive_sheet
from dnd5e_engine.events import (
    ActorMoved,
    AreaTargeted,
    AttackRolled,
    CastFailed,
    DamageApplied,
    Death,
    HealingApplied,
    IntentSubmitted,
    LegendaryActionUsed,
    RechargeRolled,
    SaveRolled,
    SpellCast,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import (
    _emit,
    _get_live,
    _granted_die,
    _LiveCombat,
    _roll_recharges,
)
from dnd5e_engine.rest import recover_feature_uses
from dnd5e_engine.spatial import cell_id as cell
from dnd5e_engine.spellcasting import slots_for_caster_level
from tests.c20_support import act as c20_act
from tests.c20_support import combatant as c20_combatant
from tests.c20_support import events as c20_events
from tests.c20_support import foe as c20_foe
from tests.c20_support import pc as c20_pc
from tests.c20_support import start as c20_start
from tests.c20_support import wizard as c20_wizard

LOADER = BundledAssetLoader()


@pytest.fixture(autouse=True)
def _bundled_loader() -> Iterator[None]:
    set_lib_loader_for_tests(LOADER)
    yield
    set_lib_loader_for_tests(None)


# ── shared combat helpers ────────────────────────────────────────────────────


def _hero(*, initiative: int = 20, hp: int = 60, col: int = 0) -> PartyMemberSpec:
    return PartyMemberSpec(
        entity_id="char:hero",
        name="Hero",
        initiative=initiative,
        hp_current=hp,
        hp_max=hp,
        ac=10,
        zone_id=cell(col, 0),
    )


def _foe(
    slug: str,
    *,
    hp: int,
    hp_max: int | None = None,
    initiative: int = 5,
    col: int = 3,
    entity_id: str = "mon:foe",
) -> EncounterMemberSpec:
    return EncounterMemberSpec(
        entity_id=entity_id,
        entity_type="Monster",
        name=entity_id,
        initiative=initiative,
        hp_current=hp,
        hp_max=hp_max if hp_max is not None else hp,
        ac=12,
        zone_id=cell(col, 0),
        monster_template_slug=slug,
    )


async def _start_async(
    party: list[PartyMemberSpec], encounter: list[EncounterMemberSpec], seed: int
) -> tuple[Any, _LiveCombat]:
    started = await start_combat(
        session_id="srd-audit",
        party=party,
        encounter=encounter,
        grid_scene=GridScene(width=10, height=10),
        rng_seed=seed,
    )
    return started.handle, _get_live(started.handle)


def _start(
    party: list[PartyMemberSpec], encounter: list[EncounterMemberSpec], seed: int = 2
) -> tuple[Any, _LiveCombat]:
    return asyncio.run(_start_async(party, encounter, seed))


def _hero_passes(handle: Any) -> None:
    asyncio.run(
        submit_player_intent(handle, actor_id="char:hero", intent=PlayerIntent(intent_type="pass"))
    )


def _monster_turn(handle: Any, **kwargs: Any) -> None:
    asyncio.run(advance_monster_turn(handle, **kwargs))


def _events[T](live: _LiveCombat, kind: type[T]) -> list[T]:
    return [e for e in live.event_log if isinstance(e, kind)]


def _foe_heals(live: _LiveCombat) -> list[int]:
    return [e.amount for e in _events(live, HealingApplied) if e.target_id == "mon:foe"]


def _regeneration_turn(
    slug: str,
    *,
    hp: int,
    hp_max: int,
    damage: tuple[int, str] | None = None,
) -> _LiveCombat:
    """A regenerating foe (acting after the hero) optionally takes one hit
    before its turn; the hero passes and the foe's turn starts."""
    handle, live = _start([_hero()], [_foe(slug, hp=hp, hp_max=hp_max)])
    if damage is not None:
        amount, damage_type = damage
        _emit(
            live,
            DamageApplied(
                target_id="mon:foe",
                amount=amount,
                damage_type=damage_type,
                is_overkill=False,
            ),
        )
    _hero_passes(handle)
    if "mon:foe" not in live.dead_ids:
        _monster_turn(handle)
    return live


# ── Monsters: Running a Monster ──────────────────────────────────────────────


def test_a_live_recharge_ability_is_used_before_the_n_day_spell_and_multiattack() -> None:
    """SRD 5.2.1 "Running a Monster": "If the monster has a special ability that
    deals a lot of damage but has a limited number of uses, such as a recharging
    breath weapon or a spell it can cast only once per day, have it use that
    special ability as quickly and as often as possible." The adult red dragon
    ranks Fire Breath, then Spellcasting, then Multiattack."""
    dragon = LOADER.get_monster("adult-red-dragon")
    assert dragon is not None
    ranked = [a.slug for a in rank_monster_actions(dragon.actions, is_available=lambda _a: True)]
    assert ranked[:3] == ["fire-breath", "spellcasting", "multiattack"]
    spent = rank_monster_actions(dragon.actions, is_available=lambda a: a.slug != "fire-breath")
    assert [a.slug for a in spent][:2] == ["spellcasting", "multiattack"]


def test_multiattack_is_used_on_turns_without_a_more_powerful_ability() -> None:
    """SRD 5.2.1 "Running a Monster": "If the monster has Multiattack, have it
    use Multiattack on any of its turns in which it's not using one of its more
    powerful abilities." A Medusa with Petrifying Gaze spent ranks Multiattack
    first."""
    medusa = LOADER.get_monster("medusa")
    assert medusa is not None
    ranked = rank_monster_actions(
        medusa.actions, is_available=lambda a: a.slug != "petrifying-gaze"
    )
    assert ranked[0].slug == "multiattack"


def test_a_monster_takes_only_actions_on_its_stat_block() -> None:
    """SRD 5.2.1 "Running a Monster": the monster uses "special abilit[ies]",
    "Multiattack" and its Bonus Actions, Reactions and Legendary Actions "in its
    stat block". An ogre's turn is attack rolls with its one Greatclub, and no
    other intent."""
    handle, live = _start([_hero(initiative=1, col=1)], [_foe("ogre", hp=59, initiative=20, col=0)])
    _monster_turn(handle)
    intents = {e.intent_type for e in _events(live, IntentSubmitted) if e.actor_id == "mon:foe"}
    assert intents == {"attack"}
    ogre = LOADER.get_monster("ogre")
    assert ogre is not None
    assert [a.slug for a in ogre.actions] == ["greatclub", "javelin"]
    swings = [e for e in _events(live, AttackRolled) if e.attacker_id == "mon:foe"]
    assert len(swings) == 1
    damage = [e for e in _events(live, DamageApplied) if e.target_id == "char:hero" and e.source_id]
    assert {e.source_id for e in damage} <= {"greatclub", "javelin"}


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: A monster's Bonus Actions are ranked as its Action",
)
@pytest.mark.parametrize("slug", ["medusa", "basilisk"])
def test_a_bonus_action_ability_does_not_replace_the_monsters_action(slug: str) -> None:
    """SRD 5.2.1 "Running a Monster": "If the monster has Bonus Actions,
    Reactions, or Legendary Actions in its stat block, make sure it uses them as
    often as it can." The Petrifying Gaze is printed under Bonus Actions, so the
    turn also holds the Action (Medusa Multiattack, Basilisk Bite)."""
    handle, live = _start(
        [_hero(initiative=1, col=1)], [_foe(slug, hp=127, initiative=20, col=0)], seed=3
    )
    _monster_turn(handle)
    gaze = [e for e in _events(live, AreaTargeted) if e.source_id == "petrifying-gaze"]
    attacks = [e for e in _events(live, AttackRolled) if e.attacker_id == "mon:foe"]
    assert gaze
    assert attacks


# ── Monsters: Multiattack and Spellcasting ───────────────────────────────────


def test_a_pit_fiend_multiattack_makes_the_attacks_its_entry_lists() -> None:
    """SRD 5.2.1 "Multiattack": "This entry details the attacks a creature can
    make, as well as any additional abilities it can use, as part of the Attack
    action." The Pit Fiend's entry is one Bite, two Devilish Claw and one Fiery
    Mace attack."""
    fiend = LOADER.get_monster("pit-fiend")
    assert fiend is not None
    multiattack = next(a for a in fiend.actions if a.slug == "multiattack")
    parts = expand_action_to_parts(fiend, multiattack, target_distance_ft=5)
    assert [a.slug for a, _ in parts] == ["bite", "devilish-claw", "devilish-claw", "fiery-mace"]


def test_the_dragon_multiattack_replace_one_attack_rider_is_not_offered() -> None:
    """SRD 5.2.1 "Multiattack": the adult red dragon "makes three Rend attacks.
    It can replace one attack with a use of Spellcasting to cast Scorching Ray."
    The engine never substitutes, so the Multiattack stays three Rend attacks
    (BACKLOG: Multiattack replacement riders are ignored; the SRD rider is
    optional, so there is no required outcome to assert as an xfail)."""
    dragon = LOADER.get_monster("adult-red-dragon")
    assert dragon is not None
    multiattack = next(a for a in dragon.actions if a.slug == "multiattack")
    parts = expand_action_to_parts(dragon, multiattack, target_distance_ft=5)
    assert [a.slug for a, _ in parts] == ["rend", "rend", "rend"]


def _caster(spell_slug: str) -> Monster:
    """A stat-block spellcaster whose only action casts ``spell_slug`` at will
    with no printed level."""
    spell = LOADER.get_spell(spell_slug)
    assert spell is not None
    return Monster(
        slug="srd-caster",
        name="Caster",
        description="A test spellcaster.",
        creature_type=CreatureType.HUMANOID,
        creature_size=CreatureSize.MEDIUM,
        hp=20,
        hp_dice="3d8+3",
        ability_scores=AbilityScores(str=10, dex=10, con=10, int=16, wis=10, cha=10),
        movement=Movement(walk=30),
        senses=Senses(),
        cr=1.0,
        proficiency_bonus=2,
        saving_throws=SavingThrowProficiencies(),
        skills=SkillProficiencies(),
        provenance=Provenance(
            source="foundry",
            source_url="x",
            ingest_date=date(2026, 6, 3),
            ingest_version="v1",
            srd_version=frozenset({"5.1"}),
        ),
        review=ReviewState(),
        spellcasting_ability="int",
        actions=[
            MonsterAction(
                slug="spellcasting",
                name="Spellcasting",
                kind=MonsterActionKind.ACTION,
                description="The creature casts a spell.",
                activities=[
                    CastActivity(name="Spellcasting", spell=CastSpellBlock(uuid=spell.foundry_uuid))
                ],
            )
        ],
    )


def test_a_monster_casts_at_its_lowest_level_unless_the_stat_block_prints_one() -> None:
    """SRD 5.2.1 "Spellcasting": "Unless noted otherwise, a spell of level 1 or
    higher is always cast at its lowest possible level and can't be cast at a
    higher level." Scorching Ray with no printed level is cast at level 2; the
    Archmage's Lightning Bolt prints "level 7 version" and is cast at 7."""
    ray = LOADER.get_spell("scorching-ray")
    assert ray is not None
    set_lib_loader_for_tests(MemoryAssetLoader(monsters=[_caster("scorching-ray")], spells=[ray]))
    handle, live = _start(
        [_hero(initiative=1)], [_foe("srd-caster", hp=20, initiative=20, col=3)], seed=5
    )
    _monster_turn(handle)
    casts = [e for e in _events(live, SpellCast) if e.actor_id == "mon:foe"]
    assert [(c.spell_id, c.slot_level) for c in casts] == [("scorching-ray", 2)]

    set_lib_loader_for_tests(LOADER)
    handle, live = _start(
        [_hero(initiative=1)], [_foe("archmage", hp=99, initiative=20, col=3)], seed=5
    )
    _monster_turn(handle)
    printed = [e for e in _events(live, SpellCast) if e.spell_id == "lightning-bolt"]
    assert [c.slot_level for c in printed] == [7]


# ── Monsters: Flee ───────────────────────────────────────────────────────────


def _flee_turn(status: str | None) -> tuple[_LiveCombat, list[ActorMoved]]:
    """A 1-HP goblin (under its 10% flee threshold) acts first, 3 cells from
    the hero, optionally under one condition."""
    effects = (
        [
            ActiveEffect(
                id=f"effect:{status}",
                name=status,
                origin=f"test:{status}",
                target_id="mon:foe",
                statuses={status},
            )
        ]
        if status
        else []
    )

    async def _inner() -> _LiveCombat:
        started = await start_combat(
            session_id="srd-flee",
            party=[_hero(initiative=1)],
            encounter=[
                EncounterMemberSpec(
                    entity_id="mon:foe",
                    entity_type="Monster",
                    name="Goblin",
                    initiative=20,
                    hp_current=1,
                    hp_max=20,
                    ac=15,
                    monster_template_slug="goblin-warrior",
                    zone_id=cell(3, 0),
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=1,
            active_effects=effects,
        )
        await advance_monster_turn(started.handle)
        return _get_live(started.handle)

    live = asyncio.run(_inner())
    moves = [e for e in _events(live, ActorMoved) if e.actor_id == "mon:foe"]
    return live, moves


def test_an_unhindered_fleeing_monster_walks_its_whole_speed() -> None:
    """SRD 5.2.1 "Incapacitated, Opportunity Attacks, Speed": the retreat is
    engine design, but it spends the monster's own Speed: a goblin's 30 feet is
    six steps."""
    _, moves = _flee_turn(None)
    assert len(moves) == 6


@pytest.mark.parametrize("status", ["grappled", "restrained"])
def test_a_monster_with_speed_zero_does_not_flee_a_step(status: str) -> None:
    """SRD 5.2.1 "Incapacitated, Opportunity Attacks, Speed": "Speed 0. Your
    Speed is 0 and can't increase." (Grappled). A grappled or restrained monster
    below its flee threshold makes no move."""
    _, moves = _flee_turn(status)
    assert moves == []


def test_an_incapacitated_monster_does_not_flee_either() -> None:
    """SRD 5.2.1 "Incapacitated, Opportunity Attacks, Speed": Incapacitated
    "You can't take any action, Bonus Action, or Reaction." A Paralyzed (hence
    Incapacitated) monster below its threshold neither moves nor acts."""
    live, moves = _flee_turn("paralyzed")
    assert moves == []
    intents = [e.intent_type for e in _events(live, IntentSubmitted) if e.actor_id == "mon:foe"]
    assert intents == ["pass"]


# ── Monsters: Legendary Actions and lairs ────────────────────────────────────


def test_the_legendary_pool_runs_dry_after_three_windows() -> None:
    """SRD 5.2.1 "Legendary Actions": "The monster expends one use whenever it
    takes a Legendary Action", and the adult red dragon's "Legendary Action
    Uses: 3". Three windows after other creatures' turns spend 2, 1, 0 uses
    left; a fourth is refused."""
    heroes = [
        PartyMemberSpec(
            entity_id=f"char:h{i}",
            name=f"H{i}",
            initiative=30 - i,
            hp_current=80,
            hp_max=80,
            ac=18,
            zone_id=cell(i, 0),
        )
        for i in range(4)
    ]
    handle, live = _start(heroes, [_foe("adult-red-dragon", hp=256, initiative=5, col=6)], seed=4)
    remaining = []
    for i in range(3):
        asyncio.run(
            submit_player_intent(
                handle, actor_id=f"char:h{i}", intent=PlayerIntent(intent_type="pass")
            )
        )
        _monster_turn(handle, legendary=True)
        remaining.append(_events(live, LegendaryActionUsed)[-1].uses_remaining)
    assert remaining == [2, 1, 0]
    asyncio.run(
        submit_player_intent(handle, actor_id="char:h3", intent=PlayerIntent(intent_type="pass"))
    )
    with pytest.raises(IntentRejectedError) as refused:
        _monster_turn(handle, legendary=True)
    assert refused.value.reason == "no_legendary_action"


def test_every_legendary_bearer_has_three_uses() -> None:
    """SRD 5.2.1 "Legendary Actions": every SRD stat block prints "Legendary
    Action Uses: 3" (4 only in the lair), and the dataset carries 3 for all 30
    legendary monsters."""
    bearers = [m for slug in LOADER.list_slugs("monsters") if (m := LOADER.get_monster(slug))]
    bearers = [m for m in bearers if m.legendary_actions]
    assert len(bearers) == 30
    assert {m.legendary_action_uses for m in bearers} == {3}


def test_the_srd_stat_blocks_carry_no_lair_actions() -> None:
    """SRD 5.2.1 "Parts of a Stat Block": a stat block lists Traits, Actions,
    Bonus Actions, Reactions and Legendary Actions; the lair appears only as the
    "(4 in Lair)" and "4/Day, or 4/Day in Lair" figures of "Legendary Action
    Uses" and "Legendary Resistance", not as lair actions. No bundled monster
    has any (the Lair actions row stays unsupported)."""
    monsters = [LOADER.get_monster(slug) for slug in LOADER.list_slugs("monsters")]
    assert len(monsters) == 344
    assert [m.slug for m in monsters if m is not None and m.lair_actions] == []


# ── Monsters: Recharge ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("slug", "action", "notation", "roll", "regained"),
    [
        ("adult-red-dragon", "fire-breath", "5-6", 4, False),
        ("adult-red-dragon", "fire-breath", "5-6", 5, True),
        ("adult-red-dragon", "fire-breath", "5-6", 6, True),
        ("magma-mephit", "fire-breath", "6", 5, False),
        ("magma-mephit", "fire-breath", "6", 6, True),
        ("basilisk", "petrifying-gaze", "4-6", 3, False),
        ("basilisk", "petrifying-gaze", "4-6", 4, True),
    ],
)
def test_recharge_succeeds_exactly_when_the_die_is_in_the_range(
    monkeypatch: pytest.MonkeyPatch,
    slug: str,
    action: str,
    notation: str,
    roll: int,
    regained: bool,
) -> None:
    """SRD 5.2.1 "Limited Usage": "At the start of each of the monster's turns,
    roll 1d6. If the roll is within the number range given in the notation
    (represented by X-Y), the monster regains the use of that part." Recharge
    5-6 regains on a 5 or 6, Recharge 6 only on a 6, Recharge 4-6 on a 4 or more."""
    _, live = _start([_hero(initiative=1)], [_foe(slug, hp=100, initiative=20)])
    monster = LOADER.get_monster(slug)
    assert monster is not None
    spent = live.monster_action_uses_by_entity["mon:foe"][action]
    assert next(a for a in monster.actions if a.slug == action).recharge == notation
    spent.recharge_spent = True
    monkeypatch.setattr(live.rng, "randint", lambda _a, _b: roll)
    foe = next(c for c in live.initiative if c.entity_id == "mon:foe")
    _roll_recharges(live, foe, monster)
    (rolled,) = _events(live, RechargeRolled)
    assert (rolled.roll, rolled.threshold, rolled.succeeded) == (roll, notation, regained)
    assert spent.recharge_spent is not regained


def test_an_incapacitated_monster_still_rolls_its_recharge() -> None:
    """SRD 5.2.1 "Limited Usage": "At the start of each of the monster's turns,
    roll 1d6." Nothing exempts a monster that cannot act: a Stunned magma
    mephit with its Fire Breath spent still rolls."""
    stun = ActiveEffect(
        id="effect:stun",
        name="Stun",
        origin="test:stun",
        target_id="mon:foe",
        statuses={"stunned"},
    )

    async def _inner() -> _LiveCombat:
        started = await start_combat(
            session_id="srd-recharge",
            party=[_hero(initiative=1)],
            encounter=[_foe("magma-mephit", hp=18, initiative=20)],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=7,
            active_effects=[stun],
        )
        live = _get_live(started.handle)
        live.monster_action_uses_by_entity["mon:foe"]["fire-breath"].recharge_spent = True
        await advance_monster_turn(started.handle)
        return live

    live = asyncio.run(_inner())
    assert len(_events(live, RechargeRolled)) == 1
    assert [e.intent_type for e in _events(live, IntentSubmitted) if e.actor_id == "mon:foe"] == [
        "pass"
    ]


# ── Monsters: Regeneration ───────────────────────────────────────────────────


def test_the_oni_and_the_shield_guardian_regain_ten_hit_points() -> None:
    """SRD 5.2.1 "Oni, Shield Guardian": "Regeneration. The oni regains 10 Hit
    Points at the start of each of its turns if it has at least 1 Hit Point."
    The Shield Guardian's entry reads the same."""
    for slug in ("oni", "shield-guardian"):
        live = _regeneration_turn(slug, hp=20, hp_max=100)
        assert _foe_heals(live) == [10], slug


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Regeneration heals and suppresses wrongly",
)
def test_a_troll_regains_fifteen_hit_points() -> None:
    """SRD 5.2.1 "Troll": "Regeneration. The troll regains 15 Hit Points at the
    start of each of its turns." The shared trait heals a flat 10."""
    live = _regeneration_turn("troll", hp=40, hp_max=94)
    assert _foe_heals(live) == [15]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Regeneration heals and suppresses wrongly",
)
def test_a_troll_limb_regains_five_hit_points() -> None:
    """SRD 5.2.1 "Troll Limb": "Regeneration. The limb regains 5 Hit Points at
    the start of each of its turns." The shared trait heals a flat 10."""
    live = _regeneration_turn("troll-limb", hp=1, hp_max=30)
    assert _foe_heals(live) == [5]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Regeneration heals and suppresses wrongly",
)
def test_acid_or_fire_damage_suppresses_regeneration_on_the_next_turn() -> None:
    """SRD 5.2.1 "Troll": "If the troll takes Acid or Fire damage, this trait
    doesn't function on the troll's next turn." A troll hit by Fire before its
    turn does not heal on it."""
    live = _regeneration_turn("troll", hp=40, hp_max=94, damage=(1, "fire"))
    assert _foe_heals(live) == []


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Regeneration heals and suppresses wrongly",
)
def test_a_troll_at_zero_hit_points_does_not_die_if_it_regenerates() -> None:
    """SRD 5.2.1 "Troll": "The troll dies only if it starts its turn with 0 Hit
    Points and doesn't regenerate." A troll brought to 0 by bludgeoning damage is
    not dead before its turn and regenerates on it."""
    handle, live = _start([_hero()], [_foe("troll", hp=40, hp_max=94)])
    _emit(
        live,
        DamageApplied(target_id="mon:foe", amount=40, damage_type="bludgeoning", is_overkill=False),
    )
    assert _events(live, Death) == []
    _hero_passes(handle)
    _monster_turn(handle)
    assert "mon:foe" not in live.dead_ids
    assert _foe_heals(live) == [15]


# ── Monsters: traits and dataset categories ──────────────────────────────────


def test_legendary_resistance_pool_sizes_match_the_srd_figures_outside_the_lair() -> None:
    """SRD 5.2.1 "Monsters A-Z trait entries: Magic Resistance, Pack Tactics,
    Sunlight Sensitivity, Undead Fortitude, Swarm, Legendary Resistance,
    Regeneration, Flyby, Nimble Escape": "Legendary Resistance (3/Day, or 4/Day
    in Lair). If the monster fails a saving throw, it can choose to succeed
    instead." 17 monsters have 3 uses, 14 have 4 and the Tarrasque has 6."""
    pools = [
        m.legendary_resistance_uses
        for slug in LOADER.list_slugs("monsters")
        if (m := LOADER.get_monster(slug)) and m.legendary_resistance_uses
    ]
    assert sorted((n, pools.count(n)) for n in set(pools)) == [(3, 17), (4, 14), (6, 1)]


def test_the_dataset_ships_exactly_the_fifteen_srd_conditions() -> None:
    """SRD 5.2.1 "Rules Glossary condition entries": the glossary defines
    Blinded, Charmed, Deafened, Exhaustion, Frightened, Grappled, Incapacitated,
    Invisible, Paralyzed, Petrified, Poisoned, Prone, Restrained, Stunned and
    Unconscious, and the conditions category holds those fifteen."""
    assert set(LOADER.list_conditions()) == {
        "blinded",
        "charmed",
        "deafened",
        "exhaustion",
        "frightened",
        "grappled",
        "incapacitated",
        "invisible",
        "paralyzed",
        "petrified",
        "poisoned",
        "prone",
        "restrained",
        "stunned",
        "unconscious",
    }
    assert len(LOADER.list_conditions()) == 15


@pytest.mark.parametrize(
    ("slug", "fragments"),
    [
        (
            "undead-fortitude",
            ("Constitution saving throw (DC 5 plus the damage taken)", "Radiant", "Critical Hit"),
        ),
        ("pack-tactics", ("Advantage on an attack roll", "within 5 feet", "Incapacitated")),
        ("sunlight-sensitivity", ("Disadvantage on ability checks and attack rolls",)),
        (
            "magic-resistance",
            ("Advantage on saving throws against spells and other magical effects",),
        ),
        ("legendary-resistance", ("fails a saving throw", "can choose to succeed instead")),
        ("swarm", ("can't regain Hit Points or gain Temporary Hit Points",)),
    ],
)
def test_trait_descriptions_keep_the_decisive_srd_wording(
    slug: str, fragments: tuple[str, ...]
) -> None:
    """SRD 5.2.1 "Monsters A-Z trait wording": Undead Fortitude's "Constitution
    saving throw (DC 5 plus the damage taken)", Pack Tactics' "Advantage on an
    attack roll", Sunlight Sensitivity's "Disadvantage on ability checks and
    attack rolls", Magic Resistance's "Advantage on saving throws against spells
    and other magical effects", Legendary Resistance's "it can choose to succeed
    instead" and Swarm's "can't regain Hit Points or gain Temporary Hit Points"."""
    trait = LOADER.get_trait(slug)
    assert trait is not None
    text = trait.description.replace("’", "'")
    for fragment in fragments:
        assert fragment in text, (slug, fragment)


# ── Characters: classes, species, Hit Points, ability scores ─────────────────


def _sheet(**fields: Any) -> DerivedSheet:
    fields.setdefault("species_slug", "human")
    return derive_sheet(CharacterBuildSpec(**fields), loader=LOADER)


_SUBCLASSES = [
    ("barbarian", "berserker"),
    ("bard", "lore"),
    ("cleric", "life"),
    ("druid", "land"),
    ("fighter", "champion"),
    ("monk", "hand"),
    ("paladin", "devotion"),
    ("ranger", "hunter"),
    ("rogue", "thief"),
    ("sorcerer", "draconic"),
    ("warlock", "fiend"),
    ("wizard", "evoker"),
]


@pytest.mark.parametrize(("class_slug", "subclass_slug"), _SUBCLASSES)
def test_every_srd_class_takes_its_subclass_at_level_three(
    class_slug: str, subclass_slug: str
) -> None:
    """SRD 5.2.1 "Subclass": "You gain a [Class] subclass of your choice" at
    level 3 in all twelve classes (for example "Level 3: Barbarian Subclass").
    Naming a subclass at class level 2 is refused; at level 3 it builds."""
    with pytest.raises(ValueError, match=f"needs {class_slug} level 3"):
        _sheet(classes={class_slug: 2}, subclass_slug=subclass_slug)
    assert _sheet(classes={class_slug: 3}, subclass_slug=subclass_slug).proficiency_bonus == 2


@pytest.mark.parametrize(
    ("species", "speed", "darkvision"),
    [
        ("dragonborn", 30, 60),
        ("dwarf", 30, 120),
        ("elf-drow", 30, 120),
        ("elf-high", 30, 60),
        ("elf-wood", 35, 60),
        ("gnome-forest", 30, 60),
        ("gnome-rock", 30, 60),
        ("goliath", 35, None),
        ("halfling", 30, None),
        ("human", 30, None),
        ("orc", 30, 120),
        ("tiefling-abyssal", 30, 60),
        ("tiefling-chthonic", 30, 60),
        ("tiefling-infernal", 30, 60),
    ],
)
def test_species_speed_and_darkvision_match_the_srd(
    species: str, speed: int, darkvision: int | None
) -> None:
    """SRD 5.2.1 "Character Origins: Character Species": Dwarf "Darkvision.
    You have Darkvision with a range of 120 feet", Goliath "Speed: 35 feet", Wood
    Elf "Speed increases to 35 feet", Drow and Orc 120 feet, and 60 feet for the
    Dragonborn, High Elf, Gnomes and Tieflings."""
    sheet = _sheet(species_slug=species, classes={"fighter": 1})
    assert (sheet.base_speed, sheet.senses.darkvision) == (speed, darkvision)


_HIT_DIE = {
    "barbarian": 12,
    "fighter": 10,
    "paladin": 10,
    "ranger": 10,
    "bard": 8,
    "cleric": 8,
    "druid": 8,
    "monk": 8,
    "rogue": 8,
    "warlock": 8,
    "sorcerer": 6,
    "wizard": 6,
}


@pytest.mark.parametrize("level", [1, 2, 9, 20])
@pytest.mark.parametrize("class_slug", sorted(_HIT_DIE))
def test_hit_points_and_hit_dice_follow_the_class_hit_die(class_slug: str, level: int) -> None:
    """SRD 5.2.1 "Character Creation: Step 5": level 1 gives the Hit Die's
    maximum plus the Constitution modifier, and "Gaining a Level" adds the fixed
    value (Barbarian 7, Fighter, Paladin or Ranger 6, Bard, Cleric, Druid, Monk,
    Rogue or Warlock 5, Sorcerer or Wizard 4) plus the Constitution modifier for
    each later level. Constitution 14 is +2."""
    die = _HIT_DIE[class_slug]
    sheet = _sheet(classes={class_slug: level}, ability_scores={"constitution": 14})
    assert sheet.hp_max == (die + 2) + (level - 1) * (die // 2 + 1 + 2)
    assert sheet.hit_dice == {die: level}


def test_each_level_gains_at_least_one_hit_point() -> None:
    """SRD 5.2.1 "Gaining a Level": "add the total (minimum of 1) to your Hit
    Point maximum". A Wizard 2 with Constitution 3 (-4) has 2 at level 1 and
    gains 1, not 0, at level 2."""
    assert _sheet(classes={"wizard": 2}, ability_scores={"constitution": 3}).hp_max == 3


@pytest.mark.parametrize(
    ("class_slug", "saves"),
    [
        ("barbarian", {"str", "con"}),
        ("bard", {"dex", "cha"}),
        ("cleric", {"wis", "cha"}),
        ("druid", {"int", "wis"}),
        ("fighter", {"str", "con"}),
        ("monk", {"str", "dex"}),
        ("paladin", {"wis", "cha"}),
        ("ranger", {"str", "dex"}),
        ("rogue", {"dex", "int"}),
        ("sorcerer", {"con", "cha"}),
        ("warlock", {"wis", "cha"}),
        ("wizard", {"int", "wis"}),
    ],
)
def test_each_class_grants_its_two_saving_throw_proficiencies(
    class_slug: str, saves: set[str]
) -> None:
    """SRD 5.2.1 "Traits": each class's Core Traits table lists its Saving
    Throw Proficiencies, for example Strength and Constitution for the Barbarian
    and Dexterity and Charisma for the Bard."""
    assert set(_sheet(classes={class_slug: 1}).save_proficiencies) == saves


@pytest.mark.parametrize(
    ("level", "bonus"),
    [(n, 2 + (n - 1) // 4) for n in range(1, 21)],
)
def test_proficiency_bonus_follows_the_advancement_table(level: int, bonus: int) -> None:
    """SRD 5.2.1 "Level Advancement: Character Advancement table": Proficiency
    Bonus +2 at levels 1 to 4, +3 at 5 to 8, +4 at 9 to 12, +5 at 13 to 16 and +6
    at 17 to 20."""
    assert _sheet(classes={"fighter": level}).proficiency_bonus == bonus
    assert bonus == {1: 2, 2: 3, 3: 4, 4: 5, 5: 6}[(level - 1) // 4 + 1]


@pytest.mark.parametrize(
    ("tokens", "scores", "result"),
    [
        (("asi:fighter:4:strength+2",), {"strength": 18}, (20,)),
        (("asi:fighter:4:strength+1,dexterity+1",), {"strength": 19, "dexterity": 10}, (20, 11)),
    ],
)
def test_ability_score_improvement_raises_one_by_two_or_two_by_one(
    tokens: tuple[str, ...], scores: dict[str, int], result: tuple[int, ...]
) -> None:
    """SRD 5.2.1 "Feats: Ability Score Improvement": "Increase one ability score
    of your choice by 2, or increase two ability scores of your choice by 1. This
    feat can't increase an ability score above 20."
    """
    sheet = _sheet(classes={"fighter": 4}, ability_scores=scores, selected_choices=tokens)
    values = [getattr(sheet.ability_scores, name) for name in scores]
    assert tuple(values) == result


@pytest.mark.parametrize(
    "token",
    [
        "asi:fighter:4:strength+3",
        "asi:fighter:4:strength+1",
        "asi:fighter:4:strength+1,dexterity+1,constitution+1",
        "asi:fighter:4:strength+2",
    ],
)
def test_ability_score_improvement_refuses_other_shapes_and_a_score_above_twenty(
    token: str,
) -> None:
    """SRD 5.2.1 "Feats: Ability Score Improvement": exactly 2 points, never
    "above 20". +3, +1 alone, three +1s and +2 on a score of 19 are all refused."""
    with pytest.raises(ValueError, match=r"1-2|exactly|20"):
        _sheet(
            classes={"fighter": 4},
            ability_scores={"strength": 19},
            selected_choices=(token,),
        )


def test_point_buy_spends_exactly_twenty_seven_points() -> None:
    """SRD 5.2.1 "Character Creation: Step 3: Ability Scores": "You have 27
    points to spend on your ability scores." Fifteen, fifteen, fifteen and three
    eights spend the 27; one more point is over budget."""
    spent_27 = {
        "strength": 15,
        "dexterity": 15,
        "constitution": 15,
        "intelligence": 8,
        "wisdom": 8,
        "charisma": 8,
    }
    sheet = _sheet(
        classes={"fighter": 1}, ability_scores=spent_27, ability_score_method="point_buy"
    )
    assert sheet.ability_scores.strength == 15
    with pytest.raises(ValueError, match="27"):
        _sheet(
            classes={"fighter": 1},
            ability_scores=spent_27 | {"intelligence": 9},
            ability_score_method="point_buy",
        )


def test_expertise_cannot_be_taken_twice_in_one_skill() -> None:
    """SRD 5.2.1 "Expertise": "You can't have Expertise in the same skill
    proficiency more than once." A repeated expertise token is refused."""
    with pytest.raises(ValueError, match="repeat"):
        _sheet(
            classes={"rogue": 1},
            selected_choices=("skill:stealth", "expertise:stealth", "expertise:stealth"),
        )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Dragonborn Draconic Ancestry is not a choice",
)
def test_a_dragonborn_has_resistance_to_its_ancestrys_damage_type() -> None:
    """SRD 5.2.1 "Character Origins: Character Species": the Dragonborn's
    "Damage Resistance. You have Resistance to the damage type determined by your
    Draconic Ancestry trait." The sheet carries one such resistance (acid, cold,
    fire, lightning or poison); today it carries none."""
    sheet = _sheet(species_slug="dragonborn", classes={"fighter": 1})
    assert len(sheet.damage_resistances) == 1
    assert set(sheet.damage_resistances) <= {"acid", "cold", "fire", "lightning", "poison"}


# ── Characters: Background ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("slug", "abilities", "skills", "feat"),
    [
        ("acolyte", {"int", "wis", "cha"}, {"ins", "rel"}, "magic-initiate"),
        ("criminal", {"dex", "con", "int"}, {"slt", "ste"}, "alert"),
        ("sage", {"con", "int", "wis"}, {"arc", "his"}, "magic-initiate"),
        ("soldier", {"str", "dex", "con"}, {"ath", "itm"}, "savage-attacker"),
    ],
)
def test_each_srd_background_lists_its_abilities_skills_and_origin_feat(
    slug: str, abilities: set[str], skills: set[str], feat: str
) -> None:
    """SRD 5.2.1 "Character Origins: Character Backgrounds": Acolyte "Ability
    Scores: Intelligence, Wisdom, Charisma; Feat: Magic Initiate (Cleric); Skill
    Proficiencies: Insight and Religion", and the Criminal, Sage and Soldier
    entries likewise. A background lists three scores, raised by 3 points with
    no more than 2 on one."""
    background = LOADER.get_background(slug)
    assert background is not None
    assert set(background.ability_options.options) == abilities
    assert (background.ability_options.points, background.ability_options.cap) == (3, 2)
    assert set(background.skill_proficiencies) == skills
    assert background.starting_feat_slug == feat


@pytest.mark.parametrize(
    "token",
    ["background:strength+2,constitution+1", "background:strength+1,dexterity+1,constitution+1"],
)
def test_a_background_raises_one_score_by_two_and_one_by_one_or_three_by_one(token: str) -> None:
    """SRD 5.2.1 "Character Creation: Step 2: Character Origin": "Increase one
    by 2 and another one by 1, or increase all three by 1." Both shapes build."""
    sheet = _sheet(
        classes={"fighter": 1},
        background_slug="soldier",
        ability_scores={"strength": 10, "dexterity": 10, "constitution": 10},
        selected_choices=(token,),
    )
    assert (
        sum(
            getattr(sheet.ability_scores, name) - 10
            for name in ("strength", "dexterity", "constitution")
        )
        == 3
    )


@pytest.mark.parametrize(
    "token",
    [
        "background:strength+3",
        "background:strength+2",
        "background:strength+1,dexterity+1",
        "background:strength+2,dexterity+2",
        "background:intelligence+2,strength+1",
    ],
)
def test_a_background_refuses_any_other_shape_or_an_ability_it_does_not_list(token: str) -> None:
    """SRD 5.2.1 "Character Creation: Step 2: Character Origin": the increase is
    "one by 2 and another one by 1, or ... all three by 1" among the three scores
    the background lists. A +3, a lone +2, two +1s, 2 and 2, or a score the
    Soldier does not list (Intelligence) is refused."""
    with pytest.raises(ValueError, match=r"."):
        _sheet(classes={"fighter": 1}, background_slug="soldier", selected_choices=(token,))


def test_a_background_cannot_raise_a_score_above_twenty() -> None:
    """SRD 5.2.1 "Character Origins: Character Backgrounds": "None of these
    increases can raise a score above 20." Strength 20 with +2 is refused."""
    with pytest.raises(ValueError, match="20"):
        _sheet(
            classes={"fighter": 1},
            background_slug="soldier",
            ability_scores={"strength": 20},
            selected_choices=("background:strength+2,constitution+1",),
        )


# ── Characters: Rage ─────────────────────────────────────────────────────────


def _hero_combat(
    *members: PartyMemberSpec, seed: int, encounter: list[EncounterMemberSpec] | None = None
) -> tuple[Any, _LiveCombat]:
    return c20_start(list(members), seed=seed, encounter=encounter)


def _first_hit(live: _LiveCombat) -> int:
    return c20_events(live, DamageApplied)[0].amount


@pytest.mark.parametrize(
    ("level", "bonus"),
    [(1, 2), (8, 2), (9, 3), (15, 3), (16, 4), (20, 4)],
)
def test_rage_damage_rises_with_barbarian_level(level: int, bonus: int) -> None:
    """SRD 5.2.1 "Barbarian Features table": the Rage Damage column is +2 from
    level 1, +3 from level 9 and +4 from level 16. A raging Barbarian's Strength
    Mace swing deals that much more than the same swing (same seed) without it."""
    amounts = []
    for raging in (False, True):
        handle, live = _hero_combat(
            c20_pc(class_slug="barbarian", character_level=level, strength=16, attack_bonus=20),
            seed=3,
        )
        if raging:
            c20_act(handle, "char:hero", intent_type="use_feature", feature_id="rage")
        c20_act(handle, "char:hero", intent_type="attack", weapon_id="mace", target_id="mon:foe")
        amounts.append(_first_hit(live))
    assert amounts[1] - amounts[0] == bonus


@pytest.mark.parametrize(
    ("level", "rages"),
    [(1, 2), (2, 2), (3, 3), (5, 3), (6, 4), (11, 4), (12, 5), (16, 5), (17, 6), (20, 6)],
)
def test_the_number_of_rages_follows_the_barbarian_features_table(level: int, rages: int) -> None:
    """SRD 5.2.1 "Barbarian: Level 1: Rage": "You can enter your Rage the number
    of times shown for your Barbarian level in the Rages column of the Barbarian
    Features table": 2 at levels 1 to 2, 3 at 3, 4 at 6, 5 at 12 and 6 at 17.
    With one use left the Rage starts; with none left it is refused."""
    for spent, refused in ((rages - 1, False), (rages, True)):
        handle, live = _hero_combat(
            c20_pc(
                class_slug="barbarian",
                character_level=level,
                custom_counters={"feature_use:rage": {"spent": spent}},
            ),
            seed=1,
        )
        c20_act(handle, "char:hero", intent_type="use_feature", feature_id="rage")
        assert [e.reason for e in c20_events(live, CastFailed)] == (
            ["no_uses_remaining"] if refused else []
        )


def test_rage_resistance_covers_bludgeoning_piercing_and_slashing() -> None:
    """SRD 5.2.1 "Barbarian: Level 1: Rage": "Damage Resistance. You have
    Resistance to Bludgeoning, Piercing, and Slashing damage." The active Rage
    effect carries all three."""
    handle, live = _hero_combat(c20_pc(class_slug="barbarian"), seed=1)
    c20_act(handle, "char:hero", intent_type="use_feature", feature_id="rage")
    modes = [
        change.value
        for effect in live.active_effects["char:hero"]
        if effect.id == "effect:rage"
        for change in effect.changes
        if "dr" in change.key
    ]
    joined = " ".join(str(v) for v in modes)
    for damage_type in ("bludgeoning", "piercing", "slashing"):
        assert damage_type in joined


def test_persistent_rage_is_a_level_fifteen_feature() -> None:
    """SRD 5.2.1 "Barbarian: Level 15: Persistent Rage": the feature arrives at
    Barbarian level 15, not before."""
    assert "persistent-rage" not in _sheet(classes={"barbarian": 14}).features
    assert "persistent-rage" in _sheet(classes={"barbarian": 15}).features


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Rage Damage and Strength Advantage are applied wrongly",
)
def test_rage_damage_needs_an_attack_using_strength() -> None:
    """SRD 5.2.1 "Barbarian: Level 1: Rage": "Rage Damage. When you make an
    attack using Strength ... you gain a bonus to the damage". A Rapier attack
    made with Dexterity (Strength 8, Dexterity 18) gets no Rage Damage."""
    amounts = []
    for raging in (False, True):
        handle, live = _hero_combat(
            c20_pc(
                class_slug="barbarian",
                character_level=5,
                strength=8,
                dexterity=18,
                attack_bonus=15,
                equipment=("rapier",),
            ),
            seed=3,
        )
        if raging:
            c20_act(handle, "char:hero", intent_type="use_feature", feature_id="rage")
        c20_act(handle, "char:hero", intent_type="attack", weapon_id="rapier", target_id="mon:foe")
        amounts.append(_first_hit(live))
    assert amounts[0] == amounts[1]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Rage Damage and Strength Advantage are applied wrongly",
)
def test_a_raging_barbarian_has_advantage_on_strength_saving_throws() -> None:
    """SRD 5.2.1 "Barbarian: Level 1: Rage": "Strength Advantage. You have
    Advantage on Strength checks and Strength saving throws." A raging Barbarian
    shoved by a Wizard rolls its Strength save with Advantage."""
    advantage = []
    for raging in (False, True):
        handle, live = _hero_combat(
            c20_pc(class_slug="barbarian", strength=20), c20_wizard(), seed=3
        )
        if raging:
            c20_act(handle, "char:hero", intent_type="use_feature", feature_id="rage")
        c20_act(handle, "char:hero", intent_type="pass")
        c20_act(handle, "char:wiz", intent_type="shove", target_id="char:hero")
        (save,) = [e for e in c20_events(live, SaveRolled) if e.target_id == "char:hero"]
        assert save.ability == "str"
        advantage.append(save.advantage)
    assert advantage == ["normal", "advantage"]


# ── Characters: feature uses and recovery ────────────────────────────────────


def _uses_until_refused(class_slug: str, level: int, feature_id: str, **intent: Any) -> int:
    """Use ``feature_id`` again and again, refreshing the Action and Bonus
    Action each time, and count the uses that succeed before the first
    ``no_uses_remaining`` refusal."""
    handle, live = _hero_combat(
        c20_pc(class_slug=class_slug, character_level=level, hp_current=100, hp_max=100), seed=1
    )
    succeeded = 0
    for _ in range(9):
        hero = c20_combatant(live)
        hero.bonus_action_available = True
        hero.action_available = True
        hero.action_surge_used_this_turn = False
        refused_before = len(c20_events(live, CastFailed))
        c20_act(handle, "char:hero", intent_type="use_feature", feature_id=feature_id, **intent)
        if len(c20_events(live, CastFailed)) > refused_before:
            break
        succeeded += 1
    return succeeded


@pytest.mark.parametrize(("level", "uses"), [(1, 2), (3, 2), (4, 3), (9, 3), (10, 4), (20, 4)])
def test_second_wind_uses_follow_the_fighter_features_table(level: int, uses: int) -> None:
    """SRD 5.2.1 "Fighter: Level 1: Second Wind; Level 2: Action Surge; Level 5:
    Extra Attack": "You can use this feature twice ... When you reach certain
    Fighter levels, you gain more uses of this feature, as shown in the Second
    Wind column of the Fighter Features table": 2, then 3 at level 4 and 4 at
    level 10."""
    assert _uses_until_refused("fighter", level, "second-wind") == uses


@pytest.mark.parametrize(("level", "uses"), [(2, 1), (16, 1), (17, 2)])
def test_action_surge_uses_follow_the_fighter_level(level: int, uses: int) -> None:
    """SRD 5.2.1 "Fighter: Level 1: Second Wind; Level 2: Action Surge; Level 5:
    Extra Attack": "Starting at level 17, you can use it twice before a rest but
    only once on a turn." One use from level 2, two from level 17."""
    assert _uses_until_refused("fighter", level, "action-surge") == uses


def test_second_wind_heals_one_d10_plus_the_fighter_level() -> None:
    """SRD 5.2.1 "Fighter: Level 1: Second Wind; Level 2: Action Surge; Level 5:
    Extra Attack": "As a Bonus Action, you can use it to regain Hit Points equal
    to 1d10 plus your Fighter level." A Fighter 5 regains 6 to 15."""
    handle, live = _hero_combat(
        c20_pc(class_slug="fighter", character_level=5, hp_current=10, hp_max=100), seed=1
    )
    c20_act(handle, "char:hero", intent_type="use_feature", feature_id="second-wind")
    (healed,) = c20_events(live, HealingApplied)
    assert 6 <= healed.amount <= 15


@pytest.mark.parametrize("slug", ["rage", "second-wind", "wild-shape"])
def test_a_short_rest_regains_one_use_and_a_long_rest_all(slug: str) -> None:
    """SRD 5.2.1 "Short Rest; Long Rest": "You regain one expended use when you
    finish a Short Rest, and you regain all expended uses when you finish a Long
    Rest" (Rage, Second Wind and Wild Shape alike). Three spent uses become two
    after a Short Rest and none after a Long Rest."""
    feature = LOADER.get_feature(slug)
    assert feature is not None
    assert feature.uses is not None
    recovery = {slug: feature.uses.recovery}
    spent = {f"feature_use:{slug}": {"spent": 3}}
    assert recover_feature_uses(spent, "sr", recovery) == {slug: 2}
    assert recover_feature_uses(spent, "lr", recovery) == {slug: 0}


_SHORT_OR_LONG_REST_FEATURES = [
    "action-surge",
    "adrenaline-rush",
    "monks-focus",
    "relentless-rage",
    "stroke-of-luck",
]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=("BACKLOG: A Long Rest does not recover features the dataset lists as Short Rest only"),
)
@pytest.mark.parametrize("slug", _SHORT_OR_LONG_REST_FEATURES)
def test_a_long_rest_also_recovers_a_feature_that_a_short_rest_recovers(slug: str) -> None:
    """SRD 5.2.1 "Short Rest; Long Rest": Action Surge, Adrenaline Rush, Monk's
    Focus, Relentless Rage and Stroke of Luck are regained on "a Short or Long
    Rest" ("Once you use this feature, you can't do so again until you finish a
    Short or Long Rest"). The dataset lists the Short Rest only, so a Long Rest
    leaves them spent."""
    feature = LOADER.get_feature(slug)
    assert feature is not None
    assert feature.uses is not None
    recovery = {slug: feature.uses.recovery}
    spent = {f"feature_use:{slug}": {"spent": 1}}
    assert recover_feature_uses(spent, "sr", recovery) == {slug: 0}
    assert recover_feature_uses(spent, "lr", recovery) == {slug: 0}


# ── Characters: Fighting Style feats ─────────────────────────────────────────


def test_archery_adds_two_to_ranged_attack_rolls() -> None:
    """SRD 5.2.1 "Feats: Fighting Style Feats": Archery: "You gain a +2 bonus to
    attack rolls you make with Ranged weapons." A Shortbow attack rolls 2 more
    with the style than without (same seed, same die)."""
    totals = []
    for style in (None, "archery"):
        handle, live = _hero_combat(
            c20_pc(dexterity=14, attack_bonus=5, fighting_style=style),
            seed=2,
            encounter=[c20_foe(zone_id=cell(3, 0))],
        )
        c20_act(
            handle, "char:hero", intent_type="attack", weapon_id="shortbow", target_id="mon:foe"
        )
        (attack,) = c20_events(live, AttackRolled)
        totals.append(attack.roll_total)
    assert totals[1] - totals[0] == 2


def test_two_weapon_fighting_adds_the_modifier_to_the_nick_extra_attack() -> None:
    """SRD 5.2.1 "Feats: Fighting Style Feats": Two-Weapon Fighting: "When you
    make an extra attack as a result of using a weapon that has the Light
    property, you can add your ability modifier to the damage of that attack if
    you aren't already adding it." The Dagger's Nick extra attack (part of the
    Attack action) gains Dexterity +4 with the style and nothing without."""
    damage = []
    for style in (None, "two-weapon-fighting"):
        handle, live = _hero_combat(
            c20_pc(
                dexterity=18,
                attack_bonus=6,
                equipment=("scimitar", "dagger"),
                fighting_style=style,
            ),
            seed=9,
            encounter=[c20_foe(zone_id=cell(0, 0))],
        )
        c20_act(
            handle, "char:hero", intent_type="attack", weapon_id="scimitar", target_id="mon:foe"
        )
        c20_act(handle, "char:hero", intent_type="attack", weapon_id="dagger", target_id="mon:foe")
        damage.append(c20_events(live, DamageApplied)[1].amount)
    assert damage[1] - damage[0] == 4


def test_the_light_extra_attack_keeps_a_negative_modifier() -> None:
    """SRD 5.2.1 "Weapons: Light property; Mastery Properties": the extra attack
    adds no ability modifier "unless that modifier is negative". With Strength
    and Dexterity 8 (-1) every die at its maximum, the Dagger's critical 2d4 is
    8 and the off-hand swing deals 7."""
    handle, live = _hero_combat(
        c20_pc(
            strength=8,
            dexterity=8,
            attack_bonus=30,
            equipment=("shortsword", "dagger"),
            class_slug="fighter",
        ),
        seed=1,
        encounter=[c20_foe(zone_id=cell(0, 0))],
    )
    live.rng = _MaxRandom()
    c20_act(handle, "char:hero", intent_type="attack", weapon_id="shortsword", target_id="mon:foe")
    c20_act(
        handle,
        "char:hero",
        intent_type="attack",
        weapon_id="dagger",
        target_id="mon:foe",
        use_bonus_action=True,
    )
    assert c20_events(live, DamageApplied)[1].amount == 2 * 4 - 1


class _MaxRandom(random.Random):
    """Every ``randint(a, b)`` returns ``b``: a natural 20 and maximum dice."""

    def randint(self, a: int, b: int) -> int:
        del a
        return b


# ── Characters: Martial Arts and Monk's Focus ────────────────────────────────


def _scale(identifier: str, key: str, level: int) -> int | str | None:
    return resolve_scale_value(identifier, key, level=level, loader=LOADER)


@pytest.mark.parametrize("level", range(1, 21))
def test_the_martial_arts_die_and_focus_points_follow_the_monk_features_table(level: int) -> None:
    """SRD 5.2.1 "Monk: Level 1: Martial Arts": "You can roll 1d6 in place of the
    normal damage of your Unarmed Strike or Monk weapons. This die changes as you
    gain Monk levels, as shown in the Martial Arts column of the Monk Features
    table" (d6, d8 from level 5, d10 from 11, d12 from 17); "Monk: Level 2: Monk's
    Focus": the Focus Points column equals the Monk level from level 2."""
    die = "d6" if level < 5 else "d8" if level < 11 else "d10" if level < 17 else "d12"
    assert _scale("monk", "die", level) == die
    assert _scale("monk", "focus", level) == (None if level == 1 else level)


def test_monks_focus_is_regained_on_a_short_rest() -> None:
    """SRD 5.2.1 "Short Rest; Long Rest": "When you expend a Focus Point, it is
    unavailable until you finish a Short or Long Rest, at the end of which you
    regain all your expended points." A Short Rest returns all three spent."""
    feature = LOADER.get_feature("monks-focus")
    assert feature is not None
    assert feature.uses is not None
    spent = {"feature_use:monks-focus": {"spent": 3}}
    assert recover_feature_uses(spent, "sr", {"monks-focus": feature.uses.recovery}) == {
        "monks-focus": 0
    }


_FLURRY_OF_BLOWS = "2ghJTBhilLrFn9xT"


def test_heightened_focus_adds_a_third_strike_to_flurry_of_blows() -> None:
    """SRD 5.2.1 "Monk: Level 10: Heightened Focus": "Flurry of Blows. You can
    make three Unarmed Strikes with it instead of two." A Monk 10 spending a
    Focus Point on Flurry owes three strikes, a Monk 9 two."""
    owed = []
    for level in (9, 10):
        handle, live = _hero_combat(c20_pc(class_slug="monk", character_level=level), seed=4)
        c20_act(
            handle,
            "char:hero",
            intent_type="use_feature",
            feature_id="monks-focus",
            activity_id=_FLURRY_OF_BLOWS,
        )
        owed.append(c20_combatant(live).flurry_strikes_remaining)
    assert owed == [2, 3]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Martial Arts ignores the off hand",
)
def test_martial_arts_needs_the_other_hand_to_hold_only_monk_weapons() -> None:
    """SRD 5.2.1 "Monk: Level 1: Martial Arts": "You gain the following benefits
    while you are unarmed or wielding only Monk weapons". A Monk 5 who also
    carries a Longsword (not a Monk weapon) gets no Martial Arts die: the Unarmed
    Strike deals 1 plus Strength, 2 with Strength 12."""
    handle, live = _hero_combat(
        c20_pc(
            class_slug="monk",
            character_level=5,
            strength=12,
            dexterity=16,
            attack_bonus=5,
            equipment=("longsword",),
        ),
        seed=4,
    )
    c20_act(
        handle, "char:hero", intent_type="attack", weapon_id="unarmed-strike", target_id="mon:foe"
    )
    assert _first_hit(live) == 2


# ── Characters: Bardic Inspiration ───────────────────────────────────────────


def _bard_table(seed: int, *, bard_level: int = 3) -> tuple[Any, _LiveCombat]:
    """A Bard (Charisma 16, initiative 20) at 0,0, an ally at 1,0 (initiative 15)
    and an AC 14 foe at 2,0."""
    return c20_start(
        [
            c20_pc("char:bard", class_slug="bard", character_level=bard_level, charisma=16),
            c20_pc("char:ally", initiative=15, attack_bonus=3, zone_id=cell(1, 0)),
        ],
        seed=seed,
        encounter=[c20_foe(ac=14, zone_id=cell(2, 0))],
    )


def _bard_inspire(handle: Any) -> None:
    c20_act(
        handle,
        "char:bard",
        intent_type="use_feature",
        feature_id="bardic-inspiration",
        target_id="char:ally",
    )


@pytest.mark.parametrize("level", range(1, 21))
def test_the_bardic_inspiration_die_follows_the_bard_features_table(level: int) -> None:
    """SRD 5.2.1 "Bard: Level 1: Bardic Inspiration": the die is a d6, "a d8 at
    level 5, a d10 at level 10, and a d12 at level 15" (Bard Features table)."""
    die = "d6" if level < 5 else "d8" if level < 10 else "d10" if level < 15 else "d12"
    assert _scale("bard", "inspiration", level) == die


@pytest.mark.parametrize(
    ("level", "die"),
    [(4, "1d6"), (5, "1d8"), (9, "1d8"), (10, "1d10"), (14, "1d10"), (15, "1d12")],
)
def test_an_inspired_ally_holds_the_die_the_bards_level_sizes(level: int, die: str) -> None:
    """SRD 5.2.1 "Bard Features table": the Bardic Inspiration die is sized by the
    granting Bard's level when it is rolled (d6, d8 at 5, d10 at 10, d12 at 15)."""
    handle, live = _bard_table(8, bard_level=level)
    _bard_inspire(handle)
    assert _granted_die(live, c20_combatant(live, "char:ally")) == die


def test_an_inspiration_die_lasts_one_hour_and_one_die_is_held_at_a_time() -> None:
    """SRD 5.2.1 "D20 Tests": the Inspired creature can add the die "Once within
    the next hour when the creature fails a D20 Test". The Inspired effect lasts
    3600 seconds, and a second inspiration replaces rather than adds a die."""
    handle, live = _bard_table(8)
    _bard_inspire(handle)
    (inspired,) = [e for e in live.active_effects["char:ally"] if e.id == "effect:inspired"]
    assert inspired.duration.seconds == 3600
    c20_combatant(live, "char:bard").bonus_action_available = True
    _bard_inspire(handle)
    assert len([e for e in live.active_effects["char:ally"] if e.id == "effect:inspired"]) == 1


# ── Characters: Wild Shape ───────────────────────────────────────────────────


@pytest.mark.parametrize(("level", "uses"), [(2, 2), (5, 2), (6, 3), (16, 3), (17, 4), (20, 4)])
def test_wild_shape_uses_follow_the_druid_features_table(level: int, uses: int) -> None:
    """SRD 5.2.1 "Druid: Level 2: Wild Shape": "You can use Wild Shape twice ...
    You gain additional uses when you reach certain Druid levels, as shown in the
    Wild Shape column of the Druid Features table": 3 at level 6 and 4 at level 17."""
    assert _uses_until_refused("druid", level, "wild-shape", form_id="wolf") == uses


@pytest.mark.parametrize("level", [2, 5, 6, 16, 17])
def test_wild_shape_grants_temporary_hit_points_equal_to_the_druid_level(level: int) -> None:
    """SRD 5.2.1 "Druid: Level 2: Wild Shape": "When you assume a Wild Shape form,
    you gain a number of Temporary Hit Points equal to your Druid level." (The
    Druid Features table column sets the level.)"""
    handle, live = _hero_combat(c20_pc(class_slug="druid", character_level=level), seed=1)
    c20_act(handle, "char:hero", intent_type="use_feature", feature_id="wild-shape", form_id="wolf")
    assert live.tracked_temp_hp["char:hero"] == level


# ── Characters: Weapon Mastery ───────────────────────────────────────────────


def test_cleave_and_the_light_extra_attack_never_drop_a_negative_modifier() -> None:
    """SRD 5.2.1 "Equipment: Weapons: Mastery Properties": Cleave: "the second
    creature takes the weapon's damage, but don't add your ability modifier to
    that damage unless that modifier is negative." A Fighter with Strength 8
    (-1), every die at its maximum, chains a Greataxe (critical 2d12 = 24) for 23
    and never adds a positive modifier."""
    handle, live = _hero_combat(
        c20_pc(class_slug="fighter", strength=8, attack_bonus=30, equipment=("greataxe",)),
        seed=1,
        encounter=[
            c20_foe(entity_id="mon:a", zone_id=cell(0, 1), initiative=10),
            c20_foe(entity_id="mon:b", zone_id=cell(1, 1), initiative=9),
        ],
    )
    live.rng = _MaxRandom()
    c20_act(handle, "char:hero", intent_type="attack", weapon_id="greataxe", target_id="mon:a")
    chain = [
        e
        for e in c20_events(live, DamageApplied)
        if e.source_id == "mastery:cleave" and e.target_id == "mon:b"
    ]
    assert [e.amount for e in chain] == [2 * 12 - 1]


def test_graze_deals_the_attack_modifier_on_a_miss() -> None:
    """SRD 5.2.1 "Equipment: Weapons: Mastery Properties": Graze: "If your attack
    roll with this weapon misses a creature, you can deal damage to that creature
    equal to the ability modifier you used to make the attack roll." A Fighter
    with Strength 16 (+3) that misses with a Greatsword deals 3."""
    handle, live = _hero_combat(
        c20_pc(class_slug="fighter", strength=16, attack_bonus=-30, equipment=("greatsword",)),
        seed=1,
    )
    c20_act(handle, "char:hero", intent_type="attack", weapon_id="greatsword", target_id="mon:foe")
    (attack,) = c20_events(live, AttackRolled)
    assert attack.is_hit is False
    assert [e.amount for e in c20_events(live, DamageApplied)] == [3]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Weapon Mastery is not gated by the feature",
)
def test_a_wizard_has_no_weapon_mastery_to_unlock_a_maces_sap() -> None:
    """SRD 5.2.1 "Equipment: Weapons: Mastery Properties": "Each weapon has a
    mastery property, which is usable only by a character who has a feature, such
    as Weapon Mastery, that unlocks the property for the character." A Wizard has
    no such feature, so a hit with a Mace (Sap) leaves no mark."""
    handle, live = _hero_combat(c20_pc(class_slug="wizard", strength=16, attack_bonus=10), seed=1)
    c20_act(handle, "char:hero", intent_type="attack", weapon_id="mace", target_id="mon:foe")
    assert c20_events(live, AttackRolled)[0].is_hit
    assert live.sap_marks == {}


# ── Characters: Multiclassing ────────────────────────────────────────────────

#: SRD 5.2.1 "Multiclass Spellcaster: Spell Slots per Spell Level": slots of
#: level 1 to 9 for each caster level 1 to 20.
_MULTICLASS_SLOTS: list[tuple[int, ...]] = [
    (2,),
    (3,),
    (4, 2),
    (4, 3),
    (4, 3, 2),
    (4, 3, 3),
    (4, 3, 3, 1),
    (4, 3, 3, 2),
    (4, 3, 3, 3, 1),
    (4, 3, 3, 3, 2),
    (4, 3, 3, 3, 2, 1),
    (4, 3, 3, 3, 2, 1),
    (4, 3, 3, 3, 2, 1, 1),
    (4, 3, 3, 3, 2, 1, 1),
    (4, 3, 3, 3, 2, 1, 1, 1),
    (4, 3, 3, 3, 2, 1, 1, 1),
    (4, 3, 3, 3, 2, 1, 1, 1, 1),
    (4, 3, 3, 3, 3, 1, 1, 1, 1),
    (4, 3, 3, 3, 3, 2, 1, 1, 1),
    (4, 3, 3, 3, 3, 2, 2, 1, 1),
]


@pytest.mark.parametrize("caster_level", range(1, 21))
def test_the_multiclass_spellcaster_table_rows_one_to_twenty(caster_level: int) -> None:
    """SRD 5.2.1 "Character Creation: Multiclassing": "Then look up this total
    level in the Level column of the Multiclass Spellcaster table. You use the
    slots for that level." Row 1 is two first-level slots, row 20 is
    4, 3, 3, 3, 3, 2, 2, 1, 1."""
    expected = dict(enumerate(_MULTICLASS_SLOTS[caster_level - 1], start=1))
    assert slots_for_caster_level(caster_level) == expected


def test_a_ranger_four_sorcerer_three_counts_as_caster_level_five() -> None:
    """SRD 5.2.1 "Character Creation: Multiclassing": "if you are a level 4 Ranger
    / level 3 Sorcerer, you count as a level 5 character when determining your
    spell slots, counting all your levels as a Sorcerer and half your Ranger
    levels ... you have four level 1 spell slots, three level 2 slots, and two
    level 3 slots." """
    assert _sheet(classes={"ranger": 4, "sorcerer": 3}).spell_slots == {1: 4, 2: 3, 3: 2}


def test_one_spellcasting_class_follows_its_own_table() -> None:
    """SRD 5.2.1 "Character Creation: Multiclassing": "If you multiclass but have
    the Spellcasting feature from only one class, follow the rules for that
    class." A Paladin 5 / Fighter 5 has the Paladin 5 slots, not a caster level 3
    row."""
    assert _sheet(classes={"paladin": 5, "fighter": 5}).spell_slots == {1: 4, 2: 2}
    assert _sheet(classes={"paladin": 5}).spell_slots == {1: 4, 2: 2}


def test_pact_magic_slots_stay_separate_from_spellcasting_slots() -> None:
    """SRD 5.2.1 "Character Creation: Multiclassing": "If you have the Pact Magic
    feature from the Warlock class and the Spellcasting feature, you can use the
    spell slots you gain from Pact Magic to cast spells ... and the slots from the
    Spellcasting feature to cast Warlock spells": two pools. A Wizard 2 / Warlock
    2 has three first-level Spellcasting slots and two first-level Pact slots."""
    sheet = _sheet(classes={"wizard": 2, "warlock": 2})
    assert (sheet.spell_slots, sheet.pact_slots) == ({1: 3}, {1: 2})


def test_hit_point_dice_of_different_sizes_are_tracked_separately() -> None:
    """SRD 5.2.1 "Character Creation: Multiclassing": "If your classes give you
    Hit Point Dice of different types, track them separately"; dice of the same
    type pool. A Cleric 5 / Paladin 5 has five d8 and five d10; a Fighter 5 /
    Paladin 5 has ten d10."""
    assert _sheet(classes={"cleric": 5, "paladin": 5}).hit_dice == {8: 5, 10: 5}
    assert _sheet(classes={"fighter": 5, "paladin": 5}).hit_dice == {10: 10}


def test_a_new_class_grants_only_its_multiclass_proficiencies() -> None:
    """SRD 5.2.1 "Each class chapter: As a Multiclass Character": a Cleric gains
    "Light and Medium armor and Shields", a Fighter "Martial weapons, Light and
    Medium armor, and Shields", a Barbarian "Martial weapons and Shields". A
    Wizard 1 gains those with each second class."""
    assert _sheet(classes={"wizard": 1, "cleric": 1}).armor_training == {
        "light",
        "medium",
        "shield",
    }
    barbarian = _sheet(classes={"wizard": 1, "barbarian": 1})
    assert barbarian.armor_training == {"shield"}
    assert {"martial_melee", "martial_ranged"} <= barbarian.weapon_proficiencies
    assert _sheet(classes={"wizard": 1}).armor_training == frozenset()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: A multiclass Monk gains weapon proficiencies",
)
def test_multiclassing_into_monk_grants_no_weapon_proficiency() -> None:
    """SRD 5.2.1 "Each class chapter: As a Multiclass Character": the Monk gives
    only "Gain the Hit Point Die trait ... and the level 1 features", with no
    weapon training. A Wizard 1 / Monk 1 gains no Hand Crossbow, Scimitar or
    Shortsword."""
    sheet = _sheet(classes={"wizard": 1, "monk": 1})
    assert not {"hand-crossbow", "scimitar", "shortsword"} & set(sheet.weapon_proficiencies)


# ── Characters: Sneak Attack and Epic Boons ──────────────────────────────────


@pytest.mark.parametrize("level", range(1, 21))
def test_sneak_attack_dice_follow_the_rogue_features_table(level: int) -> None:
    """SRD 5.2.1 "Rogue Features table": Sneak Attack is 1d6 at levels 1 and 2,
    2d6 at 3 and 4, and one more d6 every two levels to 10d6 at levels 19 and 20."""
    assert _scale("rogue", "sneak-attack", level) == f"{(level + 1) // 2}d6"


def test_an_epic_boon_is_allowed_exactly_at_character_level_nineteen() -> None:
    """SRD 5.2.1 "Epic Boon Feats": "Epic Boon Feat (Prerequisite: Level 19+)".
    A Fighter 19 may take Boon of Fate at the level 19 feat; a Fighter 18 may
    not."""
    token = "feat:fighter:19:boon-of-fate"
    assert "boon-of-fate" in _sheet(classes={"fighter": 19}, selected_choices=(token,)).feats
    with pytest.raises(ValueError, match="19"):
        _sheet(classes={"fighter": 18}, selected_choices=(token,))
