"""Dice rolling primitives.

Every roller takes an optional ``rng``. Pass a seeded ``random.Random`` for
reproducible results; omit it and the roll is drawn from ``UNSEEDED``, the
operating system's random source, which is never reproducible. Nothing here
touches the process-global ``random`` module, so no caller's seeding of it, or
use of it, can change an engine roll (spec 02a, acceptance criterion 8).

In-combat resolution never reaches this module — it draws from the
``random.Random`` threaded from ``start_combat(rng_seed=...)`` via
``dnd5e_engine.activities.dice``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

_Rng = random.Random

#: The generator a roll without an ``rng`` draws from: the operating system's
#: random source, never the process-global ``random`` module.
UNSEEDED: random.Random = random.SystemRandom()


def draw_from(rng: random.Random | None) -> random.Random:
    """``rng``, or ``UNSEEDED`` when the caller passed none."""
    return UNSEEDED if rng is None else rng


@dataclass(frozen=True)
class RollResult:
    dice: list[int]
    modifier: int
    total: int

    @property
    def raw(self) -> int:
        return sum(self.dice)


def roll(sides: int, count: int = 1, modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    """Roll `count` d`sides` dice plus a modifier."""
    if sides < 1:
        raise ValueError(f"Dice must have at least 1 side, got {sides}")
    if count < 1:
        raise ValueError(f"Must roll at least 1 die, got {count}")
    draw = draw_from(rng).randint
    dice = [draw(1, sides) for _ in range(count)]
    return RollResult(dice=dice, modifier=modifier, total=sum(dice) + modifier)


def roll_d4(count: int = 1, modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    return roll(4, count, modifier, rng=rng)


def roll_d6(count: int = 1, modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    return roll(6, count, modifier, rng=rng)


def roll_d8(count: int = 1, modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    return roll(8, count, modifier, rng=rng)


def roll_d10(count: int = 1, modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    return roll(10, count, modifier, rng=rng)


def roll_d12(count: int = 1, modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    return roll(12, count, modifier, rng=rng)


def roll_d20(modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    return roll(20, 1, modifier, rng=rng)


def roll_d100(*, rng: _Rng | None = None) -> RollResult:
    return roll(100, 1, 0, rng=rng)


def roll_with_advantage(modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    """Roll 2d20, take the highest."""
    draw = draw_from(rng).randint
    a, b = draw(1, 20), draw(1, 20)
    return RollResult(dice=[a, b], modifier=modifier, total=max(a, b) + modifier)


def roll_with_disadvantage(modifier: int = 0, *, rng: _Rng | None = None) -> RollResult:
    """Roll 2d20, take the lowest."""
    draw = draw_from(rng).randint
    a, b = draw(1, 20), draw(1, 20)
    return RollResult(dice=[a, b], modifier=modifier, total=min(a, b) + modifier)


def parse_dice_expression(expr: str) -> RollResult:
    """
    Parse and roll a dice expression like "2d6+3", "1d8-1", "d20", "4d6".
    """
    expr = expr.strip().lower().replace(" ", "")
    modifier = 0

    # Extract modifier
    if "+" in expr:
        parts = expr.split("+", 1)
        expr = parts[0]
        modifier = int(parts[1])
    elif "-" in expr[1:]:  # avoid negative first char
        idx = expr.rindex("-")
        modifier = -int(expr[idx + 1 :])
        expr = expr[:idx]

    if "d" not in expr:
        raise ValueError(f"Invalid dice expression: {expr!r}")

    count_str, sides_str = expr.split("d", 1)
    count = int(count_str) if count_str else 1
    sides = int(sides_str)
    return roll(sides, count, modifier)


def ability_modifier(score: int) -> int:
    """D&D 5e ability score modifier: (score - 10) // 2."""
    return (score - 10) // 2


def proficiency_bonus(level: int) -> int:
    """D&D 5e character proficiency bonus by level: +2 at 1-4, +3 at 5-8, etc."""
    return 2 + (level - 1) // 4


def drop_lowest(rolls: list[int], drop: int = 1) -> list[int]:
    """Return rolls with the lowest `drop` values removed (for 4d6 drop lowest)."""
    return sorted(rolls, reverse=True)[: len(rolls) - drop]


def roll_4d6_drop_lowest(*, rng: _Rng | None = None) -> int:
    """Standard D&D stat generation: roll 4d6, drop the lowest."""
    draw = draw_from(rng).randint
    rolls = [draw(1, 6) for _ in range(4)]
    return sum(drop_lowest(rolls, 1))


def generate_ability_scores_4d6(*, rng: _Rng | None = None) -> dict[str, int]:
    """Generate a full set of ability scores using 4d6 drop lowest."""
    stats = [roll_4d6_drop_lowest(rng=rng) for _ in range(6)]
    keys = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
    return dict(zip(keys, stats, strict=True))


STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

POINT_BUY_COSTS = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_TOTAL = 27


def validate_point_buy(scores: dict[str, int]) -> tuple[bool, str]:
    """Validate that a point-buy array is legal. Returns (valid, message)."""
    for stat, val in scores.items():
        if val not in POINT_BUY_COSTS:
            return False, f"{stat}={val} is not a valid point-buy value (8-15)"
    total = sum(POINT_BUY_COSTS[v] for v in scores.values())
    if total > POINT_BUY_TOTAL:
        return False, f"Total cost {total} exceeds {POINT_BUY_TOTAL}"
    return True, "OK"


__all__ = [
    "POINT_BUY_COSTS",
    "POINT_BUY_TOTAL",
    "STANDARD_ARRAY",
    "RollResult",
    "ability_modifier",
    "drop_lowest",
    "generate_ability_scores_4d6",
    "parse_dice_expression",
    "proficiency_bonus",
    "roll",
    "roll_4d6_drop_lowest",
    "roll_d4",
    "roll_d6",
    "roll_d8",
    "roll_d10",
    "roll_d12",
    "roll_d20",
    "roll_d100",
    "roll_with_advantage",
    "roll_with_disadvantage",
    "validate_point_buy",
]
