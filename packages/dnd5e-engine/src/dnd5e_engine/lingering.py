"""A spell that goes on acting after its cast, and the rules that fire its
later activities (spec 02a B3b, issue #37).

``activities.timing`` holds back a spell's later activities at the cast. When
the engine has a producer for one, ``LATER_RULES`` says how it fires: which
trigger, on whom, how often, and what a successful save does. A cast with any
such activity leaves a ``LingeringSpell`` in the combat (and in
``CombatState.lingering_spells``) until the spell ends: its concentration
drops, its duration runs out, or every one-time activity has fired.

SRD 5.2.1 sentences decide each rule; ``docs/srd-5.2.1-spell-timing.md``
quotes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dnd5e_engine.activities.timing import LaterTrigger

__all__ = [
    "LATER_RULES",
    "SPELL_USES_START",
    "LaterRule",
    "LingeringSpell",
    "Subjects",
    "rules_for",
]

#: Whom a later activity affects when it fires.
#:
#: - ``hit``: the creatures the cast's attack roll hit (Acid Arrow).
#: - ``failed_save``: the creatures that failed the cast's save (Vitriolic Sphere).
#: - ``saved``: every creature that made the cast's save (Tsunami).
#: - ``affected``: the creatures the cast laid an effect on, as cast (Haste).
#: - ``bearing``: whoever bears an effect of this cast right now, so a moved
#:   Hunter's Mark follows its new target, and an ended effect stops it
#:   (Ensnaring Strike's "While Restrained").
#: - ``area``: the creatures in the cast's area, or the activity's own area,
#:   at the cast's point of origin (Stinking Cloud, the bead's explosion).
#: - ``caster_area``: the creatures in the cast's area centred on the caster
#:   where it stands now (an Emanation: Spirit Guardians).
#: - ``none``: nobody; the activity only counts (Delayed Blast Fireball's
#:   growing damage).
Subjects = Literal[
    "hit", "failed_save", "saved", "affected", "bearing", "area", "caster_area", "none"
]


@dataclass(frozen=True)
class LaterRule:
    """How one later activity fires.

    ``once``: each subject is affected at most once (Acid Arrow's "at the end
    of its next turn"). ``steps``: on a ``caster_turn_start``, the caster's
    turns after the cast it fires on (1 is the first). ``uses_delta``: added
    to the spell's counter before the activity resolves, which
    ``@item.uses.value`` reads. ``on_save``: what a subject's successful save
    against the activity ends, the spell for that subject or the whole spell.
    ``enemies_only``: the area spares the caster's side. ``max_subjects``:
    the most creatures one firing affects. ``area_from``: whose template
    shapes an ``area`` subject, the cast's or the activity's own."""

    trigger: LaterTrigger
    subjects: Subjects
    once: bool = False
    steps: tuple[int, ...] | None = None
    uses_delta: int = 0
    on_save: Literal["end_for_subject", "end_spell"] | None = None
    enemies_only: bool = False
    max_subjects: int | None = None
    area_from: Literal["cast", "self"] = "cast"


#: The later activities the engine fires, by spell and activity id. A later
#: activity absent here is held back and never fires (``BACKLOG.md``).
LATER_RULES: Final[dict[str, dict[str, LaterRule]]] = {
    # "On a hit, the target takes 4d4 Acid damage and 2d4 Acid damage at the
    # end of its next turn."
    "acid-arrow": {"uAyE5DrJp0YpElcw": LaterRule("target_turn_end", "hit", once=True)},
    # "On a failed save, a creature takes 10d4 Acid damage and another 5d4 Acid
    # damage at the end of its next turn."
    "vitriolic-sphere": {
        "dnd5eactivity200": LaterRule("target_turn_end", "failed_save", once=True)
    },
    # "A Frightened target makes a Wisdom saving throw at the end of each of
    # its turns. On a failed save, it takes 5d10 Psychic damage. On a
    # successful save, the spell ends on that target."
    "weird": {
        "nuStSySOEkwOUnXf": LaterRule("target_turn_end", "bearing", on_save="end_for_subject")
    },
    # "While Restrained, the target takes 1d6 Piercing damage at the start of
    # each of its turns."
    "ensnaring-strike": {"ZWId9mbOE9zFnP6f": LaterRule("target_turn_start", "bearing")},
    # "At the start of each of its turns until the spell ends, the target
    # takes 1d6 Fire damage and then makes a Constitution saving throw. ... On
    # a successful save, the spell ends."
    "searing-smite": {
        "dnd5eactivity000": LaterRule("target_turn_start", "bearing", on_save="end_spell")
    },
    # "Each creature that starts its turn in the Sphere must succeed on a
    # Constitution saving throw or have the Poisoned condition until the end
    # of the current turn."
    "stinking-cloud": {"dnd5eactivity000": LaterRule("target_turn_start", "area")},
    # "whenever a creature enters the Emanation or ends its turn there, the
    # creature must make a Wisdom saving throw." The end-of-turn half; the
    # caster designates the creatures it spares, taken as its allies.
    "spirit-guardians": {
        "dnd5eactivity000": LaterRule("target_turn_end", "caster_area", enemies_only=True)
    },
    # "whenever a creature you can see enters the Emanation or ends its turn
    # there, you can force that creature to make a Wisdom saving throw." The
    # end-of-turn half, forced on the caster's enemies.
    "conjure-woodland-beings": {
        "UkXLSgFbCBI9CHMf": LaterRule("target_turn_end", "caster_area", enemies_only=True)
    },
    # "The spell's base damage is 12d6, and the damage increases by 1d6
    # whenever your turn ends and the spell hasn't ended." "When the spell
    # ends, the bead explodes, and each creature in a 20-foot-radius Sphere
    # centered on that point makes a Dexterity saving throw."
    "delayed-blast-fireball": {
        "9i14Jmun9em69EnX": LaterRule("caster_turn_end", "none", uses_delta=1),
        "M15GlfjeWy7Cdiqn": LaterRule("spell_ends", "area", area_from="self"),
    },
    # "At the start of each of your turns after the wall appears ... Any Huge
    # or smaller creature inside the wall ... must succeed on a Strength
    # saving throw or take 5d10 Bludgeoning damage"; "the damage the wall
    # deals on later rounds is reduced by 1d10". The wall can't be placed on
    # the grid, so it stays on the creatures its cast caught.
    "tsunami": {"o7MOuS6uL1ZV3dhe": LaterRule("caster_turn_start", "saved", uses_delta=-1)},
    # "At the start of each of your later turns, the storm produces different
    # effects": acid on turn 2, six lightning bolts on turn 3, hail on turn 4,
    # gusts and freezing rain on turns 5 to 10.
    "storm-of-vengeance": {
        "WOo8u0FLAzNMpMJN": LaterRule("caster_turn_start", "area", steps=(1,)),
        "Fd8ZGgmDyNmJJuj3": LaterRule(
            "caster_turn_start", "area", steps=(2,), enemies_only=True, max_subjects=6
        ),
        "H4uCtCkANBkttnov": LaterRule("caster_turn_start", "area", steps=(3,)),
        "yt2oWWelZl1zV4CB": LaterRule("caster_turn_start", "area", steps=(4, 5, 6, 7, 8, 9)),
    },
    # "When the spell ends, the target is Incapacitated and has a Speed of 0
    # until the end of its next turn".
    "haste": {"rBQrZnq7cHkSUAK9": LaterRule("spell_ends", "affected")},
    # "you deal an extra 1d6 Necrotic damage to the target whenever you hit it
    # with an attack roll."
    "hex": {"dnd5eactivity000": LaterRule("on_hit", "bearing")},
    # "you deal an extra 1d6 Force damage to the target whenever you hit it
    # with an attack roll."
    "hunters-mark": {"dnd5eactivity000": LaterRule("on_hit", "bearing")},
    # "any attack you make deals an extra 2d8 damage when you hit a creature in
    # the Emanation."
    "conjure-minor-elementals": {"dnd5eactivity000": LaterRule("on_hit", "caster_area")},
}

#: ``@item.uses.value`` when the spell is cast: Delayed Blast Fireball's extra
#: dice start at none; Tsunami's damage dice start one above the first
#: round's 5d10, since each round takes one before it rolls.
SPELL_USES_START: Final[dict[str, int]] = {"delayed-blast-fireball": 0, "tsunami": 6}


def rules_for(spell_slug: str) -> dict[str, LaterRule]:
    """The fired later activities of ``spell_slug``, by activity id."""
    return LATER_RULES.get(spell_slug, {})


class LingeringSpell(BaseModel):
    """A cast whose later activities still wait for their triggers.

    ``cast_turn`` is the ``(round, caster)`` turn of the cast, which is no
    subject's "next turn". ``subject_ids`` are the cast's ``hit`` / ``failed_save`` / ``saved`` /
    ``affected`` creatures. ``origin`` and ``direction`` place an ``area``;
    ``area_activity_id`` is the cast activity whose template shapes it.
    ``caster_turns_started`` counts the caster's turns since the cast;
    ``uses`` is the spell's counter. ``fired`` holds ``"<activity>:<subject>"``
    for each one-time firing done, and ``ended_for`` the subjects the spell
    has ended on. ``rounds_remaining`` bounds a spell without concentration,
    counted down at the end of each of the caster's turns."""

    model_config = ConfigDict(extra="forbid")

    id: str
    spell_id: str
    caster_id: str
    cast_turn: tuple[int, str]
    slot_level: int | None = None
    concentration: bool = False
    subject_ids: list[str] = Field(default_factory=list)
    origin: str | None = None
    direction: tuple[int, int] | None = None
    area_activity_id: str | None = None
    caster_turns_started: int = 0
    uses: int = 0
    fired: list[str] = Field(default_factory=list)
    ended_for: list[str] = Field(default_factory=list)
    rounds_remaining: int | None = None
