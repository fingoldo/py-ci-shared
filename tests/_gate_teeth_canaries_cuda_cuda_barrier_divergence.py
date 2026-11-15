"""Canary fragment of cuda_barrier_divergence (see _gate_teeth_canaries_more._load_cuda_fragments)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_barrier_divergence",
    lambda d: _gate("cuda_barrier_divergence").assert_cuda_barrier_divergence(d, include_advisory=True, use_git=False),
    tokens=("[cuda-barrier-in-divergent-branch]", "[cuda-barrier-after-divergent-return]", "kernel `seed_branch`", "kernel `seed_loop`"),
)
