"""The item audit's corrections (spec 02a, acceptance criterion 4).

The pinned Foundry pack weighs ammunition by the piece and a pack by its empty container,
files some magic items as mundane gear, and gets a few rows wrong outright. The translator
fixes each with ``_ITEM_SRD_CORRECTIONS``, whose entries quote the item's SRD 5.2.1 table
row, heading, or kind line. These tests pin the corrected corpus;
``audit/srd_5_2_1_ledger.json`` records every change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dnd5e_srd_data import BundledAssetLoader
from dnd5e_srd_data.schema.item import Item, MagicItem, Weapon
from tools.translators.foundry import (
    _ITEM_SRD_CORRECTIONS,
    _apply_item_srd_corrections,
    _correction_parent,
    srd_rules_disabled,
    translate_generic_item_yaml,
)

LOADER = BundledAssetLoader()
ROOT = Path(__file__).resolve().parents[1]
FOUNDRY = ROOT / "raw_sources" / "foundry" / "packs" / "_source" / "equipment24"


def _item(slug: str) -> Item:
    item = LOADER.get_item(slug)
    assert item is not None, slug
    return item


@pytest.mark.parametrize(
    ("slug", "path", "value"),
    [
        (slug, correction.path, correction.value)
        for slug, corrections in sorted(_ITEM_SRD_CORRECTIONS.items())
        for correction in corrections
    ],
)
def test_every_correction_is_in_the_corpus(slug: str, path: str, value: object) -> None:
    blob = _item(slug).model_dump(mode="json")
    parent, key = _correction_parent(blob, path)
    assert parent[key] == value


def test_ammunition_weighs_its_bundle() -> None:
    """SRD 5.2.1 Ammunition table: "Arrows 20 Quiver 1 lb. 1 GP"."""
    arrows = _item("arrows")
    assert (arrows.weight, arrows.cost_gp) == (1.0, 1.0)


def test_a_pack_weighs_its_contents() -> None:
    """Adventuring Gear table: "Explorer's Pack 55 lb. 10 GP"."""
    assert _item("explorers-pack").weight == 55.0


def test_trident_and_war_pick_are_versatile() -> None:
    """Martial Melee Weapons: "Trident 1d8 Piercing Thrown (Range 20/60), Versatile (1d10)"
    and "War Pick 1d8 Piercing Versatile (1d10) Sap"."""
    for slug in ("trident", "war-pick"):
        weapon = _item(slug)
        assert isinstance(weapon, Weapon)
        assert weapon.versatile_damage is not None
        assert weapon.versatile_damage.dice == "1d10"


def test_a_potion_is_a_magic_item() -> None:
    """Potion of Climbing: "Potion, Common"."""
    assert isinstance(_item("potion-of-climbing"), MagicItem)


def test_handy_haversack_is_rare() -> None:
    """Handy Haversack: "Wondrous Item, Rare"."""
    haversack = _item("handy-haversack")
    assert isinstance(haversack, MagicItem)
    assert haversack.rarity == "rare"


def test_dragon_orb_has_its_charges() -> None:
    """Dragon Orb: "The orb has 7 charges and regains 1d4 + 3 expended charges daily at
    dawn." Its spells spend the item's charges, which Foundry leaves without a pool."""
    uses = _item("dragon-orb").uses
    assert uses is not None
    assert (uses.max, uses.recovery[0].formula, uses.recovery[0].period) == (
        "7",
        "1d4 + 3",
        "dawn",
    )


def test_a_varying_cost_is_no_price() -> None:
    """Adventuring Gear table: "Arcane Focus Varies Varies"."""
    assert _item("arcane-focus-varies").cost_gp is None


def test_a_stale_correction_fails_the_regen() -> None:
    """A Foundry pin that fixes the Arrows' weight upstream makes the correction stale."""
    with pytest.raises(ValueError, match="stale"):
        _apply_item_srd_corrections(_item("arrows"))


def test_without_the_srd_rules_the_pinned_pack_shows() -> None:
    """``srd_rules_disabled`` gives the record as Foundry has it, which the ledger diffs."""
    path = FOUNDRY / "consumables" / "potions" / "potion-of-climbing.yml"
    if not path.is_file():
        pytest.skip("the raw Foundry sources are not checked out (make refresh-upstream)")
    from datetime import date

    with srd_rules_disabled():
        raw = translate_generic_item_yaml(path, ingest_date=date(2000, 1, 1), ingest_version="t")
    assert raw.item_kind == "item"
    corrected = translate_generic_item_yaml(path, ingest_date=date(2000, 1, 1), ingest_version="t")
    assert corrected.item_kind == "magic_item"
