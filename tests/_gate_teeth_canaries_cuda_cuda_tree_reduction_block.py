"""Canary for cuda_tree_reduction_block (fragment loaded by _gate_teeth_canaries_more)."""

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_tree_reduction_block",
    lambda d: _gate("cuda_tree_reduction_block").assert_cuda_tree_reduction_block(d, use_git=False),
    tokens=("tree-reduction kernel rowsum", "[cuda-tree-block-not-pow2]"),
)
