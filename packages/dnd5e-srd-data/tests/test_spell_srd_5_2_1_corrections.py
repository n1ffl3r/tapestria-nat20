"""The spell audit's corrections (spec 02a, acceptance criterion 4).

The pinned Foundry pack differs from the SRD 5.2.1 PDF on a few spell fields. The
translator fixes them: by reading a value Foundry writes as a formula at the spell's own
level, by reading a material component's cost and consumption from its text, and by
``_SPELL_SRD_CORRECTIONS``, whose entries each quote the SRD sentence they rest on. These
tests pin the corrected corpus; ``audit/srd_5_2_1_ledger.json`` records every change.
"""

from __future__ import annotations

import pytest

from dnd5e_srd_data import BundledAssetLoader
from dnd5e_srd_data.schema.spell import Spell
from tools.translators.foundry import (
    _SPELL_SRD_CORRECTIONS,
    _apply_spell_srd_corrections,
    _base_level_value,
    _correction_parent,
    _spell_materials,
)

LOADER = BundledAssetLoader()


def _spell(slug: str) -> Spell:
    spell = LOADER.get_spell(slug)
    assert spell is not None, slug
    return spell


@pytest.mark.parametrize(
    ("slug", "path", "value"),
    [
        (slug, correction.path, correction.value)
        for slug, corrections in sorted(_SPELL_SRD_CORRECTIONS.items())
        for correction in corrections
    ],
)
def test_every_correction_is_in_the_corpus(slug: str, path: str, value: object) -> None:
    blob = _spell(slug).model_dump(mode="json")
    parent, key = _correction_parent(blob, path)
    assert parent[key] == value


def test_raise_dead_has_touch_range() -> None:
    """SRD 5.2.1 Raise Dead: "Range: Touch" ("With a touch, you revive a dead creature")."""
    assert _spell("raise-dead").range.units == "touch"


def test_hypnotic_pattern_and_resilient_sphere_need_concentration() -> None:
    """SRD 5.2.1: both read "Duration: Concentration, up to 1 minute"."""
    assert _spell("hypnotic-pattern").concentration
    assert _spell("resilient-sphere").concentration


def test_forbiddance_is_a_ritual() -> None:
    """SRD 5.2.1 Forbiddance: "Casting Time: 10 minutes or Ritual"."""
    assert _spell("forbiddance").ritual


def test_phantasmal_killer_forces_a_wisdom_save() -> None:
    """SRD 5.2.1 Phantasmal Killer: "The target makes a Wisdom saving throw." Foundry ships
    the save with no ability."""
    (activity,) = _spell("phantasmal-killer").activities
    assert activity.save.ability == ["wis"]  # type: ignore[union-attr]


def test_glyph_explosive_rune_is_a_20_foot_sphere() -> None:
    """SRD 5.2.1 Glyph of Warding: "the glyph erupts with magical energy in a
    20-foot-radius Sphere centered on the glyph"."""
    rune = next(a for a in _spell("glyph-of-warding").activities if a.name == "Explosive Rune")
    assert (rune.target.template.type, rune.target.template.size) == ("sphere", "20")


@pytest.mark.parametrize(
    ("slug", "units", "value"),
    [
        # "Duration: 24 hours"; Foundry: "@item.level * 48 - 72".
        ("animal-messenger", "hour", 24),
        # "Duration: Concentration, up to 1 hour"; Foundry: "1 + (@item.level - 8) * 7".
        ("dominate-monster", "hour", 1),
        # "Duration: 1 hour"; Foundry: "@item.level - 2".
        ("magic-circle", "hour", 1),
    ],
)
def test_formula_durations_read_at_the_spell_level(slug: str, units: str, value: int) -> None:
    duration = _spell(slug).duration
    assert (duration.units, duration.value) == (units, value)


def test_spare_the_dying_has_a_15_foot_range() -> None:
    """SRD 5.2.1 Spare the Dying: "Range: 15 feet"; Foundry: "15 * pow(2,
    @scaling.increase)", which the Cantrip Upgrade doubles."""
    spell = _spell("spare-the-dying")
    assert (spell.range.units, spell.range.value) == ("ft", 15)


@pytest.mark.parametrize(
    ("slug", "cost", "consumed"),
    [
        # "a diamond worth 300+ GP, which the spell consumes".
        ("revivify", 300, True),
        # "one 150+ GP black onyx stone for each corpse".
        ("create-undead", 150, False),
        # "a diamond worth 25,000+ GP, which the spell consumes".
        ("true-resurrection", 25000, True),
        # "gem dust worth 5,000+ GP, which the spell consumes"; Foundry: consumed false.
        ("sequester", 5000, True),
        # "a Holy Symbol worth 5+ GP".
        ("bless", 5, False),
        # No cost: "a bit of fleece".
        ("silent-image", 0, False),
    ],
)
def test_material_cost_and_consumption(slug: str, cost: int, consumed: bool) -> None:
    materials = _spell(slug).materials
    assert (materials.cost, materials.consumed) == (cost, consumed)


@pytest.mark.parametrize(
    ("raw", "level", "expected"),
    [
        ("30", 2, 30),
        ("", 2, None),
        (None, 2, None),
        ("@item.level * 48 - 72", 2, 24),
        ("1 + (@item.level - 8) * 7", 8, 1),
        ("15 * pow(2, @scaling.increase)", 0, 15),
        ("@item.level / 2", 3, None),
        ("@details.level", 3, None),
        ("floor(@item.level)", 3, None),
        ("1 +", 3, None),
    ],
)
def test_base_level_value(raw: object, level: int, expected: int | None) -> None:
    assert _base_level_value(raw, level) == expected


def test_materials_keep_an_upstream_cost() -> None:
    materials = _spell_materials({"value": "a pearl worth 100+ GP", "cost": "40"})
    assert materials.cost == 40
    assert _spell_materials({"value": "", "cost": "bad"}).cost == 0


def test_a_stale_correction_fails_the_regen() -> None:
    """A Foundry pin that fixes Forbiddance upstream makes the correction stale."""
    stale = _spell("forbiddance").model_copy(update={"ritual": True})
    with pytest.raises(ValueError, match="stale"):
        _apply_spell_srd_corrections(stale)


def test_correction_paths_name_existing_fields() -> None:
    with pytest.raises(ValueError, match="bad correction path"):
        _correction_parent({}, "Activities.x")
