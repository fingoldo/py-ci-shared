"""Canary fragment of cuda_launch_geometry (audit 53-T9): the existing literal shapes plus a launch whose marker has no reason."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_launch_geometry",
    lambda d: _gate("cuda_launch_geometry").assert_cuda_launch_geometry(d, use_git=False),
    tokens=("block size (256,)", "block size (192,)", "block size (128,)", "block size (16, 16)", "block size (512,)", "block size (64,)"),
)
