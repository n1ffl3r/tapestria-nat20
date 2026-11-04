# Spell timing

A spell in the corpus can carry several activities: the one its cast resolves,
alternatives the caster picks between, and activities that belong to a later
trigger. Delayed Blast Fireball, for example, has the bead's creation, its
growing damage, and its explosion "when the spell ends". The engine resolves
each one when its SRD 5.2.1 text says it happens.

## What a cast resolves

`dnd5e_engine.activities.timing.SPELL_ACTIVITY_TIMINGS` records every activity
of a multi-activity spell that does not simply resolve with the cast, with the
SRD sentence that decides it ([the full table](../srd-5.2.1-spell-timing.md)).
A cast resolves:

- every activity not in the table;
- of each group of alternatives, the one `PlayerIntent.activity_id` names,
  else the group's first (Wall of Fire's "Create Wall" or "Create Ring");
- no later activity.

## Later activities

A cast whose spell has a later activity the engine fires leaves a
`LingeringSpell` (`dnd5e_engine.lingering`) in the combat, carried in
`CombatState.lingering_spells`. Each fired activity has a `LaterRule`: its
trigger, whom it affects, and what a successful save ends.

| Trigger | Fires | Spells |
|---|---|---|
| `caster_turn_start` | at the start of each of the caster's turns after the cast | Tsunami, Storm of Vengeance (one effect per turn) |
| `caster_turn_end` | at the end of each of the caster's turns, the casting turn included | Delayed Blast Fireball's growing damage |
| `target_turn_start` | at the start of a subject's own turn | Ensnaring Strike, Searing Smite, Stinking Cloud |
| `target_turn_end` | at the end of a subject's own turn (a one-time rule skips the turn of the cast) | Weird, Acid Arrow, Vitriolic Sphere, Spirit Guardians and Conjure Woodland Beings (their end-of-turn half) |
| `spell_ends` | when the spell ends: its concentration drops or its duration runs out | Delayed Blast Fireball's explosion, Haste's lethargy |
| `on_hit` | when the caster hits a subject with an attack roll, dice doubled on a Critical Hit | Hunter's Mark, Hex, Conjure Minor Elementals |

Whom a firing affects (`Subjects`):

| Subjects | Who |
|---|---|
| `hit`, `failed_save`, `saved`, `affected` | the creatures the cast hit, that failed or made its save, or that it laid an effect on |
| `bearing` | whoever bears one of the cast's effects now, so a moved Hunter's Mark follows its new target |
| `area` | the creatures in the cast's area at its point of origin |
| `caster_area` | the creatures in the cast's Emanation around the caster where it stands now |

A rule whose save ends the spell (Weird, Searing Smite) replaces the repeat
save the cast's effect would make, so the save is rolled once.
`@item.uses.value` reads the lingering spell's counter: Delayed Blast
Fireball's extra dice and Tsunami's dwindling wave.

## Held back

A later activity with no `LaterRule` never resolves: the cast holds it back
and nothing fires it. That covers a creature entering an area, the caster's
later actions (Flaming Sphere's ram, Hex's "Curse New Creature"), triggers on
being attacked or damaged, and spells whose area the grid can't place (the
walls, a moving cloud, a construct's aura). `BACKLOG.md` lists them.
