"""Tests for the in_process subprocess sandbox server (#46).

Covers the WorldView proxy objects, the action serializer, and an
integration test that spawns a real subprocess server against minds.mind1.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pathlib
import sys

import pytest

import cells
import transports.in_process_server as _srv
from transports.mcp_mind import McpMind


_SERVER_SCRIPT = str(
    pathlib.Path(__file__).resolve().parent.parent
    / "transports"
    / "in_process_server.py"
)


class _StubAgent:
    def __init__(self, x=5, y=5, team=0, energy=20, loaded=False):
        self.x, self.y, self.team, self.energy, self.loaded = x, y, team, energy, loaded

    def get_pos(self):
        return (self.x, self.y)

    def get_team(self):
        return self.team


def _make_view(x=5, y=5):
    terr = cells.ScalarMapLayer((10, 10))
    energy = cells.ScalarMapLayer((10, 10))
    return cells.WorldView(_StubAgent(x, y), [], [], terr, energy, tick=3)


def _snap(x=5, y=5):
    return _make_view(x, y).to_json()


# ---------------------------------------------------------------------------
# WorldView proxy

def test_worldview_proxy_me_fields():
    proxy = _srv._WorldViewProxy(_snap())
    me = proxy.get_me()
    assert me.get_pos() == (5, 5)
    assert me.get_team() == 0
    assert me.energy == 20
    assert me.loaded is False


def test_worldview_proxy_tick():
    proxy = _srv._WorldViewProxy(_snap())
    assert proxy.tick == 3


def test_worldview_proxy_agents_empty():
    proxy = _srv._WorldViewProxy(_snap())
    assert proxy.get_agents() == []


def test_worldview_proxy_plants_empty():
    proxy = _srv._WorldViewProxy(_snap())
    assert proxy.get_plants() == []


def test_worldview_proxy_agents_populated():
    snap = _snap()
    snap["agents"] = [{"team": 1, "pos": [6, 5]}]
    proxy = _srv._WorldViewProxy(snap)
    agents = proxy.get_agents()
    assert len(agents) == 1
    assert agents[0].get_pos() == (6, 5)
    assert agents[0].get_team() == 1


def test_worldview_proxy_plants_populated():
    snap = _snap()
    snap["plants"] = [{"eff": 12, "pos": [5, 4]}]
    proxy = _srv._WorldViewProxy(snap)
    plants = proxy.get_plants()
    assert len(plants) == 1
    assert plants[0].get_pos() == (5, 4)
    assert plants[0].get_eff() == 12
    assert plants[0].eff == 12  # direct attribute access used by some minds


def test_patchlayer_get_center():
    snap = _snap(x=5, y=5)  # agent at (5, 5); patch origin at (4, 4)
    proxy = _srv._WorldViewProxy(snap)
    energy = proxy.get_energy()
    terr = proxy.get_terr()
    # Cells are zero-initialised in the stub; all in-range values are 0.
    assert energy.get(5, 5) == 0
    assert terr.get(5, 5) == 0


def test_patchlayer_get_out_of_range_returns_none():
    proxy = _srv._WorldViewProxy(_snap(x=5, y=5))
    # (0, 0) is outside the 3x3 patch centred at (5, 5)
    assert proxy.get_energy().get(0, 0) is None
    assert proxy.get_terr().get(100, 100) is None


def test_patchlayer_get_edge_of_patch():
    snap = _snap(x=5, y=5)  # patch covers (4..6, 4..6)
    proxy = _srv._WorldViewProxy(snap)
    assert proxy.get_energy().get(4, 4) == 0  # top-left of patch
    assert proxy.get_energy().get(6, 6) == 0  # bottom-right of patch
    assert proxy.get_energy().get(3, 5) is None  # just outside
    assert proxy.get_energy().get(7, 5) is None  # just outside


# ---------------------------------------------------------------------------
# Action serializer

def test_action_to_json_no_data():
    action = cells.Action(cells.ACT_EAT)
    assert _srv._action_to_json(action) == {"type": cells.ACT_EAT}


def test_action_to_json_with_tuple_data():
    action = cells.Action(cells.ACT_MOVE, (3, 4))
    result = _srv._action_to_json(action)
    assert result == {"type": cells.ACT_MOVE, "data": [3, 4]}


def test_action_to_json_none_returns_none():
    assert _srv._action_to_json(None) is None


def test_result_to_json_single_action():
    action = cells.Action(cells.ACT_EAT)
    assert _srv._result_to_json(action) == {"type": cells.ACT_EAT}


def test_result_to_json_list():
    actions = [cells.Action(cells.ACT_MOVE, (1, 2)), cells.Action(cells.ACT_EAT)]
    result = _srv._result_to_json(actions)
    assert result == {"actions": [
        {"type": cells.ACT_MOVE, "data": [1, 2]},
        {"type": cells.ACT_EAT},
    ]}


def test_result_to_json_none_returns_empty_dict():
    assert _srv._result_to_json(None) == {}


# ---------------------------------------------------------------------------
# Integration: real subprocess server

async def test_subprocess_server_returns_action():
    """Spawn the actual in_process_server subprocess against minds.mind1
    and verify it returns a valid action via the MCP stdio protocol."""
    mind = McpMind(
        "alice",
        server_command=[sys.executable, _SERVER_SCRIPT, "minds.mind1"],
    )
    agent = mind.AgentMind(None)
    view = _make_view()
    result = await agent.act(view, [])
    await mind.aclose()
    assert result is not None
    assert hasattr(result, "type")
    assert cells.ACT_SPAWN <= result.type <= cells.ACT_DROP


async def test_subprocess_server_act_batch():
    """act_batch returns per-agent actions for two distinct agents."""
    mind = McpMind(
        "alice",
        server_command=[sys.executable, _SERVER_SCRIPT, "minds.mind1"],
    )
    view1 = _make_view(x=3, y=3)
    view2 = _make_view(x=7, y=7)
    out = await mind.act_batch(
        [("agent-1", view1), ("agent-2", view2)], []
    )
    await mind.aclose()
    assert "agent-1" in out
    assert "agent-2" in out
    assert out["agent-1"] is not None
    assert out["agent-2"] is not None
