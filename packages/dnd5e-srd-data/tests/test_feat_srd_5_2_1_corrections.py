"""The origins audit's feat corrections (spec 02a, acceptance criterion 4).

The pinned Foundry pack gives two Epic Boon feats no prerequisite and leaves a third without
its "Spellcasting Feature". The translator fixes them with ``_FEAT_SRD_CORRECTIONS``, whose
entries quote the SRD 5.2.1 feat header.
"""

from __future__ import annotations

import pytest

from dnd5e_srd_data import BundledAssetLoader
from tools.translators.foundry import (
    _FEAT_SRD_CORRECTIONS,
    _apply_feat_srd_corrections,
    _correction_parent,
)

LOADER = BundledAssetLoader()


@pytest.mark.parametrize(
    ("slug", "path", "value"),
    [
        (slug, c.path, c.value)
        for slug, corrections in sorted(_FEAT_SRD_CORRECTIONS.items())
        for c in corrections
    ],
)
def test_every_correction_is_in_the_corpus(slug: str, path: str, value: object) -> None:
    feat = LOADER.get_feat(slug)
    assert feat is not None
    parent, key = _correction_parent(feat.model_dump(mode="json"), path)
    assert parent[key] == value


@pytest.mark.parametrize("slug", ["boon-of-fate", "boon-of-irresistible-offense"])
def test_an_epic_boon_needs_level_19(slug: str) -> None:
    """SRD 5.2.1: "Epic Boon Feat (Prerequisite: Level 19+)"."""
    feat = LOADER.get_feat(slug)
    assert feat is not None
    assert [p.level for p in feat.prerequisites] == [19]


def test_boon_of_spell_recall_needs_a_spellcasting_feature() -> None:
    """SRD 5.2.1: "Epic Boon Feat (Prerequisite: Level 19+, Spellcasting Feature)"."""
    feat = LOADER.get_feat("boon-of-spell-recall")
    assert feat is not None
    assert [(p.level, p.requirement) for p in feat.prerequisites] == [(19, "Spellcasting Feature")]


def test_a_stale_correction_fails_the_regen() -> None:
    feat = LOADER.get_feat("boon-of-fate")
    assert feat is not None
    with pytest.raises(ValueError, match="stale"):
        _apply_feat_srd_corrections(feat)
