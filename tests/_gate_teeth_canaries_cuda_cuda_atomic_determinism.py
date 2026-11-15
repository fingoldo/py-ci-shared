"""Canary of cuda_atomic_determinism (loaded by tests/_gate_teeth_canaries_more.py)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_atomic_determinism",
    lambda d: _gate("cuda_atomic_determinism").assert_cuda_atomic_determinism(d, use_git=False),
    tokens=("kernel `sum_global`", "kernel `sum_shared`"),
)
