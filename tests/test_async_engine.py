"""Tests for the asyncio engine (#18) — slow-mind fallback, pre-planned moves,
sync mind compatibility, and cancellation."""

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
    """Sync mind that just eats — verifies the sync path still works."""

    def __init__(self, cargs):
        pass

    def act(self, view, msg):
        return cells.Action(cells.ACT_EAT)


async def test_sync_mind_compatibility():
    """A sync `def act` mind runs unchanged under the async engine."""
    g = cells.Game(
        20,
        [_module("a", EatMind), _module("b", EatMind)],
        symmetric=True,
        max_time=5,
        headless=True,
    )
    while g.winner is None:
        await g.tick()
    assert g.winner is not None  # game terminated normally


async def test_slow_mind_falls_back_to_last_action(monkeypatch):
    """If a mind's act doesn't return within the soft timeout, the agent
    advances using last_action and the next tick still works."""
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    class FirstFastThenSlow:
        def __init__(self, cargs):
            self.calls = 0

        async def act(self, view, msg):
            self.calls += 1
            if self.calls == 1:
                return cells.Action(cells.ACT_LIFT)
            await asyncio.sleep(2.0)  # exceeds soft timeout
            return cells.Action(cells.ACT_EAT)

    g = cells.Game(
        20,
        [_module("a", FirstFastThenSlow), _module("b", EatMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )

    await g.tick()
    team_zero = [a for a in g.agent_population if a.team == 0][0]
    assert team_zero.last_action.type == cells.ACT_LIFT

    # Tick 2: act spawns, hangs, times out, fallback to last_action.
    # last_action is unchanged because the fallback path doesn't update it.
    await g.tick()
    assert team_zero.last_action.type == cells.ACT_LIFT

    await g._cancel_all_pending()


async def test_pre_planned_moves_consumed_in_order(monkeypatch):
    """A mind returning a list of Actions has them consumed one-per-tick
    without re-calling act."""
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    class BatchMind:
        def __init__(self, cargs):
            self.calls = 0

        async def act(self, view, msg):
            self.calls += 1
            if self.calls == 1:
                return [
                    cells.Action(cells.ACT_LIFT),
                    cells.Action(cells.ACT_DROP),
                    cells.Action(cells.ACT_EAT),
                ]
            await asyncio.sleep(2.0)
            return cells.Action(cells.ACT_EAT)

    g = cells.Game(
        20,
        [_module("a", BatchMind), _module("b", EatMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )

    team_zero = [a for a in g.agent_population if a.team == 0][0]

    await g.tick()
    assert team_zero.last_action.type == cells.ACT_LIFT

    await g.tick()
    assert team_zero.last_action.type == cells.ACT_DROP

    await g.tick()
    assert team_zero.last_action.type == cells.ACT_EAT

    # Across all 3 ticks the mind was called exactly once.
    assert team_zero.mind.calls == 1

    await g._cancel_all_pending()


async def test_pending_task_cancelled_on_game_end(monkeypatch):
    """When the game ends, any in-flight pending_task is cancelled and the
    underlying coroutine sees CancelledError."""
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    cancelled = asyncio.Event()

    class HangMind:
        def __init__(self, cargs):
            pass

        async def act(self, view, msg):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return cells.Action(cells.ACT_EAT)

    g = cells.Game(
        20,
        [_module("a", HangMind), _module("b", EatMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )

    # Tick once to spawn the HangMind's act task; it times out and stays
    # pending across the await.
    await g.tick()
    team_zero = [a for a in g.agent_population if a.team == 0][0]
    assert team_zero.pending_task is not None

    # End-of-game cleanup cancels the task and observes CancelledError.
    await g._cancel_all_pending()
    assert cancelled.is_set()
    for a in g.agent_population:
        assert a.pending_task is None
