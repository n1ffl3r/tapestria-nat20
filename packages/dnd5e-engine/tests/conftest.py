"""Test isolation shared by the whole engine suite."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from dnd5e_engine import orchestrator
from dnd5e_engine.combat_state import _live_from_state, _state_from_live, _turn_tail_start
from dnd5e_engine.events import TurnStarted
from dnd5e_engine.lib_loader import set_lib_loader_for_tests

#: ``NAT20_ROUNDTRIP_STATE=1`` (``make test-roundtrip``) runs the whole suite
#: with every handle call first rebuilding its combat from the combat's own
#: ``CombatState``, through JSON. A test that passes normally but fails this way
#: names state ``CombatState`` drops (spec 02a, acceptance criterion 7).
ROUNDTRIP_STATE = os.environ.get("NAT20_ROUNDTRIP_STATE") == "1"

#: With ``NAT20_ROUNDTRIP_STATE=1``, ``NAT20_STATE_TRACE=<dir>`` also writes every
#: state the round trip passes through, one file per test. Two runs under
#: different ``PYTHONHASHSEED`` values must write identical files
#: (``make test-hashseed``): a difference is output that depends on set order.
STATE_TRACE = os.environ.get("NAT20_STATE_TRACE")

#: ``_LiveCombat`` fields a rebuild keeps from the original rather than from the
#: state: the dice stream and the event plumbing, which ``CombatState`` leaves
#: out by design (``combat_state.py``), and the hook registry, which a test may
#: have extended.
_KEPT_ACROSS_ROUNDTRIP = ("rng", "event_queue", "event_listeners", "lifecycle")


@pytest.fixture(autouse=True)
def _roundtrip_state(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if not ROUNDTRIP_STATE:
        return
    trace = None
    if STATE_TRACE:
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.nodeid)
        trace = Path(STATE_TRACE) / f"{name}.jsonl"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text("")
    get_live = orchestrator._get_live
    current_turn_events = orchestrator._current_turn_events
    # Per combat, the index where the log ``CombatState`` keeps began at the
    # start of the call in progress.
    kept_from: dict[int, int] = {}

    def rebuilt(handle: orchestrator.CombatHandle) -> orchestrator._LiveCombat:
        live = get_live(handle)
        if live.grid_scene is None:  # built by hand in a unit test
            return live
        tail = _turn_tail_start(live.event_log)
        state = _state_from_live(live)
        if trace is not None:
            with trace.open("a") as out:
                out.write(state.model_dump_json() + "\n")
        fresh = _live_from_state(state, live.rng)
        for name in _KEPT_ACROSS_ROUNDTRIP:
            setattr(fresh, name, getattr(live, name))
        if any(callable(value) for value in vars(live.topology).values()):
            # A test replaced a topology method (a line-of-sight stub). The
            # grid itself is state; the stub is the test's.
            fresh.topology = live.topology
        # The state keeps the log from the turn in progress on. Tests read the
        # whole log, so the older events stay in front of the rebuilt tail, and
        # ``checked_turn_events`` proves the engine never reads them.
        fresh.event_log = live.event_log[:tail] + fresh.event_log
        fresh.legendary_resistance_applied_event_indices = {
            i + tail for i in fresh.legendary_resistance_applied_event_indices
        }
        # In place, so a test holding the ``_LiveCombat`` sees the rebuild.
        live.__dict__.update(fresh.__dict__)
        kept_from[id(live)] = tail
        return live

    def checked_turn_events(
        live: orchestrator._LiveCombat, actor_id: str
    ) -> list[orchestrator.CombatEvent]:
        started = [
            i
            for i, e in enumerate(live.event_log)
            if isinstance(e, TurnStarted) and e.actor_id == actor_id
        ]
        assert not started or started[-1] >= kept_from.get(id(live), 0), (
            "the turn's end read an event CombatState does not keep"
        )
        return current_turn_events(live, actor_id)

    monkeypatch.setattr(orchestrator, "_get_live", rebuilt)
    monkeypatch.setattr(orchestrator, "_current_turn_events", checked_turn_events)


@pytest.fixture(autouse=True)
def _bundled_lib_loader() -> Iterator[None]:
    """Start and end every test on the bundled corpus.

    Many tests install a ``MemoryAssetLoader`` through the process-wide loader
    seam and leave it installed. The reset lives at the root of the test
    package because, given ``tests/e2e`` files, then a ``tests`` file, then
    ``tests/e2e`` files again on one command line, pytest collects
    ``tests/e2e`` twice, and only the first collection carries that
    directory's ``conftest.py`` fixtures.
    """
    set_lib_loader_for_tests(None)
    yield
    set_lib_loader_for_tests(None)
