"""Build ``srd/index.json`` from the pinned official SRD 5.2.1 PDF.

The index lists every SRD record by category, with the PDF page it starts on.
It is the SRD gate's authority (``tools/translators/srd_gate.py``): a record is
SRD content when the index lists it, or when ``srd/aliases.json`` ties it to a
listed record. Regenerate after changing this tool or the pinned PDF:

    make srd-index

The tool refuses a PDF whose SHA-256 differs from ``raw_sources/PINS.json``,
and its output is deterministic for a given PDF.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINS_PATH = ROOT / "raw_sources" / "PINS.json"
INDEX_PATH = ROOT / "srd" / "index.json"
ALIASES_PATH = ROOT / "srd" / "aliases.json"
# Pages after a parent's first page that its entry, and so an alias's evidence, may reach.
EVIDENCE_PAGES = 3

CATEGORIES = (
    "spells",
    "stat_blocks",
    "magic_items",
    "equipment",
    "traps",
    "classes",
    "subclasses",
    "class_features",
    "class_feature_options",
    "species",
    "species_traits",
    "backgrounds",
    "feats",
    "conditions",
)

SIZES = r"(?:Tiny|Small|Medium|Large|Huge|Gargantuan)"
CREATURE_TYPES = (
    r"(?:Aberration|Beast|Celestial|Construct|Dragon|Elemental|Fey|Fiend|Giant|Humanoid|"
    r"Monstrosity|Ooze|Plant|Undead|Beasts|Undead)"
)
# "Large Aberration, Lawful Evil"; "Medium or Small Monstrosity (Lycanthrope), ...";
# "Huge or Smaller Construct, Unaligned"; "Large Swarm of Tiny Beasts, Unaligned";
# "Large Celestial, Fey, or Fiend (Your Choice), Neutral".
STAT_BLOCK_TYPE = re.compile(
    rf"^{SIZES}(?: or (?:{SIZES}|Smaller))?(?: Swarm of {SIZES})? {CREATURE_TYPES}\b[^\n]*,"
)
# The kind is followed at once by a comma or its parenthetical ("Potion, Rare", "Weapon (Any
# Ammunition), ..."), which keeps out table rows such as "Potion of Giant Strength (hill) 21".
MAGIC_ITEM_KIND = re.compile(
    r"^(?:Armor|Potion|Ring|Rod|Scroll|Staff|Wand|Weapon|Wondrous Item)(?:,| \()"
)
RARITY = re.compile(r"\b(?:Common|Uncommon|Rare|Very Rare|Legendary|Artifact|Rarity Varies)\b")
SPELL_TYPE = re.compile(r"^(?:Level (\d) [A-Z][a-z]+|([A-Z][a-z]+) Cantrip) \(([^)]*)\)?")
TRAP_TYPE = re.compile(r"^(?:Nuisance|Deadly) Trap \(Levels")
COST = r"[\d,]+ (?:CP|SP|EP|GP|PP)"
# A table row ending in a weight (or a dash) and a cost: adventuring gear, armor, and the
# last line of a wrapped weapon row ("Slow 10 lb. 500 GP").
COSTED_ROW = re.compile(rf"^(.+?) (?:[\d½/,.]+ lb\.|—) {COST}$")
# A weapon row starts with its name, then its damage: "Longsword 1d8 Slashing ...".
WEAPON_ROW = re.compile(r"^([A-Z][A-Za-z,' ]+?) (?:\d+d\d+|1) (?:Bludgeoning|Piercing|Slashing)\b")
# A tool or gear heading with its cost: "Alchemist's Supplies (50 GP)", "Ammunition (Varies)".
COSTED_HEADING = re.compile(rf"^([A-Z][^()]+?) \((?:{COST}|Varies)\)$")
# "Variants: Bagpipes (30 GP, 6 lb.), drum (6 GP, 3 lb.), ..." under a tool, wrapping lines.
VARIANT_ITEM = re.compile(r"([A-Za-z][A-Za-z' -]+?) \((?:[\d,]+ (?:CP|SP|GP)[^)]*)\)")
CORE_TRAITS = re.compile(r"^Core ([A-Z][a-z]+) Traits$")
SUBCLASS = re.compile(r"^([A-Z][a-z]+) Subclass: ?(.*)$")
CLASS_FEATURE = re.compile(r"^Level \d+: (.+)$")
SPECIES_TYPE = re.compile(r"^Creature Type: ")
BACKGROUND_SCORES = re.compile(r"^Ability Scores: ")
FEAT_KIND = re.compile(r"^(?:Origin|General|Fighting Style|Epic Boon) Feat\b")
CONDITION = re.compile(r"^([A-Z][a-z]+) \[Condition\]$")
# A run-in trait inside a species entry: "Draconic Ancestry. Your lineage stems ...".
RUN_IN = re.compile(r"^([A-Z][A-Za-z']+(?: [A-Za-z']+){0,4})(?: \([^)]*\))?\. ")
METAMAGIC_COST = re.compile(r"^Cost: \d+ Sorcery Points?$")
SMALL_WORDS = ("of", "the", "and", "a", "an", "to", "with", "from")
RUNNING_HEAD = "System Reference Document 5.2.1"
# The chapter after the classes: run-ins past it belong to no class feature.
AFTER_CLASSES = "Character Origins"
# Words pypdf reads from small capitals with mixed case ("SplASh").
MIXED_CASE = re.compile(r"\b([A-Z][a-z]+)([A-Z]{2,}[a-z]*)\b")


def pinned_pdf() -> tuple[Path, str]:
    pins = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    pin = pins["srd_pdf"]
    return ROOT / pin["path"], pin["sha256"]


def verify_pdf(path: Path, sha256: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} does not match PINS.json ({sha256})")


def normalize_text(text: str) -> str:
    """Straight quotes, no soft hyphens, and no trailing spaces, so headings compare."""
    text = text.replace("’", "'").replace("‘", "'").replace("­", "")
    text = text.replace("“", '"').replace("”", '"')
    return "\n".join(line.rstrip() for line in text.split("\n"))


def read_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [strip_running_head(normalize_text(page.extract_text() or "")) for page in reader.pages]


def strip_running_head(page: str) -> str:
    """Drop the running head ("System Reference Document 5.2.1") and the page number, which
    otherwise sit between an entry and the heading after a page break."""
    lines = page.split("\n")
    while lines and (lines[0].strip() in ("", RUNNING_HEAD) or lines[0].strip().isdigit()):
        lines.pop(0)
    return "\n".join(lines)


def lines_with_pages(pages: list[str]) -> list[tuple[int, str]]:
    return [(number, line) for number, page in enumerate(pages, 1) for line in page.split("\n")]


def fix_case(name: str) -> str:
    """Undo pypdf's reading of small capitals: "Acid SplASh" is "Acid Splash"."""
    return MIXED_CASE.sub(lambda m: (m.group(1) + m.group(2)).capitalize(), name)


def heading_before(lines: list[tuple[int, str]], index: int) -> str:
    """The heading that ends just above ``lines[index]``, joining a wrapped second line
    ("Amulet of Proof against Detection" / "and Location")."""
    name = lines[index - 1][1].strip()
    above = lines[index - 2][1].strip() if index >= 2 else ""
    if name and name[0].islower() and above and not above.endswith("."):
        name = f"{above} {name}"
    return fix_case(name)


def entries(
    lines: list[tuple[int, str]], marker: re.Pattern[str]
) -> Iterator[tuple[str, int, re.Match[str]]]:
    for index, (page, line) in enumerate(lines):
        match = marker.match(line.strip())
        if match and index > 0:
            name = heading_before(lines, index)
            if name and not name.endswith((".", ",", ":")):
                yield name, page, match


def spells(lines: list[tuple[int, str]]) -> list[dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    for index, (page, line) in enumerate(lines):
        match = SPELL_TYPE.match(line.strip())
        if not match or index == 0:
            continue
        classes_text = match.group(3)
        # A long class list wraps: "(Bard, Sorcerer, Warlock," / "Wizard)".
        if not line.rstrip().endswith(")") and index + 1 < len(lines):
            classes_text += " " + lines[index + 1][1].strip().rstrip(")")
        name = fix_case(lines[index - 1][1].strip())
        found.setdefault(
            name,
            {
                "name": name,
                "page": page,
                "level": int(match.group(1)) if match.group(1) else 0,
                "classes": sorted(c.strip().lower() for c in classes_text.split(",") if c.strip()),
            },
        )
    return sorted(found.values(), key=lambda entry: str(entry["name"]))


def magic_items(lines: list[tuple[int, str]]) -> Iterator[tuple[str, int, object]]:
    """A magic item's heading is followed by its kind and rarity, which can wrap over three
    lines: "Weapon (Glaive, Greatsword, Longsword, Rapier," / "Scimitar, or Shortsword), Very
    Rare (Requires"."""
    for index, (page, line) in enumerate(lines):
        text = line.strip()
        if index == 0 or not MAGIC_ITEM_KIND.match(text):
            continue
        following = " ".join(lines[i][1].strip() for i in range(index, min(index + 3, len(lines))))
        if not RARITY.search(following):
            continue
        name = heading_before(lines, index)
        if name and name[0].isupper() and not name.endswith((".", ",", ":")):
            yield name, page, None


def equipment(lines: list[tuple[int, str]]) -> Iterator[tuple[str, int, object]]:
    """Equipment from the Equipment chapter's tables and headings: gear, armor, and weapon
    rows, tool and gear headings with a cost, and the variants listed under a tool."""
    for index, (page, line) in enumerate(lines):
        text = line.strip()
        for pattern in (WEAPON_ROW, COSTED_HEADING):
            match = pattern.match(text)
            if match:
                yield fix_case(match.group(1).strip()), page, None
        match = COSTED_ROW.match(text)
        if match and not WEAPON_ROW.match(text):
            # Cut the name at its first statistic or parenthetical: "Studded Leather Armor 12
            # + Dex modifier", "Amulet (worn or held)", "Arrows (20)".
            name = re.split(r" (?=[\d+—(])", match.group(1))[0].strip()
            if name and name[0].isupper() and not re.search(r"\d", name):
                yield fix_case(name), page, None
        if text.startswith("Variants:"):
            listed = text
            for follow in range(index + 1, min(index + 4, len(lines))):
                nxt = lines[follow][1].strip()
                ends = nxt.startswith(("Ability:", "Utilize:", "Craft:"))
                if not nxt or ends or COSTED_HEADING.match(nxt):
                    break
                listed = listed.rstrip("-") + ("" if listed.endswith("-") else " ") + nxt
            for variant in VARIANT_ITEM.finditer(listed.removeprefix("Variants:")):
                yield variant.group(1).strip().capitalize(), page, None


def matched(
    lines: list[tuple[int, str]], marker: re.Pattern[str], group: int = 1
) -> Iterator[tuple[str, int, object]]:
    for page, line in lines:
        match = marker.match(line.strip())
        if match and "..." not in line:
            yield fix_case(match.group(group).strip()), page, None


def subclasses(lines: list[tuple[int, str]]) -> Iterator[tuple[str, int, object]]:
    """ "Barbarian Subclass: Path of the Berserker", the name sometimes on the next line."""
    for index, (page, line) in enumerate(lines):
        match = SUBCLASS.match(line.strip())
        if not match:
            continue
        following = lines[index + 1][1].strip() if index + 1 < len(lines) else ""
        name = match.group(2).strip()
        if not name:
            name = following
        elif len(following.split()) <= 2:
            # A name that wraps ("Warrior of the" / "Open Hand"); the line after a whole name
            # is the subclass's tagline, which is longer ("Master Unarmed Combat Techniques").
            name = f"{name} {following}"
        if name and "..." not in name and not re.search(r"\d", name):
            yield fix_case(name), page, None


def title_case(text: str) -> bool:
    words = text.split()
    return bool(words) and all(w[0].isupper() or w in SMALL_WORDS for w in words)


def feature_options(lines: list[tuple[int, str]]) -> Iterator[tuple[str, int, object]]:
    """Options a class feature offers: Metamagic options ("Careful Spell" over "Cost: 1
    Sorcery Point"), Eldritch Invocations (the headings of the Eldritch Invocation Options
    section), and run-in choices under a "Level N: Feature" heading, named "Feature: Option"
    ("Divine Order: Protector")."""
    feature: str | None = None
    invocations = False
    for index, (page, line) in enumerate(lines):
        text = line.strip()
        if METAMAGIC_COST.match(text) and index > 0:
            yield fix_case(lines[index - 1][1].strip()), page, None
        if text == "Eldritch Invocation Options":
            invocations = True
            continue
        if invocations:
            if SUBCLASS.match(text) or text.endswith("Spell List"):
                invocations = False
            elif (
                title_case(text)
                and not text.endswith((".", ","))
                and len(text.split()) <= 6
                and lines[index - 1][1].strip().endswith(".")
            ):
                yield fix_case(text), page, None
        heading = CLASS_FEATURE.match(text)
        if heading:
            feature = heading.group(1).strip()
            continue
        if text == AFTER_CLASSES:
            break
        if SUBCLASS.match(text) or CORE_TRAITS.match(text):
            feature = None
        run_in = RUN_IN.match(text)
        if feature and run_in and line.startswith(" ") and title_case(run_in.group(1)):
            yield fix_case(f"{feature}: {run_in.group(1)}"), page, None


def species_traits(lines: list[tuple[int, str]]) -> Iterator[tuple[str, int, object]]:
    """Run-in traits inside each species entry, from its "Creature Type:" line to the next
    species or the end of the Species chapter (the Feats chapter)."""
    inside = False
    for page, line in lines:
        text = line.strip()
        if SPECIES_TYPE.match(text):
            inside = True
            continue
        if text in ("Feats", "Feat Descriptions"):
            inside = False
        if inside:
            match = RUN_IN.match(text)
            if not match:
                continue
            label = match.group(1)
            if title_case(label) and label not in ("Creature Type", "Size", "Speed"):
                yield fix_case(label), page, None


def named(found: Iterator[tuple[str, int, object]]) -> list[dict[str, object]]:
    first: dict[str, int] = {}
    for name, page, _ in found:
        first.setdefault(name, page)
    return [{"name": name, "page": page} for name, page in sorted(first.items())]


def build(pages: list[str]) -> dict[str, list[dict[str, object]]]:
    lines = lines_with_pages(pages)
    index: dict[str, list[dict[str, object]]] = {
        "spells": spells(lines),
        "stat_blocks": named(entries(lines, STAT_BLOCK_TYPE)),
        "magic_items": named(magic_items(lines)),
        "equipment": named(equipment(lines)),
        "traps": named(entries(lines, TRAP_TYPE)),
        "classes": named(matched(lines, CORE_TRAITS)),
        "subclasses": named(subclasses(lines)),
        "class_features": named(matched(lines, CLASS_FEATURE)),
        "class_feature_options": named(feature_options(lines)),
        "species": named(entries(lines, SPECIES_TYPE)),
        "species_traits": named(species_traits(lines)),
        "backgrounds": named(entries(lines, BACKGROUND_SCORES)),
        "feats": named(entries(lines, FEAT_KIND)),
        "conditions": named(matched(lines, CONDITION)),
    }
    for category in CATEGORIES:
        index.setdefault(category, [])
    return index


def flatten(text: str) -> str:
    """Lowercase, with words hyphenated across lines rejoined and whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"(\w) ?-\n(\w)", r"\1\2", text)).lower()


def alias_errors(
    aliases: list[dict[str, str]], index: dict[str, list[dict[str, object]]], pages: list[str]
) -> list[str]:
    """Every alias names a parent the index lists (or, for ``text``, a heading line in the
    PDF), and its evidence phrase appears on the parent's pages."""
    errors = []
    for alias in aliases:
        category, parent = alias["of_category"], alias["of"]
        if category == "text":
            first = next((n for n, page in enumerate(pages, 1) if parent in page.split("\n")), None)
        else:
            first = next(
                (int(str(e["page"])) for e in index.get(category, []) if e["name"] == parent), None
            )
        if first is None:
            errors.append(f"{alias['name']}: parent {parent!r} not in {category}")
            continue
        window = flatten("\n".join(pages[first - 1 : first + EVIDENCE_PAGES]))
        if flatten(alias["evidence"]) not in window:
            evidence = alias["evidence"]
            errors.append(f"{alias['name']}: {evidence!r} not near {parent!r} (p. {first})")
    return errors


def main() -> int:
    path, sha256 = pinned_pdf()
    verify_pdf(path, sha256)
    pages = read_pages(path)
    index = build(pages)
    aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))["aliases"]
    errors = alias_errors(aliases, index, pages)
    if errors:
        print("\n".join(f"[srd-index] alias: {error}" for error in errors))
        return 1
    document = {
        "source": {"path": str(path.relative_to(ROOT)), "sha256": sha256, "version": "5.2.1"},
        "categories": {category: index[category] for category in CATEGORIES},
    }
    INDEX_PATH.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for category in CATEGORIES:
        print(f"[srd-index] {category}: {len(index[category])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
