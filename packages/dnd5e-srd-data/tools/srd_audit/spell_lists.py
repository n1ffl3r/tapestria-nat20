"""The class spell lists as the SRD 5.2.1 class chapters print them.

Each spellcasting class's chapter has a "<Class> Spell List" section: a table per level
("Cantrips (Level 0 Wizard Spells)", "Level 1 Wizard Spells") with each spell's name,
school, and Special column (C for Concentration, R for Ritual, M for a specific Material
component). The shipped lists come from the spell headings instead
(``tools/translators/spell_lists.py``); these tables are the independent check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools import srd_index
from tools.srd_audit import pdf_text

# The class chapters' pages (1-based): Bard's spell list starts on page 33, and the last
# list, Wizard's, ends before the Evoker subclass on page 82.
CLASS_PAGES = (28, 82)
SECTION = re.compile(r"^(\w+) Spell List$")
LEVEL = re.compile(r"^(?:Cantrips \(Level 0 \w+ Spells\)|Level (\d) \w+ Spells)$")
ROW = re.compile(
    r"^(.+?) (Abjuration|Conjuration|Divination|Enchantment|Evocation|Illusion|Necromancy|"
    r"Transmutation) ([—–]|[CRM](?:, [CRM])*)$"
)


@dataclass(frozen=True)
class TableRow:
    """One row of a class chapter's spell table."""

    level: int
    school: str
    special: frozenset[str]


def chapter_lists(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, dict[str, TableRow]]:
    """Each class's table rows, keyed by class slug, then by spell name."""
    lines = pdf_text.plain_lines(*CLASS_PAGES) if lines is None else lines
    lists: dict[str, dict[str, TableRow]] = {}
    current: str | None = None
    level: int | None = None
    for _, raw in lines:
        line = raw.strip()
        section = SECTION.match(line)
        if section:
            current, level = section.group(1).lower(), None
            lists.setdefault(current, {})
            continue
        if current is None:
            continue
        if srd_index.SUBCLASS.match(line):
            current = None
            continue
        heading = LEVEL.match(line)
        if heading:
            level = int(heading.group(1) or 0)
            continue
        row = ROW.match(line)
        if row and level is not None:
            special = frozenset() if row.group(3) in "—–" else frozenset(row.group(3).split(", "))
            lists[current][srd_index.fix_case(row.group(1))] = TableRow(
                level=level, school=row.group(2), special=special
            )
    return lists


def chapter_pages(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, int]:
    """The page each class's "<Class> Spell List" section starts on."""
    lines = pdf_text.plain_lines(*CLASS_PAGES) if lines is None else lines
    pages: dict[str, int] = {}
    for page, raw in lines:
        section = SECTION.match(raw.strip())
        if section:
            pages.setdefault(section.group(1).lower(), page)
    return pages
