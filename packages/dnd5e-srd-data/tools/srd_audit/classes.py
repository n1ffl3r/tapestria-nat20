"""A class's mechanical fields, read from its SRD 5.2.1 chapter and from its corpus record.

Both readers return the same shape, a mapping from field name to a JSON value, so the
ledger compares them field by field (``spells.compare``). The fields are the ones spec 02a
lists for classes and subclasses: the features, the levels they arrive at, the scaling
values, and the spell slots.

The class chapter prints each class as a "Core <Class> Traits" table, a "<Class> Features"
table by level, and a "Level N: Feature" heading over each feature's text; a subclass
follows its class's features under "<Class> Subclass: <Name>". ``class_texts`` cuts the
chapter into those parts, and ``pdf_facts`` and ``subclass_facts`` read them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from tools import srd_index
from tools.srd_audit import pdf_text
from tools.srd_audit.spells import Facts

CLASS_PAGES = (28, 83)
LEVELS = range(1, 21)
# The table columns after the Class Features column, in print order, per class. Each name is
# checked against the table's header text, so a reordered table fails the build.
COLUMNS: dict[str, tuple[str, ...]] = {
    "barbarian": ("Rages", "Rage Damage", "Weapon Mastery"),
    "bard": ("Bardic Die", "Cantrips", "Prepared Spells"),
    "cleric": ("Channel Divinity", "Cantrips", "Prepared Spells"),
    "druid": ("Wild Shape", "Cantrips", "Prepared Spells"),
    "fighter": ("Second Wind", "Weapon Mastery"),
    "monk": ("Martial Arts", "Focus Points", "Unarmored Movement"),
    "paladin": ("Channel Divinity", "Prepared Spells"),
    "ranger": ("Favored Enemy", "Prepared Spells"),
    "rogue": ("Sneak Attack",),
    "sorcerer": ("Sorcery Points", "Cantrips", "Prepared Spells"),
    "warlock": ("Eldritch Invocations", "Cantrips", "Prepared Spells", "Spell Slots", "Slot Level"),
    "wizard": ("Cantrips", "Prepared Spells"),
}
# Slot columns: nine spell levels, five for a half caster.
SLOT_COLUMNS = {"bard": 9, "cleric": 9, "druid": 9, "sorcerer": 9, "wizard": 9}
SLOT_COLUMNS |= {"paladin": 5, "ranger": 5}
# The table column each corpus ScaleValue (by title) reads, per class.
SCALE_COLUMNS: dict[str, dict[str, str]] = {
    "barbarian": {
        "Rages": "Rages",
        "Rage Damage": "Rage Damage",
        "Weapon Masteries Known": "Weapon Mastery",
    },
    "bard": {
        "Inspiration Die": "Bardic Die",
        "Cantrips Known": "Cantrips",
        "Max Prepared Spells": "Prepared Spells",
    },
    "cleric": {
        "Channel Divinity Uses": "Channel Divinity",
        "Cantrips Known": "Cantrips",
        "Max Prepared Spells": "Prepared Spells",
    },
    "druid": {
        "Wild Shape Uses": "Wild Shape",
        "Cantrips Known": "Cantrips",
        "Max Prepared Spells": "Prepared Spells",
    },
    "fighter": {"Second Wind": "Second Wind", "Weapon Masteries Known": "Weapon Mastery"},
    "monk": {
        "Martial Arts Die": "Martial Arts",
        "Focus Points": "Focus Points",
        "Unarmored Movement": "Unarmored Movement",
    },
    "paladin": {
        "Channel Divinity Uses": "Channel Divinity",
        "Max Prepared Spells": "Prepared Spells",
    },
    "ranger": {"Favored Enemy": "Favored Enemy", "Max Prepared Spells": "Prepared Spells"},
    "rogue": {"Sneak Attack": "Sneak Attack"},
    "sorcerer": {
        "Sorcery Points": "Sorcery Points",
        "Cantrips Known": "Cantrips",
        "Max Prepared Spells": "Prepared Spells",
    },
    "warlock": {
        "Eldritch Invocations Known": "Eldritch Invocations",
        "Cantrips Known": "Cantrips",
        "Max Pact Magic Spells": "Prepared Spells",
    },
    "wizard": {"Cantrips Known": "Cantrips", "Max Prepared Spells": "Prepared Spells"},
}
# Features every class has that are not features of their own: they are compared as levels.
GENERIC = ("Ability Score Improvement", "Epic Boon", "Subclass feature")
SMALL_WORDS = frozenset({"a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with"})
USES = {"one": 1, "two": 2, "three": 3, "four": 4}
ROW = re.compile(r"^(\d{1,2}) \+(\d) (.*)$")
PROFICIENCY_BONUS = {level: 2 + (level - 1) // 4 for level in LEVELS}
CORE_LABELS = (
    "Primary Ability",
    "Hit Point Die",
    "Skill Proficiencies",
    "Weapon Proficiencies",
    "Armor Training",
    "Tool Proficiencies",
    "Starting Equipment",
)
ABILITIES = {
    "Strength": "str",
    "Dexterity": "dex",
    "Constitution": "con",
    "Intelligence": "int",
    "Wisdom": "wis",
    "Charisma": "cha",
}


@dataclass(frozen=True)
class ScaleReading:
    """A scaling value the Features table does not print, read by hand: its value from the
    level it starts at, with the SRD sentences it rests on."""

    steps: dict[int, Any]
    srd: tuple[str, ...]


#: Hand readings of the ScaleValues the table has no column for, keyed by class slug, then
#: the corpus ScaleValue's title. Each quote is checked against the class's PDF text when
#: the ledger is built.
BEAST_SHAPES = (
    "Beast Shapes Druid Level Known Forms Max CR Fly Speed 2 4 1/4 No 4 6 1/2 No 8 8 1 Yes"
)
SCALE_READINGS: dict[str, dict[str, ScaleReading]] = {
    "barbarian": {
        "Brutal Strike": ScaleReading(
            {9: "1d10", 17: "2d10"},
            (
                "Level 9: Brutal Strike",
                "the target takes an extra 1d10 damage of the same type dealt by the weapon or"
                " Unarmed Strike",
                "Level 17: Improved Brutal Strike The extra damage of your Brutal Strike"
                " increases to 2d10.",
            ),
        ),
    },
    "cleric": {
        "Divine Strike": ScaleReading(
            {7: "1d8", 14: "2d8"},
            (
                "Level 7: Blessed Strikes",
                "you can cause the target to take an extra 1d8 Necrotic or Radiant damage"
                " (your choice)",
                "Level 14: Improved Blessed Strikes",
                "The extra damage of your Divine Strike increases to 2d8.",
            ),
        ),
    },
    "druid": {
        "Elemental Fury": ScaleReading(
            {7: "1d8", 15: "2d8"},
            (
                "Level 7: Elemental Fury",
                "you can cause the target to take an extra 1d8 Cold, Fire, Lightning, or Thunder"
                " damage (choose when you hit)",
                "Level 15: Improved Elemental Fury",
                "The extra damage of your Primal Strike increases to 2d8.",
            ),
        ),
        "Known Forms": ScaleReading(
            {2: 4, 4: 6, 8: 8},
            (BEAST_SHAPES,),
        ),
        "Wild Shape CR": ScaleReading(
            {2: 0.25, 4: 0.5, 8: 1},
            (BEAST_SHAPES,),
        ),
    },
    "land": {
        "Lands Aid Damage": ScaleReading(
            {3: "2d6", 10: "3d6", 14: "4d6"},
            (
                "taking 2d6 Necrotic damage on a failed save",
                "The damage and healing increase by 1d6 when you reach Druid levels 10 (3d6)"
                " and 14 (4d6).",
            ),
        ),
    },
    "paladin": {
        "Aura Distance": ScaleReading(
            {6: 10, 18: 30},
            (
                "Level 6: Aura of Protection You radiate a protective, unseeable aura in a"
                " 10-foot Emanation that originates from you.",
                "Level 18: Aura Expansion Your Aura of Protection is now a 30-foot Emanation.",
            ),
        ),
    },
    "ranger": {
        "Hunter's Mark Damage": ScaleReading(
            {1: "d6", 20: "d10"},
            (
                "Level 20: Foe Slayer The damage die of your Hunter's Mark is a d10 rather"
                " than a d6.",
            ),
        ),
    },
}
# The ScaleValues the Features table prints as a qualifier on a feature: "Action Surge (one
# use)", "Indomitable (two uses)".
QUALIFIER_SCALES = {"fighter": ("Action Surge", "Indomitable")}


@dataclass
class Heading:
    level: int
    name: str
    page: int


@dataclass
class ClassText:
    name: str
    page: int
    core: dict[str, str] = field(default_factory=dict)
    header: str = ""
    rows: dict[int, str] = field(default_factory=dict)
    headings: list[Heading] = field(default_factory=list)
    subclasses: dict[str, list[Heading]] = field(default_factory=dict)
    lines: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _is_row(line: str) -> bool:
    return ROW.match(line) is not None


def _table(lines: list[str], start: int, columns: int) -> tuple[str, dict[int, str], int]:
    """The features table titled at ``lines[start]``: its header text, and each level's row
    joined across wrapped lines. A row is complete once ``columns`` values end it."""
    index = start + 1
    header: list[str] = []
    while not _is_row(lines[index]):
        header.append(lines[index])
        index += 1
    rows: dict[int, str] = {}
    while len(rows) < 20:
        row = lines[index]
        index += 1
        while not _complete(row, columns):
            row = f"{row} {lines[index]}"
            index += 1
        level = int(ROW.match(row).group(1))  # type: ignore[union-attr]
        rows[level] = row
    return " ".join(header), rows, index


def _complete(row: str, columns: int) -> bool:
    tokens = _tokens(row)
    return (
        len(tokens) >= 2 + columns
        and ROW.match(row) is not None
        and _values(row, columns) is not None
    )


def _tokens(row: str) -> list[str]:
    return re.sub(r" ft\.", "ft.", row).split()


def _values(row: str, columns: int) -> list[str] | None:
    """The last ``columns`` values of a row; each is a token such as ``3``, ``d6``, ``1d6``,
    ``+10ft.``, ``—``, or a fraction."""
    tokens = _tokens(row)
    values = tokens[len(tokens) - columns :] if columns else []
    if all(re.fullmatch(r"[+\dDd/.ft—]+", value) for value in values):
        return values
    return None


def class_texts(lines: tuple[tuple[int, str], ...] | None = None) -> dict[str, ClassText]:
    """Each class's parts of the chapter, keyed by class name."""
    lines = pdf_text.plain_lines(*CLASS_PAGES) if lines is None else lines
    stripped = [line.strip() for _, line in lines]
    starts = [
        (i, m.group(1))
        for i, line in enumerate(stripped)
        if (m := srd_index.CORE_TRAITS.match(line))
    ]
    texts: dict[str, ClassText] = {}
    for position, (start, name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(stripped)
        text = ClassText(name=name, page=lines[start][0], lines=tuple(stripped[start:end]))
        _core(text)
        _features(text)
        texts[name] = text
    return texts


def _core(text: ClassText) -> None:
    """The Core Traits table's rows, each label's value joined across wrapped lines."""
    label = ""
    lines = iter(text.lines[1:])
    for line in lines:
        if line.startswith("Becoming a"):
            break
        if line == "Saving Throw":
            next(lines)  # "Proficiencies"
            label = "Saving Throw Proficiencies"
            text.core[label] = ""
            continue
        found = next((c for c in CORE_LABELS if line.startswith(c)), None)
        if found:
            label = found
            text.core[label] = line[len(found) :].strip()
        else:
            text.core[label] = f"{text.core[label]} {line}".strip()


def _features(text: ClassText) -> None:
    lines = list(text.lines)
    slug = text.name.lower()
    columns = len(COLUMNS[slug]) + SLOT_COLUMNS.get(slug, 0)
    title = next(i for i, line in enumerate(lines) if line == f"{text.name} Features")
    text.header, text.rows, _ = _table(lines, title, columns)
    for column in COLUMNS[slug]:
        for word in re.findall(r"[A-Za-z]+", column):
            if word not in text.header:
                raise ValueError(f"{text.name}: column {column!r} is not in the table header")
    subclass: str | None = None
    page = text.page
    index = 0  # Level 1 features can be printed before the table.
    while index < len(lines):
        line = lines[index]
        index += 1
        sub = srd_index.SUBCLASS.match(line)
        if sub:
            name = sub.group(2).strip() or lines[index]
            following = lines[index] if sub.group(2).strip() else lines[index + 1]
            if sub.group(2).strip() and len(following.split()) <= 2:
                name = f"{name} {following}"
            subclass = srd_index.fix_case(name)
            text.subclasses[subclass] = []
            continue
        heading = srd_index.CLASS_FEATURE.match(line)
        if heading:
            level = int(line.split(":")[0].split()[1])
            entry = Heading(level, srd_index.fix_case(heading.group(1).strip()), page)
            (text.subclasses[subclass] if subclass else text.headings).append(entry)


def table_values(text: ClassText) -> dict[str, dict[int, str]]:
    """Each column's value at each level, as the table prints it."""
    slug = text.name.lower()
    names = (*COLUMNS[slug], *(f"slots_{n}" for n in range(1, SLOT_COLUMNS.get(slug, 0) + 1)))
    out: dict[str, dict[int, str]] = {name: {} for name in names}
    for level, row in text.rows.items():
        values = _values(row, len(names)) or []
        for name, value in zip(names, values, strict=True):
            out[name][level] = value
    return out


def table_features(text: ClassText) -> dict[int, list[str]]:
    """The Class Features column: each level's feature names, a qualifier such as "(one
    use)" kept."""
    slug = text.name.lower()
    columns = len(COLUMNS[slug]) + SLOT_COLUMNS.get(slug, 0)
    found: dict[int, list[str]] = {}
    for level, row in text.rows.items():
        tokens = _tokens(row)
        body = " ".join(tokens[2 : len(tokens) - columns]).replace("ft.", " ft.")
        found[level] = [] if body == "—" else _split(body)
    return found


def _split(body: str) -> list[str]:
    """Split a features cell at the commas outside parentheses."""
    parts, depth, current = [], 0, ""
    for char in body:
        depth += char == "("
        depth -= char == ")"
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    return [part for part in [*parts, current.strip()] if part]


def number(value: str) -> Any:
    """A table value as the corpus holds it: ``—`` is none, a count or bonus an int, a die ``dN`` or
    ``NdN`` in lower case (one die is ``dN``), a distance (``+10ft.``) its feet, a fraction
    its number."""
    if value == "—":
        return None
    if re.fullmatch(r"\+?\d+", value):
        return int(value)
    if re.fullmatch(r"\+\d+ft\.", value):
        return int(value[1:-3])
    if re.fullmatch(r"\d*[Dd]\d+", value):
        return re.sub(r"^1(?=d)", "", value.lower())
    return float(Fraction(value))


def canon(name: str) -> str:
    """A feature name with one spelling: a straight apostrophe, a small word in lower case
    ("Lay On Hands" is "Lay on Hands"), and no " (2)" a corpus record adds to a feature the
    SRD prints at two levels."""
    words = name.replace("\u2019", "'").removesuffix(" (2)").split()
    return " ".join(w.lower() if i and w.lower() in SMALL_WORDS else w for i, w in enumerate(words))


def levels_by_name(entries: Iterable[tuple[str, int]]) -> dict[str, list[int]]:
    """Each feature's levels, a feature printed at two levels having both."""
    found: dict[str, list[int]] = {}
    for name, level in entries:
        found.setdefault(canon(name), []).append(level)
    return {name: sorted(levels) for name, levels in sorted(found.items())}


def qualifier_scale(text: ClassText, title: str) -> dict[str, Any]:
    """A ScaleValue the table prints as "Title (one use)" at each level it changes."""
    steps: dict[int, Any] = {}
    for level, entries in table_features(text).items():
        for entry in entries:
            found = re.fullmatch(rf"{re.escape(title)} \((\w+) uses?\)", entry)
            if found:
                steps[level] = USES[found.group(1)]
    return expand(steps)


def expand(steps: Mapping[int, Any]) -> dict[str, Any]:
    """A scale's value at each level 1 to 20: the last step at or below it, none before."""
    out: dict[str, Any] = {}
    current: Any = None
    for level in LEVELS:
        current = steps.get(level, current)
        out[str(level)] = re.sub(r"^1(?=d)", "", current) if isinstance(current, str) else current
    return out


def feature_name(entry: str) -> str:
    """A table feature without its qualifier: "Action Surge (one use)"."""
    return re.sub(r" \([^)]*\)$", "", entry)


def _abilities(text: str) -> list[str]:
    return sorted(ABILITIES[word] for word in re.findall(r"[A-Z][a-z]+", text) if word in ABILITIES)


def _primary(abilities: list[str] | str) -> dict[str, Any]:
    """The primary ability: its abilities, and whether a character needs all of them (a
    class with one has nothing to need)."""
    listed = _abilities(abilities) if isinstance(abilities, str) else sorted(abilities)
    both = isinstance(abilities, str) and " and " in abilities
    return {"all": both and len(listed) > 1, "value": listed}


def progression(text: ClassText) -> str:
    """The spellcasting progression the Features table shows: Pact Magic for a Spell Slots
    column, else full for nine spell levels, half for five, none for no slot columns."""
    slug = text.name.lower()
    if "Spell Slots" in COLUMNS[slug]:
        return "pact"
    return {0: "none", 5: "half", 9: "full"}[SLOT_COLUMNS.get(slug, 0)]


def _scale(values: Mapping[int, str]) -> dict[str, Any]:
    return {str(level): number(values[level]) for level in LEVELS}


def pdf_facts(text: ClassText) -> Facts:
    """A class's fields as its chapter states them."""
    slug = text.name.lower()
    core = text.core
    primary = core["Primary Ability"]
    facts: Facts = {
        "hit_die": core["Hit Point Die"].split()[0].lower(),
        "primary_ability": _primary(primary),
        "saving_throws": _abilities(core["Saving Throw Proficiencies"]),
        "progression": progression(text),
    }
    table = table_features(text)
    names = {feature_name(e): level for level, entries in table.items() for e in entries}
    ability = re.search(r"(\w+) is (?:your|the) spellcasting ability", pdf_text.flatten(text.text))
    if ability:
        facts["spellcasting_ability"] = ABILITIES[ability.group(1)]
    generic = {f"{text.name} Subclass", *GENERIC}
    facts["feature_levels"] = levels_by_name(
        (h.name, h.level) for h in text.headings if h.name not in generic
    )
    facts["asi_levels"] = sorted(
        level
        for level, entries in table.items()
        if {"Ability Score Improvement", "Epic Boon"} & set(entries)
    )
    facts["subclass_level"] = next(
        level for level, entries in table.items() if f"{text.name} Subclass" in entries
    )
    facts["subclass_feature_levels"] = sorted(
        level for level, entries in table.items() if "Subclass feature" in entries
    )
    values = table_values(text)
    facts["scale"] = {
        title: _scale(values[column]) for title, column in SCALE_COLUMNS[slug].items()
    }
    for title in QUALIFIER_SCALES.get(slug, ()):
        facts["scale"][title] = qualifier_scale(text, title)
    for title, reading in SCALE_READINGS.get(slug, {}).items():
        facts["scale"][title] = expand(reading.steps)
    facts["table_features"] = {n: lv for n, lv in names.items() if n not in generic}
    return facts


def _corpus_scale(config: Mapping[str, Any]) -> dict[str, Any]:
    """A ScaleValue's value at each level 1 to 20: a level keeps the last value set at or
    below it, and none before the first."""
    steps = {int(level): value for level, value in config["scale"].items()}
    kind = config["type"]
    out: dict[str, Any] = {}
    current: Any = None
    for level in LEVELS:
        if level in steps:
            value = steps[level]
            if kind == "dice":
                number = value.get("number")
                current = f"{number if number and number > 1 else ''}d{value['faces']}"
            else:
                current = value["value"]
        out[str(level)] = current
    return out


def corpus_facts(
    record: Mapping[str, Any],
    features: Mapping[str, Mapping[str, Any]],
    subclass_levels: Iterable[int] = (),
) -> Facts:
    """A class's (or a subclass's) fields as its corpus record holds them. ``features``
    maps a feature slug to its record, and ``subclass_levels`` are the levels its
    subclasses grant features at."""
    advancement = record.get("advancement") or []
    facts: Facts = {
        "feature_levels": levels_by_name(
            (features[g["slug"]]["name"], g["level"])
            for g in record["granted_features"]
            if g["ref_type"] == "feature"
        ),
    }
    if record["entry_kind"] == "class":
        facts["hit_die"] = record["hit_die"]
        primary = record["primary_ability"]
        facts["primary_ability"] = {
            "all": primary["all"] and len(primary["value"]) > 1,
            "value": sorted(primary["value"]),
        }
        facts["saving_throws"] = sorted(record["saving_throws"])
        facts["progression"] = record["spellcasting"]["progression"]
        if record["spellcasting"]["ability"]:
            facts["spellcasting_ability"] = record["spellcasting"]["ability"]
        # A titled advancement is a feature's own ("Body and Mind").
        facts["asi_levels"] = sorted(
            a["level"]
            for a in advancement
            if a["type"] == "AbilityScoreImprovement" and not a["title"]
        )
        facts["subclass_level"] = next(a["level"] for a in advancement if a["type"] == "Subclass")
        facts["subclass_feature_levels"] = sorted(set(subclass_levels) - {facts["subclass_level"]})
        facts["scale"] = {
            a["title"]: _corpus_scale(a["configuration"])
            for a in advancement
            if a["type"] == "ScaleValue"
        }
    else:
        facts["scale"] = {
            a["title"]: _corpus_scale(a["configuration"])
            for a in advancement
            if a["type"] == "ScaleValue"
        }
    return facts


def subclass_facts(headings: Iterable[Heading]) -> Facts:
    """A subclass's fields as its chapter states them: each feature and its level."""
    return {"feature_levels": levels_by_name((h.name, h.level) for h in headings)}


def slot_rows(text: ClassText) -> dict[int, dict[int, int]]:
    """The spell slots the Features table gives each level, by spell level (a Warlock's Pact
    Magic slots by their one slot level)."""
    values = table_values(text)
    rows: dict[int, dict[int, int]] = {}
    for level in LEVELS:
        if "Spell Slots" in values:
            rows[level] = {int(values["Slot Level"][level]): int(values["Spell Slots"][level])}
        else:
            rows[level] = {
                spell_level: int(values[f"slots_{spell_level}"][level])
                for spell_level in range(1, SLOT_COLUMNS.get(text.name.lower(), 0) + 1)
                if values[f"slots_{spell_level}"][level] != "—"
            }
    return rows
