"""Canary of global_write_via_reexport_alias (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "global_write_via_reexport_alias",
    lambda d: _gate("global_write_via_reexport_alias").assert_global_write_via_reexport_alias(d, use_git=False),
    token="user.py",
    tokens=("_f._LIMIT", "from `.impl`"),
)
