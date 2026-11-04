"""Every corpus record is tagged SRD 5.2.1 (spec 02a, acceptance criterion 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CANONICAL = Path(__file__).resolve().parents[1] / "src" / "dnd5e_srd_data" / "canonical"
RECORDS = sorted(CANONICAL.glob("*/*.json"))


def test_the_corpus_has_every_record() -> None:
    """Spells 339, monsters 344, items 546, features 262, monster traits 102, classes 12,
    subclasses 12, species 14, backgrounds 4, feats 17, conditions 15, spell lists 8."""
    assert len(RECORDS) == 1675


@pytest.mark.parametrize("path", RECORDS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_a_record_is_tagged_srd_5_2_1(path: Path) -> None:
    """A record's ``provenance.srd_version`` is exactly 5.2.1; a class spell list, which has
    no provenance block, names the SRD 5.2.1 document it comes from."""
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("entry_kind") == "spell_list":
        assert record["source"]["document"] == "SRD 5.2.1"
    else:
        assert record["provenance"]["srd_version"] == ["5.2.1"]
        assert record["provenance"]["license_tag"] == "CC-BY-4.0"
