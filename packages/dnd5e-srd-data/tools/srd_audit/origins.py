"""The character origin records' mechanical fields, from their SRD 5.2.1 text and corpus.

Species, backgrounds, feats, and conditions each have a reader for the PDF and one for the
corpus returning the same shape, so the ledger compares them field by field
(``spells.compare``). The fields follow spec 02a: features, the levels they arrive at,
scaling values, and what the record grants.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tools import srd_index
from tools.srd_audit import pdf_text
from tools.srd_audit.spells import Facts

SPECIES_PAGES = (84, 86)
BACKGROUND_PAGES = (83, 84)
FEAT_PAGES = (87, 90)
CONDITION_PAGES = (177, 191)
ABILITIES = {
    "Strength": "str",
    "Dexterity": "dex",
    "Constitution": "con",
    "Intelligence": "int",
    "Wisdom": "wis",
    "Charisma": "cha",
}
SIZES = {"Tiny": "tiny", "Small": "small", "Medium": "medium", "Large": "large"}
NOT_TRAITS = ("Creature Type", "Size", "Speed")
TRAIT_LEVEL = re.compile(r"^(?:When you reach|Starting at) character level (\d+)")


@dataclass(frozen=True)
class SpeciesText:
    name: str
    page: int
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def species_texts(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, SpeciesText]:
    """Each species' text, from its name above its "Creature Type:" line to the next's.
    The last ends at "Feat Descriptions"."""
    lines = pdf_text.plain_lines(*SPECIES_PAGES) if lines is None else lines
    stripped = [(page, line.rstrip()) for page, line in lines]
    starts = [
        i - 1
        for i, (_, line) in enumerate(stripped)
        if srd_index.SPECIES_TYPE.match(line.strip()) and i > 0
    ]
    texts: dict[str, SpeciesText] = {}
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(stripped)
        body = tuple(line for _, line in stripped[start:end])
        stop = next((n for n, line in enumerate(body) if line.strip() == "Feat Descriptions"), None)
        body = body[:stop] if stop is not None else body
        name = body[0].strip()
        texts[name] = SpeciesText(name=name, page=stripped[start][0], lines=body)
    return texts


def species_traits(text: SpeciesText) -> dict[str, tuple[int, str]]:
    """Each run-in trait of a species: its level (0 for one it has from level 1) and its
    text. A trait's level is the one its first sentence names ("When you reach character
    level 5")."""
    traits: dict[str, tuple[int, str]] = {}
    name = ""
    body: list[str] = []

    def flush() -> None:
        if name:
            joined = pdf_text.flatten("\n".join(body))
            level = TRAIT_LEVEL.match(joined)
            traits[name] = (int(level.group(1)) if level else 0, joined)

    for line in text.lines[1:]:
        run_in = srd_index.RUN_IN.match(line.strip())
        label = run_in.group(1) if run_in else ""
        if (
            run_in
            and line.startswith(" ")
            and srd_index.title_case(label)
            and label not in NOT_TRAITS
        ):
            flush()
            name = srd_index.fix_case(label)
            body = [line.strip()[len(run_in.group(0)) :]]
        elif name:
            body.append(line.strip())
    flush()
    return traits


def species_facts(text: SpeciesText) -> Facts:
    """A species' creature type, sizes, Speed, Darkvision, and traits with their levels."""
    joined = pdf_text.flatten(text.text)
    size = re.search(r"Size: (\w+) \([^)]*\)(?: or (\w+) \()?", joined)
    sizes = sorted(SIZES[s] for s in (size.group(1), size.group(2)) if s) if size else []
    traits = species_traits(text)
    facts: Facts = {
        "creature_type": re.search(r"Creature Type: (\w+)", joined).group(1).lower(),  # type: ignore[union-attr]
        "sizes": sizes,
        "speed": int(re.search(r"Speed: (\d+) feet", joined).group(1)),  # type: ignore[union-attr]
        "traits": {name: level for name, (level, _) in traits.items()},
    }
    vision = traits.get("Darkvision")
    found = re.search(r"range of (\d+) feet", vision[1]) if vision else None
    facts["darkvision"] = int(found.group(1)) if found else None
    return facts


def corpus_species(
    record: Mapping[str, Any], features: Mapping[str, Mapping[str, Any]], lineage: Mapping[str, str]
) -> Facts:
    """A species' fields as its corpus record holds them. ``lineage`` maps a lineage
    feature's name ("Elven Lineage, Drow") to the trait that lists it ("Elven Lineage")."""
    abbreviations = {"tiny": "tiny", "sm": "small", "med": "medium", "lg": "large"}
    sizes: list[str] = []
    for entry in record["advancement"]:
        if entry["type"] == "Size":
            sizes = sorted(abbreviations[s] for s in entry["configuration"]["sizes"])
    traits: dict[str, int] = {}
    for grant in record["granted_features"]:
        if grant["ref_type"] != "feature":
            continue
        name = features[grant["slug"]]["name"]
        traits[lineage.get(name, name)] = grant["level"]
    return {
        "creature_type": record["creature_type"]["value"],
        "sizes": sizes,
        "speed": record["movement"]["walk"],
        "darkvision": record["senses"]["darkvision"],
        "traits": dict(sorted(traits.items())),
    }


@dataclass(frozen=True)
class Reading:
    """A field's SRD value read by hand, with the sentences it rests on. The ledger checks
    each quote against the species' or condition's text."""

    value: Any
    srd: tuple[str, ...]


def _row(*cells: str) -> str:
    return " ".join(cells)


#: Hand readings per species slug, then field: what a lineage table or prose states that the
#: run-in traits do not (a lineage's Darkvision, Speed, resistance, and spells by level, "0"
#: being the level the species has them from).
SPECIES_READINGS: dict[str, dict[str, Reading]] = {
    "dragonborn": {
        "resistance_choices": Reading(
            ["acid", "cold", "fire", "lightning", "poison"],
            (
                "Black Acid Gold Fire Blue Lightning Green Poison Brass Fire Red Fire Bronze"
                " Lightning Silver Cold Copper Acid White Cold",
            ),
        ),
        "scale.Breath Weapon": Reading(
            {1: "d10", 5: "2d10", 11: "3d10", 17: "4d10"},
            (
                "you take the Attack action on your turn, you can replace one of your attacks",
                "On a failed save, a creature takes 1d10 damage",
                "This damage increases by 1d10 when you reach character levels 5 (2d10), 11"
                " (3d10), and 17 (4d10).",
            ),
        ),
    },
    "dwarf": {
        "resistances": Reading(
            ["poison"], ("Dwarven Resilience. You have Resistance to Poison damage.",)
        ),
    },
    "elf-drow": {
        "darkvision": Reading(
            120,
            (
                _row(
                    "Drow The range of your Darkvision increases to 120 feet.",
                    "You also know the Dancing Lights cantrip. Faerie Fire Darkness",
                ),
            ),
        ),
        "spells": Reading(
            {"1": ["Dancing Lights"], "3": ["Faerie Fire"], "5": ["Darkness"]},
            ("You also know the Dancing Lights cantrip. Faerie Fire Darkness",),
        ),
    },
    "elf-high": {
        "spells": Reading(
            {"1": ["Prestidigitation"], "3": ["Detect Magic"], "5": ["Misty Step"]},
            (
                "High Elf You know the Prestidigitation cantrip. Whenever you finish a Long"
                " Rest, you can replace that cantrip with a different cantrip from the Wizard"
                " spell list. Detect Magic Misty Step",
            ),
        ),
    },
    "elf-wood": {
        "speed": Reading(
            35,
            (
                "Wood Elf Your Speed increases to 35 feet. You also know the Druidcraft"
                " cantrip. Longstrider Pass without Trace",
            ),
        ),
        "spells": Reading(
            {"1": ["Druidcraft"], "3": ["Longstrider"], "5": ["Pass without Trace"]},
            ("You also know the Druidcraft cantrip. Longstrider Pass without Trace",),
        ),
    },
    "gnome-forest": {
        "spells": Reading(
            {"0": ["Minor Illusion", "Speak with Animals"]},
            (
                "Forest Gnome. You know the Minor Illusion cantrip. You also always have the"
                " Speak with Animals spell prepared.",
            ),
        ),
    },
    "gnome-rock": {
        "spells": Reading(
            {"0": ["Mending", "Prestidigitation"]},
            ("Rock Gnome. You know the Mending and Prestidigitation cantrips.",),
        ),
    },
    "tiefling-abyssal": {
        "resistances": Reading(
            ["poison"], ("Abyssal You have Resistance to Poison damage. You also know",)
        ),
        "spells": Reading(
            {
                "0": ["Thaumaturgy"],
                "1": ["Poison Spray"],
                "3": ["Ray of Sickness"],
                "5": ["Hold Person"],
            },
            (
                "Abyssal You have Resistance to Poison damage. You also know the Poison Spray"
                " cantrip. Ray of Sickness Hold Person",
                "Otherworldly Presence. You know the Thaumaturgy cantrip.",
            ),
        ),
    },
    "tiefling-chthonic": {
        "resistances": Reading(
            ["necrotic"], ("Chthonic You have Resistance to Necrotic damage. You also know",)
        ),
        "spells": Reading(
            {
                "0": ["Thaumaturgy"],
                "1": ["Chill Touch"],
                "3": ["False Life"],
                "5": ["Ray of Enfeeblement"],
            },
            (
                "Chthonic You have Resistance to Necrotic damage. You also know the Chill"
                " Touch cantrip. False Life Ray of Enfeeblement",
                "Otherworldly Presence. You know the Thaumaturgy cantrip.",
            ),
        ),
    },
    "tiefling-infernal": {
        "resistances": Reading(
            ["fire"], ("Infernal You have Resistance to Fire damage. You also know",)
        ),
        "spells": Reading(
            {"0": ["Thaumaturgy"], "1": ["Fire Bolt"], "3": ["Hellish Rebuke"], "5": ["Darkness"]},
            (
                "Infernal You have Resistance to Fire damage. You also know the Fire Bolt"
                " cantrip. Hellish Rebuke Darkness",
                "Otherworldly Presence. You know the Thaumaturgy cantrip.",
            ),
        ),
    },
}
# A trait the corpus holds as a field rather than a feature record, so the traits compare
# without it: Darkvision is the ``senses`` field (compared as ``darkvision``), and the
# Draconic Ancestry damage resistance is a choice (compared as ``resistance_choices``).
FIELD_TRAITS = ("Darkvision", "Draconic Ancestry", "Damage Resistance")


def species_with_readings(slug: str, facts: Facts) -> tuple[Facts, dict[str, Reading]]:
    """A species' PDF facts with its hand readings laid over them: without the traits the
    corpus holds as fields, and with the lineage's own Darkvision, Speed, resistances,
    and spells. A species with none of them states no spells and no resistance."""
    readings = SPECIES_READINGS.get(slug, {})
    out = dict(facts)
    out["traits"] = {n: lv for n, lv in facts["traits"].items() if n not in FIELD_TRAITS}
    out.setdefault("spells", {})
    out.setdefault("resistances", [])
    for field, reading in readings.items():
        if field.startswith("scale."):
            scale = out.setdefault("scale", {})
            steps = reading.value
            scale[field.removeprefix("scale.")] = _expand(steps)
        else:
            out[field] = reading.value
    return out, readings


def _expand(steps: Mapping[int, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    current: Any = None
    for level in range(1, 21):
        current = steps.get(level, current)
        out[str(level)] = current
    return out


def corpus_species_more(record: Mapping[str, Any], spell_names: Mapping[str, str]) -> Facts:
    """A species' granted spells by level, resistances, and the damage-resistance choice and
    Breath Weapon scale a Dragonborn-style advancement holds."""
    spells: dict[str, list[str]] = {}
    for grant in record["granted_features"]:
        if grant["ref_type"] == "spell":
            spells.setdefault(str(grant["level"]), []).append(spell_names[grant["slug"]])
    resistances = sorted(
        g.removeprefix("dr:") for g in record["trait_grants"] if g.startswith("dr:")
    )
    facts: Facts = {
        "spells": {level: sorted(names) for level, names in sorted(spells.items())},
        "resistances": resistances,
    }
    for entry in record["advancement"]:
        config = entry["configuration"]
        if entry["type"] == "Trait":
            pools = [p for c in config["choices"] for p in c["pool"] if p.startswith("dr:")]
            if pools:
                facts["resistance_choices"] = sorted(p.removeprefix("dr:") for p in pools)
        if entry["type"] == "ScaleValue":
            facts.setdefault("scale", {})[_SCALE_TITLES.get(entry["title"], entry["title"])] = (
                _dice_scale(config)
            )
    return facts


# Foundry titles a species' scale by its identifier; the SRD names it by its trait.
_SCALE_TITLES = {"Breath Weapon": "Breath Weapon", "": "Breath Weapon"}


def _dice_scale(config: Mapping[str, Any]) -> dict[str, Any]:
    """A dice ScaleValue at each level 1 to 20; a key of 0 is level 1."""
    steps: dict[int, Any] = {}
    for level, value in config["scale"].items():
        number = value.get("number")
        steps[max(int(level), 1)] = f"{number if number and number > 1 else ''}d{value['faces']}"
    return _expand(steps)


SKILLS = {
    "acr": "Acrobatics",
    "ani": "Animal Handling",
    "arc": "Arcana",
    "ath": "Athletics",
    "dec": "Deception",
    "his": "History",
    "ins": "Insight",
    "itm": "Intimidation",
    "inv": "Investigation",
    "med": "Medicine",
    "nat": "Nature",
    "prc": "Perception",
    "prf": "Performance",
    "per": "Persuasion",
    "rel": "Religion",
    "slt": "Sleight of Hand",
    "ste": "Stealth",
    "sur": "Survival",
}
TOOLS = {
    "art:calligrapher": "Calligrapher's Supplies",
    "thief": "Thieves' Tools",
    "game:*": "Choose one kind of Gaming Set",
}


def background_texts(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, pdf_text.RecordText]:
    """Each background's text: its name above its "Ability Scores:" line, to the next's."""
    lines = pdf_text.plain_lines(*BACKGROUND_PAGES) if lines is None else lines
    stripped = [(page, line.strip()) for page, line in lines]
    starts = [
        i - 1
        for i, (_, line) in enumerate(stripped)
        if srd_index.BACKGROUND_SCORES.match(line) and i > 0
    ]
    texts: dict[str, pdf_text.RecordText] = {}
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(stripped)
        stop = next(
            (
                i
                for i in range(start + 1, end)
                if stripped[i][1] in ("Species", "Species Descriptions")
            ),
            end,
        )
        body = tuple(line for _, line in stripped[start:stop])
        texts[body[0]] = pdf_text.RecordText(name=body[0], page=stripped[start][0], lines=body)
    return texts


def background_facts(text: pdf_text.RecordText) -> Facts:
    """A background's ability scores, feat, skills, tool, and the Gold Pieces of its two
    equipment options, as its entry states them."""
    joined = pdf_text.flatten(text.text)
    labels = "Ability Scores|Feat|Skill Proficiencies|Tool Proficiency|Equipment"
    fields = {
        label: re.search(
            rf"{label}: (.*?)(?= (?:{labels}):|$)",
            joined,
        ).group(1)  # type: ignore[union-attr]
        for label in (
            "Ability Scores",
            "Feat",
            "Skill Proficiencies",
            "Tool Proficiency",
            "Equipment",
        )
    }
    equipment = fields["Equipment"]
    gold = re.search(r"\(A\) .*?(\d+) GP; or \(B\) (\d+) GP", equipment)
    return {
        "ability_options": sorted(
            ABILITIES[w] for w in re.findall(r"[A-Z][a-z]+", fields["Ability Scores"])
        ),
        "feat": re.sub(
            r" \([^)]*\)(?: \(see \"Feats\"\))?$|\s*\(see \"Feats\"\)$", "", fields["Feat"]
        ),
        "feat_list": (re.search(r"\((\w+)\)", fields["Feat"]) or [None, None])[1],
        "skills": sorted(re.split(r" and ", fields["Skill Proficiencies"].strip())),
        "tool": re.sub(r' \(see "Equipment"\)$', "", fields["Tool Proficiency"].strip()),
        "equipment_b_gold": int(gold.group(2)) if gold else None,
    }


def corpus_background(record: Mapping[str, Any], feats: Mapping[str, Mapping[str, Any]]) -> Facts:
    """A background's fields as its corpus record holds them. The feat is the corpus feat's
    name; a Magic Initiate list ("(Cleric)") is the SRD's and is not held, so it is read
    off the SRD side."""
    return {
        "ability_options": sorted(record["ability_options"]["options"]),
        "feat": feats[record["starting_feat_slug"]]["name"],
        "skills": sorted(SKILLS[s] for s in record["skill_proficiencies"]),
        "tool": ", ".join(TOOLS[t] for t in record["tool_proficiencies"]),
        "equipment_b_gold": int(record["wealth"]),
    }


FEAT_HEADER = re.compile(
    r"^(Origin|General|Fighting Style|Epic Boon) Feat(?: \(Prerequisite: (.*?)\))?"
)


def feat_texts(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, pdf_text.RecordText]:
    """Each feat's text: its name above its "<Category> Feat" line, to the next feat's. A
    wrapped prerequisite is joined."""
    lines = pdf_text.plain_lines(*FEAT_PAGES) if lines is None else lines
    stripped = [(page, line.strip()) for page, line in lines]
    starts = [
        i for i, (_, line) in enumerate(stripped) if srd_index.FEAT_KIND.match(line) and i > 0
    ]
    texts: dict[str, pdf_text.RecordText] = {}
    for position, start in enumerate(starts):
        end = starts[position + 1] - 1 if position + 1 < len(starts) else len(stripped)
        body = tuple(line for _, line in stripped[start - 1 : end])
        texts[body[0]] = pdf_text.RecordText(name=body[0], page=stripped[start - 1][0], lines=body)
    return texts


def feat_facts(text: pdf_text.RecordText) -> Facts:
    """A feat's category, prerequisite, and whether it can be taken more than once."""
    joined = pdf_text.flatten(text.text)
    header = FEAT_HEADER.match(pdf_text.flatten("\n".join(text.lines[1:4])))
    if header is None:
        raise ValueError(f"{text.name}: unread feat header")
    level: int | None = None
    requirement = ""
    fighting_style = False
    for part in (p.strip() for p in (header.group(2) or "").split(", ") if p.strip()):
        found = re.fullmatch(r"Level (\d+)\+", part)
        if found:
            level = int(found.group(1))
        elif part == "Fighting Style Feature":
            fighting_style = True
        else:
            requirement = f"{requirement}, {part}".strip(", ")
    requirement = _squash(requirement)
    return {
        "category": header.group(1).lower().replace(" ", "_"),
        "prerequisite_level": level,
        "prerequisite": requirement,
        "needs_fighting_style": fighting_style,
        "repeatable": bool(re.search(r" Repeatable\. ", joined)),
    }


def _squash(text: str) -> str:
    """Rejoin an ability name pypdf split ("Dexterit y")."""
    for name in ABILITIES:
        text = re.sub(r"\s*".join(name), name, text)
    return text


def corpus_feat(record: Mapping[str, Any]) -> Facts:
    """A feat's fields as its corpus record holds them."""
    prereq = (record["prerequisites"] or [{}])[0]
    return {
        "category": record["category"],
        "prerequisite_level": prereq.get("level"),
        "prerequisite": prereq.get("requirement", ""),
        "needs_fighting_style": "fighting-style" in prereq.get("feats", []),
        "repeatable": record["repeatable"],
    }


def condition_texts(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, pdf_text.RecordText]:
    """Each condition's text, from "<Name> [Condition]" to the next heading line."""
    lines = pdf_text.plain_lines(*CONDITION_PAGES) if lines is None else lines
    stripped = [(page, line.strip()) for page, line in lines]
    texts: dict[str, pdf_text.RecordText] = {}
    for i, (page, line) in enumerate(stripped):
        found = srd_index.CONDITION.match(line)
        if not found:
            continue
        end = next(
            (
                j
                for j in range(i + 1, len(stripped))
                if re.search(r"\[(?:Condition|Action|Hazard|Attitude)\]$", stripped[j][1])
            ),
            len(stripped),
        )
        body = tuple(line for _, line in stripped[i:end])
        texts[found.group(1)] = pdf_text.RecordText(name=found.group(1), page=page, lines=body)
    return texts


#: Each condition's effects as the SRD states them, in the corpus's vocabulary
#: (``effects[].kind``, with ``/abilities`` and ``=value``), and the conditions it implies.
#: Each quote is checked against the condition's text.
CONDITION_READINGS: dict[str, Reading] = {
    "blinded": Reading(
        {
            "implies": [],
            "effects": [
                "advantage_attacks_against",
                "auto_fail_sight_checks",
                "disadvantage_own_attacks",
            ],
        },
        (
            "Can't See. You can't see and automatically fail any ability check that requires "
            "sight.",
            "Attack rolls against you have Advantage, and your attack rolls have Disadvantage.",
        ),
    ),
    "charmed": Reading(
        {"implies": [], "effects": ["cant_attack_charmer", "charmer_social_advantage"]},
        (
            "Can't Harm the Charmer. You can't attack the charmer or target the charmer with "
            "damaging abilities or magical effects.",
            "Social Advantage. The charmer has Advantage on any ability check to interact with you "
            "socially.",
        ),
    ),
    "deafened": Reading(
        {"implies": [], "effects": ["auto_fail_hearing_checks"]},
        ("Can't Hear. You can't hear and automatically fail",),
    ),
    "exhaustion": Reading(
        {
            "implies": [],
            "effects": [
                "d20_test_penalty_per_level=2",
                "death_at_level=6",
                "speed_penalty_per_level=5",
            ],
        },
        (
            "You die if your Exhaustion level is 6.",
            "the roll is reduced by 2 times your Exhaustion level.",
            "Your Speed is reduced by a number of feet equal to 5 times your Exhaustion level.",
        ),
    ),
    "frightened": Reading(
        {
            "implies": [],
            "effects": [
                "cant_move_toward_fear_source",
                "disadvantage_ability_checks",
                "disadvantage_own_attacks",
            ],
        },
        (
            "You have Disadvantage on ability checks and attack rolls while the source of fear is "
            "within line of sight.",
            "Can't Approach. You can't willingly move closer to the source of fear.",
        ),
    ),
    "grappled": Reading(
        {
            "implies": [],
            "effects": [
                "disadvantage_attacks_except_grappler",
                "movable_by_grappler",
                "speed_zero",
            ],
        },
        (
            "Speed 0. Your Speed is 0 and can't increase.",
            "You have Disadvantage on attack rolls against any target other than the grappler.",
            "Movable. The grappler can drag or carry you when it moves,",
        ),
    ),
    "incapacitated": Reading(
        {
            "implies": [],
            "effects": [
                "breaks_concentration",
                "cannot_speak",
                "cannot_take_actions",
                "disadvantage_initiative",
            ],
        },
        (
            "Inactive. You can't take any action, Bonus Action, or Reaction.",
            "No Concentration. Your Concentration is broken.",
            "Speechless. You can't speak.",
            "If you're Incapacitated when you roll Initiative, you have Disadvantage on the roll.",
        ),
    ),
    "invisible": Reading(
        {
            "implies": [],
            "effects": [
                "advantage_initiative",
                "advantage_own_attacks",
                "disadvantage_attacks_against",
                "unseen",
            ],
        },
        (
            "If you're Invisible when you roll Initiative, you have Advantage on the roll.",
            "You aren't affected by any effect that requires its target to be seen",
            "Attack rolls against you have Disadvantage, and your attack rolls have Advantage.",
        ),
    ),
    "paralyzed": Reading(
        {
            "implies": ["incapacitated"],
            "effects": [
                "advantage_attacks_against",
                "auto_crit_within_5ft=5",
                "auto_fail_save/dex,str",
                "speed_zero",
            ],
        },
        (
            "Incapacitated. You have the Incapacitated condition.",
            "Speed 0. Your Speed is 0 and can't increase.",
            "You automatically fail Strength and Dexterity saving throws.",
            "Attack rolls against you have Advantage.",
            "Any attack roll that hits you is a Critical Hit if the attacker is within 5 feet of "
            "you.",
        ),
    ),
    "petrified": Reading(
        {
            "implies": ["incapacitated"],
            "effects": [
                "advantage_attacks_against",
                "auto_fail_save/dex,str",
                "immune_to_condition",
                "resist_all_damage",
                "speed_zero",
            ],
        },
        (
            "Incapacitated. You have the Incapacitated condition.",
            "Speed 0. Your Speed is 0 and can't increase.",
            "Attack rolls against you have Advantage.",
            "You automatically fail Strength and Dexterity saving throws.",
            "Resist Damage. You have Resistance to all damage.",
            "Poison Immunity. You have Immunity to the Poisoned condition.",
        ),
    ),
    "poisoned": Reading(
        {"implies": [], "effects": ["disadvantage_ability_checks", "disadvantage_own_attacks"]},
        ("Ability Checks and Attacks Affected. You have",),
    ),
    "prone": Reading(
        {
            "implies": [],
            "effects": [
                "advantage_attacks_against=5",
                "disadvantage_attacks_against=5",
                "disadvantage_own_attacks",
                "restricted_movement_crawl",
            ],
        },
        (
            "Restricted Movement. Your only movement options are to crawl or to spend an amount of "
            "movement equal to half your Speed (round down) to right yourself",
            "You have Disadvantage on attack rolls. An attack roll against you has Advantage if "
            "the attacker is within 5 feet of you. Otherwise, that attack roll has Disadvantage.",
        ),
    ),
    "restrained": Reading(
        {
            "implies": [],
            "effects": [
                "advantage_attacks_against",
                "disadvantage_own_attacks",
                "disadvantage_save/dex",
                "speed_zero",
            ],
        },
        (
            "Speed 0. Your Speed is 0 and can't increase.",
            "Attack rolls against you have Advantage, and your attack rolls have Disadvantage.",
            "Saving Throws Affected. You have Disadvantage on Dexterity saving throws.",
        ),
    ),
    "stunned": Reading(
        {
            "implies": ["incapacitated"],
            "effects": ["advantage_attacks_against", "auto_fail_save/dex,str"],
        },
        (
            "Incapacitated. You have the Incapacitated condition.",
            "You automatically fail Strength and Dexterity saving throws.",
            "Attack rolls against you have Advantage.",
        ),
    ),
    "unconscious": Reading(
        {
            "implies": ["incapacitated", "prone"],
            "effects": [
                "advantage_attacks_against",
                "auto_crit_within_5ft=5",
                "auto_fail_save/dex,str",
                "drops_held_items",
                "speed_zero",
            ],
        },
        (
            "Inert. You have the Incapacitated and Prone conditions, and you drop whatever you're "
            "holding.",
            "Speed 0. Your Speed is 0 and can't increase.",
            "Attack rolls against you have Advantage.",
            "You automatically fail Strength and Dexterity saving throws.",
            "Any attack roll that hits you is a Critical Hit",
        ),
    ),
}


def corpus_condition(record: Mapping[str, Any]) -> Facts:
    """A condition's implied conditions and effect kinds as its corpus record holds them."""
    kinds = []
    for effect in record["effects"]:
        kind = effect["kind"]
        if effect["abilities"]:
            kind += "/" + ",".join(sorted(effect["abilities"]))
        if effect["value"] is not None:
            kind += f"={effect['value']}"
        kinds.append(kind)
    return {"implies": sorted(record["implies"]), "effects": sorted(set(kinds))}
