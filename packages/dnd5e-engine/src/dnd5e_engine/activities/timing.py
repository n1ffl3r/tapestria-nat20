"""When each activity of a spell resolves: at the cast, as one of several
cast-time options, or on a later trigger (spec 02a B3b, issue #37).

A spell's activities are Foundry's: one spell can carry the activity its cast
resolves, alternatives the caster picks between, and activities Foundry fires
on a later trigger. The engine used to resolve every one of them when the spell
was cast, so Weird's end-of-turn save rolled at the cast, Hunter's Mark dealt
its 1d6 at the cast, and Delayed Blast Fireball exploded at the cast.

``SPELL_ACTIVITY_TIMINGS`` records, for every spell with more than one
activity, each activity that does not simply resolve with the cast, with the
SRD 5.2.1 sentence that decides it. An activity absent from the table resolves
at the cast. ``cast_time_activities`` is what a cast resolves:

- every ``cast`` activity;
- of each ``alternative`` group, the activity the caster picked
  (``PlayerIntent.activity_id``), else the group's first;
- no ``later`` activity. The orchestrator fires a later activity at its
  ``trigger`` (``dnd5e_engine.lingering``) when the engine has a producer for
  it; the others are held back (``BACKLOG.md``).

Generated from a reading of each spell's SRD 5.2.1 text; keep it in step with
the corpus (``tests/test_lingering_spells.py`` checks every key exists).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "SPELL_ACTIVITY_TIMINGS",
    "ActivityTiming",
    "LaterTrigger",
    "cast_time_activities",
]

#: The trigger a later activity waits for.
LaterTrigger = Literal[
    "caster_turn_start",
    "caster_turn_end",
    "target_turn_start",
    "target_turn_end",
    "enters_area",
    "on_hit",
    "on_attacked",
    "on_damage_taken",
    "caster_action",
    "spell_ends",
    "other",
]


@dataclass(frozen=True)
class ActivityTiming:
    """One activity that does not simply resolve with its spell's cast.

    ``timing`` is ``"alternative"`` (one of the cast-time options in
    ``group``) or ``"later"`` (it waits for ``trigger``; ``also`` names the
    other triggers the SRD gives it)."""

    timing: Literal["alternative", "later"]
    group: str | None = None
    trigger: LaterTrigger | None = None
    also: tuple[LaterTrigger, ...] = ()


def cast_time_activities[A](
    spell_slug: str, activities: Sequence[A], chosen_id: str | None = None
) -> list[A]:
    """The activities a cast of ``spell_slug`` resolves, in their order: each
    one not in the table, and of each alternative group the one ``chosen_id``
    names, else the group's first."""
    timings = SPELL_ACTIVITY_TIMINGS.get(spell_slug)
    if not timings:
        return list(activities)
    chosen = timings.get(chosen_id or "")
    chosen_group = chosen.group if chosen is not None and chosen.timing == "alternative" else None
    kept: list[A] = []
    seen_groups: set[str | None] = set()
    for activity in activities:
        timing = timings.get(getattr(activity, "id", ""))
        if timing is None:
            kept.append(activity)
        elif timing.timing == "alternative":
            group = timing.group
            if group in seen_groups:
                continue
            if group == chosen_group and getattr(activity, "id", "") != chosen_id:
                continue
            seen_groups.add(group)
            kept.append(activity)
    return kept


# SRD 5.2.1 sentences decide each entry; see ``docs/srd-5.2.1-spell-timing.md``
# for the quotes.
SPELL_ACTIVITY_TIMINGS: Final[dict[str, dict[str, ActivityTiming]]] = {
    "acid-arrow": {
        "uAyE5DrJp0YpElcw": ActivityTiming(
            "later", trigger="target_turn_end"
        ),  # Lingering Acid Damage
    },
    "alarm": {
        "dnd5eactivity000": ActivityTiming("alternative", group="ward_target"),  # Cast over Area
        "gXbhFkULaCTcB0WJ": ActivityTiming(
            "alternative", group="ward_target"
        ),  # Cast on Window or Door
    },
    "alter-self": {
        "dnd5eactivity000": ActivityTiming(
            "alternative", group="form_option"
        ),  # Aquatic Adaptation
        "wQGpfux8qzcvwsDo": ActivityTiming("alternative", group="form_option"),  # Change Appearance
        "dQnMvyjoGqhgl9wP": ActivityTiming("alternative", group="form_option"),  # Natural Weapons
        "SEwXqOHdPCyejSYR": ActivityTiming("later", trigger="caster_action"),  # Attack: Claws
        "osceJQzUOQPJ9CLn": ActivityTiming("later", trigger="caster_action"),  # Attack: Fangs/Horns
        "eHtpASAnamFUc69H": ActivityTiming("later", trigger="caster_action"),  # Attack: Hooves
    },
    "antipathy-sympathy": {
        "VlGQ41g1j0LeL8QY": ActivityTiming("later", trigger="enters_area"),  # Antipathy Save
        "J6kTEeQc2cCyfppj": ActivityTiming("later", trigger="enters_area"),  # Sympathy Save
    },
    "bestow-curse": {
        "PYFPZ4aLWjKJ1tLZ": ActivityTiming("alternative", group="curse_effect"),  # Curse Ability
        "azVyTkiGPeIqMzxu": ActivityTiming("alternative", group="curse_effect"),  # Curse Attacks
        "nWXSXy8ybiVolvEP": ActivityTiming("alternative", group="curse_effect"),  # Curse Actions
        "fkQUKM8bxHgAUcg5": ActivityTiming("alternative", group="curse_effect"),  # Curse Resilience
    },
    "blade-barrier": {
        "Y0nT1EvDbg7IDovC": ActivityTiming("alternative", group="wall_shape"),  # Blade Wall
        "SRwETzebAYXEJKux": ActivityTiming("alternative", group="wall_shape"),  # Blade Ring
    },
    "call-lightning": {
        "dnd5eactivity200": ActivityTiming("alternative", group="storm"),  # (unnamed)
    },
    "conjure-animals": {
        "dnd5eactivity100": ActivityTiming(
            "later", trigger="enters_area", also=("target_turn_end",)
        ),  # (unnamed)
    },
    "conjure-celestial": {
        "ke8eykrnAdAq5nfZ": ActivityTiming(
            "alternative", group="light_per_creature"
        ),  # Healing Light
        "naMU3mLP7jLZu1IG": ActivityTiming(
            "alternative", group="light_per_creature"
        ),  # Searing Light
    },
    "conjure-fey": {
        "sKmRcy0IDp9aOuAt": ActivityTiming("later", trigger="caster_action"),  # Teleport and Attack
    },
    "conjure-minor-elementals": {
        "dnd5eactivity000": ActivityTiming("later", trigger="on_hit"),  # Bonus Attack Damage
    },
    "conjure-woodland-beings": {
        "GAQKUmYcWR3SjxiV": ActivityTiming("later", trigger="caster_action"),  # Disengage
        "UkXLSgFbCBI9CHMf": ActivityTiming(
            "later", trigger="enters_area", also=("target_turn_end",)
        ),  # Emanation Save
    },
    "control-water": {
        "dnd5eactivity000": ActivityTiming("alternative", group="water_effect"),  # Create Whirlpool
        "uvlPi5Em9KSfKICT": ActivityTiming(
            "alternative", group="water_effect"
        ),  # Flood, Part, or Redirect
    },
    "control-weather": {
        "ucJPCYAaXqiukPF9": ActivityTiming("later", trigger="other"),  # Change Conditions
    },
    "create-undead": {
        "ExyqLKqLhGbaJVVd": ActivityTiming("alternative", group="cast_mode"),  # Animate Corpses
        "BepVtap2tWyUwaxk": ActivityTiming("alternative", group="cast_mode"),  # Reassert Control
        "CIv9t4XuVEcgmX9n": ActivityTiming("later", trigger="caster_action"),  # Mentally Command
    },
    "dancing-lights": {
        "4a3L8r8Iobi24dBr": ActivityTiming("later", trigger="caster_action"),  # Move Lights
    },
    "delayed-blast-fireball": {
        "iWd7zKpQIgWofJAp": ActivityTiming("later", trigger="other"),  # Touch Bead
        "9i14Jmun9em69EnX": ActivityTiming(
            "later", trigger="caster_turn_end"
        ),  # Turn End Damage Increase
        "M15GlfjeWy7Cdiqn": ActivityTiming("later", trigger="spell_ends"),  # Trigger Explosion
    },
    "detect-thoughts": {
        "dnd5eactivity000": ActivityTiming("later", trigger="caster_action"),  # Probe Deeper
        "cNjNJvq5tlzmAjzM": ActivityTiming("alternative", group="detect_effect"),  # Sense Thoughts
    },
    "dispel-evil-and-good": {
        "dnd5eactivity000": ActivityTiming("later", trigger="caster_action"),  # Dismissal
        "eBbyE0du7mEJWDbZ": ActivityTiming("later", trigger="caster_action"),  # Break Enchantment
    },
    "divine-smite": {
        "dnd5eactivity000": ActivityTiming(
            "alternative", group="target_type"
        ),  # Unholy Creature Damage
        "dnd5eactivity200": ActivityTiming("alternative", group="target_type"),  # Cast
    },
    "dragons-breath": {
        "61nfYLT1M7MOTibf": ActivityTiming("later", trigger="caster_action"),  # Exhale
    },
    "earthquake": {
        "2CCthCZliyjEoavy": ActivityTiming(
            "later", trigger="caster_turn_end"
        ),  # End of Turn Fissures
        "qQtXMvVJZDL3NtNF": ActivityTiming("later", trigger="other"),  # Collapsing Structure Save
    },
    "elementalism": {
        "NND0Ry2h5vSfZgq5": ActivityTiming("alternative", group="effect"),  # Beckon Element
        "8IholW9syQX6TFOg": ActivityTiming("alternative", group="effect"),  # Sculpt Element
    },
    "ensnaring-strike": {
        "ZWId9mbOE9zFnP6f": ActivityTiming(
            "later", trigger="target_turn_start"
        ),  # Start of Turn Damage
    },
    "eyebite": {
        "3PZbzzH8sBJl3heA": ActivityTiming("later", trigger="caster_action"),  # While Concentrating
    },
    "faithful-hound": {
        "vIOe9RHtAUBrnsR6": ActivityTiming("later", trigger="caster_action"),  # Move Hound
    },
    "find-steed": {
        "dnd5eactivity000": ActivityTiming("alternative", group="steed"),  # Summon Steed
        "o4YsONnsS8rOOEaS": ActivityTiming("alternative", group="steed"),  # Summon Flying Steed
    },
    "finger-of-death": {
        "bfoGmw7iFaVObnn6": ActivityTiming("later", trigger="caster_turn_start"),  # Raise Zombie
    },
    "flame-blade": {
        "dnd5eactivity000": ActivityTiming("later", trigger="caster_action"),  # (unnamed)
    },
    "flaming-sphere": {
        "dnd5eactivity100": ActivityTiming(
            "later", trigger="target_turn_end", also=("caster_action",)
        ),  # (unnamed)
    },
    "forbiddance": {
        "9IBiOeIf2PC1wcLp": ActivityTiming(
            "later", trigger="enters_area", also=("target_turn_end",)
        ),  # Damage Forbidden Creature
    },
    "forcecage": {
        "gacP7LGiwvQBdnfU": ActivityTiming("alternative", group="prison_shape"),  # Create Cage
        "xhJw5fRtiWP5p0DO": ActivityTiming("alternative", group="prison_shape"),  # Create Solid Box
    },
    "freezing-sphere": {
        "adCBWrctRmLQmb8M": ActivityTiming("alternative", group="fire_or_hold"),  # Cast and Fire
        "v9chdKbduV5WVr97": ActivityTiming("alternative", group="fire_or_hold"),  # Cast and Hold
        "NKBsnjBBIgsaOPaY": ActivityTiming("later", trigger="caster_action"),  # Throw Held Globe
    },
    "gate": {
        "2RkjJD6gPT6nVMUO": ActivityTiming("alternative", group="portal"),  # Small Vertical Portal
        "rR2zwuw2IFFRTQhi": ActivityTiming("alternative", group="portal"),  # Large Vertical Portal
        "iUhxzsW99W33R6di": ActivityTiming(
            "alternative", group="portal"
        ),  # Small Horizontal Portal
        "B3wsBcXONvUm9pUR": ActivityTiming(
            "alternative", group="portal"
        ),  # Large Horizontal Portal
    },
    "glyph-of-warding": {
        "dnd5eactivity000": ActivityTiming("alternative", group="glyph_type"),  # Spell Glyph
        "dnd5eactivity100": ActivityTiming(
            "later", trigger="other", group="glyph_type"
        ),  # Explosive Rune
    },
    "guardian-of-faith": {
        "dnd5eactivity100": ActivityTiming(
            "later", trigger="enters_area", also=("target_turn_start",)
        ),  # (unnamed)
    },
    "haste": {
        "rBQrZnq7cHkSUAK9": ActivityTiming("later", trigger="spell_ends"),  # Apply Lethargy
    },
    "heat-metal": {
        "dnd5eactivity000": ActivityTiming("later", trigger="on_damage_taken"),  # On Damage Save
        "YPEmwJHX7g68307N": ActivityTiming("later", trigger="caster_action"),  # Reheat
    },
    "heroes-feast": {
        "6GsLAWV31TuE6ysp": ActivityTiming("later", trigger="other"),  # Consume Feast
    },
    "hex": {
        "dnd5eactivity000": ActivityTiming("later", trigger="on_hit"),  # Bonus Hex Damage
        "E3f3Y8ULb3t5mBQu": ActivityTiming("later", trigger="caster_action"),  # Curse New Creature
    },
    "holy-aura": {
        "0kylpwRauH0WgW0G": ActivityTiming("later", trigger="on_attacked"),  # Fiend/Undead Save
    },
    "hunters-mark": {
        "dnd5eactivity000": ActivityTiming("later", trigger="on_hit"),  # Bonus Mark Damage
        "NQtVK0T5uwfMPGQL": ActivityTiming("later", trigger="caster_action"),  # Move Mark
    },
    "incendiary-cloud": {
        "lLAC6qfRLaes7956": ActivityTiming(
            "later", trigger="enters_area", also=("target_turn_end",)
        ),  # Per Turn Save
    },
    "instant-summons": {
        "ASO8TzkhkDzSNQAD": ActivityTiming("later", trigger="caster_action"),  # Recall Object
    },
    "magic-jar": {
        "Ak9GmFAQNh9o2ioq": ActivityTiming("later", trigger="caster_action"),  # Possess Humanoid
        "lXTy3zj3DwBH2QJc": ActivityTiming("later", trigger="caster_action"),  # Return from Host
    },
    "meld-into-stone": {
        "dnd5eactivity000": ActivityTiming("later", trigger="other"),  # (unnamed)
        "dnd5eactivity200": ActivityTiming("later", trigger="other"),  # (unnamed)
    },
    "move-earth": {
        "YTVF3OSu0S9sYkQb": ActivityTiming("later", trigger="other"),  # Affect New Area
    },
    "phantasmal-force": {
        "QCUpctimdMH6E6F8": ActivityTiming("later", trigger="other"),  # Phantasm Damage
    },
    "plant-growth": {
        "dnd5eactivity000": ActivityTiming("alternative", group="casting_time"),  # Overgrowth
        "IrJvud8Qz62PeeIQ": ActivityTiming("alternative", group="casting_time"),  # Enrichment
    },
    "prismatic-spray": {
        "hEE5tqet1GwtXI0h": ActivityTiming("later", trigger="target_turn_end"),  # Indigo Save (Con)
        "aVMkONwxZ9IGOjZs": ActivityTiming(
            "later", trigger="caster_turn_start"
        ),  # Violet Save (Wis)
    },
    "prismatic-wall": {
        "5f0JerdDSOQQXiTe": ActivityTiming("alternative", group="wall_shape"),  # Create Wall
        "fOYr1bgsB1VXROcm": ActivityTiming("alternative", group="wall_shape"),  # Create Globe
        "Pm0Re9Gt8pJNaF1C": ActivityTiming(
            "later", trigger="enters_area", also=("target_turn_start",)
        ),  # Blinding Save
        "fxEIn0sNwZReZJhV": ActivityTiming("later", trigger="enters_area"),  # Traversal Save
    },
    "produce-flame": {
        "tqKlIEdglRYdqHrp": ActivityTiming("later", trigger="caster_action"),  # Hurl Fire
    },
    "resurrection": {
        "IEI29s99C2llhqEo": ActivityTiming("alternative", group="dead_duration"),  # Cast
        "EMbtn8GwrximmlvQ": ActivityTiming("alternative", group="dead_duration"),  # Taxing Cast
    },
    "sanctuary": {
        "6TdVnZPI3lxQrycI": ActivityTiming("later", trigger="on_attacked"),  # Save on Target
    },
    "scrying": {
        "NeUDDgmX3BdnFGH4": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Secondhand and Picture
        "2CjCuSPwb4erFwZC": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Secondhand and Garment
        "AwH5PhQZyU2ryO7X": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Secondhand and Body Part
        "L9ejQXxfSH0qg8bB": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Firsthand and Picture
        "QUv35ZZBe3gVvEzK": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Firsthand and Garment
        "xhhjJb25roNtpn8g": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Firsthand and Body Part
        "7wBprDveLHpbu6Z0": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Extensive and Picture
        "QUhtomQ1kgxShABi": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Extensive and Garment
        "LwyHxV46sddf89rc": ActivityTiming(
            "alternative", group="scry_target"
        ),  # Extensive and Body Part
        "aeMjBXbdsC7SxTTv": ActivityTiming("alternative", group="scry_target"),  # Create Sensor
    },
    "searing-smite": {
        "dnd5eactivity000": ActivityTiming(
            "later", trigger="target_turn_start"
        ),  # Start of Turn Save
    },
    "secret-chest": {
        "dnd5eactivity000": ActivityTiming("later", trigger="caster_action"),  # Recall Chest
    },
    "shillelagh": {
        "ONVab5lmTqJwwMBC": ActivityTiming("later", trigger="caster_action"),  # Spellcasting Attack
    },
    "spirit-guardians": {
        "dnd5eactivity000": ActivityTiming(
            "later", trigger="enters_area", also=("target_turn_end",)
        ),  # Emanation Save
    },
    "stinking-cloud": {
        "dnd5eactivity000": ActivityTiming(
            "later", trigger="target_turn_start"
        ),  # Start of Turn Save
    },
    "storm-of-vengeance": {
        "WOo8u0FLAzNMpMJN": ActivityTiming(
            "later", trigger="caster_turn_start"
        ),  # Turn 2: Acid Rain
        "Fd8ZGgmDyNmJJuj3": ActivityTiming(
            "later", trigger="caster_turn_start"
        ),  # Turn 3: Lightning
        "H4uCtCkANBkttnov": ActivityTiming(
            "later", trigger="caster_turn_start"
        ),  # Turn 4: Hailstones
        "yt2oWWelZl1zV4CB": ActivityTiming(
            "later", trigger="caster_turn_start"
        ),  # Turn 5+: Gusts and Rain
    },
    "sunbeam": {
        "Y0cJvZuD7EfwGqkf": ActivityTiming("later", trigger="caster_action"),  # Create New Sunbeam
    },
    "symbol": {
        "oCs1sUAtUAQJwxPU": ActivityTiming(
            "later", trigger="other", group="symbol_effect", also=("enters_area", "target_turn_end")
        ),  # Death
        "DHsotF5GdSA4Nrpz": ActivityTiming(
            "later", trigger="other", group="symbol_effect", also=("enters_area", "target_turn_end")
        ),  # Discord
        "7mW8h9xFzr7NNsrr": ActivityTiming(
            "later", trigger="other", group="symbol_effect", also=("enters_area", "target_turn_end")
        ),  # Fear
        "doNUpTnMIKmfuCIa": ActivityTiming(
            "later", trigger="other", group="symbol_effect", also=("enters_area", "target_turn_end")
        ),  # Pain
        "a2n9gGZMabXdO8Sp": ActivityTiming(
            "later", trigger="other", group="symbol_effect", also=("enters_area", "target_turn_end")
        ),  # Sleep
        "4TPw3B0RROokuGNg": ActivityTiming(
            "later", trigger="other", group="symbol_effect", also=("enters_area", "target_turn_end")
        ),  # Stunning
    },
    "true-strike": {
        "CiLsaHBi9F6eIXUS": ActivityTiming(
            "alternative", group="weapon_attack"
        ),  # Spellcasting Attack
        "cGNXKjpoRima6wjQ": ActivityTiming(
            "alternative", group="weapon_attack"
        ),  # Spellcasting Attack
    },
    "tsunami": {
        "o7MOuS6uL1ZV3dhe": ActivityTiming(
            "later", trigger="caster_turn_start"
        ),  # Start of Turn Effects
    },
    "vitriolic-sphere": {
        "dnd5eactivity200": ActivityTiming(
            "later", trigger="target_turn_end"
        ),  # End of Turn Damage
    },
    "wall-of-fire": {
        "dnd5eactivity000": ActivityTiming("alternative", group="wall_shape"),  # Create Wall
        "aLgpPe1PPRQ0zIrL": ActivityTiming("alternative", group="wall_shape"),  # Create Ring
        "xHKGvC597exYwpc5": ActivityTiming(
            "later", trigger="target_turn_end", also=("enters_area",)
        ),  # (unnamed)
    },
    "wall-of-force": {
        "obcyQX0IlwinXINV": ActivityTiming("alternative", group="wall_shape"),  # Sphere or Globe
        "O3uhE0OLzsAhHoAg": ActivityTiming("alternative", group="wall_shape"),  # Panels
    },
    "wall-of-ice": {
        "c4ttlpiIpyf3dFdf": ActivityTiming("alternative", group="wall_shape"),  # Create Globe
        "zjpb29Tww0iuv1P3": ActivityTiming("alternative", group="wall_shape"),  # Create Panels
        "D8Q85bjeZgiFky5j": ActivityTiming("later", trigger="enters_area"),  # Frigid Air
    },
    "wall-of-stone": {
        "8GZK7DivdBjael8n": ActivityTiming("alternative", group="panel_size"),  # Square Panels
        "44JI57djAchqMJrU": ActivityTiming("alternative", group="panel_size"),  # Long Panels
    },
    "wall-of-thorns": {
        "Az2cq7SukeQquptz": ActivityTiming("alternative", group="wall_shape"),  # Create Wall
        "cgge2ISpX3u70Dof": ActivityTiming("alternative", group="wall_shape"),  # Create Circle
        "dMiM7Qec4keU3w7B": ActivityTiming(
            "later", trigger="enters_area", also=("target_turn_end",)
        ),  # Traversal Save
    },
    "weird": {
        "nuStSySOEkwOUnXf": ActivityTiming("later", trigger="target_turn_end"),  # End of Turn Save
    },
    "wind-wall": {
        "dnd5eactivity000": ActivityTiming("alternative", group="wall_shape"),  # Create Single Wall
        "H2KvnEEAgoE3YeK5": ActivityTiming(
            "alternative", group="wall_shape"
        ),  # Create Segmented Wall
    },
    "word-of-recall": {
        "fErYiqUpSCDHfV61": ActivityTiming("alternative", group="use"),  # Teleport to Sanctuary
        "CJ6NOsqtoO3HCGjQ": ActivityTiming("alternative", group="use"),  # Designate Sanctuary
    },
}
