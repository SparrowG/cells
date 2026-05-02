"""MCP transport for cells bots.

McpMind wraps a contestant's MCP server as a mind module. The server is
expected to expose an `act` tool that accepts {view, messages} and
returns the same wire format as HttpMind (#20):

  Single action:        {"type": int, "data": [...]}
  Pre-planned moves:    {"actions": [{"type": ..., "data": ...}, ...]}

The result can be carried in either the structuredContent field of the
CallToolResult (preferred) or as JSON text in a TextContent block.

Two transports are supported:
  - stdio:  pass server_command=["python", "bot_server.py"]
  - SSE:    pass server_url="http://bot.example.com/mcp/sse"

Errors (connection drop, malformed payload, server-reported error) are
swallowed: act() returns None and the engine falls back to last_action.
The DQ layer (#25) is responsible for counting strikes.

Resource limits (#45): stdio minds accept a `limits` dict with optional
keys memory_mb, cpu_seconds, and walltime_seconds. On POSIX, memory and
CPU caps are applied via a thin wrapper that calls resource.setrlimit
before exec-ing the real server. Walltime is enforced by an asyncio task
that tears down the session when it expires. All limits are no-ops on
Windows.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from typing import Any

from transports.http_mind import _parse_action, _parse_batch_response

import cells


def _apply_limits_to_command(command: list[str], limits: dict) -> list[str]:
    """On POSIX, prepend a Python one-liner that sets RLIMIT_AS / RLIMIT_CPU
    before exec-ing the real server command. Returns command unchanged on
    Windows or when neither cap is requested."""
    memory_mb = limits.get("memory_mb")
    cpu_seconds = limits.get("cpu_seconds")
    if sys.platform == "win32" or (memory_mb is None and cpu_seconds is None):
        return command
    stmts = ["import resource,os,sys"]
    if memory_mb is not None:
        b = int(memory_mb) * 1024 * 1024
        stmts.append(f"resource.setrlimit(resource.RLIMIT_AS,({b},{b}))")
    if cpu_seconds is not None:
        s = int(cpu_seconds)
        stmts.append(f"resource.setrlimit(resource.RLIMIT_CPU,({s},{s}))")
    stmts.append("os.execvp(sys.argv[1],sys.argv[1:])")
    return [sys.executable, "-c", ";".join(stmts)] + list(command)


def _parse_result(result) -> Any:
    """Parse an MCP CallToolResult into Action(s) or None."""
    if getattr(result, "isError", False):
        return None
    structured = getattr(result, "structuredContent", None)
    if structured:
        return _parse_action(structured)
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text is None:
            continue
        try:
            return _parse_action(json.loads(text))
        except json.JSONDecodeError:
            return None
    return None


def _parse_batch_result(result) -> dict:
    """Parse an MCP CallToolResult into {agent_id: Action | list[Action] | None}."""
    if getattr(result, "isError", False):
        return {}
    structured = getattr(result, "structuredContent", None)
    if structured:
        return _parse_batch_response(structured)
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text is None:
            continue
        try:
            return _parse_batch_response(json.loads(text))
        except json.JSONDecodeError:
            return {}
    return {}


class McpMind:
    """A mind backed by an MCP server. Acts as a mind module from the
    engine's perspective: exposes `name` and `AgentMind`.

    All agents on the same team share a single MCP session.
    """

    def __init__(
        self,
        name: str,
        *,
        server_command: list[str] | None = None,
        server_url: str | None = None,
        session=None,
        limits: dict | None = None,
    ):
        if (server_command is None) == (server_url is None) and session is None:
            raise ValueError(
                "McpMind needs exactly one of server_command, server_url, or session"
            )
        self.name = name
        self._server_command = server_command
        self._server_url = server_url
        self._session = session
        self._stack: AsyncExitStack | None = None
        self._limits: dict = limits or {}
        self._walltime_task: asyncio.Task | None = None
        self._walltime_started = False

        outer = self

        class _AgentMind:
            def __init__(self, cargs):
                self._mcp = outer

            async def act(self, view, msg):
                return await outer._call(view, msg)

        self.AgentMind = _AgentMind

    async def _ensure_session(self):
        if self._session is None:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client

            self._stack = AsyncExitStack()
            if self._server_command is not None:
                effective_command = _apply_limits_to_command(
                    self._server_command, self._limits
                )
                params = StdioServerParameters(
                    command=effective_command[0],
                    args=list(effective_command[1:]),
                )
                read, write = await self._stack.enter_async_context(stdio_client(params))
            else:
                from mcp.client.sse import sse_client

                read, write = await self._stack.enter_async_context(
                    sse_client(self._server_url)
                )

            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()

        if not self._walltime_started:
            self._walltime_started = True
            walltime = self._limits.get("walltime_seconds")
            if walltime is not None:
                self._walltime_task = asyncio.get_running_loop().create_task(
                    self._enforce_walltime(walltime)
                )

    async def _enforce_walltime(self, seconds: float):
        await asyncio.sleep(seconds)
        await self.aclose()

    async def _call(self, view, msg):
        try:
            await self._ensure_session()
            result = await self._session.call_tool(
                "act",
                arguments={"view": view.to_json(), "messages": list(msg)},
            )
            return _parse_result(result)
        except Exception:
            return None

    async def act_batch(self, agents, msg):
        """Per-team batch endpoint (#23). Calls the contestant's `act_batch`
        MCP tool with the team's full agent list."""
        if not agents:
            return {}
        arguments = {
            "tick": int(agents[0][1].tick),
            "messages": list(msg),
            "agents": [{"id": aid, "view": v.to_json()} for (aid, v) in agents],
        }
        try:
            await self._ensure_session()
            result = await self._session.call_tool("act_batch", arguments=arguments)
        except Exception:
            return {}
        return _parse_batch_result(result)

    async def aclose(self):
        task = self._walltime_task
        self._walltime_task = None
        if task is not None and not task.done() and asyncio.current_task() is not task:
            task.cancel()
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self._session = None
