"""Reactions taken at their SRD triggers, and a resolution that pauses for one
(spec 02a, acceptance criteria 10, 11 and 12; contract 03, "Reactions").

SRD 5.2.1 Shield: "Casting Time: Reaction, which you take when you are hit by an
attack roll or targeted by the Magic Missile spell" and "Until the start of your
next turn, you have a +5 bonus to AC, including against the triggering attack,
and you take no damage from Magic Missile."

Contract 03: a suspension "commits only the pending_reaction row, holding the
engine's continuation and a digest of the state"; "resume re-checks the digest
and rejects with state_changed if the state moved. It then completes the
resolution"; "A resumed resolution can suspend again for a second reaction."
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dnd5e_engine import (
    IntentRejectedError,
    PlayerIntent,
    advance_monster_turn,
    cell_id,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.combat_state import (
    CombatState,
    CombatStep,
    CombatSuspension,
    begin_combat,
    resolve_intent,
    resolve_monster_turn,
    resume,
    state_digest,
)
from dnd5e_engine.events import (
    AttackRolled,
    DamageApplied,
    EffectApplied,
    EffectExpired,
    ReactionTriggered,
    SpellCast,
)
from dnd5e_engine.reactions import ReactionChoice, ReactionPrompt
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec

WIZARD = "pc:1"
FOE = "cre:1"


def _wizard(**fields: Any) -> PartyMemberSpec:
    """Spike gate 3's wizard: level 1, AC 12, two level 1 slots, Shield known."""
    base: dict[str, Any] = {
        "entity_id": WIZARD,
        "name": "Wizard",
        "initiative": 20,
        "hp_current": 20,
        "hp_max": 20,
        "ac": 12,
        "intelligence": 16,
        "zone_id": cell_id(0, 0),
        "spell_slots": {1: 2},
        "spells_known": ["shield"],
        "class_slug": "wizard",
        "character_level": 1,
    }
    return PartyMemberSpec(**(base | fields))


def _foe(slug: str = "goblin-warrior") -> EncounterMemberSpec:
    return EncounterMemberSpec(
        entity_id=FOE,
        entity_type="Monster",
        name="Foe",
        initiative=1,
        hp_current=40,
        hp_max=40,
        ac=15,
        attack_bonus=4,
        damage_dice="1d6+2",
        zone_id=cell_id(1, 0),
        monster_template_slug=slug,
    )


def _foes_turn(slug: str = "goblin-warrior", **wizard: Any) -> CombatState:
    """The foe's turn, the wizard having passed its own."""
    opened = asyncio.run(
        begin_combat(
            session_id="reactions",
            party=[_wizard(**wizard)],
            encounter=[_foe(slug)],
            grid_scene=GridScene(width=6, height=6),
            seed=1,
        )
    )
    passed = asyncio.run(
        resolve_intent(
            opened.state, actor_id=WIZARD, intent=PlayerIntent(intent_type="pass"), seed=1
        )
    )
    return passed.state


def _suspend(_prompt: ReactionPrompt) -> str:
    return "suspend"


def _turn(state: CombatState, seed: int, **kwargs: Any) -> CombatStep:
    return asyncio.run(resolve_monster_turn(state, seed=seed, **kwargs))


def _resume(
    state: CombatState, suspension: CombatSuspension, decision: str, seed: int, **kwargs: Any
) -> CombatStep:
    return asyncio.run(resume(state, suspension, decision, seed, **kwargs))  # type: ignore[arg-type]


def _of[T](step: CombatStep, kind: type[T]) -> list[T]:
    return [e for e in step.events if isinstance(e, kind)]


# The goblin warrior's attack: seed 3 hits the wizard (8 + 4 = 12 against AC 12),
# seed 1 misses.
_HIT, _MISS = 3, 1


# ── criterion 10: suspend, resume, suspend again ────────────────────────────


def test_the_call_suspends_after_an_attack_roll_that_would_hit() -> None:
    """Nothing after the hit resolves until the player answers: no damage, and
    the state is the one the call started from."""
    state = _foes_turn()
    step = _turn(state, _HIT, reactions=_suspend)
    assert step.suspension is not None
    assert step.suspension.prompt == ReactionPrompt(
        reactor_id=WIZARD,
        reaction_key="shield",
        trigger="cre:1 hits pc:1 with an attack roll of 12",
        triggering_actor_id=FOE,
        attack_total=12,
        attack_natural=8,
    )
    assert not _of(step, DamageApplied)
    assert not _of(step, AttackRolled)  # the roll is told in the prompt, then emitted once
    assert step.state == state
    assert step.suspension.continuation.state_digest == state_digest(state)


def test_the_continuation_is_plain_json() -> None:
    step = _turn(_foes_turn(), _HIT, reactions=_suspend)
    assert step.suspension is not None
    text = step.suspension.model_dump_json()
    assert CombatSuspension.model_validate_json(text) == step.suspension


def test_resume_with_use_casts_shield_as_a_reaction() -> None:
    """ "+5 bonus to AC, including against the triggering attack": 12 now misses
    AC 17. Shield spends the Reaction and a level 1 slot, and the wizard spent
    no Action on it."""
    state = _foes_turn()
    paused = _turn(state, _HIT, reactions=_suspend).suspension
    assert paused is not None
    step = _resume(state, paused, "use", seed=9)
    assert step.suspension is None
    kinds = [type(e).__name__ for e in step.events]
    assert kinds.index("ReactionTriggered") < kinds.index("AttackRolled")
    assert [(e.actor_id, e.reaction_name) for e in _of(step, ReactionTriggered)] == [
        (WIZARD, "shield")
    ]
    assert [(e.actor_id, e.spell_id, e.slot_level) for e in _of(step, SpellCast)] == [
        (WIZARD, "shield", 1)
    ]
    [rolled] = _of(step, AttackRolled)
    assert (rolled.roll_total, rolled.is_hit) == (12, False)
    assert not _of(step, DamageApplied)
    assert step.state.spell_slots[WIZARD] == {1: 1}
    # The call ends the goblin's turn and starts the wizard's, which refreshes
    # its Reaction (and ends Shield, below).
    assert step.state.active_combatant == WIZARD


def test_shield_lasts_until_the_start_of_the_wizards_next_turn() -> None:
    state = _foes_turn()
    paused = _turn(state, _HIT, reactions=_suspend).suspension
    assert paused is not None
    step = _resume(state, paused, "use", seed=9)
    # The goblin's turn ended and the wizard's began in the same call.
    assert [(e.target_id, e.reason) for e in _of(step, EffectExpired)] == [(WIZARD, "duration")]
    assert step.state.effects[WIZARD] == []


def test_resume_with_pass_lets_the_hit_land() -> None:
    state = _foes_turn()
    paused = _turn(state, _HIT, reactions=_suspend).suspension
    assert paused is not None
    step = _resume(state, paused, "pass", seed=9)
    assert step.suspension is None
    [rolled] = _of(step, AttackRolled)
    assert rolled.is_hit
    assert [e.target_id for e in _of(step, DamageApplied)] == [WIZARD]
    assert not _of(step, ReactionTriggered)
    assert step.state.spell_slots[WIZARD] == {1: 2}


def test_resume_after_a_state_change_rejects() -> None:
    """Contract 03: "resume re-checks the digest and rejects with state_changed
    if the state moved"."""
    state = _foes_turn()
    paused = _turn(state, _HIT, reactions=_suspend).suspension
    assert paused is not None
    moved = state.model_copy(update={"hp": state.hp | {WIZARD: 3}})
    with pytest.raises(IntentRejectedError) as rejected:
        _resume(moved, paused, "use", seed=9)
    assert rejected.value.reason == "state_changed"


def test_a_resumed_call_can_suspend_again() -> None:
    """The bandit captain's Multiattack hits twice (seed 3, then the resume's
    seed 1): passing on the first hit pauses again at the second, and the
    continuation carries the first answer."""
    state = _foes_turn("bandit-captain")
    first = _turn(state, 3, reactions=_suspend).suspension
    assert first is not None
    second = _resume(state, first, "pass", seed=1, reactions=_suspend)
    assert second.suspension is not None
    assert second.suspension.continuation.choices == [
        ReactionChoice(reactor_id=WIZARD, reaction_key="shield", decision="pass", seed=1)
    ]
    assert second.state == state
    done = _resume(state, second.suspension, "use", seed=2)
    assert done.suspension is None
    # The first hit (12) landed; Shield's +5 is not enough for the second: 23
    # still hits AC 17.
    assert [(e.roll_total, e.is_hit) for e in _of(done, AttackRolled)][-2:] == [
        (12, True),
        (23, True),
    ]
    assert [e.reaction_name for e in _of(done, ReactionTriggered)] == ["shield"]


def test_a_resume_is_deterministic() -> None:
    """The same state, suspension, decision and seed give byte-identical
    output, also from the suspension's JSON."""
    state = _foes_turn()
    paused = _turn(state, _HIT, reactions=_suspend).suspension
    assert paused is not None
    first = _resume(state, paused, "pass", seed=9)
    again = _resume(
        CombatState.model_validate_json(state.model_dump_json()),
        CombatSuspension.model_validate_json(paused.model_dump_json()),
        "pass",
        seed=9,
    )
    assert again.model_dump_json() == first.model_dump_json()


def test_the_dice_after_the_decision_come_from_the_resume_seed() -> None:
    """The replay reproduces the call up to the pause; the decision's own seed
    draws everything after it, so the damage roll follows the resume seed."""
    state = _foes_turn()
    paused = _turn(state, _HIT, reactions=_suspend).suspension
    assert paused is not None
    damage = {
        _of(_resume(state, paused, "pass", seed=seed), DamageApplied)[0].amount
        for seed in range(1, 25)
    }
    assert len(damage) > 1


def test_a_finished_call_matches_the_unpaused_one() -> None:
    """Answering "pass" through a pause gives the same roll as a policy that
    passes at once: the pause adds nothing but the question."""
    state = _foes_turn()
    unpaused = _turn(state, _HIT)
    paused = _turn(state, _HIT, reactions=_suspend).suspension
    assert paused is not None
    resumed = _resume(state, paused, "pass", seed=9)
    assert _of(resumed, AttackRolled) == _of(unpaused, AttackRolled)


# ── criterion 11: Shield as a Reaction at its trigger ───────────────────────


def test_a_miss_offers_no_shield_and_spends_no_slot() -> None:
    """Shield is chosen after the roll: an attack that would miss never offers
    it (spike gate 3: "Shield was cast against a miss")."""
    asked: list[ReactionPrompt] = []

    def policy(prompt: ReactionPrompt) -> str:
        asked.append(prompt)
        return "use"

    step = _turn(_foes_turn(), _MISS, reactions=policy)
    [rolled] = _of(step, AttackRolled)
    assert not rolled.is_hit
    assert asked == []
    assert not _of(step, SpellCast)
    assert step.state.spell_slots[WIZARD] == {1: 2}


def test_a_policy_that_uses_shield_needs_no_pause() -> None:
    step = _turn(_foes_turn(), _HIT, reactions=lambda prompt: "use")
    assert step.suspension is None
    assert [e.reaction_name for e in _of(step, ReactionTriggered)] == ["shield"]
    assert not _of(step, DamageApplied)


@pytest.mark.parametrize(
    ("wizard", "why"),
    [
        ({"spells_known": []}, "does not know Shield"),
        ({"spell_slots": {}}, "has no slot to pay for it"),
    ],
)
def test_shield_is_offered_only_to_a_creature_that_can_cast_it(
    wizard: dict[str, Any], why: str
) -> None:
    asked: list[ReactionPrompt] = []

    def policy(prompt: ReactionPrompt) -> str:
        asked.append(prompt)
        return "suspend"

    step = _turn(_foes_turn(**wizard), _HIT, reactions=policy)
    assert asked == [], why
    assert [e.target_id for e in _of(step, DamageApplied)] == [WIZARD]


def test_a_spent_reaction_offers_nothing() -> None:
    """SRD 5.2.1 "Reaction": "Once you take a Reaction, you can't take another
    one until the start of your next turn"."""
    state = _foes_turn()
    state = state.model_copy(
        update={
            "combatants": [
                c.model_copy(update={"reaction_available": False}) if c.entity_id == WIZARD else c
                for c in state.combatants
            ]
        }
    )
    step = _turn(state, _HIT, reactions=_suspend)
    assert step.suspension is None
    assert _of(step, DamageApplied)


def test_without_a_policy_an_unreadied_shield_is_passed() -> None:
    """The default policy keeps 0.7.0's behaviour: nothing is used that the
    creature did not ready, and nothing ever suspends."""
    step = _turn(_foes_turn(), _HIT)
    assert step.suspension is None
    assert not _of(step, ReactionTriggered)
    assert _of(step, DamageApplied)


def test_a_readied_shield_waits_for_a_hit() -> None:
    """The 0.7.0 ``ready`` path still works, as a standing "use": but only on an
    attack roll that hits, never before the roll."""
    for seed, fires in ((_HIT, True), (_MISS, False)):
        started = asyncio.run(
            start_combat(
                session_id="reactions-ready",
                party=[_wizard()],
                encounter=[_foe()],
                grid_scene=GridScene(width=6, height=6),
                rng_seed=1,
            )
        )
        handle = started.handle
        asyncio.run(
            submit_player_intent(
                handle,
                actor_id=WIZARD,
                intent=PlayerIntent(
                    intent_type="ready", spell_id="shield", reaction_trigger="hit_by_attack"
                ),
            )
        )
        drain_pending_events(handle)
        asyncio.run(advance_monster_turn(handle, seed=seed))
        events = drain_pending_events(handle)
        assert any(isinstance(e, ReactionTriggered) for e in events) is fires
        assert not any(isinstance(e, DamageApplied) for e in events)


def test_shield_against_magic_missile_through_the_policy() -> None:
    """ "you take no damage from Magic Missile": the target is offered Shield
    when it is targeted, before any dart lands."""
    party = [
        PartyMemberSpec(
            entity_id="char:target",
            name="Target",
            initiative=20,
            hp_current=20,
            hp_max=20,
            class_slug="wizard",
            spells_known=["shield"],
            spell_slots={1: 2},
            zone_id=cell_id(0, 0),
        ),
        PartyMemberSpec(
            entity_id="char:caster",
            name="Caster",
            initiative=15,
            hp_current=20,
            hp_max=20,
            spells_known=["magic-missile"],
            spell_slots={1: 2},
            zone_id=cell_id(1, 0),
        ),
    ]
    foe = EncounterMemberSpec(
        entity_id="mon:bystander",
        entity_type="Monster",
        name="Bystander",
        initiative=1,
        hp_current=200,
        hp_max=200,
        ac=99,
        zone_id=cell_id(9, 9),
    )
    opened = asyncio.run(
        begin_combat(
            session_id="reactions-mm",
            party=party,
            encounter=[foe],
            grid_scene=GridScene(width=10, height=10),
            seed=3,
        )
    )
    state = asyncio.run(
        resolve_intent(
            opened.state,
            actor_id="char:target",
            intent=PlayerIntent(intent_type="pass"),
            seed=3,
        )
    ).state
    missile = PlayerIntent(
        intent_type="cast_spell", spell_id="magic-missile", slot_level=1, target_id="char:target"
    )
    paused = asyncio.run(
        resolve_intent(state, actor_id="char:caster", intent=missile, seed=4, reactions=_suspend)
    ).suspension
    assert paused is not None
    assert paused.prompt.trigger == "char:caster targets char:target with Magic Missile"
    used = _resume(state, paused, "use", seed=5)
    force = [e.amount for e in _of(used, DamageApplied) if e.target_id == "char:target"]
    assert force
    assert sum(force) == 0
    passed = _resume(state, paused, "pass", seed=5)
    assert sum(e.amount for e in _of(passed, DamageApplied) if e.target_id == "char:target") > 0


def test_the_handle_api_never_suspends() -> None:
    """The handle calls take no policy, so they keep the default one."""
    started = asyncio.run(
        start_combat(
            session_id="reactions-handle",
            party=[_wizard()],
            encounter=[_foe()],
            grid_scene=GridScene(width=6, height=6),
            rng_seed=1,
        )
    )
    asyncio.run(
        submit_player_intent(
            started.handle, actor_id=WIZARD, intent=PlayerIntent(intent_type="pass")
        )
    )
    drain_pending_events(started.handle)
    asyncio.run(advance_monster_turn(started.handle, seed=_HIT))
    events = drain_pending_events(started.handle)
    assert any(isinstance(e, DamageApplied) for e in events)


def test_the_shield_effect_is_the_spells_own() -> None:
    """The nested cast's effect belongs to the wizard, and the attacker's
    resolution does not claim it."""
    state = _foes_turn()
    step = _turn(state, _HIT, reactions=lambda prompt: "use")
    [applied] = _of(step, EffectApplied)
    assert applied.effect.target_id == WIZARD
    assert applied.effect.origin.endswith(WIZARD)
    assert FOE not in step.state.concentration
