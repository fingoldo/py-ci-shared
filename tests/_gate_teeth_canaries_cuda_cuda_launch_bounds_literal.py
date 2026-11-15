"""Canary of cuda_launch_bounds_literal (loaded by _gate_teeth_canaries_more._load_cuda_fragments)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_launch_bounds_literal",
    lambda d: _gate("cuda_launch_bounds_literal").assert_cuda_launch_bounds_literal(d, use_git=False),
    tokens=("(256, 8)", "(MLF_T, 4)", "[cuda-literal-min-blocks]"),
)
