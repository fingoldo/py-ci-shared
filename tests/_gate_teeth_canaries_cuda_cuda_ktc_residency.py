"""Canary of cuda_ktc_residency (loaded by tests/_gate_teeth_canaries_more.py)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_ktc_residency",
    lambda d: _gate("cuda_ktc_residency").assert_cuda_ktc_residency(d, use_git=False),
    tokens=("registration `pair_mi`", "registration `rank_bin`"),
)
