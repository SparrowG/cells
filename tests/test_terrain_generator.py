import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import random

import numpy
import pytest

import cells
from terrain.generator import terrain_generator


@pytest.fixture
def gen():
    return terrain_generator()


def _module(name, mind_cls):
    class M:
        pass
    m = M()
    m.AgentMind = mind_cls
    m.name = name
    return (name, m)


def test_create_random_shape_and_range(gen):
    numpy.random.seed(0)
    arr = gen.create_random((20, 20), 10)
    assert arr.shape == (20, 20)
    assert arr.dtype.kind in ("i", "u")
    assert arr.min() >= 0 and arr.max() <= 10


def test_create_perlin_shape_dtype_and_range(gen):
    numpy.random.seed(0)
    arr = gen.create_perlin((30, 30), 10)
    assert arr.shape == (30, 30)
    assert arr.dtype.kind in ("i", "u")
    assert arr.min() >= 0 and arr.max() <= 10


def test_create_streak_shape_dtype_and_range(gen):
    random.seed(0)
    arr = numpy.asarray(gen.create_streak((20, 20), 5))
    assert arr.shape == (20, 20)
    assert arr.dtype.kind in ("i", "u")
    assert arr.min() >= 0 and arr.max() <= 5


def test_create_simple_shape_dtype_and_range(gen):
    random.seed(0)
    arr = numpy.asarray(gen.create_simple((16, 16), 5))
    assert arr.shape == (16, 16)
    assert arr.dtype.kind in ("i", "u")
    assert arr.min() >= 0 and arr.max() <= 5


def test_symmetry_perlin(gen):
    numpy.random.seed(0)
    arr = gen.create_perlin((16, 16), 10, symmetric=True)
    assert numpy.array_equal(arr, arr.T)


def test_symmetry_streak(gen):
    random.seed(0)
    arr = numpy.asarray(gen.create_streak((16, 16), 5, symmetric=True))
    assert numpy.array_equal(arr, arr.T)


async def test_game_uses_generators_without_raising():
    random.seed(0)
    numpy.random.seed(0)

    class EatMind:
        def __init__(self, cargs):
            pass

        def act(self, view, msg):
            return cells.Action(cells.ACT_EAT)

    g = cells.Game(
        50,
        [_module("a", EatMind), _module("b", EatMind)],
        symmetric=True,
        max_time=5,
        headless=True,
    )
    assert g.terr.values is not None
    assert g.energy_map.values is not None
    ticks = 0
    while g.winner is None and ticks < 10:
        await g.tick()
        ticks += 1
    assert ticks > 0
