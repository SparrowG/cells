"""MCP stdio server that wraps a local mind module for subprocess sandboxing (#46).

Launched by config.py when sandbox='subprocess':
    python /path/to/transports/in_process_server.py <module_path>

Adds the cells repo root to sys.path so mind modules (and cells itself)
are importable, then serves two MCP tools over stdio:

  act(view, messages)               -- single agent; one AgentMind per server
  act_batch(tick, agents, messages) -- per-agent, keyed by agent id

WorldView is reconstructed from the to_json() snapshot using lightweight
proxy objects that expose the same interface existing mind modules expect:
view.get_me(), view.get_agents(), view.get_plants(), view.get_energy().get(x, y),
plant.eff attribute, etc.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

# Make the cells repo root importable inside the subprocess so that
# `minds.*` modules and `cells` itself can be found regardless of cwd.
_repo_root = str(pathlib.Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


class _Me:
    __slots__ = ("energy", "loaded", "_pos", "_team")

    def __init__(self, d: dict):
        self.energy = d["energy"]
        self.loaded = d["loaded"]
        self._pos = (d["pos"][0], d["pos"][1])
        self._team = d["team"]

    def get_pos(self):
        return self._pos

    def get_team(self):
        return self._team


class _AgentProxy:
    __slots__ = ("_pos", "_team")

    def __init__(self, d: dict):
        self._pos = (d["pos"][0], d["pos"][1])
        self._team = d["team"]

    def get_pos(self):
        return self._pos

    def get_team(self):
        return self._team


class _PlantProxy:
    __slots__ = ("_pos", "eff")

    def __init__(self, d: dict):
        self._pos = (d["pos"][0], d["pos"][1])
        self.eff = d["eff"]

    def get_pos(self):
        return self._pos

    def get_eff(self):
        return self.eff


class _PatchLayer:
    """Wraps the 3x3 terrain/energy patch from to_json() as a .get(x, y) accessor.

    patch[dx][dy] maps to world coordinate (cx-1+dx, cy-1+dy), matching
    the layout produced by WorldView.to_json().
    """

    def __init__(self, patch: list, cx: int, cy: int):
        self._patch = patch
        self._ox = cx - 1
        self._oy = cy - 1

    def get(self, x: int, y: int):
        dx = x - self._ox
        dy = y - self._oy
        if 0 <= dx < 3 and 0 <= dy < 3:
            return self._patch[dx][dy]
        return None


class _WorldViewProxy:
    """Reconstructs the WorldView interface from a to_json() snapshot dict."""

    def __init__(self, d: dict):
        me_d = d["me"]
        self._me = _Me(me_d)
        self._agents = [_AgentProxy(a) for a in d.get("agents", [])]
        self._plants = [_PlantProxy(p) for p in d.get("plants", [])]
        cx, cy = me_d["pos"]
        self._terr = _PatchLayer(d.get("terrain", []), cx, cy)
        self._energy = _PatchLayer(d.get("energy", []), cx, cy)
        self.tick = d.get("tick", 0)

    def get_me(self):
        return self._me

    def get_agents(self):
        return self._agents

    def get_plants(self):
        return self._plants

    def get_terr(self):
        return self._terr

    def get_energy(self):
        return self._energy


def _action_to_json(action) -> dict | None:
    if action is None:
        return None
    result: dict = {"type": int(action.type)}
    data = action.get_data()
    if data is not None:
        result["data"] = list(data)
    return result


def _result_to_json(result) -> dict:
    """Convert an AgentMind.act() return value to the MCP wire format."""
    if result is None:
        return {}
    if isinstance(result, list):
        return {"actions": [_action_to_json(a) for a in result if a is not None]}
    j = _action_to_json(result)
    return j if j is not None else {}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: in_process_server <module_path>")
    module_path = sys.argv[1]
    mind_module = importlib.import_module(module_path)

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("in-process-mind")

    _default_agent = None
    _agents: dict = {}

    @server.tool()
    def act(view: dict, messages: list) -> dict:
        nonlocal _default_agent
        if _default_agent is None:
            _default_agent = mind_module.AgentMind([])
        return _result_to_json(_default_agent.act(_WorldViewProxy(view), messages))

    @server.tool()
    def act_batch(tick: int, agents: list, messages: list) -> dict:
        results = []
        for entry in agents:
            aid = entry["id"]
            if aid not in _agents:
                _agents[aid] = mind_module.AgentMind([])
            result = _agents[aid].act(_WorldViewProxy(entry["view"]), messages)
            results.append({"id": aid, "action": _result_to_json(result)})
        return {"actions": results}

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
