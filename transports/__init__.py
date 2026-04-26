"""Transport adapters that route the engine's `act()` calls to bots
running outside the engine process. Each adapter implements the
AgentMind interface but its `act` is async and makes a network call.

See cells-server epic (#19) for the architecture overview.
"""

from transports.http_mind import HttpMind

__all__ = ["HttpMind"]
