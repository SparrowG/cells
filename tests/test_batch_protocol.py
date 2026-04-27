"""Tests for the per-team batch protocol (#23).

The engine groups a tick's per-agent dispatch by team. If a team's mind
module exposes `act_batch`, all agents on that team are sent in a
single call per tick; otherwise the engine falls back to the existing
per-agent path. Pre-planned moves and DQ semantics still apply.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import asyncio

import pytest

import cells


def _module(name, mind_cls=None, *, batch=None):
    """Build a mind module shim. If `batch` is set it'll be exposed as
    the module's act_batch attribute, triggering the batch path."""

    class M:
        pass

    m = M()
    m.AgentMind = mind_cls if mind_cls is not None else _NoopMind
    m.name = name
    if batch is not None:
        m.act_batch = batch
    return (name, m)


class _NoopMind:
    def __init__(self, cargs):
        pass

    def act(self, view, msg):
        return cells.Action(cells.ACT_EAT)


def _spawn_extra_agents(game, team, count):
    """Hand-add `count` agents to `team` at sparse positions. The map must
    be lockable (post-init) — `add_agent` writes pixels."""
    positions = [(15, 5 + i * 3) for i in range(count)]
    game.agent_map.lock()
    try:
        for (x, y) in positions:
            a = cells.Agent(x, y, cells.STARTING_ENERGY, team, game.minds[team], None)
            game.add_agent(a)
    finally:
        game.agent_map.unlock()


async def test_batch_called_once_per_team_per_tick():
    """A team with N agents triggers exactly one act_batch call per tick,
    receives N entries in the request, and every agent's action lands."""
    captured = {"calls": 0, "agents_per_call": []}

    async def batch_fn(agents, msg):
        captured["calls"] += 1
        captured["agents_per_call"].append(len(agents))
        return {aid: cells.Action(cells.ACT_LIFT) for (aid, _) in agents}

    g = cells.Game(
        20,
        [_module("batch", batch=batch_fn), _module("noop", _NoopMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )
    _spawn_extra_agents(g, 0, 3)  # team 0 now has 4 agents

    await g.tick()
    assert captured["calls"] == 1
    assert captured["agents_per_call"] == [4]
    for a in g.agent_population:
        if a.team == 0:
            assert a.last_action.type == cells.ACT_LIFT
    await g._cancel_all_pending()


async def test_batch_request_carries_stable_agent_ids():
    """Agent.id is stable across ticks for the lifetime of the agent."""
    seen_ids = []

    async def batch_fn(agents, msg):
        seen_ids.append([aid for (aid, _) in agents])
        return {aid: cells.Action(cells.ACT_EAT) for (aid, _) in agents}

    g = cells.Game(
        20,
        [_module("batch", batch=batch_fn), _module("noop", _NoopMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )

    await g.tick()
    await g.tick()
    assert seen_ids[0] == seen_ids[1]  # same agent, same id, two ticks
    await g._cancel_all_pending()


async def test_mixed_teams_batch_and_per_agent():
    """One team uses act_batch, the other uses per-agent. Both work in the
    same game on the same tick."""
    batch_calls = {"n": 0}
    per_agent_calls = {"n": 0}

    async def batch_fn(agents, msg):
        batch_calls["n"] += 1
        return {aid: cells.Action(cells.ACT_LIFT) for (aid, _) in agents}

    class CountingMind:
        def __init__(self, cargs):
            pass

        def act(self, view, msg):
            per_agent_calls["n"] += 1
            return cells.Action(cells.ACT_DROP)

    g = cells.Game(
        20,
        [_module("batch", batch=batch_fn), _module("per", CountingMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )

    await g.tick()
    assert batch_calls["n"] == 1
    assert per_agent_calls["n"] == 1  # one agent on team 1
    team0 = [a for a in g.agent_population if a.team == 0][0]
    team1 = [a for a in g.agent_population if a.team == 1][0]
    assert team0.last_action.type == cells.ACT_LIFT
    assert team1.last_action.type == cells.ACT_DROP
    await g._cancel_all_pending()


async def test_missing_agent_in_batch_response_strikes_and_falls_back():
    """If the batch response omits an agent, that agent gets a strike and
    falls back to its last_action."""
    state = {"missing_id": None}

    async def batch_fn(agents, msg):
        if state["missing_id"] is None:
            # Tick 1: seed last_action for everyone, mark the first agent
            # as the one we'll drop on the next tick.
            state["missing_id"] = agents[0][0]
            return {aid: cells.Action(cells.ACT_LIFT) for (aid, _) in agents}
        return {
            aid: cells.Action(cells.ACT_DROP)
            for (aid, _) in agents
            if aid != state["missing_id"]
        }

    g = cells.Game(
        20,
        [_module("batch", batch=batch_fn), _module("noop", _NoopMind)],
        symmetric=True,
        max_time=20,
        headless=True,
        strike_threshold=10,
    )
    _spawn_extra_agents(g, 0, 2)  # team 0 has 3 agents

    await g.tick()
    strikes_before = g.strikes[0]

    await g.tick()
    assert g.strikes[0] == strikes_before + 1

    missing = next(a for a in g.agent_population if a.id == state["missing_id"])
    assert missing.last_action.type == cells.ACT_LIFT
    others = [
        a for a in g.agent_population
        if a.team == 0 and a.id != state["missing_id"]
    ]
    for a in others:
        assert a.last_action.type == cells.ACT_DROP
    await g._cancel_all_pending()


async def test_batch_pre_planned_moves_consumed_from_queue():
    """A batch entry that's a list of Actions is consumed one-per-tick from
    the agent's action_queue. Subsequent ticks don't re-call act_batch
    until the queue drains."""
    call_count = {"n": 0}

    async def batch_fn(agents, msg):
        call_count["n"] += 1
        # Return a 3-action plan for every agent on the first call.
        return {
            aid: [
                cells.Action(cells.ACT_LIFT),
                cells.Action(cells.ACT_DROP),
                cells.Action(cells.ACT_EAT),
            ]
            for (aid, _) in agents
        }

    g = cells.Game(
        20,
        [_module("batch", batch=batch_fn), _module("noop", _NoopMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )

    team0 = [a for a in g.agent_population if a.team == 0][0]

    await g.tick()
    assert call_count["n"] == 1
    assert team0.last_action.type == cells.ACT_LIFT

    await g.tick()
    # Queue still has actions, no new batch call.
    assert call_count["n"] == 1
    assert team0.last_action.type == cells.ACT_DROP

    await g.tick()
    assert call_count["n"] == 1
    assert team0.last_action.type == cells.ACT_EAT

    # Queue drained -> next tick triggers a new call.
    await g.tick()
    assert call_count["n"] == 2
    await g._cancel_all_pending()


async def test_batch_timeout_strikes_every_pending_agent(monkeypatch):
    """If the batch call exceeds the soft timeout, each pending agent on
    that team gets a strike and falls back to last_action."""
    monkeypatch.setattr(cells, "SOFT_TIMEOUT_SECONDS", 0.05)

    async def slow_batch(agents, msg):
        await asyncio.sleep(2.0)
        return {aid: cells.Action(cells.ACT_EAT) for (aid, _) in agents}

    g = cells.Game(
        20,
        [_module("slow", batch=slow_batch), _module("noop", _NoopMind)],
        symmetric=True,
        max_time=20,
        headless=True,
        strike_threshold=10,
    )
    _spawn_extra_agents(g, 0, 2)  # team 0 has 3 agents

    await g.tick()
    assert g.strikes[0] == 3
    # All strikes recorded as soft_timeout.
    soft = [e for e in g.strike_log if e[2] == "soft_timeout"]
    assert len(soft) == 3
    await g._cancel_all_pending()


async def test_batch_exception_strikes_every_pending_agent():
    """If act_batch raises, each pending agent gets an 'exception' strike."""
    async def boom_batch(agents, msg):
        raise RuntimeError("boom")

    g = cells.Game(
        20,
        [_module("boom", batch=boom_batch), _module("noop", _NoopMind)],
        symmetric=True,
        max_time=20,
        headless=True,
        strike_threshold=10,
    )
    _spawn_extra_agents(g, 0, 1)  # team 0 has 2 agents

    await g.tick()
    assert g.strikes[0] == 2
    reasons = [e[2] for e in g.strike_log]
    assert reasons.count("exception") == 2
    await g._cancel_all_pending()


async def test_disqualified_team_skips_batch_call():
    """A DQ'd team's act_batch isn't called — agents NOOP unconditionally."""
    call_count = {"n": 0}

    async def batch_fn(agents, msg):
        call_count["n"] += 1
        return {aid: cells.Action(cells.ACT_LIFT) for (aid, _) in agents}

    g = cells.Game(
        20,
        [_module("batch", batch=batch_fn), _module("noop", _NoopMind)],
        symmetric=True,
        max_time=20,
        headless=True,
    )
    g.disqualified.add(0)

    await g.tick()
    assert call_count["n"] == 0
    team0 = [a for a in g.agent_population if a.team == 0][0]
    # DQ path returns ACT_EAT but doesn't update last_action (existing
    # convention — see _act_for_agent).
    assert team0.last_action is None
    await g._cancel_all_pending()


async def test_malformed_batch_response_strikes_with_malformed_reason():
    """A non-dict response strikes every pending agent as 'malformed'."""
    async def bogus_batch(agents, msg):
        return "not a dict"

    g = cells.Game(
        20,
        [_module("bogus", batch=bogus_batch), _module("noop", _NoopMind)],
        symmetric=True,
        max_time=20,
        headless=True,
        strike_threshold=10,
    )

    await g.tick()
    assert g.strikes[0] == 1
    assert g.strike_log[0][2] == "malformed"
    await g._cancel_all_pending()
