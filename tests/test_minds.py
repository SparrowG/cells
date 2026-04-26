"""Smoke tests for the priority-1 sample minds (mind1, mind2).

These minds turn out to already be Python 3-compatible - they have no
print statements, no xrange, and no iteritems. The tests exist to lock
that in: any future regression in the engine API contract that breaks
mind1 or mind2 should fail here first.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import random

import numpy
import pytest

import cells
from minds import mind1, mind2


def _seed(n=42):
    random.seed(n)
    numpy.random.seed(n)


@pytest.fixture
def named_minds():
    """Stamp `name` on each mind module the way cells.get_mind does."""
    mind1.name = "mind1"
    mind2.name = "mind2"
    return mind1, mind2


def test_mind1_exposes_agent_mind():
    assert hasattr(mind1, "AgentMind")
    m = mind1.AgentMind(None)
    assert hasattr(m, "act")


def test_mind2_exposes_agent_mind():
    assert hasattr(mind2, "AgentMind")
    m = mind2.AgentMind(None)
    assert hasattr(m, "act")


async def test_mind1_vs_mind2_runs_to_termination(named_minds):
    """End-to-end headless game with the bundled minds."""
    _seed()
    m1, m2 = named_minds
    game = cells.Game(
        60,
        [("mind1", m1), ("mind2", m2)],
        symmetric=True,
        max_time=200,
        headless=True,
    )
    ticks = 0
    while game.winner is None and ticks < 500:
        await game.tick()
        ticks += 1
    assert game.winner is not None, "game did not terminate within tick budget"
    assert ticks > 0


async def test_mind1_self_play_runs(named_minds):
    """Self-play surfaces strategy bugs that asymmetric games hide."""
    _seed(7)
    m1, _ = named_minds
    game = cells.Game(
        50,
        [("mind1a", m1), ("mind1b", m1)],
        symmetric=True,
        max_time=120,
        headless=True,
    )
    ticks = 0
    while game.winner is None and ticks < 300:
        await game.tick()
        ticks += 1
    assert game.winner is not None
