"""Canary of required_call_kwargs (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "required_call_kwargs",
    lambda d: _gate("required_call_kwargs").assert_required_call_kwargs(d, use_git=False),
    token="asyncpg.create_pool",
    tokens=("asyncpg.create_pool", "asyncpg.connect"),
)
