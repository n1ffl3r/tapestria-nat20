"""SRD license gate: the official SRD 5.2.1 PDF decides what is SRD content.

A record is SRD content when ``srd/index.json`` (built from the pinned PDF by
``tools/srd_index.py``) lists its name in the index categories for its corpus
category, or when ``srd/aliases.json`` ties it to a listed record as a variant
(an Ioun Stone type) or a helper (the Folding Boat's activation). Anything else
is quarantined, whatever the Foundry, open5e, or 5e-bits sources say.

Names match after lowercasing and dropping everything but letters and digits;
an index name written "Lantern, Bullseye" also matches "Bullseye Lantern".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "srd" / "index.json"
ALIASES_PATH = ROOT / "srd" / "aliases.json"

NOT_IN_SRD = "not_in_srd_5_2_1"

# Corpus category (the canonical/ subdirectory) -> the index categories that may list it.
INDEX_CATEGORIES: dict[str, tuple[str, ...]] = {
    "items": ("magic_items", "equipment", "traps"),
    "monsters": ("stat_blocks",),
    "spells": ("spells",),
    "species": ("species",),
    "backgrounds": ("backgrounds",),
    "feats": ("feats",),
    "classes": ("classes",),
    "subclasses": ("subclasses",),
    "features": ("class_features", "class_feature_options", "species_traits"),
    "conditions": ("conditions",),
}


@dataclass(frozen=True)
class GateDecision:
    is_srd: bool
    quarantine_reason: str | None


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def name_keys(name: str) -> set[str]:
    """A name's match keys: itself, and for "Lantern, Bullseye" also "Bullseye Lantern"."""
    keys = {name_key(name)}
    if ", " in name:
        head, tail = name.split(", ", 1)
        keys.add(name_key(f"{tail} {head}"))
    return keys


@dataclass(frozen=True)
class SrdIndex:
    """The PDF index's names by index category, and the aliases by corpus category."""

    names: dict[str, frozenset[str]]
    aliases: dict[str, dict[str, tuple[str, str]]]

    @classmethod
    def from_documents(cls, index: dict[str, object], aliases: dict[str, object]) -> SrdIndex:
        categories = index["categories"]
        assert isinstance(categories, dict)
        names = {
            category: frozenset(key for entry in entries for key in name_keys(str(entry["name"])))
            for category, entries in categories.items()
        }
        by_corpus: dict[str, dict[str, tuple[str, str]]] = {}
        alias_list = aliases["aliases"]
        assert isinstance(alias_list, list)
        for alias in alias_list:
            by_corpus.setdefault(alias["corpus_category"], {})[name_key(alias["name"])] = (
                alias["of"],
                alias["of_category"],
            )
        return cls(names=names, aliases=by_corpus)

    def lists(self, name: str, categories: tuple[str, ...]) -> bool:
        keys = name_keys(name)
        return any(keys & self.names.get(category, frozenset()) for category in categories)


@cache
def load_index() -> SrdIndex:
    return SrdIndex.from_documents(
        json.loads(INDEX_PATH.read_text(encoding="utf-8")),
        json.loads(ALIASES_PATH.read_text(encoding="utf-8")),
    )


def gate_decision(name: str, corpus_category: str, index: SrdIndex | None = None) -> GateDecision:
    """Admit ``name`` into ``corpus_category`` when the PDF index lists it, or an alias ties it
    to a record the index lists."""
    index = index if index is not None else load_index()
    if corpus_category not in INDEX_CATEGORIES:
        raise ValueError(f"no SRD index categories for corpus category {corpus_category!r}")
    if index.lists(name, INDEX_CATEGORIES[corpus_category]):
        return GateDecision(is_srd=True, quarantine_reason=None)
    alias = index.aliases.get(corpus_category, {}).get(name_key(name))
    if alias is not None:
        parent, parent_category = alias
        # A "text" parent is a heading the index tool found in the PDF; it has no category.
        if parent_category == "text" or index.lists(parent, (parent_category,)):
            return GateDecision(is_srd=True, quarantine_reason=None)
    return GateDecision(is_srd=False, quarantine_reason=NOT_IN_SRD)
