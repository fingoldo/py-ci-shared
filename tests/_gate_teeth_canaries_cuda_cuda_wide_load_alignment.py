"""Canary fragment of cuda_wide_load_alignment: byte-pointer casts to a wider type without a pitch parameter."""

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_wide_load_alignment",
    lambda d: _gate("cuda_wide_load_alignment").assert_cuda_wide_load_alignment(d, use_git=False),
    tokens=("cuda-wide-load-alignment",),
)
