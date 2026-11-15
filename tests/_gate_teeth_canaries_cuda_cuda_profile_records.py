"""Canary of cuda_profile_records (loaded by tests/_gate_teeth_canaries_more.py)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_profile_records",
    lambda d: _gate("cuda_profile_records").assert_cuda_profile_records(d / "pkg", profiles_dir=d / "profiles", use_git=False),
    tokens=(
        "has no profile record `profiles/seed_k_gpu.md`",
        "lacks the field `occupancy`",
        "lacks the field `throughput`",
    ),
)
