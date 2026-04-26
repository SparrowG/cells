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
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

from transports.http_mind import _parse_action

import cells


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

        outer = self

        class _AgentMind:
            def __init__(self, cargs):
                self._mcp = outer

            async def act(self, view, msg):
                return await outer._call(view, msg)

        self.AgentMind = _AgentMind

    async def _ensure_session(self):
        if self._session is not None:
            return
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        self._stack = AsyncExitStack()
        if self._server_command is not None:
            params = StdioServerParameters(
                command=self._server_command[0],
                args=list(self._server_command[1:]),
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

    async def aclose(self):
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self._session = None
