"""Canary of cuda_fallback_logged (loaded by tests/_gate_teeth_canaries_more.py)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_fallback_logged",
    lambda d: _gate("cuda_fallback_logged").assert_cuda_fallback_logged(d, use_git=False),
    tokens=("device handler `cp.cuda.memory.OutOfMemoryError`", "broad handler `Exception`"),
)
