"""Canary for cuda_grid_caps (fragment loaded by _gate_teeth_canaries_more)."""

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_grid_caps",
    lambda d: _gate("cuda_grid_caps").assert_cuda_grid_caps(d, use_git=False),
    tokens=("literal 64", "literal 32", "_STAT_BLOCKS = 64"),
)
