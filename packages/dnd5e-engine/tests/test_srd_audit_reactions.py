"""Pins the SRD 5.2.1 rules of the Reactions rows of
``docs/srd-5.2.1-rules-audit.md`` that no other test states: the 60 foot limits
and sight requirements of Hellish Rebuke, Counterspell and Feather Fall, the
half damage of a saved Hellish Rebuke, the 600 foot limit of Feather Fall, the
timing of an Opportunity Attack and the trigger of Shield. Where the engine
differs from the SRD, the test states the SRD outcome and is marked
``xfail(strict=True)`` with the title of its ``BACKLOG.md`` entry, so fixing the
engine turns it into an unexpected pass that has to be unmarked.
"""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace
from typing import Any

import pytest

from dnd5e_engine import ActiveEffect, IntentRejectedError, PlayerIntent, cell_id
from dnd5e_engine import combat_state as combat_state_module
from dnd5e_engine.combat_state import (
    CombatState,
    CombatStep,
    begin_combat,
    resolve_intent,
    resolve_monster_turn,
)
from dnd5e_engine.events import (
    ActorMoved,
    AttackRolled,
    CastFailed,
    CreatureFell,
    DamageApplied,
    ReactionTriggered,
    SaveRolled,
    SpellCast,
    TurnEnded,
)
from dnd5e_engine.reactions import ReactionPrompt
from dnd5e_engine.spatial import GridTopology
from dnd5e_engine.specs import GridScene, PartyMemberSpec
from tests.e2e.harness import cell, grid_scene
from tests.test_falling import (
    TARGET,
    _brute,
    _fall_damage,
    _ledge,
    _prone,
    _shove,
    _target,
)
from tests.test_falling import (
    _foe as _fall_foe,
)
from tests.test_falling import (
    _wizard as _fall_wizard,
)
from tests.test_reactions import (
    _HIT,
    FOE,
    WIZARD,
    _mage,
    _monster,
    _monsters_turn,
    _only,
    _resume,
    _turn,
    _warlock,
    _wizard,
)


def _of[T](step: CombatStep, kind: type[T]) -> list[T]:
    return [e for e in step.events if isinstance(e, kind)]


def _keys(asked: list[ReactionPrompt]) -> list[str]:
    return [p.reaction_key for p in asked]


def _recording(answer: str = "pass") -> tuple[list[ReactionPrompt], Any]:
    """A policy that records every prompt and gives ``answer`` to each."""
    asked: list[ReactionPrompt] = []

    def policy(prompt: ReactionPrompt) -> str:
        asked.append(prompt)
        return answer

    return asked, policy


def _invisible(entity_id: str) -> ActiveEffect:
    return ActiveEffect(
        id="effect:invisible", name="Invisible", origin="test", target_id=entity_id,
        statuses={"invisible"},
    )  # fmt: skip


def _mage_turn(reactor: PartyMemberSpec, *, column: int = 5, unseen: bool = False) -> CombatState:
    """The Mage's turn, ``column`` squares from the reactor, who has passed."""
    opened = asyncio.run(
        begin_combat(
            session_id="audit-reactions",
            party=[reactor],
            encounter=[_mage(zone_id=cell_id(column, 0))],
            grid_scene=GridScene(width=20, height=10),
            seed=1,
            active_effects=[_invisible(FOE)] if unseen else [],
        )
    )
    return asyncio.run(
        resolve_intent(
            opened.state,
            actor_id=reactor.entity_id,
            intent=PlayerIntent(intent_type="pass"),
            seed=1,
        )
    ).state


# ── Hellish Rebuke ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("column", "offered"), [(12, True), (13, False)])
def test_hellish_rebuke_reaches_60_feet_and_not_65(column: int, offered: bool) -> None:
    """SRD 5.2.1 "Hellish Rebuke": "Casting Time: Reaction, which you take in
    response to taking damage from a creature that you can see within 60 feet
    of yourself". The Mage's Fireball lands from 60 feet (12 squares) and from
    65 feet; only the first is answered."""
    state = _mage_turn(
        _warlock(spells_known=["hellish-rebuke"], hp_current=90, hp_max=90), column=column
    )
    asked, policy = _recording()
    step = _turn(state, 1, reactions=policy)
    assert [e.target_id for e in _of(step, DamageApplied)] == [WIZARD]
    assert (_keys(asked) == ["hellish-rebuke"]) is offered
    assert (not offered) == (asked == [])


def test_hellish_rebuke_is_not_offered_against_an_attacker_the_reactor_cannot_see() -> None:
    """SRD 5.2.1 "Hellish Rebuke": "in response to taking damage from a
    creature that you can see within 60 feet of yourself". The Mage is
    Invisible, 25 feet away; its Fireball damages the warlock and nothing is
    offered."""
    warlock = _warlock(spells_known=["hellish-rebuke"], hp_current=90, hp_max=90)
    asked, policy = _recording()
    step = _turn(_mage_turn(warlock, unseen=True), 1, reactions=policy)
    assert [e.target_id for e in _of(step, DamageApplied)] == [WIZARD]
    assert asked == []


class _ForcedAfterReseed(random.Random):
    """A seeded generator that, once reseeded with a seed in ``forced``, lands
    every d20 on that entry's first number and shows its second on every other
    die, keeping the dice asked for. A resume reseeds the replayed generator
    with its own seed, so only the dice after the decision are forced."""

    def __init__(self, seed: int, forced: dict[int, tuple[int, int]]) -> None:
        self.forced = forced
        self.pins: tuple[int, int] | None = None
        self.dice: list[tuple[int, int]] = []
        super().__init__(seed)

    def seed(self, a: Any = None, version: int = 2) -> None:
        self.pins = getattr(self, "forced", {}).get(a)
        if self.pins is None:
            super().seed(a, version)

    def randint(self, a: int, b: int) -> int:
        if self.pins is None:
            return super().randint(a, b)
        if (a, b) == (1, 20):
            return self.pins[0]
        self.dice.append((a, b))
        return self.pins[1]


def test_a_saved_hellish_rebuke_deals_half_of_its_2d10_fire_damage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SRD 5.2.1 "Hellish Rebuke": "It makes a Dexterity saving throw, taking
    2d10 Fire damage on a failed save or half as much damage on a successful
    one." Two resumes give the goblin a failed (d20 of 1) and a passed (d20 of
    20) save against the same dice, every die but the d20 showing 7."""
    state = _monsters_turn([_warlock()], [_monster("goblin-warrior")])
    paused = _turn(state, _HIT, reactions=_only("hellish-rebuke", "suspend")).suspension
    assert paused is not None
    made: list[_ForcedAfterReseed] = []

    def factory(seed: int) -> random.Random:
        made.append(_ForcedAfterReseed(seed, {901: (1, 7), 902: (20, 7)}))
        return made[-1]

    monkeypatch.setattr(combat_state_module, "random", SimpleNamespace(Random=factory))
    amounts: dict[int, int] = {}
    for seed, succeeded in ((901, False), (902, True)):
        step = _resume(state, paused, "use", seed=seed)
        [save] = _of(step, SaveRolled)
        assert (save.target_id, save.ability, save.succeeded) == (FOE, "dex", succeeded)
        [fire] = [e for e in _of(step, DamageApplied) if e.target_id == FOE]
        assert fire.damage_type == "fire"
        amounts[seed] = fire.amount
        assert made[-1].dice == [(1, 10), (1, 10)]
    assert amounts == {901: 14, 902: 7}


# ── Counterspell ────────────────────────────────────────────────────────────


def test_counterspell_is_offered_at_exactly_60_feet() -> None:
    """SRD 5.2.1 "Counterspell": "Casting Time: Reaction, which you take when
    you see a creature within 60 feet of yourself casting a spell with Verbal,
    Somatic, or Material components". The Mage 60 feet (12 squares) away is
    in reach."""
    state = _mage_turn(_warlock(), column=12)
    step = _turn(state, 1, reactions=_only("counterspell", "suspend"))
    assert step.suspension is not None
    assert step.suspension.prompt.reaction_key == "counterspell"
    assert step.suspension.prompt.triggering_actor_id == FOE


def test_counterspell_is_not_offered_for_a_caster_the_reactor_cannot_see() -> None:
    """SRD 5.2.1 "Counterspell": "when you see a creature within 60 feet of
    yourself casting a spell". An Invisible Mage 25 feet away casts its
    Fireball unanswered."""
    asked, policy = _recording("use")
    step = _turn(_mage_turn(_warlock(), unseen=True), 1, reactions=policy)
    assert "counterspell" not in _keys(asked)
    assert [e.spell_id for e in _of(step, SpellCast)] == ["fireball"]
    assert not _of(step, CastFailed)


# ── Shield ──────────────────────────────────────────────────────────────────


def test_shield_is_not_offered_against_a_saving_throw_spell() -> None:
    """SRD 5.2.1 "Shield": "Casting Time: Reaction, which you take when you are
    hit by an attack roll or targeted by the Magic Missile spell". A Fireball
    is a saving throw, not an attack roll, so the wizard is not asked."""
    wizard = _wizard(hp_current=200, hp_max=200)
    asked, policy = _recording("use")
    step = _turn(_mage_turn(wizard), 1, reactions=policy)
    assert [e.spell_id for e in _of(step, SpellCast)] == ["fireball"]
    assert [e.target_id for e in _of(step, DamageApplied)] == [WIZARD]
    assert "shield" not in _keys(asked)
    assert not _of(step, ReactionTriggered)


# ── Feather Fall ────────────────────────────────────────────────────────────


def _fall_state(
    drop_ft: int, *party: PartyMemberSpec, effects: list[ActiveEffect] | None = None
) -> CombatState:
    return asyncio.run(
        begin_combat(
            session_id="audit-falling",
            party=[_brute(), *party],
            encounter=[_fall_foe()],
            grid_scene=_ledge(drop_ft),
            seed=1,
            active_effects=effects or [],
        )
    ).state


def test_feather_fall_is_not_offered_for_a_faller_the_reactor_cannot_see() -> None:
    """SRD 5.2.1 "Feather Fall": "Casting Time: Reaction, which you take when
    you or a creature you can see within 60 feet of you falls". The wizard,
    4 squares away, cannot see the Invisible creature that falls 30 feet."""
    seen = _fall_state(30, _target(), _fall_wizard())
    assert _shove(seen, reactions=lambda _p: "suspend").suspension is not None
    asked, policy = _recording("use")
    unseen = _fall_state(30, _target(), _fall_wizard(), effects=[_invisible(TARGET)])
    step = _shove(unseen, reactions=policy)
    assert _of(step, CreatureFell)
    assert asked == []
    assert len(_fall_damage(step)) == 1


def test_feather_fall_covers_600_feet_and_not_the_610th() -> None:
    """SRD 5.2.1 "Feather Fall": "A falling creature's rate of descent slows to
    60 feet per round until the spell ends" and the spell lasts "1 minute"
    (10 rounds), so 600 feet are slowed. A 600 foot fall deals nothing and
    leaves the creature standing; a 610 foot fall deals 1d6 for the last
    10 feet (SRD 5.2.1 "Falling": "1d6 Bludgeoning damage ... for every
    10 feet it fell") and leaves it Prone."""
    exactly = _shove(_fall_state(600, _target(), _fall_wizard()), reactions=lambda _p: "use")
    assert [e.spell_id for e in _of(exactly, SpellCast)] == ["feather-fall"]
    assert [e.distance_ft for e in _of(exactly, CreatureFell)] == [600]
    assert not _fall_damage(exactly)
    assert not _prone(exactly)

    beyond = _shove(_fall_state(610, _target(), _fall_wizard()), reactions=lambda _p: "use")
    assert [e.spell_id for e in _of(beyond, SpellCast)] == ["feather-fall"]
    [damage] = _fall_damage(beyond)
    assert 1 <= damage.amount <= 6
    assert _prone(beyond)


# ── Opportunity Attacks ─────────────────────────────────────────────────────


def test_the_opportunity_attack_comes_right_before_the_mover_leaves_reach() -> None:
    """SRD 5.2.1 "Opportunity Attacks": "The attack occurs right before it
    leaves your reach." The hero walks from the goblin's side to three squares
    away: the attack is rolled after a step that still ends in the goblin's
    reach and before the step that ends outside it."""
    hero = PartyMemberSpec(
        entity_id="char:hero", name="Hero", initiative=20, hp_current=30, hp_max=30, ac=12,
        zone_id=cell(0, 0),
    )  # fmt: skip
    goblin = _monster("goblin-warrior", zone_id=cell(1, 0), ac=15)
    scene = grid_scene()
    opened = asyncio.run(
        begin_combat(
            session_id="audit-oa-order", party=[hero], encounter=[goblin], grid_scene=scene, seed=1
        )
    )
    step = asyncio.run(
        resolve_intent(
            opened.state,
            actor_id="char:hero",
            intent=PlayerIntent(intent_type="move", target_zone_id=cell(0, 3)),
            seed=1,
        )
    )
    grid = GridTopology(scene)
    log = step.events
    [attack] = [e for e in _of(step, AttackRolled) if e.is_opportunity_attack]
    assert attack.attacker_id == FOE
    at = log.index(attack)
    moves = [e for e in _of(step, ActorMoved) if e.actor_id == "char:hero"]
    before = [m for m in moves if log.index(m) < at]
    after = [m for m in moves if log.index(m) > at]
    assert before
    assert after
    inside = grid.distance_ft(before[-1].to_zone, cell(1, 0))
    outside = grid.distance_ft(after[0].to_zone, cell(1, 0))
    assert inside is not None
    assert outside is not None
    assert inside <= 5 < outside
    assert after[0].from_zone == before[-1].to_zone


# ── Ready (BACKLOG: "Ready costs and lasts nothing") ────────────────────────

READIER, ATTACKER, BYSTANDER = "pc:t", "pc:a", "pc:b"


def _caster(entity_id: str, initiative: int, column: int, **fields: Any) -> PartyMemberSpec:
    base: dict[str, Any] = {
        "entity_id": entity_id,
        "name": entity_id,
        "initiative": initiative,
        "hp_current": 40,
        "hp_max": 40,
        "ac": 10,
        "class_slug": "wizard",
        "character_level": 5,
        "intelligence": 16,
        "spells_known": ["fire-bolt"],
        "spell_slots": {1: 2},
        "zone_id": cell_id(column, 0),
    }
    return PartyMemberSpec(**(base | fields))


def _open(party: list[PartyMemberSpec], seed: int = 1) -> CombatState:
    """The party against one distant goblin; the highest initiative acts first."""
    return asyncio.run(
        begin_combat(
            session_id="audit-ready",
            party=party,
            encounter=[_monster("goblin-warrior", zone_id=cell_id(9, 9))],
            grid_scene=GridScene(width=10, height=10),
            seed=seed,
        )
    ).state


def _do(state: CombatState, actor_id: str, seed: int = 1, **intent: Any) -> CombatStep:
    return asyncio.run(
        resolve_intent(state, actor_id=actor_id, intent=PlayerIntent(**intent), seed=seed)
    )


def _ready(state: CombatState, spell_id: str = "shield", **intent: Any) -> CombatStep:
    return _do(
        state,
        READIER,
        intent_type="ready",
        spell_id=spell_id,
        slot_level=1,
        reaction_trigger="hit_by_attack",
        **intent,
    )


def _refused(attempt: Any) -> bool:
    """Whether ``attempt`` (a call returning a step) refused to ready anything:
    it raised, or it armed nothing and left the readier its Action."""
    try:
        step = attempt()
    except IntentRejectedError:
        return True
    readier = next(c for c in step.state.combatants if c.entity_id == READIER)
    return not step.state.readied and readier.action_available


def _readier(**fields: Any) -> PartyMemberSpec:
    return _caster(READIER, 30, 0, **({"spells_known": ["shield"]} | fields))


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="BACKLOG: Ready costs and lasts nothing"
)
def test_readying_a_spell_expends_the_slot_when_it_is_readied() -> None:
    """SRD 5.2.1 "Ready": "When you Ready a spell, you cast it as normal
    (expending any resources used to cast it) but hold its energy, which you
    release with your Reaction when the trigger occurs." Readying Shield at
    level 1 leaves one of the two level 1 slots."""
    state = _open([_readier(), _caster(ATTACKER, 25, 3)])
    step = _ready(state)
    assert step.state.spell_slots[READIER][1] == 1


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="BACKLOG: Ready costs and lasts nothing"
)
def test_readying_a_spell_with_no_slot_is_refused() -> None:
    """SRD 5.2.1 "Ready": "When you Ready a spell, you cast it as normal
    (expending any resources used to cast it)". A wizard with no level 1 slot
    cannot cast Shield, so cannot ready it."""
    state = _open([_readier(spell_slots={1: 0}), _caster(ATTACKER, 25, 3)])
    assert _refused(lambda: _ready(state))


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="BACKLOG: Ready costs and lasts nothing"
)
def test_a_spell_with_a_bonus_action_casting_time_cannot_be_readied() -> None:
    """SRD 5.2.1 "Ready": "To be readied, a spell must have a casting time of
    an action". Healing Word is cast as a Bonus Action."""
    state = _open(
        [_readier(spells_known=["healing-word"], class_slug="cleric", wisdom=16),
         _caster(ATTACKER, 25, 3)]
    )  # fmt: skip
    assert _refused(lambda: _ready(state, "healing-word"))


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="BACKLOG: Ready costs and lasts nothing"
)
def test_a_readied_spell_lapses_at_the_start_of_the_readiers_next_turn() -> None:
    """SRD 5.2.1 "Ready": "which lets you act by taking a Reaction before the
    start of your next turn" and "holding on to the spell's magic requires
    Concentration, which you can maintain up to the start of your next turn".
    Once the readier's next turn has started, nothing is held, and a later hit
    on the readier draws no readied Shield."""
    state = _open([_readier(), _caster(ATTACKER, 25, 3)])
    state = _ready(state).state
    state = _do(state, ATTACKER, intent_type="pass").state
    state = asyncio.run(resolve_monster_turn(state, seed=1)).state
    assert state.active_combatant == READIER  # round 2: the readier's next turn has started
    assert state.readied == []
    state = _do(state, READIER, intent_type="pass").state
    firebolt = _do(
        state, ATTACKER, seed=3, intent_type="cast_spell", spell_id="fire-bolt", target_id=READIER
    )
    assert not _of(firebolt, ReactionTriggered)


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="BACKLOG: Ready costs and lasts nothing"
)
def test_a_ready_with_no_spell_and_no_trigger_is_refused_not_silently_spent() -> None:
    """SRD 5.2.1 "Ready": "First, you decide what perceivable circumstance will
    trigger your Reaction. Then, you choose the action you will take in
    response to that trigger". A ``ready`` that names neither arms nothing, so
    it must not spend the Action: it is refused."""
    state = _open([_readier(), _caster(ATTACKER, 25, 3)])
    assert _refused(lambda: _do(state, READIER, intent_type="ready"))


# ── Shield and Magic Missile ────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: An active Shield does not stop a later Magic Missile",
)
def test_a_shield_already_up_stops_a_later_magic_missile() -> None:
    """SRD 5.2.1 "Shield": "Until the start of your next turn, you have a +5
    bonus to AC, including against the triggering attack, and you take no
    damage from Magic Missile." The target casts Shield against a Fire Bolt,
    and a Magic Missile cast at it later in the same round deals nothing."""
    target = _caster(READIER, 5, 0, spells_known=["shield"])
    missile = _caster(BYSTANDER, 20, 1, spells_known=["magic-missile"])
    state = _open([_caster(ATTACKER, 25, 3), missile, target])
    bolt = PlayerIntent(intent_type="cast_spell", spell_id="fire-bolt", target_id=READIER)
    shielded = asyncio.run(
        resolve_intent(
            state, actor_id=ATTACKER, intent=bolt, seed=3, reactions=_only("shield", "use")
        )
    )
    assert [e.spell_id for e in _of(shielded, SpellCast)] == ["fire-bolt", "shield"]
    assert shielded.state.active_combatant == BYSTANDER
    step = _do(
        shielded.state,
        BYSTANDER,
        seed=4,
        intent_type="cast_spell",
        spell_id="magic-missile",
        slot_level=1,
        target_id=READIER,
    )
    force = [e.amount for e in _of(step, DamageApplied) if e.target_id == READIER]
    assert force
    assert sum(force) == 0


# ── Casting from an item, and a countered Bonus Action spell ────────────────


def _wand_user() -> PartyMemberSpec:
    return _caster(
        ATTACKER, 25, 3, spells_known=[], spell_slots={}, equipment=["wand-of-magic-missiles"]
    )


def _use_wand(state: CombatState, reactions: Any) -> CombatStep:
    wand = PlayerIntent(intent_type="use_item", item_id="wand-of-magic-missiles", target_id=READIER)
    return asyncio.run(
        resolve_intent(state, actor_id=ATTACKER, intent=wand, seed=3, reactions=reactions)
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: An item cast offers no Shield or Counterspell",
)
def test_a_wand_cast_magic_missile_offers_shield_to_its_target() -> None:
    """SRD 5.2.1 "Shield": "Casting Time: Reaction, which you take when you are
    hit by an attack roll or targeted by the Magic Missile spell". A Wand of
    Magic Missiles casts that spell, so its target may cast Shield and take no
    damage."""
    state = _open([_wand_user(), _caster(READIER, 5, 0, spells_known=["shield"])])
    asked, policy = _recording("use")
    step = _use_wand(state, policy)
    assert _of(step, DamageApplied)  # the wand did cast Magic Missile at the target
    assert [p for p in asked if p.reaction_key == "shield" and "Magic Missile" in p.trigger]
    assert sum(e.amount for e in _of(step, DamageApplied) if e.target_id == READIER) == 0


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: An item cast offers no Shield or Counterspell",
)
def test_a_spell_cast_from_a_wand_can_be_countered() -> None:
    """SRD 5.2.1 "Counterspell": "Casting Time: Reaction, which you take when
    you see a creature within 60 feet of yourself casting a spell with Verbal,
    Somatic, or Material components". A creature that sees a Wand of Magic
    Missiles used 15 feet away is asked whether to counter the spell it casts."""
    state = _open(
        [_wand_user(), _warlock(entity_id=READIER, initiative=5, spells_known=["counterspell"])]
    )
    asked, policy = _recording("pass")
    step = _use_wand(state, policy)
    assert _of(step, DamageApplied)  # the wand did cast Magic Missile at the target
    assert "counterspell" in _keys(asked)


def _cleric_and_warlock() -> CombatState:
    cleric = _caster(
        ATTACKER, 25, 2,
        class_slug="cleric", wisdom=16, spells_known=["healing-word"],
    )  # fmt: skip
    warlock = _warlock(entity_id=READIER, initiative=5, spells_known=["counterspell"])
    return _open([cleric, warlock])


def _healing_word() -> PlayerIntent:
    return PlayerIntent(
        intent_type="cast_spell", spell_id="healing-word", slot_level=1, target_id=ATTACKER
    )


def test_an_uncountered_bonus_action_spell_leaves_the_caster_its_turn() -> None:
    """Control for the test below. SRD 5.2.1 "Counterspell": "the action, Bonus
    Action, or Reaction used to cast it is wasted". Healing Word, answered with
    "pass", is cast with the Bonus Action and the turn goes on."""
    step = asyncio.run(
        resolve_intent(
            _cleric_and_warlock(), actor_id=ATTACKER, intent=_healing_word(), seed=4,
            reactions=_only("counterspell", "pass"),
        )
    )  # fmt: skip
    assert [e.spell_id for e in _of(step, SpellCast)] == ["healing-word"]
    assert step.state.active_combatant == ATTACKER


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BACKLOG: A countered Bonus Action spell ends the whole turn",
)
def test_countering_a_bonus_action_spell_wastes_only_the_bonus_action() -> None:
    """SRD 5.2.1 "Counterspell": "On a failed save, the spell dissipates with no
    effect, and the action, Bonus Action, or Reaction used to cast it is
    wasted." Only the Bonus Action is wasted: the countered cleric still has
    its Action and its turn."""
    step = asyncio.run(
        resolve_intent(
            _cleric_and_warlock(), actor_id=ATTACKER, intent=_healing_word(), seed=4,
            reactions=_only("counterspell", "use"),
        )
    )  # fmt: skip
    assert [(e.actor_id, e.reason) for e in _of(step, CastFailed)] == [(ATTACKER, "countered")]
    assert step.state.active_combatant == ATTACKER
    assert not [e for e in _of(step, TurnEnded) if e.actor_id == ATTACKER]
    cleric = next(c for c in step.state.combatants if c.entity_id == ATTACKER)
    assert cleric.bonus_action_available is False
    assert cleric.action_available is True
