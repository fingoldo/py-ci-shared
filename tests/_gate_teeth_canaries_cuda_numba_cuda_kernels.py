"""Canary of the numba_cuda_kernels gate: one seeded kernel per rule, one token per rule id."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "numba_cuda_kernels",
    lambda d: _gate("numba_cuda_kernels").assert_numba_cuda_kernels(d, use_git=False),
    tokens=(
        "numba-cuda-unguarded-grid-index",
        "numba-cuda-divergent-syncthreads",
        "numba-cuda-unsynchronised-rmw",
        "numba-cuda-float64-in-float32-kernel",
    ),
)
