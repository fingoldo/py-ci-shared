"""Canary of cuda_parity_test_shape (loaded by tests/_gate_teeth_canaries_more.py)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_parity_test_shape",
    lambda d: _gate("cuda_parity_test_shape").assert_cuda_parity_test_shape(d, use_git=False),
    tokens=("parity test `test_gpu_matches_cpu`", "parity test `test_cuda_equals_njit_for_every_size`"),
)
