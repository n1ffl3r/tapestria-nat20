"""A stat block's mechanical fields, read from its SRD 5.2.1 text and from its corpus record.

Both readers return the same shape, a mapping from field name to a JSON value, so the
ledger compares them field by field (``spells.compare``). The fields are the ones spec 02a
lists for stat blocks: size, type, AC, HP, speed, ability scores, saves, skills,
resistances and immunities, senses, CR (with its XP and Proficiency Bonus), traits, and
every action's numbers.

The corpus stores no attack bonus or damage modifier for a stat block's own attacks; the
engine derives them (``dnd5e_engine.activities.attack``): the ability is the attack's
``attack.ability``, else Dexterity for a ranged attack and Strength otherwise, and the
bonus adds the Proficiency Bonus. A save DC with an ability ``calculation`` is 8 + that
ability's modifier + the Proficiency Bonus. ``corpus_facts`` derives the numbers the same
way, so a difference is one the engine would roll.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tools import srd_index
from tools.srd_audit import spells
from tools.srd_audit.pdf_text import RecordText, flatten
from tools.srd_audit.spells import AREA_SHAPES, AREAS, Facts, _size

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
ABILITY_NAMES = {
    "Strength": "str",
    "Dexterity": "dex",
    "Constitution": "con",
    "Intelligence": "int",
    "Wisdom": "wis",
    "Charisma": "cha",
}
DAMAGE_TYPES = {
    "acid",
    "bludgeoning",
    "cold",
    "fire",
    "force",
    "lightning",
    "necrotic",
    "piercing",
    "poison",
    "psychic",
    "radiant",
    "slashing",
    "thunder",
}
CONDITIONS = {
    "blinded",
    "charmed",
    "deafened",
    "exhaustion",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "petrified",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
}
SENSES = ("blindsight", "darkvision", "tremorsense", "truesight")
SPEEDS = ("walk", "burrow", "climb", "fly", "swim")
SECTIONS = {
    "Traits": "traits",
    "Actions": "actions",
    "Bonus Actions": "bonus_actions",
    "Reactions": "reactions",
    "Legendary Actions": "legendary_actions",
}
# The corpus's action lists, keyed by section. Foundry files bonus actions and reactions
# with the actions and tells them apart by ``kind``.
CORPUS_SECTIONS = {
    "traits": ("special_abilities", None),
    "actions": ("actions", "action"),
    "bonus_actions": ("actions", "bonus_action"),
    "reactions": ("actions", "reaction"),
    "legendary_actions": ("legendary_actions", None),
}
HEADER_KEYS = (
    "AC",
    "HP",
    "Speed",
    "Skills",
    "Resistances",
    "Vulnerabilities",
    "Immunities",
    "Gear",
    "Senses",
    "Languages",
    "CR",
)
# The Monsters chapter's first page; stat blocks before it sit inside a spell or an item.
MONSTERS_CHAPTER = 254
SIGNED = r"[+\-−–]?\s?\d+"
ABILITY_ROW = re.compile(
    rf"\b(str|dex|con|int|wis|cha)\s+(\d+)\s+({SIGNED})\s+({SIGNED})", re.IGNORECASE
)
TYPE_LINE = re.compile(r"^(.+?) ((?:Swarm of \w+ )?\w+)(?: \(([^)]*)\))?, (.+)$")
# "CR 1 (XP 200; PB +2)", "CR 17 (XP 18,000, or 20,000 in lair; PB +6)", "CR 3 (700 XP;
# PB +2)".
CR_LINE = re.compile(r"^(\S+) \((?:XP )?([\d,]+)(?: XP)?(?:,[^;]*)?; PB \+(\d+)\)")
# A run-in heading, its text on the same line or (a heading alone on its line) the next.
ENTRY = re.compile(r"^([A-Z][\w'’-]*(?: [\w'’-]+){0,6}?)(?: \(([^)]*)\))?\.(?: (.*))?$")
ATTACK = re.compile(rf"\b(Melee|Ranged|Melee or Ranged) Attack Roll: ({SIGNED})")
REACH = re.compile(r"\breach (\d+) (?:ft\.?|feet)")
RANGE = re.compile(r"\brange (\d+)(?:/(\d+))? (?:ft\.|feet)")
DAMAGE = re.compile(
    r"\b(\d+) \((\d+d\d+)(?: ?([+\-−–]) ?(\d+))?\) ((?:[A-Z][a-z]+(?:,? (?:or )?)?)+) damage"
)
FLAT_DAMAGE = re.compile(r"\b(?:Hit:|plus) (\d+) ([A-Z][a-z]+) damage")
# Hit Points lost rather than damage dealt: "loses 10 (3d6) Hit Points", "its Hit Point
# maximum decreases by 5 (1d10)". The corpus records them as untyped damage.
HP_LOSS = re.compile(r"\b(?:loses|decreases by) \d+ \((\d+d\d+)\)")
SAVE = re.compile(r"\b(" + "|".join(ABILITY_NAMES) + r") Saving Throw: DC (\d+)")
# "must succeed on a DC 14 Constitution saving throw".
SAVE_IN_TEXT = re.compile(r"\bDC (\d+) (" + "|".join(ABILITY_NAMES) + r") saving throw")
ACTION_START = re.compile(r"^(?:Melee|Ranged|(?:" + "|".join(ABILITY_NAMES) + r") Saving Throw)")
RECHARGE = re.compile(r"^Recharge (\d)(?:[–-](\d))?$")
PER_DAY = re.compile(r"^(\d)/Day")


def _signed(text: str) -> int:
    return int(re.sub(r"[−–]", "-", text.replace(" ", "")))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("’", "'").replace("'", "")).strip("-")


def _cr(text: str) -> float:
    if "/" in text:
        top, bottom = text.split("/")
        return int(top) / int(bottom)
    return float(text)


def _words(text: str) -> list[str]:
    """The entries of a Resistances, Vulnerabilities, or Immunities line, without their
    qualifiers ("Charmed (except from its vampire master)" is "charmed")."""
    text = re.sub(r"\s*\([^)]*\)", "", text)
    return [w.strip().lower() for w in re.split(r",|;| and ", text) if w.strip()]


def _header(text: RecordText) -> tuple[dict[str, str], list[str]]:
    """The header lines, each wrapped value joined, keyed by their label ("AC", "Type",
    "Abilities"), and the lines after them (the traits and actions)."""
    fields: dict[str, str] = {"Type": text.lines[1].strip() if len(text.lines) > 1 else ""}
    key = "Type"
    for position, raw in enumerate(text.lines[2:], start=2):
        line = raw.strip()
        if line in SECTIONS:
            return fields, list(text.lines[position:])
        head = line.split(" ", 1)[0]
        if line.startswith("MOD SAVE") or ABILITY_ROW.search(line):
            key = "Abilities"
            fields[key] = fields.get(key, "") + " " + line
            continue
        if head in HEADER_KEYS and head not in fields:
            key = head
            fields[key] = line[len(head) :].strip()
            continue
        if key == "Type" and not line.startswith("AC"):
            fields["Type"] = flatten(fields["Type"] + "\n" + line)
            continue
        fields[key] = flatten(fields.get(key, "") + "\n" + line)
    return fields, []


def _speed(text: str) -> dict[str, Any]:
    speed: dict[str, Any] = {kind: None for kind in SPEEDS}
    speed["hover"] = "hover" in text.lower()
    for part in re.split(r",\s*", re.sub(r"\([^)]*\)", "", text)):
        found = re.match(r"^(?:(Burrow|Climb|Fly|Swim) )?(\d+) ft\.", part.strip())
        # The first speed of a kind is the creature's own: "Speed 30 ft., 40 ft. (wolf
        # form only)" is a walking speed of 30 feet.
        kind = (found.group(1) or "walk").lower() if found else None
        if found and speed[kind] is None:
            speed[kind] = int(found.group(2))
    return speed


def _defenses(fields: Mapping[str, str]) -> dict[str, list[str]]:
    """Resistances, Vulnerabilities, and Immunities, with the Immunities line split into
    damage types and conditions ("Poison; Charmed, Poisoned")."""
    immune = _words(fields.get("Immunities", ""))
    return {
        "damage_resistances": sorted(_words(fields.get("Resistances", ""))),
        "damage_vulnerabilities": sorted(_words(fields.get("Vulnerabilities", ""))),
        "damage_immunities": sorted(w for w in immune if w in DAMAGE_TYPES),
        "condition_immunities": sorted(w for w in immune if w not in DAMAGE_TYPES),
    }


def _senses(text: str) -> dict[str, Any]:
    senses: dict[str, Any] = {sense: None for sense in SENSES}
    for sense in SENSES:
        found = re.search(rf"\b{sense} (\d+) ft\.", text, re.IGNORECASE)
        if found:
            senses[sense] = int(found.group(1))
    passive = re.search(r"Passive Perception (\d+)", text)
    senses["passive_perception"] = int(passive.group(1)) if passive else None
    return senses


@dataclass(frozen=True)
class Entry:
    section: str
    name: str
    tag: str
    text: str


SMALL_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
)


def _entry_name(name: str) -> bool:
    """Whether a run-in heading names a trait or action: its words are capitalized but for
    small words, and it ends on a capitalized word. A sentence that starts a line ("Piercing
    damage. If ...", "AC to 10.") is not one."""
    words = name.split()
    capitalized = [w[0].isupper() or (w[0].isdigit() and "/" in w) for w in words]
    return all(c or w in SMALL_WORDS for c, w in zip(capitalized, words, strict=True)) and (
        words[-1][0].isupper()
    )


def _join_wrapped_headings(lines: list[str]) -> list[str]:
    """Lines with a heading whose parenthetical wraps rejoined: "Hand Crossbow (Humanoid or
    Hybrid Form" / "Only). Ranged Attack Roll: ..." is one heading."""
    joined: list[str] = []
    for raw in lines:
        line = raw.strip()
        last = joined[-1] if joined else ""
        if last.count("(") > last.count(")") and re.match(r"^[A-Z][\w'’ -]* \(", last):
            joined[-1] = flatten(last + "\n" + line)
        else:
            joined.append(line)
    return joined


def entries(lines: list[str]) -> list[Entry]:
    """The traits and actions, each as its section, name, parenthetical tag ("Recharge
    5–6", "3/Day"), and text."""
    found: list[Entry] = []
    section: str | None = None
    previous = ""
    for raw in _join_wrapped_headings(lines):
        line = raw.strip()
        if line in SECTIONS:
            section, previous = SECTIONS[line], line
            continue
        match = ENTRY.match(line)
        # A heading starts a line after a sentence ends ("... Difficult\nTerrain. It takes"
        # is one sentence).
        # A heading after a spell list ("1/Day: Fireball") starts with an attack or a save.
        starts = previous in SECTIONS or previous.endswith((".", ":"))
        starts = starts or bool(match and ACTION_START.match(match.group(3) or ""))
        previous = line
        if section is not None and match and starts and _entry_name(match.group(1)):
            found.append(Entry(section, match.group(1), match.group(2) or "", match.group(3) or ""))
        elif found:
            last = found[-1]
            found[-1] = Entry(last.section, last.name, last.tag, flatten(last.text + "\n" + line))
    return found


def _usage(tag: str) -> dict[str, Any]:
    recharge = RECHARGE.match(tag)
    per_day = PER_DAY.match(tag)
    usage: dict[str, Any] = {}
    if recharge:
        # "Recharge 5–6" is "5-6"; "Recharge 6" is "6", as the corpus writes them.
        low, high = recharge.group(1), recharge.group(2)
        usage["recharge"] = f"{low}-{high}" if high and high != low else low
    if per_day:
        usage["uses_per_day"] = int(per_day.group(1))
    return usage


def _action_numbers(text: str) -> dict[str, Any]:
    numbers: dict[str, Any] = {}
    attack = ATTACK.search(text)
    if attack:
        numbers["attack_bonus"] = _signed(attack.group(2))
        reach = REACH.search(text)
        ranged = RANGE.search(text)
        # The corpus keeps one distance per attack: a melee attack's reach, a ranged
        # attack's normal range (Foundry's long range is dropped), and a "Melee or
        # Ranged" attack's reach.
        if reach:
            numbers["distance"] = int(reach.group(1))
        elif ranged:
            numbers["distance"] = int(ranged.group(1))
    saves = {f"{ABILITY_NAMES[m.group(1)]} {m.group(2)}" for m in SAVE.finditer(text)}
    saves |= {f"{ABILITY_NAMES[m.group(2)]} {m.group(1)}" for m in SAVE_IN_TEXT.finditer(text)}
    if saves:
        numbers["save"] = sorted(saves)
    damage = set()
    for match in DAMAGE.finditer(text):
        bonus = int(match.group(4) or 0) * (-1 if (match.group(3) or "+") in "-−–" else 1)
        for kind in re.findall(r"[A-Z][a-z]+", match.group(5)):
            if kind.lower() in DAMAGE_TYPES:
                damage.add(f"{match.group(2)}{bonus:+d} {kind.lower()}")
    for match in FLAT_DAMAGE.finditer(text):
        damage.add(f"{match.group(1)} {match.group(2).lower()}")
    for match in HP_LOSS.finditer(text):
        damage.add(f"{match.group(1)}+0 untyped")
    if damage:
        numbers["damage"] = sorted(damage)
    areas = {
        f"{shape or match.group(2).lower()} {match.group(1)}"
        for pattern, shape in AREAS
        for match in pattern.finditer(text)
    }
    if areas:
        numbers["area"] = sorted(areas)
    return numbers


def pdf_facts(text: RecordText) -> Facts:
    """A stat block's fields as its SRD 5.2.1 text states them."""
    fields, rest = _header(text)
    kind = TYPE_LINE.match(fields["Type"])
    if kind is None:
        raise ValueError(f"{text.name}: unread type line {fields['Type']!r}")
    size = kind.group(1).split(" or ")[0].lower()
    scores = {m.group(1).lower(): m for m in ABILITY_ROW.finditer(fields.get("Abilities", ""))}
    if sorted(scores) != sorted(ABILITIES):
        raise ValueError(f"{text.name}: unread ability scores {fields.get('Abilities')!r}")
    hp = re.match(r"^(\d+) \(([^)]+)\)", fields.get("HP", ""))
    ac = re.match(r"^(\d+)", fields.get("AC", ""))
    cr = CR_LINE.match(re.sub(r"(\d) ,(\d)", r"\1,\2", fields.get("CR", "")))
    facts: Facts = {
        "size": size,
        "type": kind.group(2).split()[-1].lower().rstrip("s"),
        "alignment": kind.group(4).strip().lower(),
        "ac": int(ac.group(1)) if ac else None,
        "hp": int(hp.group(1)) if hp else None,
        "hp_dice": re.sub(r"\s+", " ", hp.group(2)).replace("−", "-") if hp else None,
        "speed": _speed(fields.get("Speed", "")),
        "abilities": {a: int(scores[a].group(2)) for a in ABILITIES},
        "saves": {
            a: _signed(scores[a].group(4))
            for a in ABILITIES
            if _signed(scores[a].group(4)) != _signed(scores[a].group(3))
        },
        "skills": {
            _slug(m.group(1)).replace("-", "_"): _signed(m.group(2))
            for m in re.finditer(
                rf"([A-Z][a-z]+(?: [a-z]+)*(?: [A-Z][a-z]+)?) ({SIGNED})", fields.get("Skills", "")
            )
        },
        **_defenses(fields),
        "senses": _senses(fields.get("Senses", "")),
    }
    if cr:
        facts["cr"] = _cr(cr.group(1))
        facts["proficiency_bonus"] = int(cr.group(3))
    found = entries(rest)
    for section in SECTIONS.values():
        names = sorted(_slug(e.name) for e in found if e.section == section)
        if names:
            facts[section] = names
    for entry in found:
        numbers = {**_usage(entry.tag), **_action_numbers(entry.text)}
        if entry.section == "traits":
            # Legendary Resistance's uses are compared as ``legendary_resistance_uses``.
            numbers = {} if _slug(entry.name) == "legendary-resistance" else _usage(entry.tag)
        if numbers:
            facts[f"{entry.section}.{_slug(entry.name)}"] = numbers
    legendary = re.search(r"Legendary Action Uses: (\d+)", " ".join(rest))
    if legendary:
        facts["legendary_action_uses"] = int(legendary.group(1))
    resistance = re.search(r"Legendary Resistance \((\d+)/Day", " ".join(rest))
    if resistance:
        facts["legendary_resistance_uses"] = int(resistance.group(1))
    return facts


def _mod(score: int) -> int:
    return (score - 10) // 2


def _corpus_numbers(action: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    abilities = record["ability_scores"]
    pb = int(record["proficiency_bonus"])
    numbers: dict[str, Any] = {}
    if action.get("recharge"):
        numbers["recharge"] = action["recharge"]
    if action.get("uses_per_day"):
        numbers["uses_per_day"] = action["uses_per_day"]
    damage: set[str] = set()
    areas: set[str] = set()
    for activity in action.get("activities") or []:
        kind = activity.get("kind")
        mod = 0
        if kind == "attack":
            attack = activity["attack"]
            ranged = attack["type"]["value"] == "ranged"
            ability = attack["ability"] or ("dex" if ranged else "str")
            mod = _mod(abilities[ability]) if ability in abilities else 0
            flat = _int(attack.get("bonus"))
            numbers["attack_bonus"] = flat if attack.get("flat") else mod + pb + flat
            value = _int((activity.get("range") or {}).get("value"))
            if value:
                numbers["distance"] = value
        if kind == "save":
            save = activity["save"]
            calculation = save["dc"]["calculation"]
            if calculation in abilities:
                dc = 8 + _mod(abilities[calculation]) + pb
            else:
                dc = _int(save["dc"]["formula"])
            for ability in save["ability"] or [None]:
                numbers.setdefault("save", []).append(f"{ability} {dc}")
        if kind == "save":
            # ``@mod`` in a save's damage is the DC's ability, else the creature's
            # spellcasting ability (``dnd5e_engine.activities.save._governing_ability``).
            calculation = activity["save"]["dc"]["calculation"]
            governing = (
                calculation if calculation in abilities else record.get("spellcasting_ability")
            )
            mod = _mod(abilities[governing]) if governing in abilities else 0
        parts = list((activity.get("damage") or {}).get("parts") or [])
        # Foundry adds the ability modifier to an attack's base damage unless a part already
        # carries ``@mod`` (``dnd5e_engine.activities.attack._implicit_mod_damage_parts``).
        implicit_mod = bool((activity.get("damage") or {}).get("include_base")) and not any(
            "@mod" in str(part.get("bonus") or "") for part in parts
        )
        for index, part in enumerate(parts):
            bonus = _bonus(str(part.get("bonus") or ""), mod, abilities)
            if not (part.get("number") and part.get("denomination")):
                # A flat amount: the Badger's Bite, "Hit: 1 Piercing damage".
                for damage_type in part.get("types") or ["untyped"]:
                    if bonus:
                        damage.add(f"{bonus} {damage_type}")
                continue
            if index == 0 and kind == "attack" and implicit_mod:
                bonus += mod
            for damage_type in part.get("types") or ["untyped"]:
                damage.add(f"{part['number']}d{part['denomination']}{bonus:+d} {damage_type}")
        template = (activity.get("target") or {}).get("template") or {}
        shape = AREA_SHAPES.get(str(template.get("type") or ""))
        size = _size(str(template.get("size") or ""), 0)
        if shape and size:
            areas.add(f"{shape} {size}")
    if damage:
        numbers["damage"] = sorted(damage)
    if areas:
        numbers["area"] = sorted(areas)
    if "save" in numbers:
        numbers["save"] = sorted(set(numbers["save"]))
    return numbers


def _bonus(formula: str, mod: int, abilities: Mapping[str, int]) -> int:
    """A damage part's flat bonus: integers, ``@mod``, and ``@abilities.<x>.mod``."""
    total = 0
    for sign, term in re.findall(r"([+-]?)\s*([^+\s-][^+-]*)", formula):
        term = term.strip()
        found = re.fullmatch(r"@abilities\.(\w+)\.mod", term)
        if term == "@mod":
            value = mod
        elif found and found.group(1) in abilities:
            value = _mod(abilities[found.group(1)])
        elif term.isdigit():
            value = int(term)
        else:
            continue
        total += -value if sign == "-" else value
    return total


def _int(raw: Any) -> int:
    try:
        return int(str(raw).strip()) if raw not in (None, "") else 0
    except ValueError:
        return 0


def corpus_facts(record: Mapping[str, Any]) -> Facts:
    """A stat block's fields as its corpus record (``canonical/monsters/<slug>.json``)
    holds them."""
    saves = record.get("saving_throws") or {}
    skills = record.get("skills") or {}
    facts: Facts = {
        "size": record["creature_size"],
        "type": record["creature_type"],
        "alignment": (record.get("alignment") or "").lower() or None,
        "ac": record.get("ac"),
        "hp": record["hp"],
        "hp_dice": record["hp_dice"],
        "speed": {k: (record["movement"] or {}).get(k) for k in (*SPEEDS, "hover")},
        "abilities": dict(record["ability_scores"]),
        "saves": {a: v for a, v in saves.items() if v is not None},
        "skills": {k: v for k, v in skills.items() if v is not None},
        "damage_resistances": sorted(record.get("damage_resistances") or []),
        "damage_vulnerabilities": sorted(record.get("damage_vulnerabilities") or []),
        "damage_immunities": sorted(record.get("damage_immunities") or []),
        "condition_immunities": sorted(record.get("condition_immunities") or []),
        "senses": {k: (record["senses"] or {}).get(k) for k in (*SENSES, "passive_perception")},
        "cr": float(record["cr"]),
        "proficiency_bonus": record["proficiency_bonus"],
    }
    for section, (field, kind) in CORPUS_SECTIONS.items():
        actions = [a for a in record.get(field) or [] if kind is None or a["kind"] == kind]
        if actions:
            facts[section] = sorted(a["slug"] for a in actions)
        for action in actions:
            numbers = _corpus_numbers(action, record)
            if section == "traits":
                numbers = {k: v for k, v in numbers.items() if k in ("recharge", "uses_per_day")}
            if numbers:
                facts[f"{section}.{action['slug']}"] = numbers
    if record.get("legendary_action_uses"):
        facts["legendary_action_uses"] = record["legendary_action_uses"]
    if record.get("legendary_resistance_uses"):
        facts["legendary_resistance_uses"] = record["legendary_resistance_uses"]
    return facts


def stat_block_texts(
    lines: tuple[tuple[int, str], ...] | list[tuple[int, str]],
) -> dict[str, RecordText]:
    """Each stat block's text, keyed by its name, from plain-mode PDF lines: the heading,
    the type line, and everything up to the next stat block's heading."""
    starts: list[tuple[int, str, int]] = []
    for index, (page, line) in enumerate(lines):
        if index and srd_index.STAT_BLOCK_TYPE.match(line.strip()):
            starts.append((index, srd_index.heading_before(list(lines), index), page))
    texts: dict[str, RecordText] = {}
    for position, (start, name, page) in enumerate(starts):
        # The next block's heading sits on the line (or two, if it wraps) above its type
        # line; dropping one line is enough, since no field reads the last line's end.
        end = starts[position + 1][0] - 1 if position + 1 < len(starts) else len(lines)
        body_lines = [line for _, line in lines[start:end]]
        if page < MONSTERS_CHAPTER:
            # A summon form or figurine printed in a spell or a magic item runs until the
            # entry's next paragraph, which pypdf indents (" Golden Lions (Rare).").
            # A spell's heading (the line above its level line) ends it too.
            cut = next(
                (
                    i - (1 if srd_index.SPELL_TYPE.match(line.strip()) else 0)
                    for i, line in enumerate(body_lines)
                    if line.startswith(" ") or srd_index.SPELL_TYPE.match(line.strip())
                ),
                None,
            )
            body_lines = body_lines[:cut]
        body = (name, *body_lines)
        texts.setdefault(name, RecordText(name=name, page=page, lines=body))
    return texts


def compare(srd: Facts, corpus: Facts) -> dict[str, dict[str, Any]]:
    """``spells.compare`` with an action's area compared only when the SRD states one: Foundry
    gives many single-target actions a template as a targeting aid (Chuul's Paralyzing
    Tentacles, "one creature Grappled by the chuul", carries a 10-foot radius)."""
    corpus = dict(corpus)
    for field, numbers in corpus.items():
        if "." in field and isinstance(numbers, dict) and "area" not in (srd.get(field) or {}):
            trimmed = {k: v for k, v in numbers.items() if k != "area"}
            corpus[field] = trimmed or None
    corpus = {k: v for k, v in corpus.items() if v is not None}
    return spells.compare(srd, corpus)


#: Hand readings, keyed by monster slug, then field (``spells.Reading``: ``None`` drops a
#: field the reader takes from text that is not one). Each quote is checked against the
#: stat block's PDF text when the ledger is built.
READINGS: dict[str, dict[str, spells.Reading]] = {
    # The weaknesses are bullets under one trait, not traits of their own.
    "vampire": {
        "traits": spells.Reading(
            ["legendary-resistance", "misty-escape", "spider-climb", "vampire-weakness"],
            "Vampire Weakness. The vampire has these weaknesses:",
        ),
    },
    "vampire-spawn": {
        "traits": spells.Reading(
            ["spider-climb", "vampire-weakness"],
            "Vampire Weakness. The vampire has these weaknesses:",
        ),
    },
    # The three roars are the one Roar action's effects, in sequence.
    "sphinx-of-valor": {
        "actions": spells.Reading(
            ["claw", "multiattack", "roar", "spellcasting"],
            "Roar (3/Day). The sphinx emits a magical roar. Whenever it roars, the roar has a"
            " different effect, as detailed below",
        ),
        "actions.roar": spells.Reading(
            {
                "uses_per_day": 3,
                "save": ["con 20", "wis 20"],
                "damage": ["8d10+0 thunder"],
                "area": ["emanation 500"],
            },
            "Third Roar. Constitution Saving Throw: DC 20, each enemy in a 500-foot Emanation"
            " originating from the sphinx. Failure: 44 (8d10) Thunder damage",
        ),
        "actions.first-roar": spells.Reading(None, "First Roar. Wisdom Saving Throw: DC 20"),
        "actions.second-roar": spells.Reading(None, "Second Roar. Wisdom Saving Throw: DC 20"),
        "actions.third-roar": spells.Reading(None, "Third Roar. Constitution Saving Throw: DC 20"),
    },
    # "Climb or Fly 20 ft. (GM's choice)": the corpus takes the Fly Speed.
    "swarm-of-insects": {
        "speed": spells.Reading(
            {"walk": 20, "burrow": None, "climb": None, "fly": 20, "swim": None, "hover": False},
            "Speed 20 ft., Climb or Fly 20 ft. (GM's choice)",
        ),
    },
    # The Intelligence save is printed "2" for "−2" (MOD −2): not a proficient save.
    "young-white-dragon": {
        "saves": spells.Reading({"dex": 3, "wis": 3}, "Int 6 −2 2"),
    },
}

#: Fields the corpus holds in a form this audit does not compare, keyed by monster slug,
#: then field, with the reason.
_SUMMON = (
    "The stat block's numbers are formulas of the summoning spell's level and its caster"
    " ({quote}); the corpus holds Foundry's summon formulas, which the engine evaluates"
    " (dnd5e_engine.activities.conjuration.evaluate_summon_formula)."
)
NOT_COMPARED: dict[str, dict[str, str]] = {
    "avatar-of-death": {
        field: "Its Hit Points depend on its summoner ('HP Half the HP maximum of its"
        " summoner'), and it has no Challenge Rating ('CR None')."
        for field in ("hp", "hp_dice", "cr", "proficiency_bonus")
    },
    "draconic-spirit": {
        field: _SUMMON.format(quote="'HP 50 + 10 for each spell level above 5'")
        for field in (
            "hp",
            "hp_dice",
            "cr",
            "proficiency_bonus",
            "actions.rend",
            "actions.breath-weapon",
        )
    },
    "giant-insect": {
        **{
            field: _SUMMON.format(quote="'HP 30 + 10 for each spell level above 4'")
            for field in (
                "hp",
                "hp_dice",
                "cr",
                "proficiency_bonus",
                "actions.poison-jab",
                "actions.web-bolt",
                "bonus_actions.venomous-spew",
            )
        },
        "speed": "Its forms differ ('Fly 40 ft. (Wasp only)'); the corpus's Giant Insect is"
        " the form without a Fly Speed.",
    },
    "half-dragon": {
        field: "The damage type is chosen for the Draconic Origin trait ('Resistances Damage"
        " type chosen for the Draconic Origin trait below'); the corpus lists every option."
        for field in ("damage_resistances", "actions.claw", "actions.dragons-breath")
    },
}

#: Corpus records that are a size or form of a stat block the index lists (srd/aliases.json):
#: the Animated Object's sizes and the Otherworldly Steed's forms. The parent prints one
#: value per variant for these fields ("HP 10 (Medium or smaller), 20 (Large), 40 (Huge)"),
#: so a variant is compared on the others.
VARIANT_FIELDS = (
    "ac",
    "abilities",
    "saves",
    "skills",
    "damage_resistances",
    "damage_vulnerabilities",
    "damage_immunities",
    "condition_immunities",
    "senses",
)


def audited_facts(slug: str, srd: Facts) -> tuple[Facts, dict[str, str]]:
    """``srd`` with the monster's hand readings applied, and the quote each rests on."""
    read: dict[str, str] = {}
    facts = dict(srd)
    for field, reading in READINGS.get(slug, {}).items():
        read[field] = reading.srd
        if reading.value is None:
            facts.pop(field, None)
        else:
            facts[field] = reading.value
    return facts, read
