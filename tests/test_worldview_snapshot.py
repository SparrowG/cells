"""Tests for WorldView.to_json — the serializable snapshot used by network
transport adapters (#22). Schema is documented in issue #22."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import json

import cells


class _StubAgent:
    """Minimal agent stand-in for direct WorldView construction in tests.

    The real `Agent` (cells.py) requires an AgentMind constructor and is
    overkill for snapshot tests."""

    def __init__(self, x, y, team=0, energy=20, loaded=False):
        self.x = x
        self.y = y
        self.team = team
        self.energy = energy
        self.loaded = loaded

    def get_pos(self):
        return (self.x, self.y)

    def get_team(self):
        return self.team


class _StubPlant:
    def __init__(self, x, y, eff):
        self.x = x
        self.y = y
        self.eff = eff

    def get_pos(self):
        return (self.x, self.y)

    def get_eff(self):
        return self.eff


def _build_view(me_pos=(5, 5), tick=42):
    """Build a WorldView with a small map and a couple of nearby agents/plants."""
    terr_map = cells.ScalarMapLayer((10, 10))
    energy_map = cells.ScalarMapLayer((10, 10))
    # Stamp a known terrain/energy pattern so we can verify the patch.
    for x in range(10):
        for y in range(10):
            terr_map.values[x, y] = x + y
            energy_map.values[x, y] = (x * 10) + y

    me = _StubAgent(*me_pos, team=0, energy=23, loaded=False)
    other = _StubAgent(me_pos[0] + 1, me_pos[1], team=1)
    plant = _StubPlant(me_pos[0], me_pos[1] - 1, eff=12)

    agent_views = [cells.AgentView(other)]
    plant_views = [cells.PlantView(plant)]

    return cells.WorldView(
        me, agent_views, plant_views, terr_map, energy_map, tick=tick
    )


def test_snapshot_has_all_schema_fields():
    snap = _build_view().to_json()
    assert set(snap.keys()) == {"me", "agents", "plants", "terrain", "energy", "tick"}
    assert set(snap["me"].keys()) == {"team", "energy", "loaded", "pos"}


def test_snapshot_round_trips_through_json():
    snap = _build_view().to_json()
    encoded = json.dumps(snap)
    decoded = json.loads(encoded)
    assert decoded == snap


def test_snapshot_me_fields():
    snap = _build_view(me_pos=(5, 5), tick=99).to_json()
    assert snap["me"] == {"team": 0, "energy": 23, "loaded": False, "pos": [5, 5]}
    assert snap["tick"] == 99


def test_snapshot_includes_neighbor_agents_and_plants():
    snap = _build_view(me_pos=(5, 5)).to_json()
    assert {"team": 1, "pos": [6, 5]} in snap["agents"]
    assert {"eff": 12, "pos": [5, 4]} in snap["plants"]


def test_snapshot_terrain_and_energy_are_3x3_patches_centered_on_agent():
    """For agent at (5, 5), terrain[dx][dy] == map.get(4 + dx, 4 + dy)."""
    snap = _build_view(me_pos=(5, 5)).to_json()
    for dx in range(3):
        for dy in range(3):
            ax = 4 + dx
            ay = 4 + dy
            assert snap["terrain"][dx][dy] == ax + ay
            assert snap["energy"][dx][dy] == (ax * 10) + ay


def test_snapshot_handles_world_edge_with_nulls():
    """At (0, 0) the upper-left of the 3x3 patch is out of range."""
    snap = _build_view(me_pos=(0, 0)).to_json()
    # (-1, -1), (-1, 0), (0, -1) are out of range -> None.
    assert snap["terrain"][0][0] is None
    assert snap["terrain"][0][1] is None
    assert snap["terrain"][1][0] is None
    # (0, 0) and beyond are valid.
    assert snap["terrain"][1][1] == 0
    assert snap["terrain"][2][2] == 2


def test_snapshot_values_are_native_python_types():
    """numpy scalars in MapLayer must be coerced for json.dumps to work."""
    snap = _build_view().to_json()
    # No numpy types should leak through — json.dumps would have failed
    # if anything was a numpy.int64 etc, but verify explicitly.
    for row in snap["terrain"]:
        for v in row:
            assert v is None or isinstance(v, int)
    assert isinstance(snap["me"]["energy"], int)
    assert isinstance(snap["me"]["loaded"], bool)
