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
answer. Without a policy, an opportunity attack and a reaction the creature
readied with a `"ready"` intent are used and any other is passed, which is the
0.7.0 behaviour; the
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

## Counterspell

SRD 5.2.1 Counterspell: "Casting Time: Reaction, which you take when you see a
creature within 60 feet of yourself casting a spell with Verbal, Somatic, or
Material components"; "The creature makes a Constitution saving throw. On a
failed save, the spell dissipates with no effect, and the action, Bonus Action,
or Reaction used to cast it is wasted. If that spell was cast with a spell slot,
the slot isn't expended."

- **When:** as a character casts on its turn, and as a monster casts a spell
  from its stat block, before the spell has any effect.
- **Who is offered it,** in initiative order: every other creature that knows
  Counterspell (or readied it on `"cast_spell"`), can pay for it (at level 3 or
  above), can cast a spell as a Reaction, and sees the caster within 60 feet.
- **A failed attempt** (the caster makes its save) leaves the cast to the next
  creature in the order.
- **When countered,** `CastFailed(reason="countered")`: a character keeps its
  slot (a slotless cast is still spent) and its turn ends, as a cast always
  ends it in this engine; a monster's daily use is spent.

## Hellish Rebuke

SRD 5.2.1 Hellish Rebuke: "Casting Time: Reaction, which you take in response to
taking damage from a creature that you can see within 60 feet of yourself";
"The creature that damaged you is momentarily surrounded by green flames. It
makes a Dexterity saving throw, taking 2d10 Fire damage on a failed save or half
as much damage on a successful one."

- **When:** after each attack or spell activity that deals damage, before the
  attacker's next attack.
- **Who is offered it:** each creature that took damage, knows Hellish Rebuke,
  can pay for it, can cast a spell as a Reaction (so not at 0 Hit Points), and
  sees the attacker within 60 feet.
- **Its effect** is cast at the attacker. A rebuke that drops a monster in the
  middle of its Multiattack ends the attacks it has left.

## Opportunity attacks

SRD 5.2.1 "Opportunity Attacks": "You can make an Opportunity Attack when a
creature that you can see leaves your reach". Every walk that leaves an enemy's
reach draws one, on either side, unless the mover took the Disengage action; a
summon makes none. A character strikes with
`PartyMemberSpec.opportunity_attack_weapon_id`, else the first melee weapon in
its `equipment`, else an Unarmed Strike; a monster with the first non-ranged
attack on its stat block.

A character is offered its opportunity attack through the policy
(`reaction_key="opportunity-attack"`); the default policy uses it, as 0.7.0
always did, and a character that passes keeps its Reaction. A monster or NPC is
never asked: contract 03 has monsters decide "by their stat block behavior and
directive. No pause.", and this engine's monsters always take the attack.

## Readied spells and Feather Fall

A spell other than these, readied with a `"ready"` intent, still fires on its
`reaction_trigger`: `"hit_by_attack"` once an attack roll hits the creature,
`"targeted_by_magic_missile"` when Magic Missile targets it, `"cast_spell"`
when another creature casts a spell. `PlayerIntent.reaction_trigger` accepts
only those three values. The set is not data-driven: a new reaction spell needs
engine code (`BACKLOG.md`).

SRD 5.2.1 Feather Fall is taken "when you or a creature you can see within 60
feet of you falls". The engine models no falling, so it has no trigger and is
never offered.

## Reaction economy

Each creature has one Reaction per round, refreshed at the start of its turn,
the same budget an opportunity attack spends. Readying a reaction spends the
Action, not the Reaction; taking it spends the Reaction.

For the 0.7.0 design of the readied-reaction queue, see the
[reaction queue design note](../dev/reaction-queue.md).
