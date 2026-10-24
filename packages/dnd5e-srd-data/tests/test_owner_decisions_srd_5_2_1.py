"""The owner's decisions on the audit's divergence issues (spec 02a, criterion 4).

n1ffl3r/tapestria-nat20#16: author the Monk's Self-Restoration. #18: use the SRD's text for
three shared monster traits. #7: the Couatl's Divine Aid and the Gnoll Warrior's Rampage,
which the pinned Foundry pack does not ship, are authored from the stat block.
"""

from __future__ import annotations

import pytest

from dnd5e_srd_data import BundledAssetLoader
from tools.srd_audit import ledger
from tools.translators.authored import AUTHORED_FEATURES
from tools.translators.foundry import (
    _MONSTER_SRD_ADDITIONS,
    _TRAIT_SRD_CORRECTIONS,
    _apply_monster_srd_corrections,
    _corrected_trait_description,
)

LOADER = BundledAssetLoader()
LEDGER = ledger.load_json(ledger.LEDGER_PATH)
TRAITS = ledger.canonical_records("monster_traits")


def test_the_monk_has_self_restoration_at_level_10() -> None:
    """SRD 5.2.1 Monk Features: "10 +4 Heightened Focus, Self-Restoration"."""
    monk = LOADER.get_class("monk")
    assert monk is not None
    assert [(g.level, g.slug) for g in monk.granted_features if g.slug == "self-restoration"] == [
        (10, "self-restoration")
    ]
    feature = LOADER.get_feature("self-restoration")
    assert feature is not None
    assert (feature.name, feature.feature_type, feature.source_slug) == (
        "Self-Restoration",
        "class_feature",
        "monk",
    )
    assert feature.provenance.source == "srd_pdf"
    assert feature.provenance.source_url.endswith("#page=52")


@pytest.mark.parametrize("authored", AUTHORED_FEATURES, ids=lambda a: a.slug)
def test_an_authored_feature_is_the_srd_text(authored) -> None:  # type: ignore[no-untyped-def]
    pdf = ledger.pdf_text.srd_index.pinned_pdf()[0]
    if not pdf.is_file():
        pytest.skip("the PDF ships in the repository, not in the sdist")
    from tools.srd_audit import classes

    text = classes.class_texts()[authored.source_slug.capitalize()].text
    for paragraph in authored.description.split("\n\n"):
        assert ledger.contains(text, paragraph), authored.slug


def test_the_ledger_has_the_authored_feature() -> None:
    entry = LEDGER["records"]["class_features"]["self-restoration"]
    assert entry["status"] == "verified"
    assert entry["compared"] == {"levels": [10]}
    assert "monk" not in LEDGER["records"]["classes"] or (
        LEDGER["records"]["classes"]["monk"]["status"] != "divergence"
    )


@pytest.mark.parametrize(
    ("slug", "absent"),
    [
        ("standing-leap", "20 feet"),
        ("stench", "Success:"),
        ("water-susceptibility", "Foundry Note"),
    ],
)
def test_a_trait_has_the_srd_text(slug: str, absent: str) -> None:
    description = TRAITS[slug]["description"]
    assert description == _TRAIT_SRD_CORRECTIONS[slug].value
    assert absent not in description


def test_standing_leap_is_the_frogs() -> None:
    """SRD 5.2.1 Frog: "Long Jump is up to 10 feet and its High Jump is up to 5 feet"."""
    description = TRAITS["standing-leap"]["description"]
    assert "10 feet" in description and "5 feet" in description


def test_a_stale_trait_correction_fails_the_regen() -> None:
    with pytest.raises(ValueError, match="stale"):
        _corrected_trait_description("standing-leap", "Foundry fixed this upstream.")


@pytest.mark.parametrize(
    ("monster", "action", "uses"), [("couatl", "divine-aid", 2), ("gnoll-warrior", "rampage", 1)]
)
def test_an_authored_bonus_action_is_on_its_stat_block(
    monster: str, action: str, uses: int
) -> None:
    record = LOADER.get_monster(monster)
    assert record is not None
    found = next(a for a in record.actions if a.slug == action)
    assert (found.kind.value, found.uses_per_day) == ("bonus_action", uses)
    assert found.description == _MONSTER_SRD_ADDITIONS[monster][0].action["description"]


def test_an_addition_already_shipped_fails_the_regen() -> None:
    couatl = LOADER.get_monster("couatl")
    assert couatl is not None
    with pytest.raises(ValueError, match="already shipped"):
        _apply_monster_srd_corrections(couatl)
