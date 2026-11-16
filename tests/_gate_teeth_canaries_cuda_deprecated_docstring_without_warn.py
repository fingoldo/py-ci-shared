"""Canary of deprecated_docstring_without_warn (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "deprecated_docstring_without_warn",
    lambda d: _gate("deprecated_docstring_without_warn").assert_deprecated_docstrings_warn(d, use_git=False),
    token="old_name",
    tokens=("old_name", "cv_score"),
)
