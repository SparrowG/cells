import numpy
import pytest

from terrain.generator import terrain_generator


@pytest.fixture
def gen():
    return terrain_generator()


def test_create_random_shape_and_range(gen):
    numpy.random.seed(0)
    arr = gen.create_random((20, 20), 10)
    assert arr.shape == (20, 20)
    assert arr.dtype.kind in ("i", "u")
    assert arr.min() >= 0 and arr.max() <= 10


def test_create_perlin_shape_and_dtype(gen):
    numpy.random.seed(0)
    arr = gen.create_perlin((30, 30), 10)
    assert arr.shape == (30, 30)
    assert arr.dtype == int


def test_create_streak_runs(gen):
    arr = gen.create_streak((20, 20), 5)
    assert numpy.asarray(arr).shape == (20, 20)


def test_create_simple_runs(gen):
    arr = gen.create_simple((16, 16), 5)
    assert numpy.asarray(arr).shape == (16, 16)


def test_symmetry(gen):
    numpy.random.seed(0)
    arr = gen.create_perlin((16, 16), 10, symmetric=True)
    assert numpy.array_equal(arr, arr.T)
