"""Canary of sql_where_mixed_and_or (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "sql_where_mixed_and_or",
    lambda d: _gate("sql_where_mixed_and_or").assert_sql_where_mixed_and_or(d, use_git=False),
    tokens=("lang = :l AND text = :t", "sense IN (3, 4)"),
)
