"""Tests for the disqualify-on-N-strikes layer (#25).

A misbehaving bot (timeout, exception, malformed) accrues strikes; once
the threshold is hit the team is marked disqualified and its agents
NOOP for the rest of the game. The tournament keeps running.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import asyncio

import pytest

import cells


def _module(name, mind_cls):
    class M:
        pass

    m = M()
    m.AgentMind = mind_cls
    m.name = name
    return (name, m)


class EatMind:
    def __init__(self, cargs):
        pass

    def act(self, view, msg):
        return cells.Action(cells.ACT_EAT)


async def test_timeout_strike_increments(monkeypatch):
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    class HangMind:
        def __init__(self, cargs):
            pass

        async def act(self, view, msg):
            await asyncio.sleep(2.0)
            return cells.Action(cells.ACT_EAT)

    g = cells.Game(
        20,
        [_module("hang", HangMind), _module("eat", EatMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )
    await g.tick()
    assert g.strikes[0] >= 1
    assert g.strike_log[0][2] == "soft_timeout"
    await g._cancel_all_pending()


async def test_disqualified_after_threshold(monkeypatch):
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    class RaiseMind:
        def __init__(self, cargs):
            pass

        async def act(self, view, msg):
            raise RuntimeError("boom")

    g = cells.Game(
        20,
        [_module("raise", RaiseMind), _module("eat", EatMind)],
        symmetric=True,
        max_time=50,
        headless=True,
        strike_threshold=3,
    )

    for _ in range(5):
        if 0 in g.disqualified:
            break
        await g.tick()

    assert 0 in g.disqualified
    assert g.strikes[0] >= 3
    # DISQUALIFIED entry recorded.
    reasons = [entry[2] for entry in g.strike_log]
    assert "DISQUALIFIED" in reasons
    await g._cancel_all_pending()


async def test_disqualified_team_agents_noop(monkeypatch):
    """Once DQ'd, the agent's act is no longer called even if it would hang."""
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    class CountingHangMind:
        def __init__(self, cargs):
            self.calls = 0

        async def act(self, view, msg):
            self.calls += 1
            await asyncio.sleep(2.0)
            return cells.Action(cells.ACT_EAT)

    g = cells.Game(
        20,
        [_module("hang", CountingHangMind), _module("eat", EatMind)],
        symmetric=True,
        max_time=50,
        headless=True,
        strike_threshold=2,
    )

    # Tick until DQ.
    for _ in range(5):
        if 0 in g.disqualified:
            break
        await g.tick()
    assert 0 in g.disqualified

    team_zero = [a for a in g.agent_population if a.team == 0][0]
    calls_at_dq = team_zero.mind.calls

    # Subsequent ticks should not invoke act.
    for _ in range(3):
        await g.tick()
    assert team_zero.mind.calls == calls_at_dq
    await g._cancel_all_pending()


async def test_recovering_mind_under_generous_threshold(monkeypatch):
    """A mind that times out a few times but eventually succeeds should not
    be DQ'd when the threshold is generous. (With threshold=3 and a 0.05s
    soft timeout, even a brief sleep accumulates strikes — that's the
    expected aggressive behavior, not a bug. Real games use 5s soft
    and threshold 3.)"""
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    class EventuallyFastMind:
        def __init__(self, cargs):
            self.calls = 0

        async def act(self, view, msg):
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.1)  # may produce 1-2 strikes
            return cells.Action(cells.ACT_EAT)

    g = cells.Game(
        20,
        [_module("flaky", EventuallyFastMind), _module("eat", EatMind)],
        symmetric=True,
        max_time=20,
        headless=True,
        strike_threshold=20,
    )

    for _ in range(8):
        await g.tick()

    assert 0 not in g.disqualified
    await g._cancel_all_pending()


async def test_strike_log_records_tick_and_reason(monkeypatch):
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    class NoneMind:
        """A mind that returns None — counts as 'malformed'."""

        def __init__(self, cargs):
            pass

        def act(self, view, msg):
            return None

    g = cells.Game(
        20,
        [_module("none", NoneMind), _module("eat", EatMind)],
        symmetric=True,
        max_time=20,
        headless=True,
        strike_threshold=2,
    )
    await g.tick()
    assert any(entry[2] == "malformed" for entry in g.strike_log)
    # tick is the engine's self.time at the moment of the strike (0 on
    # the first tick before the increment).
    assert all(isinstance(entry[0], int) for entry in g.strike_log)
