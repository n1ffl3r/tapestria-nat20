# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The official SRD 5.2.1 PDF now decides what is SRD content (spec 02a of the
dungeons-and-agents fork, workstream A).

### Added

- **`srd/SRD_CC_v5.2.1.pdf`**, the official SRD 5.2.1, pinned by SHA-256 in
  `raw_sources/PINS.json`.
- **`srd/index.json`**, built from the PDF by `tools/srd_index.py`
  (`make srd-index`): every SRD record by category with its page.
- **`srd/aliases.json`**: 115 corpus records that are variants (an Ioun Stone
  type, a Mysterious Deck card) or helpers (the Folding Boat's activation) of a
  record the index lists, each with an evidence phrase the index tool finds on
  the parent's pages.
- **Ice Knife**, **Giant Centipede**, **Giant Wasp**, and **Werewolf**, which
  SRD 5.2.1 contains and the old gate dropped.
- **Class spell lists** (`canonical/spell_lists/<class>.json`, `SpellList`,
  `BundledAssetLoader.get_spell_list` and `MemoryAssetLoader(spell_lists=)`;
  `list_slugs("spell_lists")`): the eight spellcasting classes' lists, from the
  classes each SRD 5.2.1 spell heading names, joined to the corpus by name.
  Barbarian, Fighter, Monk and Rogue have none. The `AssetLoader` Protocol is
  unchanged, so other loaders still conform.
- **`audit/srd_5_2_1_ledger.json`**, the per-record rules-text audit, built by
  `tools/srd_audit/` (`make srd-ledger`; `make check-provenance` checks it):
  every spell's level, school, classes, casting time, ritual, range,
  components, material cost and consumption, duration, concentration, attack,
  save, damage, healing, area and scaling, compared with its PDF text. 339
  spells: 260 verified, 63 corrected, 16 divergence; 8 spell lists: 5
  verified, 3 divergence. `audit/srd_5_2_1_divergences.json` records each
  divergence and its issue.
- **The stat block audit** (`tools/srd_audit/stat_blocks.py`): every monster's
  size, type, alignment, AC, HP, speed, ability scores, saves, skills,
  resistances and immunities, senses, CR and Proficiency Bonus, the traits and
  actions in each section, and every action's attack bonus, reach or range,
  saves, damage, area, recharge and uses, compared with its stat block in the
  PDF. The corpus side derives a monster attack's numbers the way the engine
  does. 344 monsters: 24 verified, 313 corrected, 7 divergence (#7).
- **The item audit** (`tools/srd_audit/items.py`): every magic item's category,
  rarity, attunement and its constraint, and charges, compared with its Magic
  Items chapter kind line and text; every other item's cost and weight, and a
  weapon's or armor's table columns, compared with its Equipment chapter row or
  heading or its sample poison heading. A variant is compared on its parent's
  kind line; a field the SRD does not state is listed as not compared, and so
  is a magic item's price and weight. 546 items: 493 verified, 37 corrected, 16
  divergence (#10: eight traps and eight Foundry helper records).
- **The class audit** (`tools/srd_audit/classes.py`): each class's Hit Point Die,
  primary ability, saving throws, spellcasting progression and ability, the
  level of every feature, the Ability Score Improvement and subclass levels, and
  every scaling value (Rages, Rage Damage, Sneak Attack, Wild Shape uses) against
  its Features table and "Level N: Feature" headings; each subclass's features
  and levels; and each of the 226 class and subclass features against the
  heading that names it at the level its owner grants it. A scale the table has
  no column for (Brutal Strike, Divine Strike, Aura Distance) is read by hand
  with its quoted sentence. 12 classes: 10 verified, 2 corrected; 12
  subclasses and 226 features verified. A test checks the engine's
  spell slot tables (`derive_spell_slots`, `derive_pact_slots`) against each
  caster's table.
- **The origins audit** (`tools/srd_audit/origins.py`): each species' creature
  type, sizes, Speed, Darkvision, and traits with their levels (Draconic Flight
  at 5), with a lineage's Darkvision, Speed, resistances and spells, and the
  Dragonborn's Breath Weapon scale and resistance choices, read by hand with
  quoted sentences; each of the 36 species traits at the level its species grants
  it; each background's ability scores, feat, skills, tool, and Gold Pieces; each
  feat's category, prerequisite, and Repeatable; each condition's implied
  conditions and effects, read by hand and quoted; and each of the 102 shared
  monster traits against the stat block it was taken from. 14 species, 36 species
  traits, 4 backgrounds, and 15 conditions verified; 17 feats: 14 verified, 3
  corrected; 102 monster traits: 99 verified, 3 corrected (#18). With this, every
  corpus record has a ledger entry.

### Changed

- **Every record is tagged SRD 5.2.1** (spec 02a, acceptance criterion 5):
  `Provenance.srd_version` accepts `5.2.1`, the translator and the authored features
  emit it, and the 1,675 records of the corpus carry it (a class spell list names
  the SRD 5.2.1 document it comes from). `tests/test_corpus_srd_tag.py` checks
  each record.

- **Monster attacks keep what Foundry stores on the item**: a melee attack's
  reach (`system.range.reach`, else 5 feet: SRD 5.2.1 "A creature has a reach of
  5 feet unless a rule says otherwise"), a ranged attack's normal range, and
  the Finesse property, which makes an attack use Dexterity when it is the
  higher modifier (Goblin Warrior, Scimitar: "Melee Attack Roll: +4" with Str 8
  and Dex 15). A thrown melee weapon's melee attack reaches 5 feet, not its
  throwing range.
- **Monster corrections against SRD 5.2.1** (`_MONSTER_SRD_CORRECTIONS`, 134
  entries over 101 monsters, each quoting its stat block): condition
  immunities (14 monsters) and resistances (9) Foundry left empty; AC (Elk,
  Flying Snake); 21 reaches (Adult Brass Dragon's Rend reaches 10 feet, the
  Kraken's Tentacle 30); damage types and dice (Ankheg's Bite is Slashing,
  Behir's Lightning Breath 12d10, untyped parts typed); 13 save DCs and
  abilities (the five lycanthropes' curse DCs, Pit Fiend's Bite save, which had
  no ability); three recharges (Ghost, Minotaur of Baphomet, Succubus); and 19
  traits and actions filed in the wrong section (Aboleth's Eldritch
  Restoration is a trait).
- **Item corrections against SRD 5.2.1** (`_ITEM_SRD_CORRECTIONS`, 38 entries
  over 37 items, each quoting its table row, heading, or kind line): ammunition
  and Iron Spikes weigh their bundle ("Arrows 20 Quiver 1 lb. 1 GP"), where
  Foundry weighs one piece; the seven packs weigh their contents ("Explorer's
  Pack 55 lb. 10 GP"); Bagpipes, Chain, Herbalism Kit and Waterskin weights; the
  Hooded Lantern costs 5 GP; Trident and War Pick are Versatile (1d10); a cost
  that "Varies" is no price; Dwarven Thrower's attunement constraint loses
  Foundry's link markup; the Dragon Orb has its 7 charges, which its spells
  spend; and eleven items the Magic Items chapter prints (two potions, three
  Spell Scrolls, +1 Ammunition, Bead of Nourishment, Feather Token, Figurine of
  Wondrous Power, Handy Haversack, Horn of Valhalla) are magic items, the
  Handy Haversack Rare.
- **Feat corrections against SRD 5.2.1** (`_FEAT_SRD_CORRECTIONS`, each quoting
  the feat's header): Boon of Fate and Boon of Irresistible Offense need Level
  19+, and Boon of Spell Recall needs a Spellcasting Feature too.
- **The owner's decisions on the audit's issues**: the Monk's **Self-Restoration**
  is authored from the SRD text (#16; `tools/translators/authored.py`, a record
  whose `provenance.source` is the new `srd_pdf`); the shared monster traits
  **Standing Leap**, **Stench** and **Water Susceptibility** take the SRD's text
  (#18); and the Couatl's **Divine Aid** and the Gnoll Warrior's **Rampage**,
  which the pinned pack does not ship, are authored from their stat blocks (#7).
  The other seven stat block divergences remain.
- **Class corrections against SRD 5.2.1** (`_CLASS_SRD_CORRECTIONS`, each quoting
  its Features table): the Barbarian's Rages rise to 6 at level 17, not 16; a
  Monk has no Focus Points at level 1. The engine's
  multiclass scale test expected the Monk's level 1 Focus Point and now expects none.
- **Spell corrections against SRD 5.2.1** (`_SPELL_SRD_CORRECTIONS` in
  `tools/translators/foundry.py`, each quoting its SRD sentence): Raise Dead
  has Touch range; Mirage Arcane's range is Sight (`spec`); Forbiddance is a
  Ritual; Hypnotic Pattern and Resilient Sphere need Concentration; Plant
  Growth's casting time is an Action; Phantasmal Killer's save is Wisdom (it
  had no ability); Glyph of Warding's Explosive Rune is a 20-foot-radius
  Sphere; Pass without Trace has its 30-foot Emanation.
- **Spell material costs** are read from the component's text ("a diamond
  worth 500+ GP"): Foundry ships `cost: 0` for every spell, and 56 spells
  change. Sequester's material is consumed ("which the spell consumes").
- **Range and duration formulas** are read at the spell's level: Animal
  Messenger lasts 24 hours, Dominate Monster and Magic Circle 1 hour, and
  Spare the Dying's range is 15 feet (each had no value).
- **The SRD gate** (`tools/translators/srd_gate.py`) admits a Foundry document
  when the PDF index lists its name, or an alias ties it to a listed record.
  It used Foundry's `system.source.license` tag before (the open5e and 5e-bits
  verdicts were copies of Foundry's), which admitted Arcane Vigor and left
  Ice Knife, Giant Centipede, and Giant Wasp out.
- **Regen keeps a stat block over a same-slug summon form**: the Giant Insect
  spell's `summons/giant-insects/*` forms no longer overwrite the Giant
  Centipede, Giant Spider, and Giant Wasp beasts.
- **Werewolf's creature type** is corrected to Monstrosity: Foundry's
  `type.value: (lycanthrope)` made the translator drop the stat block.

### Removed

- **Arcane Vigor**, which SRD 5.2.1 does not contain.

## [0.7.0]

Lockstep release with `dnd5e-engine` 0.7.0 and `nat20-bridge` 0.7.0.

C26a corrects who an area affects and the monster area actions against their
SRD 5.2 text; C28 adds `Feat.repeatable` and resolves each background's feat
to its canonical slug, a value change for a consumer that matched the Foundry
compendium id (`phbftAlert000000`). `dnd5e-engine` 0.7.0 requires this release
(`dnd5e-srd-data>=0.7.0`).

### Added

- **`Feat.repeatable`** (C28) — SRD 5.2: "A feat can be taken only once unless
  its description states otherwise in a "Repeatable" subsection." True for
  Ability Score Improvement, Magic Initiate and Skilled (Foundry's
  `system.prerequisites.repeatable`).

### Changed

- **`Background.starting_feat_slug` names the canonical feat** (C28): `alert`
  (Criminal), `magic-initiate` (Acolyte, Sage), `savage-attacker` (Soldier),
  where it held the Foundry compendium id (`phbftAlert000000`).

### Fixed

- **Who an area affects follows the SRD 5.2 text** (C26a). Sleep, Slow, Mass
  Cure Wounds and the Mace of Terror's Wave of Terror carry
  `target.affects.choice: true` ("each creature of your choice", "up to six
  creatures of your choice", "Choose up to six creatures"), and Slow and Mass
  Cure Wounds `target.affects.count: "6"`.
- **Monster area actions follow their SRD 5.2 stat blocks** (C26a):
  - "Recharge 5–6" on the adult blue, adult copper, ancient copper, ancient
    gold, ancient green and young red dragons' breaths and the Centaur
    Trooper's Trampling Charge (they had no recharge, so a dragon never
    breathed) and on the Tarrasque's Thunderous Bellow (it had 6);
  - the adult gold, ancient gold and gold wyrmling Fire Breath and the magma
    mephit's are Cones, not Lines; the adult copper's Acid Breath is a 60-foot
    Line (was 20); the adult gold's Cone is 60 feet, the ancient gold's and the
    ancient green's 90 feet (were 15);
  - the four green dragons' and the iron golem's Poison Breath affect each
    creature (`affects.type: "creature"`), not only enemies.
- **A recharge with no formula recharges on a 6**, Foundry's own default; it
  used to translate as no recharge at all.

## [0.6.0]

Lockstep release with `dnd5e-engine` 0.6.0 and `nat20-bridge` 0.6.0.

Core-mechanics C22 — the dataset now carries the mechanics the engine used to
keep only in Python — and C18 — `Monster.spellcasting_ability`, the field
that lets stat-block monster spellcasting derive an honest save DC from the
monster's own ability score instead of a flat approximation, plus the typed
legendary pool sizes (`legendary_resistance_uses`, `legendary_action_uses`). Every change is
additive; old canonical JSON without the new keys still validates. C21a
derives the Armor Class of the 133 monsters that shipped none (below).

### Added

- **`canonical/conditions/`** (15 files) — the SRD 5.2 rules-glossary
  conditions as typed data: `schema/condition.py::Condition` with
  `effects: list[ConditionEffect]` (`ConditionEffectKind`, 29 kinds) and
  `implies`. `AssetLoader.get_condition(slug)` / `list_conditions()`;
  `Category` gains `"conditions"`. Mechanics are authored in
  `tools/translators/conditions.py` with the SRD sentence per row (Foundry
  ships no structured condition mechanics).
- **`canonical/traits/`** — de-duplicated SRD monster traits
  (`schema/monster.py::MonsterTrait`, `AssetLoader.get_trait`, `Category`
  `"traits"`), sourced from the SRD actors' embedded trait items.
- **`MonsterAction.mechanic: MonsterTraitMechanic | None`** on
  `special_abilities` entries (14 typed traits: Magic Resistance ×34,
  Legendary Resistance ×32, Amphibious ×31, Pack Tactics ×17, …).
- **`SaveBlock.ignore_cover`** — `true` only on Sacred Flame ("The target
  gains no benefit from Half Cover or Three-Quarters Cover for this save").
- **`Weapon.magical` / `Armor.magical`** — Foundry's `mgc` item property,
  previously dropped by the translator; independent of `magical_bonus`.
- **`ActivationBlock.reaction_conditions: list[ReactionCondition]`** —
  typed reaction triggers (`ReactionTriggerKind`: hit_by_attack,
  targeted_by_spell, sees_spell_cast, takes_damage, creature_falls) derived
  from Foundry's free-text `activation.condition`; populated for Shield,
  Counterspell, Hellish Rebuke and Feather Fall (and the monsters that embed
  them), empty everywhere else. Also populated for six non-spell reactions
  whose trigger text matches the shapes: Superior Hunter's Defense,
  Retaliation, Stone's Endurance, Storm's Thunder, Mummy Lord's Whirlwind of
  Sand, Gloves of Missile Snaring.
- **`Monster.spellcasting_ability: Ability | None`** — set only for monsters
  with at least one `cast`-kind activity (`actions`/`legendary_actions`/
  `special_abilities`), e.g. Mage → `int`, adult+ dragons → `cha`, Stone
  Golem → `con`. Sourced primarily from each cast activity's own
  `spell.ability` (the most common non-empty value among them), falling
  back to the top-level `system.attributes.spellcasting` only when every
  cast activity's ability is empty; valid abilities are `{int, wis, cha,
  con}` (`str`/`dex` are never valid — they are Foundry dropdown
  placeholders left on non-casters, e.g. Zombie's `str`). Every
  non-spellcasting monster is `None`. One residual: Mummy Lord's
  spellcasting ability is stated only in trait prose, not in any structured
  field, so it resolves to `None` (tracked in `BACKLOG.md`).
- **`Monster.legendary_resistance_uses` / `Monster.legendary_action_uses:
  PositiveInt | None`** — the SRD 5.2 Legendary Resistance (N/Day) and
  Legendary Action Uses pool sizes, from Foundry's
  `system.resources.legres.max` / `legact.max` (0 → `None`). Legendary
  Resistance: 3 for 18 bearers (the adult dragons, Ancient Gold Dragon,
  Aboleth, Balor, Mummy Lord, both sphinxes, Unicorn, Vampire), 4 for 13
  (the other eight ancient dragons, Kraken, Lich, Pit Fiend, Solar), 6 for
  the Tarrasque; legendary actions: 3 for all 30 legendary-action monsters.
  Regen adds exactly these two keys to each of the 341 monster files.

### Changed

- The translator now labels bare sibling-item tokens corpus-wide (139
  monster/trait files); the five multiattacks that referenced sibling
  attacks by opaque Foundry key (bandit-captain, doppelganger, chain-devil,
  scout, ettin) — which could not be joined to a typed sibling before — are
  the motivating fix, and now carry the sibling name as a `{Label}` on the
  `[[/item .<id>]]` token.
- **C19 — `unarmored-defense` is the Barbarian's feature (`unarmoredBarb`);
  the Monk's ships as `unarmored-defense-monk`.** Foundry gives both
  classes' Unarmored Defense the same identifier, and the translator used
  to collapse them onto one canonical file, which the Monk's copy won
  alphabetically — so the Barbarian's granted `unarmored-defense` resolved
  to the Monk's DEX+WIS formula instead of its own DEX+CON one. A one-entry
  class-scoping table in `tools/translators/foundry.py` now gives the
  Monk's copy its own slug.
  (`packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/features/unarmored-defense-monk.json`)

### Fixed

- **C20 — the Unarmed Strike deals its SRD 5.2 base damage.** SRD 5.2: "On a
  hit, the target takes Bludgeoning damage equal to 1 plus your Strength
  modifier." Foundry ships that damage only as a `custom.formula`
  (`@mod + @prof`, which is not the SRD rule) that the translator drops, so
  `items/unarmed-strike.json` had `damage_parts: []` and every unarmed hit
  dealt 0. A one-entry, SRD-grounded correction table in
  `tools/translators/foundry.py` (`_WEAPON_BASE_DAMAGE_CORRECTIONS`) now fills
  an empty part list with a flat 1 Bludgeoning; the engine adds the Strength
  modifier. Regen changes only that file.
  (`packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/items/unarmed-strike.json`)
- **C21a — every monster ships its Armor Class.** 133 of 341 monsters had
  `ac: null`: Foundry computes their AC (`attributes.ac.calc: "default"`), and
  the translator kept only flat values. `tools/translators/foundry.py` now
  derives it: 10 + the Dexterity modifier, or the equipped body armor's
  formula (light: base + DEX; medium: base + DEX up to the armor's cap; heavy:
  base), plus one equipped Shield (a creature wields only one). An actor
  effect that overrides the AC calculation is honoured first, so the Mage's
  Mage Armor (13 + DEX) gives it the SRD's 15. Every AC that shipped before is
  unchanged, and the regen changes only the `ac` field of those 133 files.
  Checked against the SRD 5.2 stat blocks, Flying Snake derives 12 against the
  SRD's 14, recorded in `tests/oracle/known_oracle_divergence.json`; `elk`'s
  flat 11 (SRD: 10) predates this change (BACKLOG). The engine reads
  `Monster.ac` for Wild Shape and Polymorph forms, so `dnd5e-engine` 0.6.0
  requires `dnd5e-srd-data>=0.6.0`.
  (`packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/monsters/`)
- **C23 — the translator applies the last AC-calculation override.** Foundry
  applies an actor's changes in priority order, so of several enabled
  overrides the last applied wins; the translator took the first. No
  shipped monster has two, so no canonical file changes.
  (`packages/dnd5e-srd-data/tools/translators/foundry.py::_with_actor_ac_calc`)

## [0.5.0]

Lockstep release with `dnd5e-engine` 0.5.0 and `nat20-bridge` 0.5.0.

No schema or canonical-content changes.

## [0.4.0]

Lockstep release with `dnd5e-engine` 0.4.0 and `nat20-bridge` 0.4.0.

No schema or canonical-content changes.

## [0.3.2]

Lockstep release with `dnd5e-engine` 0.3.2 and `nat20-bridge` 0.3.2.

No schema or canonical-content changes.

### Fixed

- `__version__` derives from installed package metadata (0.3.0 shipped reporting
  `0.2.0`).

### Added

- **Oracle coverage floors** (`tests/test_oracle_coverage_floor.py`). The
  fidelity suite compares canonical entries only against slugs the oracle
  contains, silently skipping the rest — so it reported green while the monster
  oracle covered **3 of 341 monsters**. Coverage is now a tested property with a
  per-category floor pinned at today's ratio, to be ratcheted upward.
- **Corpus prose integrity gate** (`tests/test_corpus_prose_integrity.py`).
  Fails on unsubstituted template placeholders in shipped descriptions. It found
  `monsters/ancient-gold-dragon` shipping `"makes {count} [[/item]] attacks"`;
  registered as an upstream defect in `tests/oracle/known_prose_defects.json`
  rather than hand-edited (the corpus is translator output).

### Changed

- Coverage floor raised 90 → 96 (actual 96.2%).

## [0.3.0]

Lockstep release with `dnd5e-engine` 0.3.0 (the release workflow publishes both
packages together). All schema changes below are additive — every new field is
nullable/defaulted, no existing field moved or changed value, and canonical
output stays byte-deterministic (`make check-regen-clean`). See
`docs/migration/v0.2-to-v0.3.md` for the full, host-facing migration guide.

### Added
- **Item charge pools** — `Item.uses: ItemUses | None` (Foundry `system.uses`: `max`,
  `spent`, `auto_destroy`, `recovery[]`). Regen populated 225 of 546 canonical items;
  the rest carry `"uses": null`. `RecoveryPeriod` gains `"dawn"` and `"dusk"`.
- **Spell uuid join key** — `Spell.foundry_uuid` (full `Compendium.dnd5e.<pack>.Item.<_id>`)
  on all 339 canonical spells, plus `AssetLoader.get_spell_by_uuid` (lazy index on
  `BundledAssetLoader`). Enables `CastActivity` uuid→Spell delegation in the engine.

### Changed
- `AssetLoader` protocol gained a required member (`get_spell_by_uuid`) — third-party
  implementers must add it. Both bundled implementations already carry it.

## [0.2.0]

Lockstep release with `dnd5e-engine` 0.2.0 (the release workflow publishes both
packages together). All schema changes below are additive — every new field is
nullable/defaulted, no existing field moved or changed value, and canonical
output stays byte-deterministic (`make check-regen-clean`).

### Added

- **`Feature.uses`** schema field (`FeatureUses`, `RecoveryRule` models) —
  carries a feature's Foundry top-level `system.uses` cap (Second Wind's capped,
  rest-recharged pool), threaded by the translator. Regenerating the canonical
  corpus added a nullable `"uses"` key to all 260 `canonical/features/*.json`
  files (47 populated, 213 `null`).
- **`Feature.advancement`** schema field — carries a feature's own
  `system.advancement[]` (ScaleValue tables owned by a granted feature, e.g.
  Channel Divinity). Regeneration added an `"advancement"` key to every
  `canonical/features/*.json` (non-empty only where the source YAML carries it).
- **`applied_effects`** field on the relevant activity schema — models Foundry's
  legacy flat `appliedEffects` id list for round-trip fidelity.

### Changed

- **SRD-correct spell damage types** — a translator override corrects damage
  types for spells whose upstream source left them typeless/mislabeled, so
  canonical spell JSON now reflects the SRD 5.2 text. Affected `canonical/spells/*.json`
  regenerated deterministically.
## [0.1.2]

Lockstep version bump; no content change.

## [0.1.1]

No content changes — version bump only, to release in lockstep with
`dnd5e-engine` 0.1.1 (the release workflow publishes both packages).

## [0.1.0]

First public release.

### Added

- Canonical D&D 5e SRD asset dataset (SRD 5.1 and 5.2), distributed under
  CC-BY-4.0 with full provenance attribution (see `NOTICE`).
- Canonical JSON shipped inside the package for `importlib.resources` lookup.
- `py.typed` marker — the package ships inline type information.
