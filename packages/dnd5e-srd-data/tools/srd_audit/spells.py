"""A spell's mechanical fields, read from its SRD 5.2.1 text and from its corpus record.

Both readers return the same shape, a mapping from field name to a JSON value, so the
ledger compares them field by field. A field the PDF text does not state (a spell with no
damage has no ``damage``) is left out of the PDF side and so is not compared.

The fields are the ones spec 02a lists for spells: level, school, classes, casting time,
range, components (with the material's cost and whether the spell consumes it), duration,
concentration, ritual, attack or save (with its ability), damage dice and type, healing
dice, area shape and size, and higher-level scaling.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tools.srd_audit.pdf_text import RecordText, flatten

Facts = dict[str, Any]

SCHOOLS = {
    "Abjuration": "abj",
    "Conjuration": "con",
    "Divination": "div",
    "Enchantment": "enc",
    "Evocation": "evo",
    "Illusion": "ill",
    "Necromancy": "nec",
    "Transmutation": "trs",
}
ABILITIES = {
    "Strength": "str",
    "Dexterity": "dex",
    "Constitution": "con",
    "Intelligence": "int",
    "Wisdom": "wis",
    "Charisma": "cha",
}
DAMAGE_TYPES = (
    "Acid",
    "Bludgeoning",
    "Cold",
    "Fire",
    "Force",
    "Lightning",
    "Necrotic",
    "Piercing",
    "Poison",
    "Psychic",
    "Radiant",
    "Slashing",
    "Thunder",
)
# The SRD's area shapes, keyed by the template type the corpus (Foundry) records for each.
# Foundry records an Emanation as "radius"; its "wall", "circle", and "square" templates
# have no SRD shape, so they are not compared.
AREA_SHAPES = {
    "sphere": "sphere",
    "cube": "cube",
    "cone": "cone",
    "cylinder": "cylinder",
    "line": "line",
    "radius": "emanation",
}
HEADER_LINE = re.compile(r"(Casting Time|Range|Components?|Duration): (.+)$")
UNIT_WORDS = {
    "round": "round",
    "minute": "minute",
    "hour": "hour",
    "day": "day",
    "foot": "ft",
    "feet": "ft",
    "mile": "mi",
}

TYPE_LINE = re.compile(r"^(?:Level (\d) (\w+)|(\w+) Cantrip) \((.*)\)$")
AMOUNT = re.compile(r"^(\d+) (round|minute|hour|day|foot|feet|mile)s?$")
COST = re.compile(r"([\d,]+)\+ ?GP\b")
DICE = r"\d+d\d+"
TYPE_RUN = rf"(?:{'|'.join(DAMAGE_TYPES)})"
# "8d6 Fire damage", "2d8 Radiant or Necrotic damage", "1d8 damage of a type you choose",
# "1d6 plus your spellcasting ability modifier Fire damage".
DAMAGE = re.compile(
    rf"\b({DICE})(?: \+ \d+| plus [a-z' ]+?modifier)? ((?:{TYPE_RUN}(?:,? (?:or |and )?)?)*)"
    rf"\s?damage\b"
)
# "Fire damage equal to 3d6 plus your spellcasting ability modifier".
DAMAGE_EQUAL = re.compile(rf"\b((?:{TYPE_RUN}(?:,? (?:or |and )?)?)+) damage equal to ({DICE})\b")
# An untyped roll whose type the spell gives elsewhere: "1d8 damage of a type you choose",
# "3d6 damage of the chosen type", "an extra 2d8 damage ... This damage is Acid, Cold, Fire,
# or Lightning".
CHOSEN_TYPE = re.compile(
    r" of (?:a|the|that) (?:type|chosen type|spirit's type)|[^.]*\. This damage is ([^.(]*)"
)
HEALING = (
    re.compile(rf"\bregains? (?:a number of Hit Points equal to |Hit Points equal to )?({DICE})\b"),
    re.compile(rf"\bgains? ({DICE})(?: \+ \d+)? Temporary Hit Points\b"),
    re.compile(rf"\bHit Point maximum (?:also )?increases by ({DICE})\b"),
)
SAVE = re.compile(r"\b(" + "|".join(ABILITIES) + r") saving throws?\b")
MODIFIED_SAVE = re.compile(r"\b(?:Advantage|Disadvantage|penalty|bonus) (?:on|to)\b")
ATTACK = re.compile(r"\b(melee|ranged) spell attack", re.IGNORECASE)
SHAPE = r"(Sphere|Cube|Cone|Cylinder|Emanation)"
# Each pattern reads an area's shape and size; the size is a Sphere's, Cylinder's, or
# Emanation's radius, a Cube's side, and a Cone's or Line's length.
AREAS: tuple[tuple[re.Pattern[str], str | None], ...] = (
    # "20-foot-radius Sphere", "15-foot Cone", "10-foot-radius, 40-foot-high Cylinder",
    # "50-foot-radius, 100-foot high Cylinder".
    (re.compile(rf"\b(\d+)-foot(?:-radius)?(?:,? \d+-foot[- ](?:high|tall))? {SHAPE}"), None),
    # "Cylinder that is 10 feet tall with a 60-foot radius".
    (re.compile(r"\bCylinder that is \d+ feet tall with a (\d+)-foot radius"), "cylinder"),
    # "a Cube up to 100 feet on a side".
    (re.compile(r"\bCube up to (\d+) feet on a side"), "cube"),
    # "a 100-foot-long, 5-foot-wide Line", "a 5-foot-wide, 60-foot-long Line", "A Line of
    # strong wind 60 feet long".
    (re.compile(r"\b(\d+)-foot-long, \d+-foot-wide Line"), "line"),
    (re.compile(r"\b\d+-foot-wide, (\d+)-foot-long Line"), "line"),
    (re.compile(r"\bLine (?:of [a-z ]+ )?(\d+) feet long"), "line"),
)
# "The damage increases by 1d6 for each spell slot level above 3"; "the damage the wall
# deals when it appears increases by 2d6 and the damage from passing through the sheet of
# frigid air increases by 1d6"; "The Temporary Hit Points increase by 5 for each ...".
SCALING = re.compile(rf"\bincreases? by ({DICE}|\d+(?= for each))\b")
BONUS_DAMAGE = re.compile(rf"^({DICE})(?:\[(\w+)\])?$")
HIGHER_LEVEL = re.compile(r"\b(?:Using a Higher-Level Spell Slot|Cantrip Upgrade)\.")


def _header(text: RecordText) -> tuple[dict[str, str], list[str]]:
    """The header fields, each wrapped value joined, and the description lines after them."""
    fields: dict[str, str] = {}
    key: str | None = None
    lines = list(text.lines[1:])
    body_start = len(lines)
    for position, line in enumerate(lines):
        stripped = line.strip()
        header = HEADER_LINE.search(stripped)
        if header is not None:
            # A sidebar stat block's widest row can spill into the column beside it and
            # sit in front of a header line ("10 +0 +0Casting Time: 1 hour").
            key = "Components" if header.group(1) == "Component" else header.group(1)
            fields[key] = header.group(2).strip()
            continue
        if key == "Duration":
            body_start = position
            break
        if key is not None:
            fields[key] = flatten(fields[key] + "\n" + stripped)
        else:
            fields["Type"] = flatten(fields.get("Type", "") + "\n" + stripped)
    return fields, lines[body_start:]


def _amount(value: str) -> dict[str, Any] | None:
    match = AMOUNT.match(value)
    if match is None:
        return None
    return {"units": UNIT_WORDS[match.group(2)], "value": int(match.group(1))}


def _casting_time(value: str) -> tuple[dict[str, Any], bool]:
    """The first casting time listed, and whether the spell can be cast as a Ritual.
    "Action (Overgrowth) or 8 hours (Enrichment)" records the Action."""
    ritual = value.endswith(" or Ritual")
    first = value.removesuffix(" or Ritual").split(",")[0]
    first = re.sub(r" \([^)]*\).*$", "", first).strip()
    if first in ("Action", "Bonus Action", "Reaction"):
        unit = {"Action": "action", "Bonus Action": "bonus", "Reaction": "reaction"}[first]
        return {"unit": unit, "value": 1}, ritual
    amount = _amount(first)
    if amount is None:
        raise ValueError(f"unread casting time {value!r}")
    return {"unit": amount["units"], "value": amount["value"]}, ritual


def _range(value: str) -> dict[str, Any]:
    first = re.sub(r" \(.*\)$", "", value).strip()
    named = {"Self": "self", "Touch": "touch", "Sight": "spec", "Special": "spec"}
    if first in named:
        return {"units": named[first], "value": None}
    if first == "Unlimited":
        return {"units": "any", "value": None}
    amount = _amount(first)
    if amount is None:
        raise ValueError(f"unread range {value!r}")
    return amount


def _duration(value: str) -> tuple[dict[str, Any], bool]:
    concentration = value.startswith("Concentration")
    rest = re.sub(r"^(?:Concentration,? )?(?:[Uu]p to )?", "", value).strip()
    rest = re.sub(r"\s+", " ", rest)
    named = {
        "Instantaneous": "inst",
        "Until dispelled": "disp",
        "Until dispelled or triggered": "dstr",
        "Special": "spec",
    }
    if rest in named:
        return {"units": named[rest], "value": None}, concentration
    amount = _amount(rest)
    if amount is None:
        raise ValueError(f"unread duration {value!r}")
    return amount, concentration


def _components(value: str) -> tuple[list[str], int, bool]:
    letters = re.sub(r"\(.*\)", "", value)
    components = sorted(c.strip() for c in letters.split(",") if c.strip())
    cost = COST.search(value)
    consumed = "consumes" in value
    return components, int(cost.group(1).replace(",", "")) if cost else 0, consumed


def _forced(effect: str, match: re.Match[str]) -> bool:
    """Whether a saving throw the text names is one the spell forces, not one it modifies
    ("Advantage on Wisdom saving throws", "a -2 penalty to AC and Dexterity saving throws")."""
    clause = re.split(r"[.;:]", effect[: match.start()])[-1]
    return MODIFIED_SAVE.search(clause) is None


def _damage_types(run: str) -> list[str]:
    return sorted({word.lower() for word in re.findall(rf"\b{TYPE_RUN}\b", run)})


def _pdf_damage(effect: str) -> list[str]:
    """Each damage roll as "<dice> <type>", one entry per type a roll can have. A roll "of a
    type you choose" or "of the chosen type" can have each type the spell's text names."""
    pairs: set[str] = set()
    for match in DAMAGE.finditer(effect):
        types = _damage_types(match.group(2))
        chosen = CHOSEN_TYPE.match(effect, match.end())
        if not types and chosen:
            types = _damage_types(chosen.group(1) or effect)
        pairs.update(f"{match.group(1)} {kind}" for kind in types or ["untyped"])
    for match in DAMAGE_EQUAL.finditer(effect):
        pairs.update(f"{match.group(2)} {kind}" for kind in _damage_types(match.group(1)))
    return sorted(pairs)


def pdf_facts(text: RecordText, classes: list[str]) -> Facts:
    """A spell's fields as its SRD 5.2.1 text states them. ``classes`` comes from the index,
    which reads it from the same heading."""
    fields, body_lines = _header(text)
    match = TYPE_LINE.match(fields["Type"])
    if match is None:
        raise ValueError(f"{text.name}: unread level line {fields['Type']!r}")
    level = int(match.group(1)) if match.group(1) else 0
    school = SCHOOLS[match.group(2) or match.group(3)]
    casting_time, ritual = _casting_time(fields["Casting Time"])
    duration, concentration = _duration(fields["Duration"])
    components, cost, consumed = _components(fields["Components"])
    body = flatten("\n".join(body_lines))
    parts = HIGHER_LEVEL.split(body, maxsplit=1)
    effect, higher = parts[0], (parts[1] if len(parts) > 1 else "")
    facts: Facts = {
        "level": level,
        "school": school,
        "classes": sorted(classes),
        "casting_time": casting_time,
        "ritual": ritual,
        "range": _range(fields["Range"]),
        "components": components,
        "material_cost": cost,
        "material_consumed": consumed,
        "duration": duration,
        "concentration": concentration,
    }
    attack = sorted({m.group(1).lower() for m in ATTACK.finditer(effect)})
    if attack:
        facts["attack"] = attack
    saves = sorted({ABILITIES[m.group(1)] for m in SAVE.finditer(effect) if _forced(effect, m)})
    if saves:
        facts["save"] = saves
    damage = _pdf_damage(effect)
    if damage:
        facts["damage"] = damage
    healing = sorted({m.group(1) for pattern in HEALING for m in pattern.finditer(effect)})
    if healing:
        facts["healing"] = healing
    areas = {
        f"{shape or match.group(2).lower()} {match.group(1)}"
        for pattern, shape in AREAS
        for match in pattern.finditer(effect)
    }
    if areas:
        facts["area"] = sorted(areas)
    scaling = sorted({m.group(1) for m in SCALING.finditer(higher)})
    if scaling and level > 0:
        facts["scaling"] = scaling
    return facts


def _dice(part: Mapping[str, Any]) -> str | None:
    custom = part.get("custom") or {}
    if custom.get("enabled") and custom.get("formula"):
        found = re.match(rf"^\s*({DICE})", str(custom["formula"]))
        return found.group(1) if found else None
    if part.get("number") and part.get("denomination"):
        return f"{part['number']}d{part['denomination']}"
    return None


def _increment(part: Mapping[str, Any]) -> str | None:
    scaling = part.get("scaling") or {}
    if scaling.get("formula"):
        return str(scaling["formula"]).strip()
    if scaling.get("mode") == "whole" and scaling.get("number") and part.get("denomination"):
        return f"{scaling['number']}d{part['denomination']}"
    return None


def _size(formula: str, level: int) -> int | None:
    """A template size, which Foundry can write as a formula of the spell's level
    ("@item.level * 5 - 10"), evaluated at the spell's own level."""
    expression = formula.replace("@item.level", str(level)).replace(" ", "")
    if not re.fullmatch(r"[\d+*-]+", expression):
        return None
    total = 0
    for term in re.findall(r"[+-]?[^+-]+", expression):
        product = 1
        for factor in term.lstrip("+-").split("*"):
            product *= int(factor)
        total += -product if term.startswith("-") else product
    return total


def corpus_facts(record: Mapping[str, Any], classes: list[str]) -> Facts:
    """A spell's fields as its corpus record (``canonical/spells/<slug>.json``) holds them.
    ``classes`` comes from the shipped class spell lists."""
    casting = record["casting_time"]
    materials = record.get("materials") or {}
    facts: Facts = {
        "level": record["level"],
        "school": record["school"],
        "classes": sorted(classes),
        "casting_time": {"unit": casting["unit"], "value": casting["value"]},
        "ritual": record["ritual"],
        "range": {"units": record["range"]["units"], "value": record["range"]["value"]},
        "components": sorted(record["components"]),
        "material_cost": materials.get("cost", 0),
        "material_consumed": bool(materials.get("consumed")),
        "duration": {"units": record["duration"]["units"], "value": record["duration"]["value"]},
        "concentration": record["concentration"],
    }
    attack: set[str] = set()
    saves: set[str] = set()
    damage: set[str] = set()
    healing: set[str] = set()
    areas: set[str] = set()
    scaling: set[str] = set()
    for activity in record.get("activities") or []:
        kind = activity.get("kind")
        if kind == "attack":
            attack.add(str(activity["attack"]["type"]["value"]))
        if kind == "save":
            saves.update(activity["save"]["ability"])
        parts = list((activity.get("damage") or {}).get("parts") or [])
        if activity.get("healing"):
            parts.append(activity["healing"])
        for part in parts:
            # A flat amount scales too: Heal's 70 Hit Points "increases by 10".
            increment = _increment(part)
            if increment is not None:
                scaling.add(increment)
            dice = _dice(part)
            types = sorted(part.get("types") or [])
            if dice is None:
                continue
            if types in (["healing"], ["temphp"]):
                healing.add(dice)
            else:
                damage.update(f"{dice} {kind}" for kind in types or ["untyped"])
        template = (activity.get("target") or {}).get("template") or {}
        shape = AREA_SHAPES.get(str(template.get("type") or ""))
        size = _size(str(template.get("size") or ""), int(record["level"]))
        if shape and size:
            areas.add(f"{shape} {size}")
    for effect in record.get("passive_effects") or []:
        for change in effect.get("changes") or []:
            # A damage bonus the spell grants: Divine Favor's "1d4[radiant]" on weapon
            # attacks, Enlarge's "1d4". A penalty ("- 1d4") deals no damage.
            bonus = BONUS_DAMAGE.match(str(change.get("value") or ""))
            if str(change.get("key", "")).endswith(".damage") and bonus:
                damage.add(f"{bonus.group(1)} {bonus.group(2) or 'untyped'}")
    if int(record["level"]) == 0:
        scaling = set()
    for field, values in (
        ("attack", attack),
        ("save", saves),
        ("damage", damage),
        ("healing", healing),
        ("area", areas),
        ("scaling", scaling),
    ):
        if values:
            facts[field] = sorted(values)
    return facts


# Fields compared only when the SRD text states them. Foundry adds a template to many
# spells whose text names no area shape (a wall's footprint, a sensing radius, a circle),
# as a targeting aid; the SRD has no value to compare it with.
SRD_STATED_ONLY = frozenset({"area"})


def compared_fields(srd: Facts, corpus: Facts) -> list[str]:
    """The fields compared: every field either side states, except an ``SRD_STATED_ONLY``
    field the SRD does not state."""
    return sorted(
        field for field in set(srd) | set(corpus) if field in srd or field not in SRD_STATED_ONLY
    )


def compare(srd: Facts, corpus: Facts) -> dict[str, dict[str, Any]]:
    """The fields where the corpus differs from the SRD text: field to both values. A field
    one side lacks differs."""
    return {
        field: {"srd": srd.get(field), "corpus": corpus.get(field)}
        for field in compared_fields(srd, corpus)
        if srd.get(field) != corpus.get(field)
    }


@dataclass(frozen=True)
class Reading:
    """A field's SRD value read by hand where the text reader cannot read it, with the
    sentence it rests on. ``None`` removes a value the reader takes from text that is not
    the field (a size limit read as an area)."""

    value: Any
    srd: str


#: Hand readings, keyed by spell slug, then field. Each quote is checked against the
#: spell's PDF text when the ledger is built.
READINGS: dict[str, dict[str, Reading]] = {
    # Weapon attacks the spell changes, which the reader looks for as "spell attack".
    "alter-self": {
        "attack": Reading(
            ["melee"],
            "When you use your Unarmed Strike to deal damage with that new growth, it deals"
            " 1d6 damage of the type in parentheses",
        ),
    },
    "shillelagh": {
        "attack": Reading(
            ["melee"],
            "you can use your spellcasting ability instead of Strength for the attack and"
            " damage rolls of melee attacks using that weapon",
        ),
    },
    "true-strike": {
        "attack": Reading(
            ["melee", "ranged"],
            "Guided by a flash of magical insight, you make one attack with the weapon used"
            " in the spell's casting.",
        ),
    },
    # A second damage roll the text gives as an increase, not as dice.
    "call-lightning": {
        "damage": Reading(
            ["3d10 lightning", "4d10 lightning"],
            "Under such conditions, the spell's damage increases by 1d10.",
        ),
    },
    "divine-smite": {
        "damage": Reading(
            ["2d8 radiant", "3d8 radiant"],
            "The damage increases by 1d8 if the target is a Fiend or an Undead.",
        ),
    },
    "delayed-blast-fireball": {
        "damage": Reading(
            ["12d6 fire"],
            "The spell's base damage is 12d6, and the damage increases by 1d6 whenever your"
            " turn ends and the spell hasn't ended.",
        ),
    },
    "false-life": {
        "scaling": Reading(
            ["5"],
            "You gain 5 additional Temporary Hit Points for each spell slot level above 1.",
        ),
    },
    # Size limits on what the spell makes, not areas of effect.
    "fabricate": {
        "area": Reading(
            None,
            "You can fabricate a Large or smaller object (contained within a 10-foot Cube or"
            " eight connected 5-foot Cubes)",
        ),
    },
    "magnificent-mansion": {
        "area": Reading(
            None,
            "You can create any floor plan you like for the dwelling, but it can't exceed 50"
            " contiguous 10-foot Cubes.",
        ),
    },
    "major-image": {
        "area": Reading(
            None,
            "You create the image of an object, a creature, or some other visible phenomenon"
            " that is no larger than a 20-foot Cube.",
        ),
    },
    "minor-illusion": {
        "area": Reading(None, "it must be no larger than a 5-foot Cube."),
    },
    "silent-image": {
        "area": Reading(None, "that is no larger than a 15-foot Cube."),
    },
    "web": {
        "area": Reading(
            ["cube 20"],
            "You conjure a mass of sticky webbing at a point within range. The webs fill a"
            " 20-foot Cube there for the duration.",
        ),
    },
}

#: Fields the corpus holds in a form this audit does not compare, keyed by spell slug,
#: then field, with where the corpus holds it.
NOT_COMPARED: dict[str, dict[str, str]] = {
    "alarm": {
        "area": "The corpus adds a 5-foot Cube for a door or window beside the SRD's 20-foot"
        " Cube ('Choose a door, a window, or an area within range that is no larger than a"
        " 20-foot Cube').",
    },
    "control-water": {
        "area": "The corpus adds a 25-foot Cylinder for the whirlpool, which the SRD sizes"
        " without naming an area shape ('The whirlpool is 5 feet wide at the base, up to 50"
        " feet wide at the top, and 25 feet tall').",
    },
    "delayed-blast-fireball": {
        "area": "The corpus adds a 1-foot Sphere for the bead ('condenses at a chosen point"
        " within range as a glowing bead') beside the blast's 20-foot-radius Sphere.",
    },
    "tsunami": {
        "damage": "The later rounds' damage is the formula '(@item.uses.value)d10', which"
        " starts at 5d10 and falls by 1d10 a round ('the damage the wall deals on later"
        " rounds is reduced by 1d10').",
    },
    "animate-objects": {
        "scaling": "The Animated Object stat blocks (canonical/monsters/*-animated-object.json)"
        " carry it as '(@flags.dnd5e.summon.level - 4)d4' and the like ('The creature's Slam"
        " damage increases by 1d4 (Medium or smaller), 1d6 (Large), or 1d12 (Huge) for each"
        " spell slot level above 5').",
    },
}


def audited_facts(slug: str, srd: Facts) -> tuple[Facts, dict[str, str]]:
    """``srd`` with the slug's hand readings applied, and the quote each reading rests on."""
    read: dict[str, str] = {}
    facts = dict(srd)
    for field, reading in READINGS.get(slug, {}).items():
        read[field] = reading.srd
        if reading.value is None:
            facts.pop(field, None)
        else:
            facts[field] = reading.value
    return facts, read
