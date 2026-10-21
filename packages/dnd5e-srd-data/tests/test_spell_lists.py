"""Class spell lists (spec 02a, acceptance criterion 6).

The corpus ships each spellcasting class's spell list, translated from the SRD 5.2.1 PDF's
spell headings ("Level 3 Evocation (Sorcerer, Wizard)") as ``srd/index.json`` records
them. These tests check the shipped lists against the index (hermetic) and against the
class chapters' spell tables in the pinned PDF itself, which are an independent reading.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dnd5e_srd_data import BundledAssetLoader, MemoryAssetLoader
from dnd5e_srd_data.schema.spell_list import SpellList, SpellListEntry, SpellListSource
from tools.translators.spell_lists import CLASS_NAMES, build_spell_lists

ROOT = Path(__file__).resolve().parents[1]
INDEX = json.loads((ROOT / "srd" / "index.json").read_text(encoding="utf-8"))
LOADER = BundledAssetLoader()
PDF = ROOT / "srd" / "SRD_CC_v5.2.1.pdf"
SCHOOL_CODES = {
    "Abjuration": "abj",
    "Conjuration": "con",
    "Divination": "div",
    "Enchantment": "enc",
    "Evocation": "evo",
    "Illusion": "ill",
    "Necromancy": "nec",
    "Transmutation": "trs",
}
# Where the class chapters' tables leave out a spell its own heading puts on the class's
# list (audit/srd_5_2_1_divergences.json, "spell_lists"). The shipped lists follow the
# headings.
TABLE_OMISSIONS = {
    "bard": {"Phantasmal Force"},
    "sorcerer": {"Phantasmal Force", "Mind Spike"},
    "wizard": {"Phantasmal Force"},
}
# Where a chapter table gives a spell a school its own heading does not: Flaming Sphere's
# heading reads "Level 2 Conjuration (Druid, Sorcerer, Wizard)", and the Druid, Sorcerer,
# and Wizard tables list it as Evocation. The corpus follows the heading (issue #3).
TABLE_SCHOOLS = {"Flaming Sphere": "Evocation"}


def _lists() -> dict[str, SpellList]:
    lists = {}
    for slug in LOADER.list_slugs("spell_lists"):
        spell_list = LOADER.get_spell_list(slug)
        assert spell_list is not None
        lists[slug] = spell_list
    return lists


def _names() -> dict[str, str]:
    names = {}
    for slug in LOADER.list_slugs("spells"):
        spell = LOADER.get_spell(slug)
        assert spell is not None
        names[slug] = spell.name
    return names


def test_eight_classes_have_a_spell_list() -> None:
    assert sorted(_lists()) == sorted(CLASS_NAMES)
    for slug in ("barbarian", "fighter", "monk", "rogue", "no-such-class"):
        assert LOADER.get_spell_list(slug) is None


@pytest.mark.parametrize(
    ("class_slug", "count"),
    [
        ("bard", 130),
        ("cleric", 109),
        ("druid", 124),
        ("paladin", 38),
        ("ranger", 48),
        ("sorcerer", 140),
        ("warlock", 72),
        ("wizard", 218),
    ],
)
def test_spell_list_sizes(class_slug: str, count: int) -> None:
    """The number of SRD 5.2.1 spells whose heading names the class."""
    spell_list = LOADER.get_spell_list(class_slug)
    assert spell_list is not None
    assert len(spell_list.spells) == count


def test_every_spell_is_on_a_list_at_its_level() -> None:
    """All 339 SRD spells are on at least one list, each at its own level."""
    lists = _lists()
    on_a_list = {entry.slug for spell_list in lists.values() for entry in spell_list.spells}
    assert len(on_a_list) == 339
    assert on_a_list == set(LOADER.list_slugs("spells"))
    for spell_list in lists.values():
        for entry in spell_list.spells:
            spell = LOADER.get_spell(entry.slug)
            assert spell is not None
            assert entry.level == spell.level, (spell_list.slug, entry.slug)


def test_lists_match_the_spell_headings() -> None:
    """Each spell's classes, as the shipped lists give them, are the classes its heading
    names in the PDF, and each list is sorted by level, then slug."""
    lists = _lists()
    slug_by_name = {name: slug for slug, name in _names().items()}
    for spell in INDEX["categories"]["spells"]:
        slug = slug_by_name[spell["name"]]
        on = sorted(c for c, sl in lists.items() if slug in {e.slug for e in sl.spells})
        assert on == spell["classes"], spell["name"]
        for class_slug in on:
            assert slug in lists[class_slug].at_level(spell["level"])
    for spell_list in lists.values():
        keys = [(entry.level, entry.slug) for entry in spell_list.spells]
        assert keys == sorted(keys)
        assert spell_list.source.sha256 == INDEX["source"]["sha256"]


def test_build_spell_lists_rejects_an_unknown_spell_or_class() -> None:
    index = {
        "source": INDEX["source"],
        "categories": {"spells": [{"name": "Fireball", "level": 3, "classes": ["wizard"]}]},
    }
    with pytest.raises(ValueError, match="no corpus record"):
        build_spell_lists(index, {})
    index["categories"]["spells"][0]["classes"] = ["fighter"]
    with pytest.raises(ValueError, match="unknown class"):
        build_spell_lists(index, {"Fireball": "fireball"})


def test_memory_loader_serves_spell_lists() -> None:
    spell_list = SpellList(
        slug="wizard",
        class_name="Wizard",
        spells=[SpellListEntry(slug="fireball", level=3)],
        source=SpellListSource(path="srd/SRD_CC_v5.2.1.pdf", sha256="0" * 64),
    )
    loader = MemoryAssetLoader(spell_lists=[spell_list])
    assert loader.get_spell_list("wizard") == spell_list
    assert loader.get_spell_list("bard") is None
    assert loader.list_slugs("spell_lists") == ["wizard"]
    assert ("spell_lists", "wizard") in loader


@pytest.fixture(scope="module")
def chapter_tables() -> dict[str, dict[str, object]]:
    if not PDF.is_file():
        pytest.skip("the PDF ships in the repository, not in the sdist")
    from tools.srd_audit.spell_lists import chapter_lists

    return chapter_lists()  # type: ignore[return-value]


def test_lists_match_the_class_chapter_tables(chapter_tables: dict[str, dict]) -> None:
    """The class chapters' tables list the same spells at the same levels, except the
    four omissions the SRD makes (``TABLE_OMISSIONS``)."""
    names = _names()
    assert sorted(chapter_tables) == sorted(CLASS_NAMES)
    for class_slug, spell_list in _lists().items():
        shipped = {names[entry.slug]: entry.level for entry in spell_list.spells}
        table = {name: row.level for name, row in chapter_tables[class_slug].items()}
        omitted = TABLE_OMISSIONS.get(class_slug, set())
        assert set(shipped) - set(table) == omitted, class_slug
        assert set(table) <= set(shipped), class_slug
        assert {n: lv for n, lv in shipped.items() if n not in omitted} == table, class_slug


def test_chapter_tables_agree_with_the_spells(chapter_tables: dict[str, dict]) -> None:
    """Each table row's school and its Special column's C (Concentration), R (Ritual), and
    M (a specific Material component: one with a cost, or one the spell consumes) match
    the spell's corpus record, apart from ``TABLE_SCHOOLS``."""
    by_name = {}
    for slug in LOADER.list_slugs("spells"):
        spell = LOADER.get_spell(slug)
        assert spell is not None
        by_name[spell.name] = spell
    for class_slug, table in chapter_tables.items():
        for name, row in table.items():
            spell = by_name[name]
            if name in TABLE_SCHOOLS:
                assert row.school == TABLE_SCHOOLS[name] and spell.school == "con", name
            else:
                assert SCHOOL_CODES[row.school] == spell.school, (class_slug, name)
            assert ("C" in row.special) == spell.concentration, (class_slug, name)
            assert ("R" in row.special) == spell.ritual, (class_slug, name)
            specific = spell.materials.cost > 0 or spell.materials.consumed
            assert ("M" in row.special) == specific, (class_slug, name)
