"""Bot registration loader (#24).

Parses a bots.toml describing the contestants in a tournament. Each
[bots.NAME] section selects a transport (in_process | http | mcp) and
the parameters needed to construct that mind. The optional [tournament]
section carries shared knobs (bounds, symmetric, rounds, max_time).

Loaded values are returned in the (name, mind_module) shape that
cells.Game expects so the engine wiring is unchanged.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

from transports.http_mind import HttpMind
from transports.mcp_mind import McpMind


def _load_in_process(name, spec):
    module_path = spec.get("module")
    if not module_path:
        raise ValueError("bot %r (in_process) requires 'module'" % name)
    mind = importlib.import_module(module_path)
    mind.name = name
    return mind


def _load_http(name, spec):
    url = spec.get("url")
    if not url:
        raise ValueError("bot %r (http) requires 'url'" % name)
    return HttpMind(
        name,
        url,
        headers=spec.get("headers"),
        timeout=spec.get("timeout", 5.0),
        verify=spec.get("verify", True),
    )


def _load_mcp(name, spec):
    mode = spec.get("mode")
    if mode == "stdio":
        command = spec.get("command")
        if not command:
            raise ValueError("bot %r (mcp/stdio) requires 'command'" % name)
        return McpMind(name, server_command=list(command))
    if mode == "sse":
        url = spec.get("url")
        if not url:
            raise ValueError("bot %r (mcp/sse) requires 'url'" % name)
        return McpMind(name, server_url=url)
    raise ValueError(
        "bot %r has unknown mcp mode %r; use 'stdio' or 'sse'" % (name, mode)
    )


_LOADERS = {
    "in_process": _load_in_process,
    "http": _load_http,
    "mcp": _load_mcp,
}


def load_bots(path):
    """Read `path` and return (mind_list, tournament_cfg).

    `mind_list` is `[(name, mind_module), ...]` in the order the [bots.*]
    sections appear, so it can be handed straight to `cells.Game`.
    `tournament_cfg` is the raw [tournament] dict, possibly empty.
    """
    cfg = tomllib.loads(Path(path).read_text())
    bots_section = cfg.get("bots") or {}
    if not bots_section:
        raise ValueError("%s has no [bots.<name>] sections" % path)

    mind_list = []
    for name, spec in bots_section.items():
        transport = spec.get("transport")
        if transport is None:
            raise ValueError("bot %r is missing required 'transport' field" % name)
        loader = _LOADERS.get(transport)
        if loader is None:
            raise ValueError(
                "bot %r has unknown transport %r; use one of %s"
                % (name, transport, sorted(_LOADERS))
            )
        mind_list.append((name, loader(name, spec)))

    return mind_list, cfg.get("tournament") or {}


def select_bots(mind_list, names):
    """Filter `mind_list` to the subset `names`, preserving the requested
    order. Raises if any name isn't registered."""
    by_name = dict(mind_list)
    missing = [n for n in names if n not in by_name]
    if missing:
        raise ValueError("unknown bots in --bots config: %s" % missing)
    return [(n, by_name[n]) for n in names]


def warn_legacy_cfg(path):
    """Emit a stderr deprecation notice when a legacy configparser cfg
    drives mind selection. Done as a print rather than warnings.warn so
    end users running the CLI actually see it without setting filters."""
    sys.stderr.write(
        "DeprecationWarning: %s-based mind config is deprecated; "
        "use --bots bots.toml instead. See bots.example.toml.\n" % path
    )
