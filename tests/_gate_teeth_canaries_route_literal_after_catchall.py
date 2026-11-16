"""Canary of route_literal_after_catchall (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "route_literal_after_catchall",
    lambda d: _gate("route_literal_after_catchall").assert_route_literal_after_catchall(d, router_names=("router",), use_git=False),
    tokens=("/words/search", "/words/{sense_id}"),
)
