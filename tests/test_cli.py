"""End-to-end tests for the cells CLI entry point.

The engine + display + config wiring is exercised by spawning the
script as a subprocess so the __main__ path is covered, including
argparse, the restart loop break for headless, and the auto-creation
of default.cfg.
"""

import os
import pathlib
import subprocess
import sys

import pytest


REPO = pathlib.Path(__file__).resolve().parent.parent


def _run(args, cwd, timeout=30):
    return subprocess.run(
        [sys.executable, str(REPO / "cells.py"), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_headless_run_with_explicit_minds(tmp_path):
    """--headless --max-time --seed exits cleanly with mind names on argv."""
    result = _run(
        ["--headless", "--max-time", "30", "--seed", "1", "mind1", "mind2"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "It's a draw!" in result.stdout or "Winner is" in result.stdout


def test_headless_creates_default_cfg_when_missing(tmp_path):
    """Running with no positional args and no config file should auto-create one."""
    cfg = tmp_path / "default.cfg"
    assert not cfg.exists()
    result = _run(
        ["--headless", "--max-time", "20", "--seed", "2"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert cfg.exists(), "default.cfg was not created"
    contents = cfg.read_text()
    assert "[minds]" in contents
    assert "[terrain]" in contents
    assert "minds = mind1,mind2" in contents


def test_help_flag_exits_zero(tmp_path):
    result = _run(["--help"], cwd=tmp_path)
    assert result.returncode == 0
    assert "--headless" in result.stdout
    assert "--max-time" in result.stdout
    assert "--seed" in result.stdout
