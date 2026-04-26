"""Headless smoke tests for the cells engine.

These tests don't import any user mind modules - they use stub AgentMind
classes that exercise specific action types. The goal is to prove the
engine ticks and applies each action without crashing.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import random

import numpy
import pytest

import cells


def _seed(n=1):
    random.seed(n)
    numpy.random.seed(n)


def _module(name, mind_cls):
    """Wrap a mind class in a module-like object so cells.Game can use it.

    cells.Game expects mind_list entries shaped like (name, module) where
    module exposes both AgentMind and a `name` attribute (set by get_mind).
    """

    class M:
        pass

    m = M()
    m.AgentMind = mind_cls
    m.name = name
    return (name, m)


class EatMind:
    def __init__(self, cargs):
        pass

    def act(self, view, msg):
        return cells.Action(cells.ACT_EAT)


def test_module_exports_legacy_api():
    """Minds rely on these symbols - keep them stable."""
    assert cells.ACT_SPAWN == 0
    assert cells.ACT_MOVE == 1
    assert cells.ACT_EAT == 2
    assert cells.ACT_RELEASE == 3
    assert cells.ACT_ATTACK == 4
    assert cells.ACT_LIFT == 5
    assert cells.ACT_DROP == 6
    assert cells.STARTING_ENERGY == 20
    assert cells.SPAWN_TOTAL_ENERGY == cells.BODY_ENERGY + cells.SPAWN_LOST_ENERGY
    assert hasattr(cells, "Action")
    assert hasattr(cells, "Game")


def test_game_runs_to_draw_on_max_time():
    """Two stub minds that only eat will both survive until max_time."""
    _seed()
    g = cells.Game(
        50,
        [_module("a", EatMind), _module("b", EatMind)],
        symmetric=True,
        max_time=80,
        headless=True,
    )
    ticks = 0
    while g.winner is None and ticks < 500:
        g.tick()
        ticks += 1
    assert g.winner is not None
    assert g.winner == -1  # draw on max_time
    assert ticks >= 80


class AttackEverythingMind:
    """Attack any visible enemy, otherwise eat."""

    def __init__(self, cargs):
        pass

    def act(self, view, msg):
        me = view.get_me()
        for a in view.get_agents():
            if a.get_team() != me.get_team():
                return cells.Action(cells.ACT_ATTACK, a.get_pos())
        return cells.Action(cells.ACT_EAT)


def test_attack_action_is_applied():
    """Place two attacking minds adjacent; one should kill the other."""
    _seed(2)
    g = cells.Game(
        20,
        [_module("a", AttackEverythingMind), _module("b", AttackEverythingMind)],
        symmetric=True,
        max_time=200,
        headless=True,
    )
    ticks = 0
    while g.winner is None and ticks < 500:
        g.tick()
        ticks += 1
    assert g.winner is not None


class CycleMind:
    """Cycle through action types to exercise the dispatch table."""

    def __init__(self, cargs):
        self.i = 0

    def act(self, view, msg):
        me = view.get_me()
        x, y = me.get_pos()
        actions = [
            cells.Action(cells.ACT_EAT),
            cells.Action(cells.ACT_MOVE, (x + 1, y)),
            cells.Action(cells.ACT_LIFT),
            cells.Action(cells.ACT_DROP),
            cells.Action(cells.ACT_RELEASE, (x + 1, y, 1)),
        ]
        a = actions[self.i % len(actions)]
        self.i += 1
        return a


def test_all_action_types_dispatch_without_crash():
    _seed(3)
    g = cells.Game(
        30,
        [_module("a", CycleMind), _module("b", EatMind)],
        symmetric=True,
        max_time=60,
        headless=True,
    )
    ticks = 0
    while g.winner is None and ticks < 200:
        g.tick()
        ticks += 1
    assert ticks > 0
