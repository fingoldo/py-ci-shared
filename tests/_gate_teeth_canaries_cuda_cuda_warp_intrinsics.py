"""Canary fragment of cuda_warp_intrinsics (see _gate_teeth_canaries_more._load_cuda_fragments)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_warp_intrinsics",
    lambda d: _gate("cuda_warp_intrinsics").assert_cuda_warp_intrinsics(d, include_advisory=True, use_git=False),
    tokens=("[cuda-legacy-shfl]", "[cuda-legacy-vote]", "[cuda-full-mask-in-divergent-branch]", "[cuda-activemask-as-mask]"),
)
