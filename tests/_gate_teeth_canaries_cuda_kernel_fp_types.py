"""Teeth canary for cuda_kernel_fp_types."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_kernel_fp_types",
    lambda d: _gate("cuda_kernel_fp_types").assert_cuda_kernel_fp_types(d, use_git=False),
    tokens=("[cuda-bare-fp-type]",),
)
