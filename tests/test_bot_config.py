"""Tests for the bots.toml loader (#24).

Covers parsing all four transport types with mocked HTTP / MCP
clients, error reporting on missing or unknown fields, subset
selection, [tournament] propagation, and end-to-end coverage of
the legacy deprecation path through the cells / tournament CLIs.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pathlib
import subprocess
import sys
import textwrap

import pytest

from config import load_bots, select_bots
from transports.http_mind import HttpMind
from transports.mcp_mind import McpMind


REPO = pathlib.Path(__file__).resolve().parent.parent


def _write(tmp_path, body):
    path = tmp_path / "bots.toml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_in_process_transport(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "in_process"
        module = "minds.mind1"
    """)
    mind_list, t = load_bots(path)
    assert len(mind_list) == 1
    name, mind = mind_list[0]
    assert name == "alice"
    assert hasattr(mind, "AgentMind")
    assert mind.name == "alice"
    assert t == {}


def test_http_transport_constructs_httpmind(tmp_path):
    path = _write(tmp_path, """
        [bots.bob]
        transport = "http"
        url = "https://bob.example.com/act"
        timeout = 7.5
        headers = { Authorization = "Bearer xyz" }
    """)
    mind_list, _ = load_bots(path)
    name, mind = mind_list[0]
    assert name == "bob"
    assert isinstance(mind, HttpMind)
    assert mind._url == "https://bob.example.com/act"
    assert mind._timeout == 7.5
    assert mind._headers == {"Authorization": "Bearer xyz"}


def test_mcp_stdio_transport(tmp_path):
    path = _write(tmp_path, """
        [bots.carol]
        transport = "mcp"
        mode = "stdio"
        command = ["python", "bots/carol_server.py"]
    """)
    mind_list, _ = load_bots(path)
    name, mind = mind_list[0]
    assert name == "carol"
    assert isinstance(mind, McpMind)
    assert mind._server_command == ["python", "bots/carol_server.py"]
    assert mind._server_url is None


def test_mcp_sse_transport(tmp_path):
    path = _write(tmp_path, """
        [bots.dave]
        transport = "mcp"
        mode = "sse"
        url = "https://dave.example.com/mcp"
    """)
    mind_list, _ = load_bots(path)
    name, mind = mind_list[0]
    assert isinstance(mind, McpMind)
    assert mind._server_url == "https://dave.example.com/mcp"
    assert mind._server_command is None


def test_all_four_transports_in_one_file(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "in_process"
        module = "minds.mind1"

        [bots.bob]
        transport = "http"
        url = "https://bob.example.com/act"

        [bots.carol]
        transport = "mcp"
        mode = "stdio"
        command = ["python", "x.py"]

        [bots.dave]
        transport = "mcp"
        mode = "sse"
        url = "https://dave.example.com/mcp"
    """)
    mind_list, _ = load_bots(path)
    assert [n for (n, _) in mind_list] == ["alice", "bob", "carol", "dave"]
    assert isinstance(mind_list[1][1], HttpMind)
    assert isinstance(mind_list[2][1], McpMind)
    assert isinstance(mind_list[3][1], McpMind)


def test_tournament_section_propagates(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "in_process"
        module = "minds.mind1"

        [tournament]
        bounds = 50
        symmetric = false
        rounds = 7
        max_time = 1234
    """)
    _, t = load_bots(path)
    assert t == {"bounds": 50, "symmetric": False, "rounds": 7, "max_time": 1234}


def test_missing_transport_raises(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        module = "minds.mind1"
    """)
    with pytest.raises(ValueError, match="missing required 'transport'"):
        load_bots(path)


def test_unknown_transport_raises(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "ftp"
    """)
    with pytest.raises(ValueError, match="unknown transport"):
        load_bots(path)


def test_unknown_mcp_mode_raises(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "mcp"
        mode = "websocket"
    """)
    with pytest.raises(ValueError, match="unknown mcp mode"):
        load_bots(path)


def test_in_process_missing_module_raises(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "in_process"
    """)
    with pytest.raises(ValueError, match="requires 'module'"):
        load_bots(path)


def test_http_missing_url_raises(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "http"
    """)
    with pytest.raises(ValueError, match="requires 'url'"):
        load_bots(path)


def test_mcp_stdio_missing_command_raises(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "mcp"
        mode = "stdio"
    """)
    with pytest.raises(ValueError, match="requires 'command'"):
        load_bots(path)


def test_mcp_sse_missing_url_raises(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "mcp"
        mode = "sse"
    """)
    with pytest.raises(ValueError, match="requires 'url'"):
        load_bots(path)


def test_no_bots_section_raises(tmp_path):
    path = _write(tmp_path, """
        [tournament]
        bounds = 50
    """)
    with pytest.raises(ValueError, match="no \\[bots"):
        load_bots(path)


def test_select_bots_subset_preserves_order(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "in_process"
        module = "minds.mind1"

        [bots.bob]
        transport = "in_process"
        module = "minds.mind2"

        [bots.carol]
        transport = "in_process"
        module = "minds.mind3"
    """)
    mind_list, _ = load_bots(path)
    sub = select_bots(mind_list, ["carol", "alice"])
    assert [n for (n, _) in sub] == ["carol", "alice"]


def test_select_bots_unknown_name_raises(tmp_path):
    path = _write(tmp_path, """
        [bots.alice]
        transport = "in_process"
        module = "minds.mind1"
    """)
    mind_list, _ = load_bots(path)
    with pytest.raises(ValueError, match="unknown bots"):
        select_bots(mind_list, ["alice", "ghost"])


# ---------------------------------------------------------------------------
# CLI integration via subprocess.

def _cells(args, cwd, timeout=30):
    return subprocess.run(
        [sys.executable, str(REPO / "cells.py"), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "SDL_VIDEODRIVER": "dummy"},
    )


def _tournament(args, cwd, timeout=120):
    return subprocess.run(
        [sys.executable, str(REPO / "tournament.py"), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "SDL_VIDEODRIVER": "dummy"},
    )


def _write_in_process_toml(tmp_path, bounds=30):
    body = textwrap.dedent("""
        [bots.alice]
        transport = "in_process"
        module = "minds.mind1"

        [bots.bob]
        transport = "in_process"
        module = "minds.mind2"

        [bots.carol]
        transport = "in_process"
        module = "minds.mind3"

        [tournament]
        bounds = %d
        symmetric = true
    """ % bounds)
    path = tmp_path / "bots.toml"
    path.write_text(body)
    return str(path)


def test_cells_runs_with_bots_flag(tmp_path):
    bots = _write_in_process_toml(tmp_path)
    result = _cells(
        ["--headless", "--max-time", "30", "--seed", "1", "--bots", bots, "alice", "bob"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "It's a draw!" in result.stdout or "Winner is" in result.stdout
    # No deprecation warning when --bots is used.
    assert "DeprecationWarning" not in result.stderr


def test_cells_legacy_emits_deprecation_warning(tmp_path):
    result = _cells(
        ["--headless", "--max-time", "20", "--seed", "2", "mind1", "mind2"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "DeprecationWarning" in result.stderr
    assert "default.cfg" in result.stderr


def test_cells_unknown_bot_name_in_subset_fails(tmp_path):
    bots = _write_in_process_toml(tmp_path)
    result = _cells(
        ["--headless", "--max-time", "10", "--bots", bots, "alice", "ghost"],
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "unknown bots" in (result.stderr + result.stdout)


def test_tournament_runs_with_bots_flag(tmp_path):
    bots = _write_in_process_toml(tmp_path)
    result = _tournament(
        ["--bots", bots, "--seed", "1", "--rounds", "1", "--max-time", "30"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    csv = tmp_path / "scores.csv"
    assert csv.exists()
    rows = [r for r in csv.read_text().splitlines() if r.strip()]
    names = sorted(r.split(";")[0] for r in rows)
    assert names == ["alice", "bob", "carol"]
    assert "DeprecationWarning" not in result.stderr


def test_tournament_legacy_emits_deprecation_warning(tmp_path):
    (tmp_path / "tournament.cfg").write_text(
        "[minds]\nminds = mind1,mind2\n[terrain]\nbounds = 30\nsymmetric = true\n"
    )
    result = _tournament(
        ["--seed", "2", "--rounds", "1", "--max-time", "20"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "DeprecationWarning" in result.stderr
    assert "tournament.cfg" in result.stderr


def test_tournament_section_drives_rounds_and_max_time(tmp_path):
    body = textwrap.dedent("""
        [bots.alice]
        transport = "in_process"
        module = "minds.mind1"

        [bots.bob]
        transport = "in_process"
        module = "minds.mind2"

        [tournament]
        bounds = 30
        symmetric = true
        rounds = 1
        max_time = 25
    """)
    path = tmp_path / "bots.toml"
    path.write_text(body)
    result = _tournament(
        ["--bots", str(path), "--seed", "3"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    csv = tmp_path / "scores.csv"
    rows = [r for r in csv.read_text().splitlines() if r.strip()]
    assert len(rows) == 2
