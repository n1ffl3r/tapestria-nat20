# Reactions

SRD 5.2.1 "Reaction": "A Reaction is a special action taken in response to a
trigger defined in the Reaction's description. You can take a Reaction on
another creature's turn". The engine offers a reaction at its SRD trigger,
inside the resolution that reaches it, and asks the host what to do.

## Offers, policies and pauses

When a resolution reaches a reaction's trigger and a creature can take it, the
engine builds a `ReactionPrompt` (`dnd5e_engine.reactions`): who may react,
which reaction (`reaction_key`), what triggered it, and for an attack roll its
total, its natural d20 and whether it is a Critical Hit. The host's
`ReactionPolicy` answers each prompt:

| Answer | Effect |
|---|---|
| `"use"` | The creature takes the reaction now, and the resolution goes on. |
| `"pass"` | It does not, and the resolution goes on. |
| `"suspend"` | The call stops here and returns a `CombatSuspension`. |

The stateless calls take the policy as `reactions=`:

```python
step = await resolve_monster_turn(state, seed=seed, reactions=lambda prompt: "suspend")
if step.suspension is not None:
    prompt = step.suspension.prompt          # put this to the player
    step = await resume(state, step.suspension, "use", seed=next_seed)
```

A policy must be deterministic: the same prompt in the same state gets the same
answer. Without a policy, a reaction the creature readied with a `"ready"`
intent is used and any other is passed, which is the 0.7.0 behaviour; the
handle calls (`submit_player_intent`, `advance_monster_turn`) always use that
default, so they never pause.

## Suspension and resume

A suspended step's `state` is the state the call started from, unchanged: a
pause commits nothing. Its `events` are the events up to the trigger, for
display only. `suspension.continuation` is plain JSON: the call, its seed, every
answer the call took before the pause, and `state_digest(state)`.

`resume(state, suspension, decision, seed, reactions=...)`:

1. rejects with `IntentRejectedError("state_changed", ...)` when `state` is not
   the one the call started from;
2. replays the call from that state and its own seed, answering its earlier
   prompts as they were answered, so it reaches the same trigger with the same
   dice;
3. takes `decision` there, and draws every die after it from `seed`;
4. finishes the call, or pauses again at a later prompt the policy suspends.

The finished step holds every event of the whole call, from its start, as if it
had never paused.

## Shield

SRD 5.2.1 Shield: "Casting Time: Reaction, which you take when you are hit by an
attack roll or targeted by the Magic Missile spell", and "Until the start of
your next turn, you have a +5 bonus to AC, including against the triggering
attack, and you take no damage from Magic Missile."

- **After the attack roll.** An attack roll that would hit offers the target
  Shield before any damage; a miss offers nothing. On `"use"`, Shield is cast as
  the target's Reaction, its +5 AC counts against the triggering roll, which is
  compared again, and `AttackRolled` reports the final outcome after
  `ReactionTriggered`, `SpellCast` and `EffectApplied`.
- **On being targeted by Magic Missile,** before the darts land; on `"use"` the
  target takes no force damage from that cast.
- **Who is offered it:** a creature that knows Shield (`spells_known` or a
  slotless cast) or readied it, can pay for it (its slotless cast, else the
  lowest slot it has), is not Incapacitated or shape-shifted, and still has its
  Reaction. No `ready` intent and no Action are needed.
- **Its effect** ends at the start of the caster's next turn.

## Other reactions

Until spec 02a's next step lands, the other reactions keep the 0.7.0 model:

| Reaction | Trigger |
|---|---|
| Opportunity attack | Every walk that leaves an enemy's reach draws one, on either side, unless the mover took the Disengage action; a summon makes none. A character strikes with `PartyMemberSpec.opportunity_attack_weapon_id`, else the first melee weapon in its `equipment`, else an Unarmed Strike; a monster with the first non-ranged attack on its stat block. |
| Counterspell | Armed with `PlayerIntent(intent_type="ready", spell_id="counterspell", reaction_trigger="cast_spell")`; fires when another creature within 60 feet casts a spell. |
| Any other readied spell | Fires on its `reaction_trigger`: `"hit_by_attack"` once an attack roll hits the creature, `"targeted_by_magic_missile"` when Magic Missile targets it, `"cast_spell"` when another creature casts a spell. |

`PlayerIntent.reaction_trigger` accepts only those three values. The set is not
data-driven: a new reaction spell needs engine code (`BACKLOG.md`).

## Reaction economy

Each creature has one Reaction per round, refreshed at the start of its turn,
the same budget an opportunity attack spends. Readying a reaction spends the
Action, not the Reaction; taking it spends the Reaction.

For the 0.7.0 design of the readied-reaction queue, see the
[reaction queue design note](../dev/reaction-queue.md).
