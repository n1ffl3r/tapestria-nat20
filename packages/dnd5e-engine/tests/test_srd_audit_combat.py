"""Pins the SRD 5.2.1 rules of the Combat loop rows of
``docs/srd-5.2.1-rules-audit.md``, each test quoting the decisive sentence it
checks. Where the engine differs from the SRD, the test states the SRD outcome
and is a strict ``xfail`` whose reason is the title of its ``BACKLOG.md`` entry
(section "SRD 5.2.1 rules-application audit"); fixing the engine turns it into
an unexpected pass, which fails the suite until the marker is removed. Every
die is forced or seeded.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest

import dnd5e_engine.rules.dice as dice_mod
from dnd5e_engine import ActiveEffect, PlayerIntent
from dnd5e_engine.activities.apply import _apply_modifiers
from dnd5e_engine.activities.context import ActivityResolutionContext
from dnd5e_engine.activities.passive_stats import CombatantMovementModes
from dnd5e_engine.activities.save_primitive import FORCE_SAVE_D20, roll_save
from dnd5e_engine.check import CheckSpec, resolve_check
from dnd5e_engine.combat_state import begin_combat, resolve_intent
from dnd5e_engine.death_saves import roll_death_save
from dnd5e_engine.events import (
    AttackFailed,
    AttackRolled,
    CheckRolled,
    CombatantMoved,
    ConcentrationCheck,
    ConditionApplied,
    ConditionRemoved,
    DamageApplied,
    DashTaken,
    Death,
    HealingApplied,
    RoundStarted,
    SaveRolled,
    TempHpApplied,
    TurnStarted,
)
from dnd5e_engine.orchestrator import (
    _emit,
    _fire_opportunity_attacks_on_step,
    _get_live,
    _LiveCombat,
    _update_combatant,
    start_combat,
)
from dnd5e_engine.spatial import cell_id
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec
from dnd5e_engine.types.combat import Combatant
from dnd5e_engine.types.conditions import ActiveCondition
from tests.c21_support import act, cleric, combatant, events, foe, monster_turn, pc, start, wizard


class _Dice(random.Random):
    """A stand-in for ``live.rng``: every d20 shows ``d20``; every other die
    shows its top face (``top=True``) or ``pips``."""

    def __init__(self, d20: int = 15, pips: int = 1, top: bool = False) -> None:
        super().__init__(0)
        self._d20, self._pips, self._top = d20, pips, top

    def randint(self, a: int, b: int) -> int:
        if (a, b) == (1, 20):
            return self._d20
        return b if self._top else self._pips


class _Fixed(random.Random):
    """A ``roll_death_save`` rng that always rolls ``value``."""

    def __init__(self, value: int) -> None:
        super().__init__(0)
        self._value = value

    def randint(self, a: int, b: int) -> int:
        return self._value


def _cond(name: str, **fields: Any) -> ActiveCondition:
    return ActiveCondition(
        condition=name, source_entity_id="implied:scenario", scope="combat", **fields
    )


def _pass_turns(handle: Any, live: _LiveCombat, until_round: int, limit: int = 40) -> None:
    """Pass every turn (monsters through ``monster_turn``) until round ``until_round`` begins."""
    for _ in range(limit):
        if any(
            isinstance(e, RoundStarted) and e.round_number >= until_round for e in live.event_log
        ):
            return
        actor = live.current_actor_id
        assert actor is not None
        if actor.startswith("char:"):
            act(handle, actor, intent_type="pass")
        else:
            monster_turn(handle)
    raise AssertionError("the round never began")


def _grid_start(
    party: list[PartyMemberSpec],
    encounter: list[EncounterMemberSpec],
    *,
    seed: int = 3,
    **grid: Any,
) -> tuple[Any, _LiveCombat]:
    result = asyncio.run(
        start_combat(
            session_id=f"srd-audit-{seed}",
            party=party,
            encounter=encounter,
            grid_scene=GridScene(width=10, height=10, **grid),
            rng_seed=seed,
        )
    )
    return result.handle, _get_live(result.handle)


def _swing(**fields: Any) -> EncounterMemberSpec:
    """A template-less foe that attacks (legacy ``attack_bonus`` / ``damage_dice`` swing)."""
    return foe(attack_bonus=5, damage_dice="1d6", **fields)


# ── Initiative order, rounds, turns ─────────────────────────────────────────


def test_turns_run_highest_initiative_first_and_repeat_each_round() -> None:
    """SRD 5.2.1 "Initiative Order": "The GM ranks the combatants, from highest
    to lowest Initiative. This is the order in which they act during each
    round. The Initiative order remains the same from round to round."
    """
    handle, live = start(
        [
            pc("char:a", initiative=18, hp_current=400, hp_max=400),
            pc("char:b", initiative=12, hp_current=400, hp_max=400, zone_id=cell_id(0, 1)),
        ],
        seed=4,
        encounter=[
            foe(initiative=15),
            foe(entity_id="mon:two", name="Two", initiative=5, zone_id=cell_id(2, 0)),
        ],
    )
    _pass_turns(handle, live, until_round=3)
    order = [e.actor_id for e in events(live, TurnStarted)]
    expected = ["char:a", "mon:foe", "char:b", "mon:two"]
    assert order[:4] == expected
    assert order[4:8] == expected
    assert [e.round_number for e in events(live, RoundStarted)][:3] == [1, 2, 3]


# ── Combat state and per-call seeds ─────────────────────────────────────────


def test_a_call_replayed_from_the_same_state_and_seed_gives_the_same_result() -> None:
    """engine design (spec 02a): the stateless API resolves one call from a
    ``CombatState`` and a seed. The same state, call and seed give the same
    events and the same new state, the state passed in is not changed, and a
    different seed draws different dice (contract 03, "Determinism and seeds")."""
    step = asyncio.run(
        begin_combat(
            session_id="state-seed",
            party=[pc("char:hero")],
            encounter=[foe()],
            grid_scene=GridScene(width=10, height=10),
            seed=1,
        )
    )
    state = step.state
    before = state.model_dump_json()
    intent = PlayerIntent(intent_type="attack", weapon_id="longsword", target_id="mon:foe")

    def call(seed: int) -> str:
        return asyncio.run(
            resolve_intent(state, actor_id="char:hero", intent=intent, seed=seed)
        ).model_dump_json()

    assert call(9) == call(9)
    assert state.model_dump_json() == before
    assert len({call(seed) for seed in range(1, 12)}) > 1


# ── Turn lifecycle, Reactions (also the Action economy and Opportunity attacks rows) ──


def test_a_reaction_is_regained_at_the_start_of_the_reactors_own_next_turn() -> None:
    """SRD 5.2.1 "Reactions": "When you take a Reaction, you can't take another
    one until the start of your next turn." The foe's Opportunity Attack spends
    its Reaction; it stays spent through the two heroes' turns and is back once
    the foe's own turn starts."""
    handle, live = start(
        [pc("char:a", initiative=20), pc("char:b", initiative=10, zone_id=cell_id(0, 1))],
        seed=5,
        encounter=[_swing(initiative=1)],
    )
    act(handle, "char:a", intent_type="move", target_zone_id=cell_id(0, 5))
    assert [e for e in events(live, AttackRolled) if e.attacker_id == "mon:foe"]
    assert combatant(live, "mon:foe").reaction_available is False
    act(handle, "char:a", intent_type="pass")
    assert combatant(live, "mon:foe").reaction_available is False
    act(handle, "char:b", intent_type="pass")
    assert live.current_actor_id == "mon:foe"
    assert combatant(live, "mon:foe").reaction_available is True


# ── Attack rolls, crits, damage ─────────────────────────────────────────────


def test_a_critical_hit_rolls_the_damage_dice_twice_and_adds_the_modifier_once() -> None:
    """SRD 5.2.1 "Critical Hits": "Roll the attack's damage dice twice, add them
    together, and add any relevant modifiers as normal." A Longsword (1d8) at
    Strength 16 (+3) with every die on its top face deals 8 + 8 + 3 on a
    natural 20 and 8 + 3 on a natural 15."""
    amounts = []
    for d20 in (20, 15):
        handle, live = start([pc(strength=16, attack_bonus=5)], seed=6, encounter=[foe()])
        live.rng = _Dice(d20=d20, top=True)
        act(handle, "char:hero", intent_type="attack", weapon_id="longsword", target_id="mon:foe")
        [rolled] = events(live, AttackRolled)
        [damage] = events(live, DamageApplied)
        assert rolled.is_crit is (d20 == 20)
        amounts.append(damage.amount)
    assert amounts == [19, 11]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Resistance and Vulnerability apply in the wrong order",
)
def test_resistance_halves_before_vulnerability_doubles() -> None:
    """SRD 5.2.1 "Resistance and Vulnerability": "Modifiers to damage are applied
    in the following order: adjustments such as bonuses, penalties, or
    multipliers are applied first; Resistance is applied second; and
    Vulnerability is applied third." Its example: 23 Fire damage, Resistance to
    all and Vulnerability to Fire, is halved to 11 then doubled to 22; 5 damage
    with both on one type is halved to 2 then doubled to 4."""
    assert _apply_modifiers(23, "fire", {"all"}, set(), {"fire"}) == 22
    assert _apply_modifiers(5, "fire", {"fire"}, set(), {"fire"}) == 4


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Damage has no floor of zero",
)
def test_a_damage_penalty_never_deals_negative_damage() -> None:
    """SRD 5.2.1 "Damage Rolls": "If there's a penalty to the damage, it's
    possible to deal 0 damage but not negative damage." A Strength 3 attacker
    (-4) rolling a 1 on a Dagger's d4 nets -3, which must deal 0."""
    handle, live = start(
        [pc(strength=3, dexterity=3, attack_bonus=20)],
        seed=47,
        encounter=[foe(ac=5, hp_current=50, hp_max=50)],
    )
    live.rng = _Dice(d20=15, pips=1)
    act(handle, "char:hero", intent_type="attack", weapon_id="dagger", target_id="mon:foe")
    damage = events(live, DamageApplied)
    assert damage
    assert min(e.amount for e in damage) >= 0
    assert combatant(live, "mon:foe").hp_current <= 50


# ── Saving throws ───────────────────────────────────────────────────────────


def _save_ctx(forced_d20: int, modifier: int) -> tuple[ActivityResolutionContext, Combatant]:
    target = Combatant(
        entity_id="mon:foe",
        entity_type="Monster",
        name="Foe",
        initiative=1,
        hp_current=50,
        hp_max=50,
    )
    ctx = ActivityResolutionContext(
        rng=random.Random(1),
        caster=target,
        targets=[],
        event_emitter=lambda ev: None,
        caster_abilities={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        variables={FORCE_SAVE_D20: forced_d20},
        passive_save_modifiers={"mon:foe": {"wis": modifier}},
    )
    return ctx, target


def test_a_natural_20_does_not_pass_a_save_and_a_natural_1_does_not_fail_one() -> None:
    """SRD 5.2.1 "D20 Tests": "If the total of the d20 and its modifiers equals or
    exceeds the target number, the D20 Test succeeds." The special treatment of
    a 20 or a 1 is printed under "Attack Rolls" only, so a Saving Throw is
    decided by its total: a natural 20 at -10 against DC 15 fails, and a natural
    1 at +20 against DC 10 succeeds."""
    ctx, target = _save_ctx(20, -10)
    low = roll_save(ctx, target, "wis", dc=15)
    assert (low.total, low.succeeded) == (10, False)
    ctx, target = _save_ctx(1, 20)
    high = roll_save(ctx, target, "wis", dc=10)
    assert (high.total, high.succeeded) == (21, True)


# ── Ability and skill checks ────────────────────────────────────────────────


def test_proficiency_is_added_to_a_check_only_with_the_proficiency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SRD 5.2.1 "Proficiency Bonus": "Add your Proficiency Bonus to an ability
    check when the GM determines that a skill or tool proficiency is relevant to
    the check and you have that proficiency." Two Strength (Athletics) checks on
    the same roll differ by exactly the Proficiency Bonus; a total equal to the
    DC succeeds (SRD 5.2.1 "Ability Checks")."""
    monkeypatch.setattr(dice_mod.UNSEEDED, "randint", lambda a, b: 9)
    results = []
    for proficient in (("athletics",), ()):
        spec = CheckSpec(
            kind="skill",
            skill="athletics",
            ability_scores={"strength": 14},
            proficient_skills=proficient,
            proficient_saves=(),
            proficiency_bonus=3,
            dc=14,
        )
        results.append(resolve_check(spec))
    with_it, without = results
    assert with_it.modifier - without.modifier == 3
    assert (with_it.is_proficient, without.is_proficient) == (True, False)
    assert (with_it.roll_total, with_it.success) == (14, True)
    assert (without.roll_total, without.success) == (11, False)


# ── Action economy ("Your Turn"), Dodge, Help, Grapple / Shove ──────────────


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Dodge, Help, Grapple and Shove end the turn and forfeit the rest of it",
)
def test_a_creature_may_take_the_dodge_action_and_then_move() -> None:
    """SRD 5.2.1 "Your Turn": "On your turn, you can move a distance up to your
    Speed and take one action. You decide whether to move first or take your
    action first." After the Dodge action the hero still has its movement."""
    handle, live = start([pc()], seed=7, encounter=[foe()])
    act(handle, "char:hero", intent_type="dodge")
    assert live.current_actor_id == "char:hero"
    act(handle, "char:hero", intent_type="move", target_zone_id=cell_id(0, 2))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Dodge, Help, Grapple and Shove end the turn and forfeit the rest of it",
)
def test_a_grapple_is_one_attack_so_extra_attack_continues() -> None:
    """SRD 5.2.1 "Unarmed Strike": "In game terms, this is an Unarmed Strike: a
    melee attack that involves you using your body to damage, grapple, or shove a
    target within 5 feet of you." A Fighter 5 (Extra Attack, two attacks) who
    spends one attack on a Grapple keeps the turn and the second attack."""
    handle, live = start(
        [pc(class_slug="fighter", character_level=5, strength=16)], seed=3, encounter=[foe()]
    )
    act(handle, "char:hero", intent_type="grapple", target_id="mon:foe")
    assert live.current_actor_id == "char:hero"
    assert combatant(live).attacks_remaining == 1


def test_a_grapple_dc_is_8_plus_the_strength_modifier_and_proficiency_bonus() -> None:
    """SRD 5.2.1 "Unarmed Strike": "The DC for the saving throw and any escape
    attempts equals 8 plus your Strength modifier and Proficiency Bonus." A
    level 5 character (Proficiency Bonus +3) with Strength 16 (+3) sets DC 14,
    on the save and on the stored escape DC of the Grappled condition."""
    handle, live = start(
        [pc(class_slug="fighter", character_level=5, strength=16)], seed=3, encounter=[foe()]
    )
    _update_combatant(live, "mon:foe", strength=1, dexterity=1)
    act(handle, "char:hero", intent_type="grapple", target_id="mon:foe")
    [saved] = events(live, SaveRolled)
    assert saved.dc == 14
    grappled = next(c for c in combatant(live, "mon:foe").conditions if c.condition == "grappled")
    assert grappled.save_dc == 14


def test_a_shove_pushes_the_target_5_feet_away_from_the_shover() -> None:
    """SRD 5.2.1 "Unarmed Strike": "you either push it 5 feet away or cause it
    to have the Prone condition." The shover on 0,0 pushes a target on 1,0 to
    2,0, along the line away from itself."""
    handle, live = start(
        [pc(class_slug="fighter", character_level=5, strength=16)], seed=3, encounter=[foe()]
    )
    _update_combatant(live, "mon:foe", strength=1, dexterity=1)
    act(handle, "char:hero", intent_type="shove", target_id="mon:foe", shove_push=True)
    [moved] = events(live, CombatantMoved)
    assert (moved.from_zone, moved.to_zone, moved.distance_ft) == (cell_id(1, 0), cell_id(2, 0), 5)
    assert not [e for e in events(live, ConditionApplied) if e.condition == "prone"]


def test_a_second_action_in_the_same_turn_is_refused() -> None:
    """SRD 5.2.1 "Your Turn": "On your turn, you can move a distance up to your
    Speed and take one action." A hero without Action Surge who has Dashed
    cannot also Disengage: the second Action is refused and the turn stays."""
    from dnd5e_engine.orchestrator import IntentRejectedError

    handle, live = start([pc()], seed=8, encounter=[foe()])
    act(handle, "char:hero", intent_type="dash")
    movement = combatant(live).movement_remaining
    with pytest.raises(IntentRejectedError) as raised:
        act(handle, "char:hero", intent_type="disengage")
    assert raised.value.reason == "no_action_economy"
    assert live.current_actor_id == "char:hero"
    assert combatant(live).movement_remaining == movement


# ── Dash ────────────────────────────────────────────────────────────────────


def test_dash_adds_the_speed_after_modifiers() -> None:
    """SRD 5.2.1 "Dash": "The increase equals your Speed after applying any
    modifiers." Exhaustion level 1 cuts a Speed of 30 to 25, so Dash makes the
    turn's movement 50."""
    handle, live = start([pc()], seed=9, encounter=[foe()])
    _update_combatant(live, "char:hero", conditions=[_cond("exhaustion", exhaustion_level=1)])
    _update_combatant(live, "char:hero", movement_remaining=25)
    act(handle, "char:hero", intent_type="dash")
    [dash] = events(live, DashTaken)
    assert dash.doubled_movement_remaining == 50
    assert combatant(live).movement_remaining == 50


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Dash uses only the walking Speed",
)
def test_dash_may_use_a_flying_speed_instead_of_the_walking_speed() -> None:
    """SRD 5.2.1 "Dash": "If you have a special speed, such as a Fly Speed or Swim
    Speed, you can use that speed instead of your Speed when you take this
    action." A creature with Speed 30 and a Fly Speed of 60 that Dashes gains 60
    feet of movement, not 30."""
    handle, live = start(
        [pc(base_speed=30, movement_modes=CombatantMovementModes(fly=60))],
        seed=9,
        encounter=[foe()],
    )
    before = combatant(live).movement_remaining
    act(handle, "char:hero", intent_type="dash")
    assert combatant(live).movement_remaining - before == 60


# ── Dodge ───────────────────────────────────────────────────────────────────


def test_a_dodger_with_speed_zero_loses_the_dexterity_save_advantage() -> None:
    """SRD 5.2.1 "Dodge": "You lose these benefits if you have the Incapacitated
    condition or if your Speed is 0." A dodging foe saves against Acid Splash
    with Advantage on Dexterity saves; once it is Grappled (Speed 0) it saves
    normally."""
    modes = []
    for grappled in (False, True):
        handle, live = start(
            [cleric(spells_known=["acid-splash"], spell_slots={})],
            seed=13,
            encounter=[foe(dexterity=10)],
        )
        _update_combatant(live, "mon:foe", dodging=True)
        if grappled:
            _update_combatant(live, "mon:foe", conditions=[_cond("grappled")])
        act(
            handle,
            "char:cleric",
            intent_type="cast_spell",
            spell_id="acid-splash",
            target_id="mon:foe",
        )
        [saved] = [e for e in events(live, SaveRolled) if e.target_id == "mon:foe"]
        assert saved.ability == "dex"
        modes.append(saved.advantage)
    assert modes == ["advantage", "normal"]


# ── Help ────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Help leaks into the helper's own Opportunity Attack",
)
def test_the_helpers_own_opportunity_attack_gets_no_help_advantage() -> None:
    """SRD 5.2.1 "Help": "You momentarily distract an enemy within 5 feet of you,
    giving Advantage to the next attack roll by one of your allies against that
    enemy." The helper is not its own ally, so its Opportunity Attack against the
    helped-against foe rolls with no Advantage."""
    handle, live = start([pc(attack_bonus=5)], seed=11, encounter=[_swing()])
    act(handle, "char:hero", intent_type="help", target_id="mon:foe")
    assert live.help_grants.get("mon:foe") == ["char:hero"]
    _fire_opportunity_attacks_on_step(
        live, mover_id="mon:foe", from_cell=cell_id(1, 0), to_cell=cell_id(2, 0)
    )
    [rolled] = [e for e in events(live, AttackRolled) if e.attacker_id == "char:hero"]
    assert "help" not in rolled.sources
    assert rolled.advantage == "normal"


def test_a_help_grant_is_used_up_by_the_next_attack_even_when_it_misses() -> None:
    """SRD 5.2.1 "Help": "giving Advantage to the next attack roll by one of your
    allies against that enemy." The first ally's attack misses with Advantage;
    the second ally's attack is a plain roll and the grant is gone."""
    handle, live = start(
        [
            pc("char:helper", initiative=20),
            pc("char:one", initiative=15, attack_bonus=5, zone_id=cell_id(0, 1)),
            pc("char:two", initiative=14, attack_bonus=5, zone_id=cell_id(1, 1)),
        ],
        seed=13,
        encounter=[foe(ac=30)],
    )
    live.rng = _Dice(d20=2)
    act(handle, "char:helper", intent_type="help", target_id="mon:foe")
    for striker in ("char:one", "char:two"):
        act(handle, striker, intent_type="attack", weapon_id="longsword", target_id="mon:foe")
    first, second = events(live, AttackRolled)
    assert (first.is_hit, first.advantage, "help" in first.sources) == (False, "advantage", True)
    assert (second.advantage, "help" in second.sources) == ("normal", False)
    assert not live.help_grants.get("mon:foe")


# ── Hide ────────────────────────────────────────────────────────────────────


def _hide_start(
    d20: int, *, dexterity: int = 10, foe_ac: int = 10, seed: int = 7
) -> tuple[Any, _LiveCombat]:
    cover = cell_id(1, 1)
    handle, live = _grid_start(
        [pc("char:hider", dexterity=dexterity, zone_id=cover)],
        [foe(ac=foe_ac, zone_id=cell_id(2, 1))],
        seed=seed,
        cover_cells={cover: "three_quarters"},
    )
    live.rng = _Dice(d20=d20)
    return handle, live


def test_the_hide_check_succeeds_at_exactly_dc_15() -> None:
    """SRD 5.2.1 "Hide": "you must succeed on a DC 15 Dexterity (Stealth) check".
    With a Stealth modifier of +0, a natural 14 fails (no Invisible condition)
    and a natural 15 succeeds."""
    outcomes = []
    for d20 in (14, 15):
        handle, live = _hide_start(d20)
        act(handle, "char:hider", intent_type="hide")
        [check] = events(live, CheckRolled)
        assert (check.skill, check.dc, check.roll_total) == ("stealth", 15, d20)
        outcomes.append((check.succeeded, "char:hider" in live.hidden_entities))
    assert outcomes == [(False, False), (True, True)]


def test_hiding_ends_when_the_hider_makes_an_attack_roll_that_misses() -> None:
    """SRD 5.2.1 "Hide": "You stop being hidden immediately after any of the
    following occurs: ... you make an attack roll". A hidden hero whose attack
    misses is no longer Invisible."""
    handle, live = _hide_start(2, dexterity=40, foe_ac=40)
    act(handle, "char:hider", intent_type="hide")
    assert "char:hider" in live.hidden_entities
    act(handle, "char:hider", intent_type="attack", weapon_id="longsword", target_id="mon:foe")
    [rolled] = events(live, AttackRolled)
    assert rolled.is_hit is False
    assert "char:hider" not in live.hidden_entities
    assert any(e.condition == "invisible" for e in events(live, ConditionRemoved))


# ── Opportunity attacks ─────────────────────────────────────────────────────


def test_a_reactor_makes_only_one_opportunity_attack_per_round() -> None:
    """SRD 5.2.1 "Opportunity Attacks": "To make the Opportunity Attack, take a
    Reaction to make one melee attack with a weapon or an Unarmed Strike against
    the provoking creature." The Reaction is spent, so a second provoking step in
    the same round draws no second attack."""
    _handle, live = start([pc(attack_bonus=5)], seed=12, encounter=[_swing()])
    _fire_opportunity_attacks_on_step(
        live, mover_id="mon:foe", from_cell=cell_id(1, 0), to_cell=cell_id(2, 0)
    )
    _fire_opportunity_attacks_on_step(
        live, mover_id="mon:foe", from_cell=cell_id(1, 0), to_cell=cell_id(2, 0)
    )
    attacks = [e for e in events(live, AttackRolled) if e.attacker_id == "char:hero"]
    assert len(attacks) == 1
    assert combatant(live).reaction_available is False


# ── Death saves, Stable ─────────────────────────────────────────────────────


def _dying(**saves: Any) -> Combatant:
    return Combatant(
        entity_id="char:pc",
        entity_type="Character",
        name="PC",
        initiative=10,
        hp_current=0,
        hp_max=20,
        death_saves=saves,
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Damage does not end Stable, and becoming Stable keeps the tallies",
)
def test_damage_ends_stable_and_death_saves_resume() -> None:
    """SRD 5.2.1 "Stabilizing a Character": "If the creature takes damage, it
    stops being Stable and starts making Death Saving Throws again." A Stable
    hero at 0 Hit Points that takes 1 damage is no longer Stable and has a
    Death Saving Throw failure ("Damage at 0 Hit Points")."""
    _handle, live = start(
        [pc(initiative=1, hp_current=0, hp_max=20)], seed=14, encounter=[foe(initiative=20)]
    )
    _update_combatant(
        live, "char:hero", death_saves={"successes": 0, "failures": 0, "is_stable": True}
    )
    _emit(
        live,
        DamageApplied(target_id="char:hero", amount=1, damage_type="slashing", is_overkill=False),
    )
    saves = combatant(live).death_saves
    assert saves["is_stable"] is False
    assert saves["failures"] == 1


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Damage does not end Stable, and becoming Stable keeps the tallies",
)
def test_becoming_stable_resets_both_death_save_tallies() -> None:
    """SRD 5.2.1 "Death Saving Throws": "The number of both is reset to zero when
    you regain any Hit Points or become Stable." A third success with two
    failures on the tally leaves the creature Stable with both counts at zero."""
    result = roll_death_save(_dying(successes=2, failures=2, is_stable=False), _Fixed(15))
    assert result.outcome == "stabilized"
    assert result.combatant.death_saves == {"successes": 0, "failures": 0, "is_stable": True}


def test_damage_equal_to_the_hit_point_maximum_at_zero_hit_points_kills() -> None:
    """SRD 5.2.1 "Death Saving Throws": "If the damage equals or exceeds your Hit
    Point maximum, you die." At 0 Hit Points with a maximum of 12, 12 damage
    kills and 11 damage is one Death Saving Throw failure."""
    results = []
    for amount in (12, 11):
        _handle, live = start(
            [pc(hp_current=0, hp_max=12, initiative=1)], seed=15, encounter=[foe(initiative=20)]
        )
        _emit(
            live,
            DamageApplied(
                target_id="char:hero", amount=amount, damage_type="slashing", is_overkill=False
            ),
        )
        died = [e for e in events(live, Death) if e.target_id == "char:hero"]
        results.append((bool(died), combatant(live).is_alive))
        if not died:
            assert combatant(live).death_saves["failures"] == 1
    assert results == [(True, False), (False, True)]


def test_exhaustion_lowers_a_death_save_total_but_not_the_natural_20_or_1() -> None:
    """SRD 5.2.1 "Death Saving Throws": "If you roll a 20 on the d20, you regain 1
    Hit Point." With Exhaustion level 2 ("Exhaustion": "the roll is reduced by 2
    times your Exhaustion level"), a natural 20 still regains 1 Hit Point, a
    natural 11 (total 7) fails, and a natural 1 is two failures."""
    tired = [_cond("exhaustion", exhaustion_level=2)]

    def dying() -> Combatant:
        return _dying().model_copy(update={"conditions": tired})

    crit = roll_death_save(dying(), _Fixed(20))
    assert (crit.outcome, crit.combatant.hp_current) == ("critical_success", 1)
    middling = roll_death_save(dying(), _Fixed(11))
    assert middling.combatant.death_saves["failures"] == 1
    assert middling.combatant.death_saves["successes"] == 0
    fumble = roll_death_save(dying(), _Fixed(1))
    assert fumble.combatant.death_saves["failures"] == 2


# ── Instant death ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("amount", "dies"), [(18, True), (17, False)])
def test_massive_damage_kills_when_the_remainder_equals_the_hit_point_maximum(
    amount: int, dies: bool
) -> None:
    """SRD 5.2.1 "Instant Death": "if your character has a Hit Point maximum of
    12, currently has 6 Hit Points, and takes 18 damage, the character drops to 0
    Hit Points, but 12 damage remains. The character then dies, since 12 equals
    their Hit Point maximum." Taking 17 leaves a remainder of 11, so the
    character falls Unconscious instead."""
    _handle, live = start([pc(hp_current=6, hp_max=12)], seed=16, encounter=[foe()])
    _emit(
        live,
        DamageApplied(
            target_id="char:hero", amount=amount, damage_type="slashing", is_overkill=False
        ),
    )
    deaths = events(live, Death)
    assert bool(deaths) is dies
    if dies:
        assert deaths[0].reason == "instant_kill"
        assert combatant(live).is_alive is False
    else:
        assert combatant(live).hp_current == 0
        assert any(c.condition == "unconscious" for c in combatant(live).conditions)


@pytest.mark.parametrize(("amount", "dies"), [(23, True), (22, False)])
def test_massive_damage_is_measured_after_temporary_hit_points(amount: int, dies: bool) -> None:
    """SRD 5.2.1 "Instant Death": "When damage reduces a character to 0 Hit Points
    and damage remains, the character dies if the remainder equals or exceeds
    their Hit Point maximum." A character at 6 of 12 Hit Points with 5 Temporary
    Hit Points takes 5 first, then 6, so 23 damage leaves a remainder of 12 and
    22 leaves 11."""
    _handle, live = start([pc(hp_current=6, hp_max=12)], seed=16, encounter=[foe()])
    _emit(live, TempHpApplied(target_id="char:hero", amount=5))
    _emit(
        live,
        DamageApplied(
            target_id="char:hero", amount=amount, damage_type="slashing", is_overkill=False
        ),
    )
    assert bool(events(live, Death)) is dies


def test_a_monster_dies_the_instant_it_drops_to_zero_hit_points() -> None:
    """SRD 5.2.1 "Instant Death": "A monster dies the instant it drops to 0 Hit
    Points". A foe with 30 of 30 Hit Points that takes 30 damage dies with no
    Unconscious condition and no Death Saving Throws."""
    _handle, live = start([pc()], seed=17, encounter=[foe(hp_current=30, hp_max=30)])
    _emit(
        live,
        DamageApplied(target_id="mon:foe", amount=30, damage_type="slashing", is_overkill=False),
    )
    assert [e.reason for e in events(live, Death)] == ["damage"]
    assert "mon:foe" in live.dead_ids
    assert not [e for e in events(live, ConditionApplied) if e.target_id == "mon:foe"]
    assert combatant(live, "mon:foe").death_saves == {}


# ── Concentration ───────────────────────────────────────────────────────────


def _concentrating(seed: int = 18, **fields: Any) -> tuple[Any, _LiveCombat]:
    """A hero with 400 Hit Points and Constitution 30 concentrating on one effect."""
    handle, live = start(
        [pc(hp_current=400, hp_max=400, constitution=30, **fields)], seed=seed, encounter=[foe()]
    )
    live.concentration_chain["char:hero"] = [("char:hero", "effect:x", "cast:bless:char:hero")]
    return handle, live


@pytest.mark.parametrize(("amount", "dc"), [(21, 10), (59, 29), (61, 30)])
def test_the_concentration_dc_is_half_the_damage_rounded_down_up_to_30(
    amount: int, dc: int
) -> None:
    """SRD 5.2.1 "Concentration": "The DC equals 10 or half the damage taken
    (round down), whichever number is higher, up to a maximum DC of 30." 21
    damage is DC 10, 59 is DC 29 and 61 is DC 30."""
    _handle, live = _concentrating()
    _emit(
        live,
        DamageApplied(target_id="char:hero", amount=amount, damage_type="fire", is_overkill=False),
    )
    assert [c.dc for c in events(live, ConcentrationCheck)] == [dc]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Concentration checks roll per damage type and on zero damage, and Eldritch Mind is ignored",
)
def test_zero_damage_prompts_no_concentration_save() -> None:
    """SRD 5.2.1 "Concentration": "Damage. If you take damage, you must succeed on
    a Constitution saving throw to maintain Concentration." A hit that deals 0
    damage (an immunity, a fully absorbed hit) takes none, so it prompts no
    save."""
    _handle, live = _concentrating()
    _emit(
        live, DamageApplied(target_id="char:hero", amount=0, damage_type="fire", is_overkill=False)
    )
    assert events(live, ConcentrationCheck) == []


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Concentration checks roll per damage type and on zero damage, and Eldritch Mind is ignored",
)
def test_one_hit_with_two_damage_types_prompts_a_single_save_for_the_whole_hit() -> None:
    """SRD 5.2.1 "Concentration": "The DC equals 10 or half the damage taken (round
    down), whichever number is higher". An Ice Storm that deals 20 Bludgeoning
    and 24 Cold in one casting is 44 damage taken: one Constitution save at DC
    22, not one save per damage type."""
    handle, live = start(
        [
            wizard(spells_known=["ice-storm"], spell_slots={4: 1}),
            pc(
                "char:ally",
                hp_current=400,
                hp_max=400,
                initiative=5,
                zone_id=cell_id(6, 0),
                constitution=30,
                dexterity=1,
            ),
        ],
        seed=3,
        encounter=[foe(zone_id=cell_id(9, 9))],
    )
    live.concentration_chain["char:ally"] = [("char:ally", "effect:x", "cast:bless:char:ally")]
    live.rng = _Dice(d20=20, top=True)
    act(
        handle,
        "char:wiz",
        intent_type="cast_spell",
        spell_id="ice-storm",
        target_id="char:ally",
        slot_level=4,
    )
    taken = sum(e.amount for e in events(live, DamageApplied) if e.target_id == "char:ally")
    assert taken == 44
    checks = [c for c in events(live, ConcentrationCheck) if c.target_id == "char:ally"]
    assert [c.dc for c in checks] == [22]


def test_the_concentration_save_carries_the_exhaustion_penalty() -> None:
    """SRD 5.2.1 "Exhaustion": "When you make a D20 Test, the roll is reduced by 2
    times your Exhaustion level." A concentrating hero at Exhaustion level 2 has a
    Concentration save modifier 4 lower than the same hero without it."""
    modifiers = []
    for level in (None, 2):
        _handle, live = _concentrating()
        if level:
            _update_combatant(
                live, "char:hero", conditions=[_cond("exhaustion", exhaustion_level=level)]
            )
        _emit(
            live,
            DamageApplied(target_id="char:hero", amount=1, damage_type="fire", is_overkill=False),
        )
        [check] = events(live, ConcentrationCheck)
        modifiers.append(check.modifier)
    untired, tired = modifiers
    assert untired is not None
    assert tired is not None
    assert untired - tired == 4


# ── Temporary Hit Points, Healing, Dead ─────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Healing revives the dead",
)
def test_healing_does_not_revive_a_dead_creature() -> None:
    """SRD 5.2.1 "Dead": "A dead creature has no Hit Points and can't regain them
    unless it is first revived by magic such as the Raise Dead or Revivify
    spell." Healing Word on an ally killed by massive damage leaves it dead at 0
    Hit Points."""
    handle, live = start(
        [
            cleric(spells_known=["healing-word"]),
            pc("char:ally", hp_current=3, hp_max=12, initiative=5, zone_id=cell_id(0, 1)),
        ],
        seed=3,
    )
    _emit(
        live,
        DamageApplied(target_id="char:ally", amount=40, damage_type="slashing", is_overkill=False),
    )
    assert combatant(live, "char:ally").is_alive is False
    act(
        handle,
        "char:cleric",
        intent_type="cast_spell",
        spell_id="healing-word",
        target_id="char:ally",
        slot_level=1,
    )
    ally = combatant(live, "char:ally")
    assert ally.hp_current == 0
    assert ally.is_alive is False


def test_temporary_hit_points_do_not_stack_and_the_larger_grant_is_kept() -> None:
    """SRD 5.2.1 "Temporary Hit Points": "if a spell grants you 12 Temporary Hit
    Points when you already have 10, you can have 12 or 10, not 22." Grants of 10,
    12 and 5 leave 12, and the real Hit Points are untouched."""
    _handle, live = start([pc(hp_current=14, hp_max=20)], seed=19, encounter=[foe()])
    held = []
    for grant in (10, 12, 5):
        _emit(live, TempHpApplied(target_id="char:hero", amount=grant))
        held.append(live.tracked_temp_hp["char:hero"])
    assert held == [10, 12, 12]
    assert live.tracked_hp["char:hero"] == 14


def test_temporary_hit_points_do_not_wake_a_creature_at_zero_hit_points() -> None:
    """SRD 5.2.1 "Temporary Hit Points": "If you have 0 Hit Points, receiving
    Temporary Hit Points doesn't restore you to consciousness. Only true healing
    can save you." A hero at 0 Hit Points who gains 5 stays Unconscious at 0."""
    _handle, live = start([pc(hp_current=3, hp_max=20)], seed=19, encounter=[foe()])
    _emit(
        live,
        DamageApplied(target_id="char:hero", amount=3, damage_type="slashing", is_overkill=False),
    )
    assert any(c.condition == "unconscious" for c in combatant(live).conditions)
    _emit(live, TempHpApplied(target_id="char:hero", amount=5))
    hero = combatant(live)
    assert hero.hp_current == 0
    assert any(c.condition == "unconscious" for c in hero.conditions)
    assert not events(live, ConditionRemoved)


def test_healing_cannot_exceed_the_maximum_and_does_not_fill_temporary_hit_points() -> None:
    """SRD 5.2.1 "Healing": "if you receive 8 Hit Points of healing and have 14
    Hit Points and a Hit Point maximum of 20, you regain 6 Hit Points, not 8."
    Temporary Hit Points are not Hit Points, so they stay 12."""
    _handle, live = start([pc(hp_current=14, hp_max=20)], seed=19, encounter=[foe()])
    _emit(live, TempHpApplied(target_id="char:hero", amount=12))
    _emit(live, HealingApplied(target_id="char:hero", amount=8))
    assert live.tracked_hp["char:hero"] == 20
    assert live.tracked_temp_hp["char:hero"] == 12


# ── Conditions ──────────────────────────────────────────────────────────────


def test_a_blinded_creature_attacks_with_disadvantage_and_is_hit_with_advantage() -> None:
    """SRD 5.2.1 "Blinded": "Attack rolls against you have Advantage, and your
    attack rolls have Disadvantage." A blinded hero attacks with Disadvantage;
    the hero attacking a blinded foe has Advantage."""
    modes = []
    for blind in ("char:hero", "mon:foe"):
        effect = ActiveEffect(
            id="effect:blinded",
            name="Blinded",
            origin="cast:blindness-deafness:mon:x",
            target_id=blind,
            statuses={"blinded"},
        )
        handle, live = start(
            [pc(attack_bonus=5)], seed=20, encounter=[foe()], active_effects=[effect]
        )
        act(handle, "char:hero", intent_type="attack", weapon_id="longsword", target_id="mon:foe")
        [rolled] = events(live, AttackRolled)
        modes.append(rolled.advantage)
    assert modes == ["disadvantage", "advantage"]


# ── Exhaustion ──────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Exhaustion does not accumulate, and level 6 does not kill",
)
def test_receiving_exhaustion_twice_gives_level_two() -> None:
    """SRD 5.2.1 "Exhaustion": "This condition is cumulative. Each time you
    receive it, you gain 1 Exhaustion level." Two receipts make level 2."""
    from dnd5e_engine.rules.conditions import exhaustion_level_of

    _handle, live = start([pc()], seed=21, encounter=[foe()])
    for _ in range(2):
        _emit(live, ConditionApplied(target_id="char:hero", condition="exhaustion"))
    assert exhaustion_level_of(combatant(live).conditions) == 2


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Exhaustion does not accumulate, and level 6 does not kill",
)
def test_exhaustion_level_6_kills() -> None:
    """SRD 5.2.1 "Exhaustion": "You die if your Exhaustion level is 6." A hero at
    level 5 who receives the condition once more dies."""
    _handle, live = start([pc()], seed=21, encounter=[foe()])
    _update_combatant(live, "char:hero", conditions=[_cond("exhaustion", exhaustion_level=5)])
    _emit(live, ConditionApplied(target_id="char:hero", condition="exhaustion"))
    assert [e.target_id for e in events(live, Death)] == ["char:hero"]
    assert combatant(live).is_alive is False


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Initiative ties, group rolls and Exhaustion differ",
)
def test_rolled_initiative_is_reduced_by_two_per_exhaustion_level() -> None:
    """SRD 5.2.1 "Exhaustion": "When you make a D20 Test, the roll is reduced by 2
    times your Exhaustion level." Initiative is a Dexterity check ("Initiative"),
    so the same seed gives an engine-rolled Initiative 2 lower at level 1."""
    seated = []
    for effects in (
        [],
        [
            ActiveEffect(
                id="effect:tired",
                name="Exhausted",
                origin="item:ration:x",
                target_id="char:hero",
                statuses={"exhaustion"},
            )
        ],
    ):
        _, live = start(
            [pc(initiative=None, dexterity=14)], seed=5, encounter=[foe()], active_effects=effects
        )
        seated.append(combatant(live).initiative)
    assert seated[1] == seated[0] - 2


# ── Dead creatures take no turn ─────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: A dead monster still takes its turn",
)
def test_a_dead_combatant_takes_no_turn() -> None:
    """SRD 5.2.1 "Combat Step by Step": "Take Turns. Each participant in the
    battle takes a turn in Initiative order." A dead creature is no longer a
    participant: once the hero kills the weak foe, no later turn starts for it."""
    handle, live = start(
        [pc(attack_bonus=30)],
        seed=22,
        encounter=[
            foe(
                entity_id="mon:weak",
                name="Weak",
                initiative=15,
                hp_current=1,
                hp_max=1,
                zone_id=cell_id(1, 0),
            ),
            foe(
                entity_id="mon:strong",
                name="Strong",
                initiative=12,
                hp_current=50,
                hp_max=50,
                zone_id=cell_id(5, 5),
            ),
        ],
    )
    live.rng = _Dice(d20=15, pips=4)
    act(handle, "char:hero", intent_type="attack", weapon_id="longsword", target_id="mon:weak")
    assert any(e.target_id == "mon:weak" for e in events(live, Death))
    _pass_turns(handle, live, until_round=3)
    turns = [e.actor_id for e in events(live, TurnStarted)]
    assert "mon:weak" not in turns


# ── Surprise ────────────────────────────────────────────────────────────────


def test_a_surprised_creature_still_takes_its_first_turn() -> None:
    """SRD 5.2.1 "Surprise": "If a combatant is surprised by combat starting, that
    combatant has Disadvantage on their Initiative roll." That is the whole
    effect: a surprised hero with the highest Initiative takes a normal first turn
    with its Action, Bonus Action and Reaction."""
    handle, live = start([pc(initiative=20, is_surprised=True)], seed=23, encounter=[foe()])
    hero = combatant(live)
    assert live.current_actor_id == "char:hero"
    assert (hero.action_available, hero.bonus_action_available, hero.reaction_available) == (
        True,
        True,
        True,
    )
    act(handle, "char:hero", intent_type="attack", weapon_id="longsword", target_id="mon:foe")
    assert events(live, AttackRolled)


# ── Two-weapon fighting ─────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: Two-weapon fighting compares weapons by slug",
)
def test_two_handaxes_can_be_used_for_the_extra_attack() -> None:
    """SRD 5.2.1 "Light": "That extra attack must be made with a different Light
    weapon". A second Handaxe is a different weapon from the first, so a hero
    carrying two swings once with each and the second swing is the Bonus Action
    extra attack."""
    handle, live = start([pc(attack_bonus=7)], seed=24, encounter=[foe()])
    live.rng = _Dice(d20=15, pips=4)
    act(handle, "char:hero", intent_type="attack", weapon_id="handaxe", target_id="mon:foe")
    act(handle, "char:hero", intent_type="attack", weapon_id="handaxe", target_id="mon:foe")
    assert not [e for e in events(live, AttackFailed) if e.reason == "no_action_economy"]
    hero = combatant(live)
    assert hero.offhand_attack_spent is True
    assert hero.bonus_action_available is False


def test_the_extra_attack_needs_the_attack_action_first() -> None:
    """SRD 5.2.1 "Light": "When you take the Attack action on your turn and attack
    with a Light weapon, you can make one extra attack as a Bonus Action later on
    the same turn." The first swing of the turn is the Attack action, not the
    extra attack: it leaves the Bonus Action and the off-hand unspent."""
    handle, live = start([pc(attack_bonus=7)], seed=24, encounter=[foe()])
    live.rng = _Dice(d20=15, pips=4)
    act(handle, "char:hero", intent_type="attack", weapon_id="dagger", target_id="mon:foe")
    hero = combatant(live)
    assert hero.offhand_attack_spent is False
    assert hero.bonus_action_available is True
    assert hero.light_weapon_swing_slug == "dagger"


# ── Cover ───────────────────────────────────────────────────────────────────


def test_only_the_most_protective_degree_of_cover_applies() -> None:
    """SRD 5.2.1 "Cover": "If a target is behind multiple sources of cover, only
    the most protective degree of cover applies; the degrees aren't added
    together." Half Cover and Three-Quarters Cover on the line give +5, so an
    AC 10 target is AC 15: a total of 14 misses and 15 hits, never AC 17."""
    cover = {cell_id(1, 0): "half", cell_id(2, 0): "three_quarters"}

    def swing(d20: int) -> AttackRolled:
        handle, live = _grid_start(
            [pc("char:archer", attack_bonus=0, zone_id=cell_id(0, 0))],
            [foe(ac=10, zone_id=cell_id(3, 0))],
            cover_cells=cover,
        )
        live.rng = _Dice(d20=d20)
        act(handle, "char:archer", intent_type="attack", weapon_id="longbow", target_id="mon:foe")
        [rolled] = events(live, AttackRolled)
        return rolled

    modifier = swing(10).modifier
    assert modifier is not None
    miss, hit = swing(14 - modifier), swing(15 - modifier)
    assert (miss.roll_total, miss.is_hit) == (14, False)
    assert (hit.roll_total, hit.is_hit) == (15, True)


# ── Flanking ────────────────────────────────────────────────────────────────


def test_an_ally_on_the_far_side_of_the_target_grants_no_advantage() -> None:
    """none (the SRD has no Flanking rule): SRD 5.2.1 contains no Flanking rule
    (searching "flank" finds nothing), so an ally on the opposite side of the
    target gives no Advantage: the attack is a single plain d20, identical to the
    same attack with no ally."""
    rolls = []
    for ally in (False, True):
        party = [pc(attack_bonus=5, initiative=20)]
        if ally:
            party.append(pc("char:ally", initiative=10, zone_id=cell_id(2, 0)))
        handle, live = start(party, seed=25, encounter=[foe()])
        act(handle, "char:hero", intent_type="attack", weapon_id="longsword", target_id="mon:foe")
        [rolled] = events(live, AttackRolled)
        assert (rolled.advantage, rolled.sources) == ("normal", [])
        rolls.append((rolled.natural, rolled.roll_total))
    assert rolls[0] == rolls[1]
