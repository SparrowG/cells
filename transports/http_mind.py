"""HTTP transport for cells bots.

HttpMind wraps an HTTP endpoint as a mind module — the engine sees a
mind_list entry shaped like (name, module_with_AgentMind), and each
tick the mind's act() POSTs the WorldView snapshot to the contestant's
URL and parses the action(s) from the response.

Wire format (request):
    POST <url>
    {
      "view": <WorldView.to_json() output>,
      "messages": ["string", ...]
    }

Wire format (response — single action):
    {"type": <int>, "data": [...]}    # data is optional

Wire format (response — pre-planned moves):
    {"actions": [{"type": <int>, "data": [...]}, ...]}

A bot that times out, returns malformed JSON, or returns 5xx is treated
the same way as a local mind that raised: the engine falls back to
last_action and the strike will be counted by the DQ layer (#25).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

import cells


def _parse_single(d: Any):
    if not isinstance(d, dict):
        return None
    action_type = d.get("type")
    if action_type is None:
        return None
    data = d.get("data")
    return cells.Action(int(action_type), data)


def _parse_action(payload: Any):
    """Decode an HTTP response body into an Action or list[Action].

    Returns None if the payload is malformed; the caller treats None the
    same as a mind exception (fall back to last_action)."""
    if payload is None:
        return None
    if isinstance(payload, dict):
        if "actions" in payload:
            return [a for a in (_parse_single(x) for x in payload["actions"]) if a is not None]
        return _parse_single(payload)
    return None


class HttpMind:
    """A mind backed by an HTTP endpoint. Acts as a mind module from the
    engine's perspective: exposes `name` and `AgentMind`.

    All agents on the same team share a single HTTP client.
    """

    def __init__(
        self,
        name: str,
        url: str,
        *,
        headers: dict | None = None,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.name = name
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

        outer = self

        class _AgentMind:
            def __init__(self, cargs):
                self._http = outer

            async def act(self, view, msg):
                return await outer._call(view, msg)

        self.AgentMind = _AgentMind

    async def _call(self, view, msg):
        payload = {
            "view": view.to_json(),
            "messages": list(msg),
        }
        try:
            r = await self._client.post(self._url, json=payload, headers=self._headers)
            r.raise_for_status()
            return _parse_action(r.json())
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return None

    async def aclose(self):
        await self._client.aclose()
