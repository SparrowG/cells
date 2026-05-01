"""HTTP transport for cells bots.

HttpMind wraps an HTTP endpoint as a mind module — the engine sees a
mind_list entry shaped like (name, module_with_AgentMind), and each
tick the mind's act() POSTs the WorldView snapshot to the contestant's
URL and parses the action(s) from the response.

Wire format (request):
    POST <url>
    X-Cells-Timestamp: <unix_seconds>
    X-Cells-Signature: HMAC(secret, "<ts>.<body_bytes>")  # when hmac_secret set
    {
      "view": <WorldView.to_json() output>,
      "messages": ["string", ...]
    }

Wire format (response — single action):
    {"type": <int>, "data": [...]}    # data is optional

Wire format (response — pre-planned moves):
    {"actions": [{"type": <int>, "data": [...]}, ...]}

When hmac_secret is configured both sides sign their payloads; the engine
rejects responses with a missing, stale, or wrong signature (treated as
malformed — engine falls back to last_action and the DQ layer strikes).

A bot that times out, returns malformed JSON, or returns 5xx is treated
the same way as a local mind that raised: the engine falls back to
last_action and the strike will be counted by the DQ layer (#25).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import time
from typing import Any

import httpx

import cells

_HMAC_ALGORITHMS = {"sha256": hashlib.sha256}


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


def _parse_batch_response(payload: Any) -> dict:
    """Decode a batch response body into {agent_id: Action | list[Action] | None}.

    Missing or malformed entries are simply omitted; the engine treats
    absence as a strike + last_action fallback."""
    if not isinstance(payload, dict):
        return {}
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return {}
    out = {}
    for entry in actions:
        if not isinstance(entry, dict):
            continue
        aid = entry.get("id")
        if not isinstance(aid, str):
            continue
        out[aid] = _parse_action(entry.get("action"))
    return out


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
        verify: bool = True,
        max_response_bytes: int = 1_048_576,
        hmac_secret: str | None = None,
        hmac_algorithm: str = "sha256",
        hmac_skew_seconds: int = 30,
    ):
        self.name = name
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout
        self._verify = verify
        self._max_response_bytes = max_response_bytes
        self._hmac_secret = hmac_secret
        self._hmac_digestmod = _HMAC_ALGORITHMS.get(hmac_algorithm, hashlib.sha256)
        self._hmac_skew_seconds = hmac_skew_seconds
        client_kwargs = {"timeout": timeout}
        if transport is not None:
            client_kwargs["transport"] = transport
        else:
            client_kwargs["verify"] = verify
        self._client = httpx.AsyncClient(**client_kwargs)
        if not verify:
            import sys
            sys.stderr.write(
                "warning: HttpMind %r constructed with verify=False; "
                "TLS certificates will not be checked.\n" % name
            )
        if hmac_secret is None:
            import sys
            sys.stderr.write(
                "warning: HttpMind %r constructed without hmac_secret; "
                "requests will be unsigned and responses unverified.\n" % name
            )

        outer = self

        class _AgentMind:
            def __init__(self, cargs):
                self._http = outer

            async def act(self, view, msg):
                return await outer._call(view, msg)

        self.AgentMind = _AgentMind

    def _sign(self, ts: str, body_bytes: bytes) -> str:
        message = ts.encode() + b"." + body_bytes
        return _hmac.new(
            self._hmac_secret.encode(), message, self._hmac_digestmod
        ).hexdigest()

    def _verify_response(self, headers, body_bytes: bytes) -> bool:
        ts_str = headers.get("X-Cells-Timestamp")
        sig = headers.get("X-Cells-Signature")
        if not ts_str or not sig:
            return False
        try:
            ts = int(ts_str)
        except ValueError:
            return False
        if abs(ts - int(time.time())) > self._hmac_skew_seconds:
            return False
        expected = self._sign(ts_str, body_bytes)
        return _hmac.compare_digest(expected, sig)

    async def _post_capped(self, payload):
        """POST `payload` to the bot's URL, return the parsed JSON, or None
        on error or if the response exceeds `max_response_bytes` (#43).

        When hmac_secret is set, signs the request body and verifies the
        response signature; mismatched or stale signatures return None (#44).

        The body is streamed and the cap is checked after every chunk so a
        gigabyte response can't OOM the engine before the JSON decoder
        notices."""
        body_bytes = json.dumps(payload).encode()
        request_headers = {"Content-Type": "application/json", **self._headers}
        if self._hmac_secret is not None:
            ts = str(int(time.time()))
            request_headers["X-Cells-Timestamp"] = ts
            request_headers["X-Cells-Signature"] = self._sign(ts, body_bytes)
        try:
            async with self._client.stream(
                "POST", self._url, content=body_bytes, headers=request_headers
            ) as r:
                r.raise_for_status()
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_response_bytes:
                        return None
                resp_bytes = bytes(body)
                if self._hmac_secret is not None:
                    if not self._verify_response(r.headers, resp_bytes):
                        return None
                return json.loads(resp_bytes)
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return None

    async def _call(self, view, msg):
        payload = {
            "view": view.to_json(),
            "messages": list(msg),
        }
        return _parse_action(await self._post_capped(payload))

    async def act_batch(self, agents, msg):
        """Per-team batch endpoint (#23). `agents` is a list of
        (agent_id, WorldView). Returns {agent_id: Action | list[Action] | None}."""
        if not agents:
            return {}
        payload = {
            "tick": int(agents[0][1].tick),
            "messages": list(msg),
            "agents": [{"id": aid, "view": v.to_json()} for (aid, v) in agents],
        }
        raw = await self._post_capped(payload)
        if raw is None:
            return {}
        return _parse_batch_response(raw)

    async def aclose(self):
        await self._client.aclose()
