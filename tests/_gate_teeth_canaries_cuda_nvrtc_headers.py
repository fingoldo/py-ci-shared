"""Teeth canary for cuda_nvrtc_headers."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_nvrtc_headers",
    lambda d: _gate("cuda_nvrtc_headers").assert_cuda_nvrtc_headers(d, use_git=False),
    tokens=("`INFINITY` is undefined", "`FLT_MAX` is undefined"),
)
