"""Canary fragment of gpu_timing_sync (audit 53-T2/T3/T9): one token per seeded shape, so a gate neutered on one rule still fails."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate, _py

CANARY = Canary(
    "gpu_timing_sync",
    lambda d: _gate("gpu_timing_sync").assert_no_unsynchronized_gpu_timings(_py(d), d),
    token="seed_measure",
    tokens=("seed_measure", "seed_rawkernel_var", "seed_imported_launch", "seed_get_function_inline", "seed_bare_marker", "seed_misnamed_sync"),
)
