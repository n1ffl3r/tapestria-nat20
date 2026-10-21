"""An item's mechanical fields, read from its SRD 5.2.1 text and from its corpus record.

Both readers return the same shape, a mapping from field name to a JSON value, so the
ledger compares them field by field (``spells.compare``). The fields are the ones spec 02a
lists for items: category, rarity, attunement, cost, weight, properties, and charges.

The Equipment chapter prints weapons, armor, and adventuring gear as tables, and tools and
some gear as headings with a cost ("Alchemist's Supplies (50 GP)"); ``equipment_facts``
reads them all, keyed by item name. The Magic Items chapter prints each item as a heading
over its kind, rarity, and attunement ("Weapon (Any Melee Weapon), Rare (Requires
Attunement)"); ``magic_item_facts`` reads one item's text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from fractions import Fraction
from typing import Any

from tools import srd_index
from tools.srd_audit import pdf_text
from tools.srd_audit.spells import Facts

# The Equipment chapter's pages (1-based), from Coins to the end of the chapter.
EQUIPMENT_PAGES = (89, 103)
# The Gameplay Toolbox's Sample Poisons, which price each poison in its heading.
POISON_PAGES = (197, 198)
MAGIC_ITEM_PAGES = (204, 253)
DAMAGE_TYPES = ("Bludgeoning", "Piercing", "Slashing")
MASTERIES = ("Cleave", "Graze", "Nick", "Push", "Sap", "Slow", "Topple", "Vex")
WEAPON_HEADINGS = {
    "Simple Melee Weapons": "simple_melee",
    "Simple Ranged Weapons": "simple_ranged",
    "Martial Melee Weapons": "martial_melee",
    "Martial Ranged Weapons": "martial_ranged",
}
ARMOR_HEADINGS = {
    "Light Armor": "light",
    "Medium Armor": "medium",
    "Heavy Armor": "heavy",
    "Shield": "shield",
}
COINS = {"CP": Fraction(1, 100), "SP": Fraction(1, 10), "EP": Fraction(1, 2), "GP": 1, "PP": 10}
COST = r"(?:[\d,]+ (?:CP|SP|EP|GP|PP)|Varies)"
WEIGHT = r"(?:[\d½/,.]+ lb\.|—|Varies)"
WEAPON_ROW = re.compile(
    rf"^(.+?) (\d+d\d+|1) ({'|'.join(DAMAGE_TYPES)}) (.*) ({'|'.join(MASTERIES)}) "
    rf"({WEIGHT}) ({COST})$"
)
ARMOR_ROW = re.compile(
    rf"^(.+?) (\+?\d+)(?: \+ Dex modifier( \(max 2\))?)? (—|Str \d+) (—|Disadvantage) "
    rf"({WEIGHT}) ({COST})$"
)
# A weapon row's damage is followed by its properties or a dash, where prose that names
# damage goes on in lower case ("take 1 Piercing damage").
WEAPON_START = re.compile(rf"^[A-Z][^.]*? (\d+d\d+|1) ({'|'.join(DAMAGE_TYPES)}) (?:[A-Z]|—)")
# A gear row's weight can carry a note: "Waterskin 5 lb. (full) 2 SP".
GEAR_ROW = re.compile(rf"^(.+?) ({WEIGHT})(?: \([a-z]+\))? ({COST})$")
# An Ammunition table row: "Arrows 20 Quiver 1 lb. 1 GP".
AMMUNITION_ROW = re.compile(rf"^(.+?) \d+ (?:Quiver|Case|Pouch) ({WEIGHT}) ({COST})$")
VARIANT = re.compile(rf"([A-Za-z][\w' -]+?) \(({COST})(?:, ({WEIGHT}))?\)")
COSTED_HEADING = re.compile(rf"^([A-Z][^()]+?) \(({COST})\)$")
TOOL_WEIGHT = re.compile(rf"\bWeight: ({WEIGHT})")
PROPERTY_NAMES = {
    "Ammunition": "ammunition",
    "Finesse": "finesse",
    "Heavy": "heavy",
    "Light": "light",
    "Loading": "loading",
    "Reach": "reach",
    "Thrown": "thrown",
    "Two-Handed": "two_handed",
    "Versatile": "versatile",
}
RARITIES = {
    "Common": "common",
    "Uncommon": "uncommon",
    "Rare": "rare",
    "Very Rare": "very_rare",
    "Legendary": "legendary",
    "Artifact": "artifact",
}
KIND_LINE = re.compile(
    r"^(Armor|Potion|Ring|Rod|Scroll|Staff|Wand|Weapon|Wondrous Item)(?: \(([^)]*)\))?, "
    r"(Rarity Varies|Very Rare|Common|Uncommon|Rare|Legendary|Artifact)"
    r"(?: \([^)]*\))?"
)
ATTUNEMENT = re.compile(r"\(Requires Attunement(?: by ([^)]*))?\)")
# A second rarity after the first: "Rare (+1), Very Rare (+2), or Legendary (+3)".
RARITY_LIST = re.compile(r"^,? (?:or )?(?:Very Rare|Common|Uncommon|Rare|Legendary)\b")
# The item's own charges, which its text states as "This wand has 7 charges" or "The staff
# has 10 charges"; a form's ("It has 24 charges") is the form's.
CHARGES = re.compile(r"\b(?:This|The) [a-z]+ has (\d+) charges\b")
MAGIC_WEAPON_FORM = re.compile(r"\b(?:functions|be wielded) as a magic (?:Mace|Quarterstaff)\b")


def money(text: str) -> float | None:
    """A cost in GP: "5 SP" is 0.5. "Varies" and a dash are no cost."""
    found = re.fullmatch(r"([\d,]+) (CP|SP|EP|GP|PP)", text.strip())
    if found is None:
        return None
    return float(int(found.group(1).replace(",", "")) * COINS[found.group(2)])


def pounds(text: str) -> float | None:
    """A weight in pounds: "1/4 lb." is 0.25, "58½ lb." 58.5, and a dash 0."""
    text = text.strip()
    if text == "—":
        return 0.0
    found = re.fullmatch(r"([\d,./½]+) lb\.", text)
    if found is None:
        return None
    raw = found.group(1).replace(",", "").replace("½", ".5")
    if raw.startswith("."):
        raw = "0" + raw
    return float(Fraction(raw))


def corpus_name(srd_name: str) -> str:
    """The name the corpus gives a table row the SRD inverts: "Clothes, Fine" is "Fine
    Clothes", "Case, Map or Scroll" is "Map or Scroll Case"."""
    head, _, rest = srd_name.partition(", ")
    return f"{rest} {head}" if rest else srd_name


def _rows(lines: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    """Stripped lines with their pages, a wrapped weapon row rejoined: a weapon row wraps its
    properties ("Light Crossbow 1d8 Piercing Ammunition (Range 80/320; Bolt), Loading," /
    "Two-Handed" / "Slow 5 lb. 25 GP")."""
    rows: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for page, raw in lines:
        line = raw.strip()
        if pending:
            pending = (pending[0], f"{pending[1]} {line}")
            if re.search(rf" {COST}$", pending[1]):
                rows.append(pending)
                pending = None
            continue
        if WEAPON_START.match(line) and not re.search(rf" {COST}$", line):
            pending = (page, line)
            continue
        rows.append((page, line))
    return rows


def _properties(text: str) -> tuple[list[str], dict[str, Any]]:
    """A weapon's properties, and the numbers some carry: a range and a Versatile die."""
    extra: dict[str, Any] = {}
    found: set[str] = set()
    for name, code in PROPERTY_NAMES.items():
        if re.search(rf"\b{name}\b", text):
            found.add(code)
    ranged = re.search(r"Range (\d+)/(\d+)", text)
    if ranged:
        extra["range"] = [int(ranged.group(1)), int(ranged.group(2))]
    versatile = re.search(r"Versatile \((\d+d\d+)\)", text)
    if versatile:
        extra["versatile"] = versatile.group(1)
    return sorted(found), extra


def _stated(weight: str | None, cost: str | None) -> Facts:
    """The weight and cost a row states. A weight that "Varies" states none; a cost that
    "Varies" states that the entry has no one price."""
    facts: Facts = {}
    if weight is not None and pounds(weight) is not None:
        facts["weight"] = pounds(weight)
    if cost is not None and (cost == "Varies" or money(cost) is not None):
        facts["cost"] = money(cost)
    return facts


def _variants(text: str, weight: float | None) -> dict[str, Facts]:
    """A tool's variants and their costs: "Variants: Bagpipes (30 GP, 6 lb.), drum (6 GP, 3
    lb.)". A variant without a weight has the tool's."""
    found: dict[str, Facts] = {}
    for variant in VARIANT.finditer(text.removeprefix("Variants:")):
        facts = _stated(variant.group(3), variant.group(2))
        if "weight" not in facts and weight is not None:
            facts["weight"] = weight
        found[variant.group(1).strip().capitalize()] = facts
    return found


def equipment_facts(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, tuple[int, Facts]]:
    """Each item's fields with its page, keyed by its SRD name: the Equipment chapter's
    tables and costed headings, and the sample poisons' headings."""
    if lines is None:
        lines = pdf_text.plain_lines(*EQUIPMENT_PAGES) + pdf_text.plain_lines(*POISON_PAGES)
    found: dict[str, tuple[int, Facts]] = {}
    weapon_category: str | None = None
    armor_category: str | None = None
    heading: str | None = None
    tool_weight: float | None = None
    variants = ""
    for page, line in _rows(lines):
        if variants:
            if line.startswith(("Ability:", "Utilize:", "Craft:")) or COSTED_HEADING.match(line):
                for name, facts in _variants(variants, tool_weight).items():
                    found.setdefault(name, (page, facts))
                variants = ""
            else:
                variants = variants.rstrip("-") + ("" if variants.endswith("-") else " ") + line
                continue
        if line.startswith("Variants:"):
            variants = line
            continue
        if line in WEAPON_HEADINGS:
            weapon_category, armor_category = WEAPON_HEADINGS[line], None
            continue
        armor = next((c for h, c in ARMOR_HEADINGS.items() if line.startswith(h + " (")), None)
        if armor:
            armor_category, weapon_category = armor, None
            continue
        if line.endswith("Weight Cost"):
            # Another table's column headings: "Item Weight Cost", "Focus Weight Cost".
            weapon_category = armor_category = None
            continue
        weapon = WEAPON_ROW.match(line)
        if weapon and weapon_category:
            properties, extra = _properties(weapon.group(4))
            found[weapon.group(1)] = (
                page,
                {
                    "category": weapon_category,
                    "damage": f"{weapon.group(2)} {weapon.group(3).lower()}",
                    "properties": properties,
                    "mastery": weapon.group(5).lower(),
                    **_stated(weapon.group(6), weapon.group(7)),
                    **extra,
                },
            )
            continue
        row = ARMOR_ROW.match(line)
        if row and armor_category:
            strength = row.group(4)
            found[row.group(1)] = (
                page,
                {
                    "category": armor_category,
                    "base_ac": int(row.group(2).lstrip("+")),
                    "dex_bonus_max": 2 if row.group(3) else (None if "Dex" in line else 0),
                    "strength_min": int(strength.split()[1]) if strength != "—" else None,
                    "stealth_disadvantage": row.group(5) == "Disadvantage",
                    **_stated(row.group(6), row.group(7)),
                },
            )
            if armor_category == "shield":
                # A Shield adds to AC; it has no Dexterity cap to state.
                del found[row.group(1)][1]["dex_bonus_max"]
            continue
        costed = COSTED_HEADING.match(line)
        if costed:
            heading, tool_weight = costed.group(1), None
            entry = found.setdefault(heading, (page, {}))[1]
            for field, value in _stated(None, costed.group(2)).items():
                entry.setdefault(field, value)
            continue
        weight = TOOL_WEIGHT.search(line)
        if weight and heading:
            tool_weight = pounds(weight.group(1))
            for field, value in _stated(weight.group(1), None).items():
                found[heading][1].setdefault(field, value)
            continue
        gear = AMMUNITION_ROW.match(line) or GEAR_ROW.match(line)
        if gear and not weapon_category and not armor_category:
            # A note on the row is not part of the name: "Amulet (worn or held)".
            name = re.sub(r" \([a-z][^)]*\)$", "", gear.group(1))
            found[name] = (page, _stated(gear.group(2), gear.group(3)))
    return found


def equipment_entry(
    found: Mapping[str, tuple[int, Facts]], name: str
) -> tuple[str, tuple[int, Facts]] | None:
    """The SRD entry a corpus name reads, under its own name or the SRD's inverted one
    ("Fine Clothes" is the table's "Clothes, Fine")."""
    wanted = name.casefold()
    for srd_name in found:
        if wanted in (srd_name.casefold(), corpus_name(srd_name).casefold()):
            return srd_name, found[srd_name]
    return None


def magic_item_facts(text: pdf_text.RecordText) -> Facts:
    """A magic item's category, rarity, attunement, and charges as its SRD 5.2.1 text
    states them. ``text`` starts at the item's heading. An item whose rarity depends on its
    form ("Rare (+1), Very Rare (+2), or Legendary (+3)", "Rarity Varies") states none."""
    head = pdf_text.flatten("\n".join(text.lines[1:4]))
    kind = KIND_LINE.match(head)
    if kind is None:
        raise ValueError(f"{text.name}: unread kind line {head[:80]!r}")
    body = pdf_text.flatten(text.text)
    # The attunement can follow a list of rarities: "Very Rare (+3) (Requires Attunement by
    # a Spellcaster)".
    attuned = ATTUNEMENT.search(head)
    facts: Facts = {
        "category": _magic_category(kind.group(1), kind.group(2) or "", body),
        "attunement": attuned is not None,
    }
    varies = kind.group(3) == "Rarity Varies" or RARITY_LIST.match(head[kind.end() :])
    if not varies:
        facts["rarity"] = RARITIES[kind.group(3)]
    if attuned and attuned.group(1):
        facts["attunement_by"] = attuned.group(1).strip()
    charges = CHARGES.search(body)
    if charges:
        facts["charges"] = int(charges.group(1))
    return facts


def _magic_category(kind: str, form: str, body: str) -> str:
    """The corpus category a magic item's SRD kind gives. A staff is a weapon: "Unless its
    description notes otherwise, a staff can be used as a nonmagical Quarterstaff". So is an
    item that "functions as a magic Mace". Magic ammunition is an item, as the corpus files
    all ammunition: it deals its weapon's damage, not its own."""
    if kind == "Weapon":
        return "magic_item" if form == "Any Ammunition" else "weapon"
    if kind == "Armor":
        return "armor"
    if kind == "Staff" or MAGIC_WEAPON_FORM.search(body):
        return "weapon"
    return "magic_item"


def magic_item_texts(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, pdf_text.RecordText]:
    """Each magic item's text, keyed by its name: its heading, then its lines from its kind
    line to the next item's heading. The headings are the ones ``srd_index.magic_items``
    indexes, a wrapped heading taking two lines."""
    flat = list(pdf_text.plain_lines(*MAGIC_ITEM_PAGES) if lines is None else lines)
    starts: list[tuple[int, int, str, int]] = []
    for index, (page, line) in enumerate(flat):
        if index == 0 or not srd_index.MAGIC_ITEM_KIND.match(line.strip()):
            continue
        following = " ".join(flat[i][1].strip() for i in range(index, min(index + 3, len(flat))))
        name = srd_index.heading_before(flat, index)
        if not srd_index.RARITY.search(following) or not name[0].isupper():
            continue
        if name.endswith((".", ",", ":")):
            continue
        wrapped = name != srd_index.fix_case(flat[index - 1][1].strip())
        starts.append((index - (2 if wrapped else 1), index, name, page))
    texts: dict[str, pdf_text.RecordText] = {}
    for position, (_, kind, name, page) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(flat)
        body = (name, *(line.strip() for _, line in flat[kind:end]))
        texts.setdefault(name, pdf_text.RecordText(name=name, page=page, lines=body))
    return texts


def corpus_facts(record: Mapping[str, Any], magic: bool) -> Facts:
    """An item's fields as its corpus record (``canonical/items/<slug>.json``) holds them.
    ``magic`` reads it as a magic item (kind, rarity, attunement, charges), else as
    equipment (cost, weight, and a weapon's or armor's table columns)."""
    kind = record.get("item_kind", "item")
    if magic:
        facts: Facts = {
            "category": kind if kind in ("weapon", "armor", "magic_item") else "item",
            "attunement": bool(record.get("requires_attunement")),
            "rarity": record.get("rarity"),
        }
        if record.get("attunement_constraint"):
            facts["attunement_by"] = record["attunement_constraint"].removeprefix("by ")
        charges = _charges(record)
        if charges is not None:
            facts["charges"] = charges
        return facts
    facts = {"weight": float(record.get("weight") or 0.0), "cost": record.get("cost_gp")}
    if kind == "weapon":
        parts = record.get("damage_parts") or []
        rng = record.get("range") or {}
        facts.update(
            {
                "category": record.get("weapon_category"),
                "damage": f"{parts[0]['dice']} {parts[0]['damage_type']}" if parts else None,
                "properties": sorted(
                    p for p in record.get("properties") or [] if p in PROPERTY_NAMES.values()
                ),
                "mastery": record.get("mastery"),
            }
        )
        if rng.get("long"):
            # A reach weapon's 10 feet is a reach, not a range.
            facts["range"] = [rng["value"], rng["long"]]
        if record.get("versatile_damage"):
            facts["versatile"] = record["versatile_damage"]["dice"]
    if kind == "armor":
        facts.update(
            {
                "category": record.get("armor_category"),
                "base_ac": record.get("base_ac"),
                "dex_bonus_max": record.get("dex_bonus_max"),
                "strength_min": record.get("strength_min"),
                "stealth_disadvantage": bool(record.get("stealth_disadvantage")),
            }
        )
    return facts


def _charges(record: Mapping[str, Any]) -> int | None:
    """An item's charges: its own pool, else the pool of the one activity that has one (the
    Hammer of Thunderbolts' throw, the only use its charges have)."""
    pools = [record.get("uses") or {}]
    if not str(pools[0].get("max") or "").isdigit():
        pools = [
            a["uses"]
            for a in record.get("activities") or []
            if str((a.get("uses") or {}).get("max") or "").isdigit()
        ]
    if len(pools) == 1 and str(pools[0].get("max") or "").isdigit():
        return int(pools[0]["max"]) if int(pools[0]["max"]) > 1 else None
    return None


# The fields a variant record is compared on: the parent entry's kind line gives them for
# every form. A Mysterious Deck card is a piece of the deck, which alone has a rarity.
VARIANT_FIELDS = ("category", "attunement", "rarity")
PART_FIELDS = {"Mysterious Deck": ("attunement",)}
MAGIC_NOT_COMPARED = {
    "cost": (
        "The SRD prices no magic item; its Magic Item Rarities and Values table suggests a"
        " value by rarity, which the corpus's price follows or not."
    ),
    "weight": "The SRD weighs no magic item.",
}


def audited(
    srd: Mapping[str, Any], corpus: Mapping[str, Any], magic: bool, parent: str | None
) -> tuple[Facts, Facts, dict[str, str]]:
    """The SRD and corpus fields to compare, and why each other corpus field is not: the
    SRD does not state it, the record is a variant (``parent``), or it is a magic item's
    price or weight."""
    not_compared: dict[str, str] = dict(MAGIC_NOT_COMPARED) if magic else {}
    fields = set(srd)
    if parent is not None:
        kept = PART_FIELDS.get(parent, VARIANT_FIELDS)
        for field in sorted(fields - set(kept)):
            not_compared[field] = (
                f"A form of the SRD's {parent}, whose entry does not state it for each form"
                " (srd/aliases.json)."
            )
        fields &= set(kept)
    for field in sorted(set(corpus) - fields):
        not_compared.setdefault(field, "The SRD's entry does not state it.")
    return (
        {k: srd[k] for k in sorted(fields)},
        {k: corpus.get(k) for k in sorted(fields)},
        not_compared,
    )
