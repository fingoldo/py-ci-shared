"""Canary of tool_config_dead_entries (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "tool_config_dead_entries",
    lambda d: _gate("tool_config_dead_entries").assert_tool_config_dead_entries(d, use_git=False),
    token="pyproject.toml",
    tokens=("legacy/**", "gone-package"),
    parses=False,
)
