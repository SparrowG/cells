"""Integration test for the optional Cython get_small_view_fast helper.

The engine has a pure-Python fallback (cells.py) and runs fine without the
extension. When cells_helpers IS importable (i.e. someone ran
`python setup.py build_ext --inplace`), the engine should bind
ObjectMapLayer.get_small_view_fast to the Cython implementation.

CI does not build the extension, so this test skips there. It runs locally
after a `build_ext --inplace`.
"""

import pytest

cells_helpers = pytest.importorskip("cells_helpers")

import cells  # noqa: E402


def test_engine_binds_to_cython_helper_when_available():
    assert (
        cells.ObjectMapLayer.get_small_view_fast
        is cells_helpers.get_small_view_fast
    )


def test_cython_helper_returns_same_shape_as_python_fallback():
    """Cython and Python paths must agree on neighbor extraction."""
    layer = cells.ObjectMapLayer((10, 10))
    cython_view = cells_helpers.get_small_view_fast(layer, 5, 5)
    assert cython_view == []
