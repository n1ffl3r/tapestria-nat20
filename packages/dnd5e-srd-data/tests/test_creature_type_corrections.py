"""SRD 5.2.1 creature-type corrections for pinned Foundry data (spec 02a, AC 3).

Foundry's werewolf carries ``type.value: (lycanthrope)``, which is not a creature type, so the
translator raised and the stat block was dropped. The SRD's type line is "Medium or Small
Monstrosity (Lycanthrope), Chaotic Evil".
"""

import pytest

from dnd5e_srd_data import CreatureType
from tools.translators.foundry import _creature_type


def test_werewolf_is_corrected_to_monstrosity() -> None:
    assert _creature_type("werewolf", "(lycanthrope)") is CreatureType.MONSTROSITY


def test_a_valid_upstream_type_wins_over_the_correction() -> None:
    """Once a Foundry pin ships a real type, the correction goes inert."""
    assert _creature_type("werewolf", "humanoid") is CreatureType.HUMANOID


def test_an_invalid_type_without_a_correction_still_raises() -> None:
    with pytest.raises(ValueError, match="custom"):
        _creature_type("otherworldly-steed", "custom")
