"""Canary of cuda_const_restrict (loaded by tests/_gate_teeth_canaries_more.py)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_const_restrict",
    lambda d: _gate("cuda_const_restrict").assert_cuda_const_restrict(d, use_git=False),
    tokens=("pointer parameter `joint_ro`", "pointer parameter `x_ro`"),
)
