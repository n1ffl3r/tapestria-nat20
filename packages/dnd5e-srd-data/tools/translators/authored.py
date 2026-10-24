"""Records the corpus authors from the SRD 5.2.1 PDF, because the pinned Foundry pack has no
document for them.

Each record's text is the SRD's, verbatim, and ``tools/srd_audit/ledger.py`` compares the
record with the PDF like any other (its level, from the class's "Level N: Feature"
heading). The owner approved authoring each one on the issue it names. A record here is
granted where Foundry's advancement already grants it: ``uuid`` is the Foundry grant target
that regen otherwise skips (``tools/regen.py::_KNOWN_UNRESOLVABLE_GRANTS``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dnd5e_srd_data.schema.common import Provenance, ReviewState
from dnd5e_srd_data.schema.feature import Feature, FeatureType

PDF_URL = "srd/SRD_CC_v5.2.1.pdf"


@dataclass(frozen=True)
class AuthoredFeature:
    slug: str
    name: str
    description: str
    feature_type: FeatureType
    source_slug: str
    uuid: str
    page: int
    issue: str


AUTHORED_FEATURES: tuple[AuthoredFeature, ...] = (
    AuthoredFeature(
        slug="self-restoration",
        name="Self-Restoration",
        description=(
            "Through sheer force of will, you can remove one of the following conditions from"
            " yourself at the end of each of your turns: Charmed, Frightened, or Poisoned.\n\n"
            "In addition, forgoing food and drink doesn't give you levels of Exhaustion."
        ),
        feature_type="class_feature",
        source_slug="monk",
        uuid="Compendium.dnd5e.classes24.Item.phbmnkSelfrestor",
        page=52,
        issue="https://github.com/n1ffl3r/tapestria-nat20/issues/16",
    ),
)


def translate_authored_feature(
    authored: AuthoredFeature, *, ingest_date: date, ingest_version: str
) -> Feature:
    """The corpus record for an authored feature, its provenance the PDF page."""
    return Feature(
        slug=authored.slug,
        name=authored.name,
        description=authored.description,
        feature_type=authored.feature_type,
        foundry_id=authored.uuid.rsplit(".", 1)[1],
        source_slug=authored.source_slug,
        provenance=Provenance(
            source="srd_pdf",
            source_url=f"{PDF_URL}#page={authored.page}",
            ingest_date=ingest_date,
            ingest_version=ingest_version,
            srd_version=frozenset({"5.2.1"}),
        ),
        review=ReviewState(),
    )
