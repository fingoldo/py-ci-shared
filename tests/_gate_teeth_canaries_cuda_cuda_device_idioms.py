"""Canary fragment of cuda_device_idioms: literal device ids, cupy small powers and direct axis-0 device sorts."""

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_device_idioms",
    lambda d: _gate("cuda_device_idioms").assert_cuda_device_idioms(d, use_git=False),
    tokens=("cuda-literal-device-id", "cuda-cupy-small-power", "cuda-direct-device-sort"),
)
