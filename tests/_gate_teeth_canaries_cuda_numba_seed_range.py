"""Canary fragment of numba_seed_range (audit 53-T9): the seeded wide seed and the bare marker that must not suppress it."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "numba_seed_range",
    lambda d: _gate("numba_seed_range").assert_numba_seeds_fit_int64(d, use_git=False),
    tokens=("seed_restore:", "seed_bare_marker:"),
)
