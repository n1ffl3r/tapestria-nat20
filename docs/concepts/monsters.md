# Monsters

Monsters take their turns through `advance_monster_turn(handle)`. The engine
picks the action, resolves it, and emits events — there is no monster intent for
a host to submit.

## Getting a real monster

Pass `monster_template_slug` on an `EncounterMemberSpec` and the engine resolves
that creature from the bundled SRD corpus, giving it its actual actions,
resistances, saves, senses and condition immunities:

```python
EncounterMemberSpec(
    entity_id="mon:goblin",
    entity_type="Monster",
    name="Goblin Warrior",
    monster_template_slug="goblin-warrior",   # <- the SRD creature
    initiative=1,
    hp_current=7,
    hp_max=7,
    zone_id=cell_id(2, 0),
)
```

Omit it and you get a generic combatant driven by the inline `attack_bonus` /
`damage_dice` fields — fine for a training dummy, but it has no real repertoire.

The template supplies the creature's actions, ability scores (Dexterity too,
while the spec leaves it at 10), proficiencies, traits and spellcasting
ability. It also supplies the creature's senses — Blindsight, Darkvision,
Tremorsense, Truesight — unless the spec's `senses` is set
(`senses={}` gives it none, and any value gives an un-templated
creature its own), and its condition immunities while the spec's
`condition_immunities` is empty (a Skeleton can't be Poisoned). So a Goblin
Warrior sees a character standing in Darkness, and a Bat sees an Invisible one
within its Blindsight. Its Armor Class, Hit Points and speed stay the spec's
(pin `ac` from the stat block: every SRD monster in the dataset carries its
Armor Class, `Monster.ac`).

A template monster whose spec sets no `attack_bonus` attacks with its stat
block's numbers: its six ability scores and Proficiency Bonus, so a Tough's
Mace rolls d20 + 4 for 1d6 + 2 and a Bandit's Scimitar +3 for 1d6 + 1. Set
`attack_bonus` and the creature keeps the uniform model instead: that to-hit
on every roll, no ability modifier on its damage, and a save DC of
`8 + attack_bonus` when it has no spellcasting ability. A creature with no
template always uses the field.

A transformed creature — a druid in a Wild Shape form, or a creature under
*Polymorph* — rolls at its form's ability scores and Proficiency Bonus, so a
Wolf form bites at +4 for 1d6 + 2 (C21). A
host can command one attack from a creature's current stat block with
`PlayerIntent(intent_type="attack", stat_block_action_id=..., target_id=...)`.

## How the AI chooses

The built-in AI is deliberately simple and predictable:

1. **Target** the lowest-HP living enemy — a PC or a party-side summon.
2. **Prefer Multiattack** when the creature has it — it is the full-action play.
3. **Otherwise** pick an action whose own range covers the target, closing the
   distance first if needed (and Dashing when that helps).
4. **Flee** when badly hurt, unless the creature is `DEFENSIVE`.

An area of effect — a breath, a spell, a legendary action — the AI places
itself. From the cell the monster stands in, it takes the placement that
affects the most enemies minus allies and never the monster itself: an
Emanation from that cell; a Cone, Cube or Line in one of eight directions; a
Sphere or Cylinder centred on a foe it can see within the action's or spell's
range. Whom it affects follows the stat block ("each creature", "each enemy").
An area that would affect no enemy is not an option that turn: the monster
takes its next action, spell or legendary action, and a charged breath stays
charged. It never moves first to bring a breath into reach. Each placed area
emits `AreaTargeted`, whose `source_id` names the action (`"fire-breath"`) or
the spell (`"fireball"`); `IntentSubmitted.target_id` still names the creature
the AI targets.

`EncounterMemberSpec.behavior_profile` selects between three profiles:

| Profile | Behaviour |
|---|---|
| `AGGRESSIVE` (default) | Closes and attacks; flees below 10% HP |
| `RANGED` | Prefers to keep distance; flees below 25% HP |
| `DEFENSIVE` | Never flees |

This is a reasonable default opponent, not a tactical AI. If you want smarter
monsters, drive them yourself and use the engine as the resolver.

## Multiattack

A multiattack names its sub-attacks only in prose, so the engine parses the
description to fan it out — "makes two Claw attacks and uses Roar" becomes two
claws and one roar.

**128 of the 180 multiattacks in the corpus resolve to the exact SRD attack
mix.** Of the remaining 52, 51 fall back to repeating one attack N times —
one the description names, when it names any: "three attacks, using Storm
Blade or Storm Bolt in any combination" repeats Storm Blade or Storm Bolt,
whichever reaches — and the Avatar of Death's, with no attack to repeat,
resolves nothing; each logs `multiattack_join_unresolved` at WARNING, so the
loss is always visible in your logs rather than silent. For homogeneous
multiattacks ("three Rend attacks")
the fallback is correct; for a heterogeneous one it repeats one of the attacks
named. An action a multiattack uses "if available" (the Doppelganger's
Unsettling Visage) sits out while its Recharge is spent, and an area it uses
(the Sphinx of Valor's Roar) is aimed like any other. See the
[capability matrix](../capabilities.md) for the specifics.

## Monster action economy

A monster's turn picks the most powerful option it has: a charged Recharge
action first, then a Spellcasting action whose N/Day offensive spell still has
a use left, then Multiattack, then its other attacks and at-will spells in
stat-block order — skipping any whose area would affect no enemy. At the start
of a living monster's own turn the engine rolls
Recharge for each spent recharge action, applies Regeneration and refills its
legendary-action pool. Legendary actions are host-driven:
`advance_monster_turn(handle, legendary=True)` after another creature's turn
ends. Legendary Resistance is armed ahead of a save with
`resolve_legendary_resistance`. The capability matrix lists the traits the
engine consumes.

## What is not modelled

- **Lair actions** — the corpus ships none.
- Some `special_abilities` (Flyby, Nimble Escape) and the ability-check half of
  Sunlight Sensitivity.
- Commanding a stat block's save actions (a Breath Weapon).
- Moving into reach and then using an area: a monster chooses an area only
  from where its turn starts (an area its Multiattack uses is placed after
  the Multiattack's walk).

All are tracked in `BACKLOG.md`. If you need them, resolve them host-side and
apply the results through the engine's normal paths.
