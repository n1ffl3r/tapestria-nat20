"""Class spell list schema: the spells a class can prepare, by level.

Foundry ships no class spell lists, so these come from the official SRD 5.2.1 PDF itself:
each spell's heading names the classes whose list it is on ("Level 3 Evocation (Sorcerer,
Wizard)"), recorded in ``srd/index.json``. The class chapters print the same lists as
tables; ``tests/test_spell_lists.py`` checks the two against each other.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, NonNegativeInt


class SpellListEntry(BaseModel, frozen=True):
    """One spell on a class's list: the spell's corpus slug and its level (0 = cantrip)."""

    slug: str
    level: NonNegativeInt


class SpellListSource(BaseModel, frozen=True):
    """Where a spell list comes from: the pinned SRD PDF, by path and SHA-256."""

    document: Literal["SRD 5.2.1"] = "SRD 5.2.1"
    path: str
    sha256: str
    license_tag: Literal["CC-BY-4.0"] = "CC-BY-4.0"


class SpellList(BaseModel):
    """One class's spell list. ``slug`` is the class's slug (``canonical/classes/``);
    ``spells`` is sorted by level, then slug."""

    slug: str
    class_name: str
    spells: list[SpellListEntry] = Field(default_factory=list)
    source: SpellListSource

    entry_kind: Literal["spell_list"] = "spell_list"

    def at_level(self, level: int) -> list[str]:
        """The slugs of the list's spells of ``level``."""
        return [entry.slug for entry in self.spells if entry.level == level]
