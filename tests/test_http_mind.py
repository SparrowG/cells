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

import hashlib
import hmac as _hmac
import json
import time

import httpx
import pytest

import cells
from transports.http_mind import HttpMind


def _hmac_sign(secret: str, ts: str, body_bytes: bytes) -> str:
    msg = ts.encode() + b"." + body_bytes
    return _hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _signed_response(secret: str, body_bytes: bytes, skew: int = 0) -> httpx.Response:
    ts = str(int(time.time()) + skew)
    sig = _hmac_sign(secret, ts, body_bytes)
    return httpx.Response(
        200,
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Cells-Timestamp": ts,
            "X-Cells-Signature": sig,
        },
    )


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


async def test_act_batch_round_trip():
    """The batch endpoint serialises the team's agents and parses the
    keyed response back into per-agent actions."""
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "actions": [
                    {"id": "agent-0", "action": {"type": cells.ACT_LIFT}},
                    {"id": "agent-1", "action": {"actions": [{"type": cells.ACT_DROP}, {"type": cells.ACT_EAT}]}},
                ]
            },
        )

    mind = _make_mind(handler)
    v0 = _make_view()
    v1 = _make_view()
    out = await mind.act_batch([("agent-0", v0), ("agent-1", v1)], ["hello"])

    body = captured["body"]
    assert body["tick"] == 1
    assert body["messages"] == ["hello"]
    assert [a["id"] for a in body["agents"]] == ["agent-0", "agent-1"]
    assert body["agents"][0]["view"]["me"]["pos"] == [5, 5]

    assert isinstance(out["agent-0"], cells.Action)
    assert out["agent-0"].type == cells.ACT_LIFT
    assert isinstance(out["agent-1"], list)
    assert [a.type for a in out["agent-1"]] == [cells.ACT_DROP, cells.ACT_EAT]
    await mind.aclose()


async def test_act_batch_http_error_returns_empty():
    def handler(request):
        return httpx.Response(500, text="upstream error")

    mind = _make_mind(handler)
    out = await mind.act_batch([("agent-0", _make_view())], [])
    assert out == {}
    await mind.aclose()


async def test_act_batch_partial_response():
    """Missing entries in the response dict are simply absent from the
    result; the engine handles the per-agent fallback."""
    def handler(request):
        return httpx.Response(
            200,
            json={"actions": [{"id": "agent-1", "action": {"type": cells.ACT_LIFT}}]},
        )

    mind = _make_mind(handler)
    out = await mind.act_batch(
        [("agent-0", _make_view()), ("agent-1", _make_view())], []
    )
    assert "agent-0" not in out
    assert out["agent-1"].type == cells.ACT_LIFT
    await mind.aclose()


def test_max_response_bytes_defaults_to_1mb():
    mind = HttpMind("bot", "https://test.invalid/act")
    assert mind._max_response_bytes == 1_048_576


async def test_oversized_response_returns_none():
    """A response body exceeding `max_response_bytes` is dropped, mirroring
    a malformed response — engine falls back to last_action."""
    huge = {"type": cells.ACT_EAT, "padding": "x" * 200_000}

    def handler(request):
        return httpx.Response(200, json=huge)

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, max_response_bytes=1_000)
    agent = mind.AgentMind(None)
    result = await agent.act(_make_view(), [])
    assert result is None
    await mind.aclose()


async def test_response_just_under_cap_decoded_normally():
    """A response within the cap parses normally."""
    payload = {"type": cells.ACT_EAT}

    def handler(request):
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, max_response_bytes=1_000_000)
    agent = mind.AgentMind(None)
    result = await agent.act(_make_view(), [])
    assert isinstance(result, cells.Action)
    assert result.type == cells.ACT_EAT
    await mind.aclose()


async def test_oversized_batch_response_returns_empty_dict():
    """For act_batch, an oversized response yields {} so the engine strikes
    every pending agent (per the existing #25 path)."""
    huge = {
        "actions": [{"id": "agent-0", "action": {"type": cells.ACT_EAT}}],
        "padding": "x" * 200_000,
    }

    def handler(request):
        return httpx.Response(200, json=huge)

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, max_response_bytes=1_000)
    out = await mind.act_batch([("agent-0", _make_view())], [])
    assert out == {}
    await mind.aclose()


async def test_default_cap_handles_realistic_batch_response():
    """Default 1 MB cap is generous enough for a 50-agent batch response."""
    actions = [
        {"id": f"agent-{i}", "action": {"type": cells.ACT_EAT}} for i in range(50)
    ]

    def handler(request):
        return httpx.Response(200, json={"actions": actions})

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport)
    out = await mind.act_batch(
        [(f"agent-{i}", _make_view()) for i in range(50)], []
    )
    assert len(out) == 50
    assert all(a.type == cells.ACT_EAT for a in out.values())
    await mind.aclose()


def test_verify_defaults_to_true():
    mind = HttpMind("bot", "https://test.invalid/act")
    assert mind._verify is True


def test_verify_false_propagates_and_warns(capsys):
    mind = HttpMind("bot", "https://test.invalid/act", verify=False)
    assert mind._verify is False
    captured = capsys.readouterr()
    assert "verify=False" in captured.err
    assert "'bot'" in captured.err


def test_verify_true_does_not_warn(capsys):
    HttpMind("bot", "https://test.invalid/act", verify=True)
    captured = capsys.readouterr()
    assert "verify=False" not in captured.err


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


# ---------------------------------------------------------------------------
# HMAC mutual-auth tests (#44)
# ---------------------------------------------------------------------------

def test_hmac_no_secret_warns(capsys):
    HttpMind("bot", "https://test.invalid/act")
    captured = capsys.readouterr()
    assert "hmac_secret" in captured.err
    assert "unsigned" in captured.err


def test_hmac_with_secret_no_unsigned_warn(capsys):
    HttpMind("bot", "https://test.invalid/act", hmac_secret="s3cr3t")
    captured = capsys.readouterr()
    assert "unsigned" not in captured.err


async def test_hmac_signed_response_accepted():
    """Engine signs outbound request; bot signs response with same key → action decoded."""
    SECRET = "shared-secret"
    resp_body = json.dumps({"type": cells.ACT_EAT}).encode()
    req_captured = {}

    def handler(request):
        req_captured["ts"] = request.headers.get("X-Cells-Timestamp")
        req_captured["sig"] = request.headers.get("X-Cells-Signature")
        # Verify the engine's request signature.
        expected = _hmac_sign(SECRET, req_captured["ts"], request.content)
        assert _hmac.compare_digest(expected, req_captured["sig"])
        return _signed_response(SECRET, resp_body)

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, hmac_secret=SECRET)
    action = await mind.AgentMind(None).act(_make_view(), [])
    assert isinstance(action, cells.Action)
    assert action.type == cells.ACT_EAT
    assert req_captured["ts"] is not None
    assert req_captured["sig"] is not None
    await mind.aclose()


async def test_hmac_mismatched_response_secret_returns_none():
    """Bot signs response with wrong key — engine rejects, returns None → strike."""
    resp_body = json.dumps({"type": cells.ACT_EAT}).encode()

    def handler(request):
        return _signed_response("wrong-secret", resp_body)

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, hmac_secret="correct-secret")
    result = await mind.AgentMind(None).act(_make_view(), [])
    assert result is None
    await mind.aclose()


async def test_hmac_stale_response_timestamp_returns_none():
    """Response timestamp older than skew window — engine rejects."""
    resp_body = json.dumps({"type": cells.ACT_EAT}).encode()

    def handler(request):
        return _signed_response("s3cr3t", resp_body, skew=-60)

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, hmac_secret="s3cr3t")
    result = await mind.AgentMind(None).act(_make_view(), [])
    assert result is None
    await mind.aclose()


async def test_hmac_missing_response_headers_returns_none():
    """Bot returns no signature headers — engine rejects."""
    def handler(request):
        return httpx.Response(200, json={"type": cells.ACT_EAT})

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, hmac_secret="s3cr3t")
    result = await mind.AgentMind(None).act(_make_view(), [])
    assert result is None
    await mind.aclose()


async def test_hmac_batch_signed_round_trip():
    """act_batch signs the batch request; bot signs response — result decoded."""
    SECRET = "batch-secret"
    resp_body = json.dumps(
        {"actions": [{"id": "agent-0", "action": {"type": cells.ACT_EAT}}]}
    ).encode()

    def handler(request):
        return _signed_response(SECRET, resp_body)

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, hmac_secret=SECRET)
    out = await mind.act_batch([("agent-0", _make_view())], [])
    assert out["agent-0"].type == cells.ACT_EAT
    await mind.aclose()


async def test_hmac_batch_bad_response_signature_returns_empty():
    """act_batch with mismatched response signature → {} → engine strikes all agents."""
    resp_body = json.dumps(
        {"actions": [{"id": "agent-0", "action": {"type": cells.ACT_EAT}}]}
    ).encode()

    def handler(request):
        return _signed_response("wrong-secret", resp_body)

    transport = httpx.MockTransport(handler)
    mind = HttpMind("bot", "http://test.invalid/act", transport=transport, hmac_secret="correct-secret")
    out = await mind.act_batch([("agent-0", _make_view())], [])
    assert out == {}
    await mind.aclose()
