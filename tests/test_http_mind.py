"""Tests for the HTTP transport adapter (#20).

Uses httpx.MockTransport to stand in for a contestant's bot server, so
no actual sockets are opened. Covers:
- Single-action response decoded into an Action.
- Pre-planned-moves response queues a list of Actions.
- Malformed responses fall back to None (engine -> last_action).
- HTTP errors fall back to None.
- A full game can be played with one HttpMind opponent.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import json

import httpx
import pytest

import cells
from transports.http_mind import HttpMind


def _make_mind(handler, name="bot"):
    transport = httpx.MockTransport(handler)
    return HttpMind(name, "http://test.invalid/act", transport=transport)


class _StubAgent:
    def __init__(self, x=5, y=5, team=0, energy=20, loaded=False):
        self.x, self.y, self.team, self.energy, self.loaded = x, y, team, energy, loaded

    def get_pos(self):
        return (self.x, self.y)

    def get_team(self):
        return self.team


def _make_view():
    terr = cells.ScalarMapLayer((10, 10))
    energy = cells.ScalarMapLayer((10, 10))
    me = _StubAgent()
    return cells.WorldView(me, [], [], terr, energy, tick=1)


async def test_single_action_response():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"type": 2})

    mind = _make_mind(handler)
    agent = mind.AgentMind(None)
    action = await agent.act(_make_view(), [])
    assert isinstance(action, cells.Action)
    assert action.type == cells.ACT_EAT
    # Verify the request body has the snapshot schema.
    assert "view" in captured["body"]
    assert "messages" in captured["body"]
    assert captured["body"]["view"]["me"]["pos"] == [5, 5]
    await mind.aclose()


async def test_action_with_data_field():
    def handler(request):
        return httpx.Response(200, json={"type": 1, "data": [3, 4]})

    mind = _make_mind(handler)
    agent = mind.AgentMind(None)
    action = await agent.act(_make_view(), [])
    assert action.type == cells.ACT_MOVE
    assert action.get_data() == [3, 4]
    await mind.aclose()


async def test_pre_planned_moves_response():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "actions": [
                    {"type": 1, "data": [3, 4]},
                    {"type": 2},
                ]
            },
        )

    mind = _make_mind(handler)
    agent = mind.AgentMind(None)
    actions = await agent.act(_make_view(), [])
    assert isinstance(actions, list)
    assert len(actions) == 2
    assert actions[0].type == cells.ACT_MOVE
    assert actions[0].get_data() == [3, 4]
    assert actions[1].type == cells.ACT_EAT
    await mind.aclose()


async def test_malformed_response_returns_none():
    """Non-JSON or missing 'type' falls back to None."""
    def handler(request):
        return httpx.Response(200, text="not json at all")

    mind = _make_mind(handler)
    agent = mind.AgentMind(None)
    result = await agent.act(_make_view(), [])
    assert result is None
    await mind.aclose()


async def test_5xx_response_returns_none():
    def handler(request):
        return httpx.Response(500, text="upstream error")

    mind = _make_mind(handler)
    agent = mind.AgentMind(None)
    result = await agent.act(_make_view(), [])
    assert result is None
    await mind.aclose()


async def test_response_missing_type_returns_none():
    def handler(request):
        return httpx.Response(200, json={"foo": "bar"})

    mind = _make_mind(handler)
    agent = mind.AgentMind(None)
    result = await agent.act(_make_view(), [])
    assert result is None
    await mind.aclose()


async def test_http_mind_plays_a_full_game():
    """End-to-end: an HttpMind opponent runs through the engine without
    errors. The remote bot just eats every tick."""
    def handler(request):
        return httpx.Response(200, json={"type": cells.ACT_EAT})

    http_bot = _make_mind(handler, name="http_bot")

    # Build a sync mind module for the other team.
    class _Local:
        class AgentMind:
            def __init__(self, cargs):
                pass

            def act(self, view, msg):
                return cells.Action(cells.ACT_EAT)

    local = _Local()
    local.name = "local"

    g = cells.Game(
        20,
        [(http_bot.name, http_bot), (local.name, local)],
        symmetric=True,
        max_time=5,
        headless=True,
    )
    while g.winner is None:
        await g.tick()
    assert g.winner is not None
    await http_bot.aclose()
