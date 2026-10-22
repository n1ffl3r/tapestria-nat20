"""The class audit's corrections (spec 02a, acceptance criterion 4).

The pinned Foundry pack raises the Barbarian's Rages one level early and gives the Monk a
Focus Point at level 1. The translator fixes both with ``_CLASS_SRD_CORRECTIONS``, whose
entries quote the SRD 5.2.1 Features table. These tests pin the corrected corpus;
``audit/srd_5_2_1_ledger.json`` records every change.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dnd5e_srd_data import BundledAssetLoader
from dnd5e_srd_data.schema.class_ import Class
from tools.srd_audit import classes
from tools.translators.foundry import (
    _CLASS_SRD_CORRECTIONS,
    _apply_class_srd_corrections,
    _correction_parent,
    srd_rules_disabled,
    translate_class_yaml,
)

LOADER = BundledAssetLoader()
ROOT = Path(__file__).resolve().parents[1]
FOUNDRY = ROOT / "raw_sources" / "foundry" / "packs" / "_source" / "classes24"


def _scale(slug: str, title: str) -> dict[str, object]:
    record = LOADER.get_class(slug)
    assert record is not None
    blob = record.model_dump(mode="json")
    entry = next(a for a in blob["advancement"] if a["title"] == title)
    return classes._corpus_scale(entry["configuration"])


@pytest.mark.parametrize(
    ("slug", "path", "value"),
    [
        (slug, c.path, c.value)
        for slug, corrections in sorted(_CLASS_SRD_CORRECTIONS.items())
        for c in corrections
    ],
)
def test_every_correction_is_in_the_corpus(slug: str, path: str, value: object) -> None:
    record = LOADER.get_class(slug)
    assert record is not None
    blob = record.model_dump(mode="json")
    parent, key = _correction_parent(blob, path)
    assert parent[key] == value


def test_barbarian_rages_rise_at_level_17() -> None:
    """SRD 5.2.1 Barbarian Features: level 16 has 5 Rages, level 17 has 6."""
    rages = _scale("barbarian", "Rages")
    assert (rages["15"], rages["16"], rages["17"]) == (5, 5, 6)


def test_a_monk_has_no_focus_points_at_level_1() -> None:
    """SRD 5.2.1 Monk Features: Focus Points begin at level 2 with Monk's Focus."""
    focus = _scale("monk", "Focus Points")
    assert (focus["1"], focus["2"], focus["20"]) == (None, 2, 20)


def test_a_stale_correction_fails_the_regen() -> None:
    """A Foundry pin that fixes the Rages upstream makes the correction stale."""
    barbarian = LOADER.get_class("barbarian")
    assert isinstance(barbarian, Class)
    with pytest.raises(ValueError, match="stale"):
        _apply_class_srd_corrections(barbarian)


def test_without_the_srd_rules_the_pinned_pack_shows() -> None:
    """``srd_rules_disabled`` gives the class as Foundry has it, which the ledger diffs."""
    path = next(FOUNDRY.glob("barbarian*/_class.yml"), None) or next(
        FOUNDRY.rglob("barbarian.yml"), None
    )
    if path is None:
        pytest.skip("the raw Foundry sources are not checked out (make refresh-upstream)")
    with srd_rules_disabled():
        raw = translate_class_yaml(path, ingest_date=date(2000, 1, 1), ingest_version="t")
    entry = next(a for a in raw.model_dump(mode="json")["advancement"] if a["title"] == "Rages")
    assert classes._corpus_scale(entry["configuration"])["16"] == 6
