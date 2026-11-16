"""Canary of thread_lifecycle (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "thread_lifecycle",
    lambda d: _gate("thread_lifecycle").assert_thread_lifecycle(d, use_git=False),
    tokens=("`threading.Thread`",),
)
