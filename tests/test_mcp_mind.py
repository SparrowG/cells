"""Tests for the MCP transport adapter (#21).

McpMind has the same wire format as HttpMind — only the transport
differs. Tests inject a fake MCP session via the `session=` kwarg so we
don't need to spin up a stdio subprocess or an SSE server.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import asyncio
import json
import sys

import pytest
from mcp.types import CallToolResult, TextContent

import cells
from transports.mcp_mind import McpMind, _apply_limits_to_command


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


async def test_act_batch_round_trip():
    """McpMind.act_batch calls the `act_batch` tool and parses the
    structuredContent response into per-agent actions."""
    session = FakeSession(
        structured={
            "actions": [
                {"id": "agent-0", "action": {"type": cells.ACT_LIFT}},
                {"id": "agent-1", "action": {"actions": [{"type": cells.ACT_DROP}]}},
            ]
        }
    )
    mind = McpMind("bot", session=session)
    out = await mind.act_batch(
        [("agent-0", _make_view()), ("agent-1", _make_view())], ["hello"]
    )
    assert session.calls[0][0] == "act_batch"
    args = session.calls[0][1]
    assert args["tick"] == 1
    assert args["messages"] == ["hello"]
    assert [a["id"] for a in args["agents"]] == ["agent-0", "agent-1"]
    assert out["agent-0"].type == cells.ACT_LIFT
    assert isinstance(out["agent-1"], list)
    assert out["agent-1"][0].type == cells.ACT_DROP


async def test_act_batch_text_content():
    payload = {"actions": [{"id": "agent-0", "action": {"type": cells.ACT_EAT}}]}
    session = FakeSession(text=json.dumps(payload))
    mind = McpMind("bot", session=session)
    out = await mind.act_batch([("agent-0", _make_view())], [])
    assert out["agent-0"].type == cells.ACT_EAT


async def test_act_batch_call_raises_returns_empty():
    session = FakeSession(raises=RuntimeError("boom"))
    mind = McpMind("bot", session=session)
    out = await mind.act_batch([("agent-0", _make_view())], [])
    assert out == {}


async def test_act_batch_is_error_returns_empty():
    session = FakeSession(structured={"actions": []}, is_error=True)
    mind = McpMind("bot", session=session)
    out = await mind.act_batch([("agent-0", _make_view())], [])
    assert out == {}


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


# ---------------------------------------------------------------------------
# Resource caps (#45)


def test_apply_limits_no_limits_returns_command_unchanged():
    cmd = ["python", "bot.py", "--arg"]
    assert _apply_limits_to_command(cmd, {}) == cmd
    assert _apply_limits_to_command(cmd, {"walltime_seconds": 600}) == cmd


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_apply_limits_memory_wraps_command():
    cmd = ["python", "bot.py", "--arg"]
    result = _apply_limits_to_command(cmd, {"memory_mb": 256})
    assert result[0] == sys.executable
    assert result[1] == "-c"
    assert "RLIMIT_AS" in result[2]
    assert str(256 * 1024 * 1024) in result[2]
    assert result[3:] == cmd


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_apply_limits_cpu_wraps_command():
    cmd = ["python", "bot.py"]
    result = _apply_limits_to_command(cmd, {"cpu_seconds": 60})
    assert result[0] == sys.executable
    assert result[1] == "-c"
    assert "RLIMIT_CPU" in result[2]
    assert "60" in result[2]
    assert result[3:] == cmd


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_apply_limits_both_caps_include_both_rlimits():
    result = _apply_limits_to_command(
        ["python", "bot.py"], {"memory_mb": 128, "cpu_seconds": 30}
    )
    assert "RLIMIT_AS" in result[2]
    assert "RLIMIT_CPU" in result[2]
    assert str(128 * 1024 * 1024) in result[2]
    assert "30" in result[2]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_apply_limits_wrapper_ends_with_execvp():
    result = _apply_limits_to_command(["python", "bot.py"], {"cpu_seconds": 5})
    assert "execvp" in result[2]


async def test_no_limits_no_walltime_task():
    """When no limits are configured, no walltime task is created."""
    session = FakeSession(structured={"type": cells.ACT_EAT})
    mind = McpMind("bot", session=session)
    agent = mind.AgentMind(None)
    await agent.act(_make_view(), [])
    assert mind._walltime_task is None


async def test_walltime_task_started_on_first_act():
    """A walltime task is created on the first act() call."""
    session = FakeSession(structured={"type": cells.ACT_EAT})
    mind = McpMind("bot", session=session, limits={"walltime_seconds": 60.0})
    agent = mind.AgentMind(None)
    assert mind._walltime_task is None
    await agent.act(_make_view(), [])
    assert mind._walltime_task is not None
    assert not mind._walltime_task.done()
    # Clean up so the task doesn't outlive the test.
    await mind.aclose()
    await asyncio.sleep(0)


async def test_walltime_closes_session_after_expiry():
    """After walltime_seconds elapses, the session is torn down and
    subsequent act() calls return None (counted as strikes by the DQ layer)."""
    session = FakeSession(structured={"type": cells.ACT_EAT})
    mind = McpMind("bot", session=session, limits={"walltime_seconds": 0.05})
    agent = mind.AgentMind(None)

    result = await agent.act(_make_view(), [])
    assert result is not None

    await asyncio.sleep(0.15)

    result = await agent.act(_make_view(), [])
    assert result is None


async def test_aclose_cancels_pending_walltime_task():
    """Calling aclose() before walltime fires cancels the pending task."""
    session = FakeSession(structured={"type": cells.ACT_EAT})
    mind = McpMind("bot", session=session, limits={"walltime_seconds": 60.0})
    agent = mind.AgentMind(None)

    await agent.act(_make_view(), [])
    task = mind._walltime_task
    assert task is not None

    await mind.aclose()
    assert mind._walltime_task is None

    await asyncio.sleep(0)
    assert task.cancelled()


async def test_walltime_only_config_does_not_wrap_command():
    """walltime_seconds alone must not alter the server command — it only
    affects the asyncio side, not the subprocess invocation."""
    cmd = ["python", "bot.py"]
    result = _apply_limits_to_command(cmd, {"walltime_seconds": 600})
    assert result == cmd
