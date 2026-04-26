"""End-to-end tests for the tournament runner.

The script is exercised via subprocess so the __main__ path is covered,
including argparse, the config auto-create, the round-robin loop, and the
scores.csv writer. Tests use small bounds and short max-time to keep the
suite fast in CI.
"""

import pathlib
import subprocess
import sys

import pytest


REPO = pathlib.Path(__file__).resolve().parent.parent


def _run(args, cwd, timeout=120):
    return subprocess.run(
        [sys.executable, str(REPO / "tournament.py"), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_small_cfg(tmp_path, bounds=30, symmetric="true"):
    (tmp_path / "tournament.cfg").write_text(
        "[minds]\n"
        "minds = mind1,mind2\n"
        "[terrain]\n"
        f"bounds = {bounds}\n"
        f"symmetric = {symmetric}\n"
    )


def test_tournament_writes_scores_csv(tmp_path):
    _write_small_cfg(tmp_path)
    result = _run(
        [
            "--seed", "1",
            "--rounds", "1",
            "--max-time", "30",
            "mind1", "mind2", "mind3",
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    csv = tmp_path / "scores.csv"
    assert csv.exists()
    rows = [r for r in csv.read_text().splitlines() if r.strip()]
    assert len(rows) == 3
    names = []
    for row in rows:
        name, score = row.split(";")
        assert name in {"mind1", "mind2", "mind3"}
        assert int(score) >= 0
        names.append(name)
    assert sorted(names) == ["mind1", "mind2", "mind3"]


def test_tournament_creates_cfg_when_missing(tmp_path):
    cfg = tmp_path / "tournament.cfg"
    assert not cfg.exists()
    result = _run(
        ["--seed", "2", "--rounds", "1", "--max-time", "20"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert cfg.exists(), "tournament.cfg was not auto-created"
    contents = cfg.read_text()
    assert "[minds]" in contents
    assert "[terrain]" in contents
    assert "minds = mind1,mind2" in contents
    assert "bounds = 300" in contents


def test_tournament_score_total_within_expected_range(tmp_path):
    """rounds * games_per_round * 3 is the win-only ceiling;
    rounds * games_per_round * 2 is the all-draws floor."""
    _write_small_cfg(tmp_path)
    rounds = 2
    minds = ["mind1", "mind2", "mind3"]
    games_per_round = len(minds) * (len(minds) - 1) // 2
    result = _run(
        [
            "--seed", "3",
            "--rounds", str(rounds),
            "--max-time", "30",
            *minds,
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    csv = tmp_path / "scores.csv"
    rows = [r for r in csv.read_text().splitlines() if r.strip()]
    total = sum(int(r.split(";")[1]) for r in rows)
    expected_low = rounds * games_per_round * 2
    expected_high = rounds * games_per_round * 3
    assert expected_low <= total <= expected_high


def test_tournament_scores_sorted_descending(tmp_path):
    _write_small_cfg(tmp_path)
    result = _run(
        [
            "--seed", "4",
            "--rounds", "1",
            "--max-time", "30",
            "mind1", "mind2", "mind3",
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    csv = tmp_path / "scores.csv"
    rows = [r for r in csv.read_text().splitlines() if r.strip()]
    scores = [int(r.split(";")[1]) for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_tournament_help_flag(tmp_path):
    result = _run(["--help"], cwd=tmp_path)
    assert result.returncode == 0
    assert "--rounds" in result.stdout
    assert "--max-time" in result.stdout
    assert "--seed" in result.stdout
    assert "--output" in result.stdout
