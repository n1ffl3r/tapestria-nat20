# Combat model

Combat in Nat20 is a turn-based loop. There are two ways to drive it, on the
same resolution code:

- **Through a handle.** You open a combat with `start_combat`, submit intents
  turn by turn through an opaque **`CombatHandle`**, and close it. The engine
  keeps the combat in memory behind the handle.
- **As state.** You open it with `begin_combat` and keep the
  **`CombatState`** yourself. Every call takes the state and a seed and returns
  the events and the new state; nothing stays in the engine between calls. See
  [Combat as state](#combat-as-state).

## The loop

`start_combat(...)` takes a `session_id`, a `party` (list of
`PartyMemberSpec`), an `encounter` (list of `EncounterMemberSpec`), an
`rng_seed`, and the `grid_scene` (a [`GridScene`](grid.md)) to fight on. It rolls
initiative, materializes runtime state, and returns a `StartCombatResult`
wrapping the `CombatHandle` plus the opening `CombatEvent` stream.

On a PC's turn, call `submit_player_intent(handle, actor_id, intent)` with a
`PlayerIntent`. The `intent_type` (an `IntentType` literal — `"attack"`,
`"cast_spell"`, `"move"`, `"dash"`, `"dodge"`, `"disengage"`, `"hide"`,
`"help"`, `"use_item"`, `"use_feature"`, `"pass"`, and more) selects which
optional fields the resolver consumes (`weapon_id`, `spell_id`,
`target_id`, `target_zone_id`, …). Monster turns advance via
`advance_monster_turn(handle)`, which runs the [built-in monster
AI](monsters.md).

A `"move"` intent names a destination cell, and the engine walks the
fewest-cells legal route there in one intent ([Grid](grid.md#pathing)). Off-turn reactions
are offered at their triggers: the stateless calls let a host answer or pause
for them, and the handle calls take the default answer ([Reactions](reactions.md)).

### Summoned creatures

The initiative order can grow and shrink mid-combat. SRD 5.2 Summon Dragon
seats a Draconic Spirit that "shares your Initiative count, but it takes its
turn immediately after yours": the engine inserts it in the slot after its
caster and emits `CombatantJoined`, which carries everything a host needs to
seat the creature itself — its id, name, stat block, caster, spell, count, the
creature it acts after, its cell, Hit Point maximum and Armor Class. "The
creature disappears when it drops to 0 Hit Points or when the spell ends": the
engine removes it and emits `CombatantLeft` with the reason (`"zero_hp"`,
`"concentration_drop"` or `"spell_ended"`). If it was the current actor, the
next creature's turn starts once the intent or legendary action that removed
it has resolved; if it left during its own intent, its own turn first ends as
any turn does, with `TurnEnded`.

A summon's id starts with `summon:` and its `entity_type` is `"Monster"`, so a
host loop that calls `advance_monster_turn` whenever the engine's pointer names
a non-PC needs no summon tracking: on the summon's turn the engine plays the
SRD default, the Dodge action. To command it instead, submit its intents
through `submit_player_intent(handle, <summon id>, intent)` — each Rend is one
`attack` naming `stat_block_action_id="rend"`; an attack off its stat block,
or a spell, is refused. `LiveCombatView.summons` maps each summon's id to its
owner, spell, stat block and slot level. A summon is its caster's ally, but it
never enters `party_ids` or `encounter_ids`, and the `CombatOutcome` holds no
Hit Points, XP or death of its own. A `DeathRecord.killer_id` can still name
it, as the current actor that dealt the blow: `CombatantJoined.origin_caster_id`
names its caster.

## Combat as state

`CombatState` (`dnd5e_engine.combat_state`) is a pydantic model holding
everything a combat needs for its next call. It round-trips through JSON
without loss, so a host can store it anywhere and resume from it in another
process:

| Call | Takes | Returns |
| --- | --- | --- |
| `begin_combat(session_id=, party=, encounter=, grid_scene=, seed=)` | The specs `start_combat` takes; the seed draws engine-rolled Initiative | `CombatStep` |
| `resolve_intent(state, actor_id=, intent=, seed=)` | A state and one `PlayerIntent` | `CombatStep` |
| `resolve_monster_turn(state, seed=, legendary=False, actor_id=None)` | A state whose active combatant is a monster, or a legendary-action window | `CombatStep` |
| `arm_legendary_resistance(state, entity_id)` | A state (it rolls nothing, so no seed) | `CombatStep` |
| `close_combat(state)` | A state | `EndCombatStep`: the outcome, the closing events, the effects still active, the ended state |

A `CombatStep` is the events the call emitted, in order, and the new state.
The state passed in is never changed, so a rejected intent
(`IntentRejectedError`, as on the handle path) leaves the caller's state as it
was.

**What the state holds.** The round, the initiative order and the active
combatant; each combatant (its statistics, Hit Points, conditions, death saving
throws, turn resources and Reaction), its position, Temporary Hit Points and
spell slots; every active effect, the concentration each caster holds and the
rounds left on it, and every pending end-of-turn repeat save; each readied
reaction; each monster's spent recharge actions, per-day uses and legendary
pools; the conjurations in play; and the events of the turn in progress, which
the turn's end reads. The `CombatState` docstring lists every field, and the
module docstring lists what is left out and why it cannot matter.

**Seeds.** Every die a call rolls comes from `random.Random(seed)`. The same
state, call and seed give byte-identical output, in any process; nothing reads
the global `random` module or the clock. A host records each call's seed with
its result to replay it.

**Reactions.** `resolve_intent` and `resolve_monster_turn` take a
`reactions=` policy that answers each reaction a resolution offers. A
`"suspend"` answer returns a step whose `suspension` holds a plain-JSON
continuation, and `resume(state, suspension, decision, seed)` finishes the
call; see [Reactions](reactions.md).

**A host's own record.** A host that stores the round and the active combatant
itself can move a state's turn with `state.at_turn(round, active_combatant)`.

**Between the two styles.** `load_combat(state)` puts a state behind a handle,
and `combat_state_of(handle)` reads a handle's state back. `submit_player_intent`
and `advance_monster_turn` take an optional `seed=` too: with it, the call
draws from that seed, exactly as the stateless call does; without it, the
handle keeps the one dice stream `start_combat(rng_seed=...)` began.

## Conditions a host adjudicates

The engine applies every condition rule that touches a roll it makes. Two SRD
5.2 rules touch only checks it never rolls, so they are a host's to apply:

- **Blinded and Deafened:** "You can't see and automatically fail any ability
  check that requires sight" (Deafened: hearing). No check the engine rolls
  needs a sense — Hide is Stealth, escaping a grapple is Athletics or
  Acrobatics. When a check of your own does (a Wisdom (Perception) check to
  spot something), read the creature's conditions from
  `get_live(handle).active_conditions` and fail the check without rolling it.
- **Charmed:** "The charmer has Advantage on any ability check to interact with
  you socially." When the charmer makes a social check against the creature it
  charmed, pass `CheckSpec(advantage=True)` to `resolve_check`. The
  `EffectApplied` that charmed the creature names its caster at the end of the
  effect's `origin`.

## Determinism and events

Every die roll flows through the seeded RNG you pass to `start_combat`, so a
given seed and intent sequence always reproduce the same combat. Each call
emits a stream of typed `CombatEvent`s (attacks, damage, deaths, turn and
round boundaries) that a host renders or narrates.

## Closing out

Between combats, `resolve_short_rest` / `resolve_long_rest` restore hit points,
hit dice, spell slots and feature uses; `resolve_check` resolves a one-off
ability, skill or saving throw with no handle at all.

`end_combat(handle)` returns an `EndCombatResult` carrying a `CombatOutcome`
— its `ended_reason` (victory, defeat, flee, forced), `residual_hp`,
`deaths`, and `loot_drops` — plus the final tuple of `ActiveEffect`s, which
the engine discards (effects are combat-scoped). The ended combat stays
readable — a repeat `end_combat`, `get_live`, a last `drain_pending_events`
— until 64 later combats have ended; its handle then raises
`UnknownHandleError`.

Not every SRD rule is resolved. The [capability matrix](../capabilities.md) is
the per-mechanic inventory of what is and is not enforced.
