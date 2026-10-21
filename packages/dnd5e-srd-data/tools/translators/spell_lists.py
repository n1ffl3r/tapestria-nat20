"""Class spell lists, translated from the SRD 5.2.1 PDF index (spec 02a, acceptance
criterion 6).

Foundry ships no class spell lists. The PDF gives each spell's classes in its heading
("Level 3 Evocation (Sorcerer, Wizard)"), which ``tools/srd_index.py`` records in
``srd/index.json``; this module turns those into one ``SpellList`` per class, joining each
spell to its corpus slug by name. The class chapters print the same lists as tables, and
``tests/test_spell_lists.py`` checks the shipped lists against them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dnd5e_srd_data.schema.spell_list import SpellList, SpellListEntry, SpellListSource

ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "srd" / "index.json"

#: Class slug to the class's name, for the classes the SRD gives a spell list.
CLASS_NAMES: dict[str, str] = {
    "bard": "Bard",
    "cleric": "Cleric",
    "druid": "Druid",
    "paladin": "Paladin",
    "ranger": "Ranger",
    "sorcerer": "Sorcerer",
    "warlock": "Warlock",
    "wizard": "Wizard",
}


def build_spell_lists(index: Mapping[str, Any], slug_by_name: Mapping[str, str]) -> list[SpellList]:
    """One ``SpellList`` per class, from ``index`` (the parsed ``srd/index.json``) and the
    corpus's spell slugs keyed by spell name. A spell the index lists but the corpus lacks,
    or a class outside ``CLASS_NAMES``, is an error."""
    entries: dict[str, list[SpellListEntry]] = {slug: [] for slug in CLASS_NAMES}
    for spell in index["categories"]["spells"]:
        slug = slug_by_name.get(spell["name"])
        if slug is None:
            raise ValueError(f"index spell {spell['name']!r} has no corpus record")
        for class_slug in spell["classes"]:
            if class_slug not in entries:
                raise ValueError(f"{spell['name']}: unknown class {class_slug!r}")
            entries[class_slug].append(SpellListEntry(slug=slug, level=spell["level"]))
    source = SpellListSource(path=index["source"]["path"], sha256=index["source"]["sha256"])
    return [
        SpellList(
            slug=class_slug,
            class_name=CLASS_NAMES[class_slug],
            spells=sorted(spells, key=lambda entry: (entry.level, entry.slug)),
            source=source,
        )
        for class_slug, spells in entries.items()
    ]


def write_spell_lists(canonical: Path) -> int:
    """Write ``canonical/spell_lists/<class>.json`` from the committed index and the
    ``canonical/spells/`` records. Returns the number of lists written."""
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    slug_by_name = {
        json.loads(path.read_text(encoding="utf-8"))["name"]: path.stem
        for path in sorted((canonical / "spells").glob("*.json"))
    }
    out = canonical / "spell_lists"
    out.mkdir(parents=True, exist_ok=True)
    lists = build_spell_lists(index, slug_by_name)
    for spell_list in lists:
        (out / f"{spell_list.slug}.json").write_text(
            json.dumps(spell_list.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return len(lists)
