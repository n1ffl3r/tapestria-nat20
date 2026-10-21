"""Reactions a host answers: the prompt at a trigger, the policy that answers
it, and the record of each answer.

SRD 5.2.1 "Reaction": "A Reaction is a special action taken in response to a
trigger defined in the Reaction's description. You can take a Reaction on
another creature's turn". When a resolution reaches a reaction's trigger and a
creature is eligible to take it, the engine asks the host's ``ReactionPolicy``
with a ``ReactionPrompt``. The policy answers ``"use"``, ``"pass"`` or
``"suspend"``. A suspended call returns a ``CombatSuspension``
(``combat_state.py``) instead of finishing, and ``resume`` continues it with the
player's decision.

Every answer the call took is kept, in order, as a ``ReactionChoice``. A resume
replays the suspended call from its starting state and seed, answering the
earlier prompts from those choices, so the replay reaches the same trigger with
the same dice; the resume's own seed draws every die after its decision.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "PolicyAnswer",
    "ReactionChoice",
    "ReactionDecision",
    "ReactionKey",
    "ReactionPolicy",
    "ReactionPrompt",
    "ReactionSuspended",
    "default_reaction_policy",
]

#: The reactions the engine offers at their SRD triggers, by SRD key: four
#: reaction spells and the Opportunity Attack every creature has.
ReactionKey = Literal[
    "shield", "counterspell", "hellish-rebuke", "feather-fall", "opportunity-attack"
]

#: A player's decision on one prompt.
ReactionDecision = Literal["use", "pass"]

#: What a ``ReactionPolicy`` may answer: a decision now, or pause the call.
PolicyAnswer = Literal["use", "pass", "suspend"]


class ReactionPrompt(BaseModel):
    """One eligible creature at one reaction's trigger.

    ``readied`` is true when the creature armed this reaction with a ``ready``
    intent (the 0.7.0 path), which the default policy treats as a standing
    ``"use"``. The attack fields describe the triggering attack roll, when
    there is one: its total, its natural d20, and whether it is a Critical
    Hit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reactor_id: str
    reaction_key: ReactionKey
    trigger: str
    triggering_actor_id: str
    readied: bool = False
    attack_total: int | None = None
    attack_natural: int | None = None
    critical: bool = False


#: The host's answer to each prompt. It must be deterministic: the same prompt
#: in the same state gets the same answer.
ReactionPolicy = Callable[[ReactionPrompt], PolicyAnswer]


def default_reaction_policy(prompt: ReactionPrompt) -> PolicyAnswer:
    """The 0.7.0 behaviour: an Opportunity Attack, which needed no arming, and
    a reaction the creature readied are used; any other is passed. Never
    suspends."""
    return "use" if prompt.readied or prompt.reaction_key == "opportunity-attack" else "pass"


class ReactionChoice(BaseModel):
    """The answer one prompt got. ``seed`` is set on a choice a ``resume``
    made: every die after it is drawn from that seed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reactor_id: str
    reaction_key: ReactionKey
    decision: ReactionDecision
    seed: int | None = None


class ReactionSuspended(Exception):  # noqa: N818 - a control-flow signal, not an error
    """Raised at a trigger the policy suspended, unwinding the call. The
    stateless calls catch it and return a ``CombatSuspension``; the combat it
    unwound is discarded, so nothing it did is kept."""

    def __init__(self, prompt: ReactionPrompt) -> None:
        super().__init__(f"{prompt.reactor_id} may take {prompt.reaction_key}: {prompt.trigger}")
        self.prompt = prompt
