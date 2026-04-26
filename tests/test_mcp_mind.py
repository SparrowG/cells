"""Tests for the MCP transport adapter (#21).

McpMind has the same wire format as HttpMind — only the transport
differs. Tests inject a fake MCP session via the `session=` kwarg so we
don't need to spin up a stdio subprocess or an SSE server.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import json

import pytest
from mcp.types import CallToolResult, TextContent

import cells
from transports.mcp_mind import McpMind


class FakeSession:
    def __init__(self, *, structured=None, text=None, is_error=False, raises=None):
        self.calls = []
        self._structured = structured
        self._text = text
        self._is_error = is_error
        self._raises = raises

    async def call_tool(self, name, arguments=None, **kwargs):
        self.calls.append((name, arguments))
        if self._raises is not None:
            raise self._raises
        content = []
        if self._text is not None:
            content.append(TextContent(type="text", text=self._text))
        return CallToolResult(
            content=content,
            structuredContent=self._structured,
            isError=self._is_error,
        )


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
    return cells.WorldView(_StubAgent(), [], [], terr, energy, tick=1)


async def test_structured_content_action():
    session = FakeSession(structured={"type": 2})
    mind = McpMind("bot", session=session)
    agent = mind.AgentMind(None)
    action = await agent.act(_make_view(), [])
    assert action.type == cells.ACT_EAT
    # Verify the call_tool args carry the snapshot.
    assert session.calls[0][0] == "act"
    assert session.calls[0][1]["view"]["me"]["pos"] == [5, 5]
    assert session.calls[0][1]["messages"] == []


async def test_text_content_action():
    session = FakeSession(text=json.dumps({"type": 1, "data": [3, 4]}))
    mind = McpMind("bot", session=session)
    agent = mind.AgentMind(None)
    action = await agent.act(_make_view(), [])
    assert action.type == cells.ACT_MOVE
    assert action.get_data() == [3, 4]


async def test_pre_planned_moves_via_structured():
    session = FakeSession(
        structured={
            "actions": [
                {"type": 1, "data": [3, 4]},
                {"type": 2},
            ]
        }
    )
    mind = McpMind("bot", session=session)
    agent = mind.AgentMind(None)
    actions = await agent.act(_make_view(), [])
    assert len(actions) == 2
    assert actions[0].type == cells.ACT_MOVE
    assert actions[1].type == cells.ACT_EAT


async def test_is_error_returns_none():
    session = FakeSession(structured={"type": 2}, is_error=True)
    mind = McpMind("bot", session=session)
    agent = mind.AgentMind(None)
    result = await agent.act(_make_view(), [])
    assert result is None


async def test_call_tool_raises_returns_none():
    session = FakeSession(raises=RuntimeError("connection dropped"))
    mind = McpMind("bot", session=session)
    agent = mind.AgentMind(None)
    result = await agent.act(_make_view(), [])
    assert result is None


async def test_text_content_malformed_returns_none():
    session = FakeSession(text="not json at all")
    mind = McpMind("bot", session=session)
    agent = mind.AgentMind(None)
    result = await agent.act(_make_view(), [])
    assert result is None


async def test_mcp_mind_plays_a_full_game():
    session = FakeSession(structured={"type": cells.ACT_EAT})
    mcp_bot = McpMind("mcp_bot", session=session)

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
        [(mcp_bot.name, mcp_bot), (local.name, local)],
        symmetric=True,
        max_time=5,
        headless=True,
    )
    while g.winner is None:
        await g.tick()
    assert g.winner is not None


def test_constructor_requires_one_transport():
    with pytest.raises(ValueError):
        McpMind("bot")
    with pytest.raises(ValueError):
        McpMind(
            "bot",
            server_command=["python", "x.py"],
            server_url="http://example/sse",
        )
