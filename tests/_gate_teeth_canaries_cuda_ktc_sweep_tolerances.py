"""Canary of ktc_sweep_tolerances (loaded by _gate_teeth_canaries_more._load_cuda_fragments)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "ktc_sweep_tolerances",
    lambda d: _gate("ktc_sweep_tolerances").assert_ktc_sweep_tolerances(d, use_git=False),
    tokens=("in tune_a", "in tune_b", "equiv_rtol=0.05", "equiv_atol=0.0003", "[ktc-loose-sweep-tolerance]"),
)
