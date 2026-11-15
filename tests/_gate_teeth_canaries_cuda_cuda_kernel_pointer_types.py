"""Canary of cuda_kernel_pointer_types (loaded by tests/_gate_teeth_canaries_more.py; replaces the entry of tests/_gate_teeth_canaries.py).

One token per seeded shape: the plain double pointer, __launch_bounds__ (53-T1), a typedef alias, double2, an array parameter, an empty marker (53-T9), the probe rule and the advisory float rule (53-T13).
"""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_kernel_pointer_types",
    lambda d: _gate("cuda_kernel_pointer_types").assert_cuda_kernel_pointer_types(d, use_git=False, include_advisory=True),
    tokens=(
        'kernel `seed_argmax`', 'kernel `lb_gather`', 'kernel `alias_k`', 'kernel `vec_k`', 'kernel `array_k`', 'kernel `empty_reason_k`', '[cuda-type-probe-not-launched]', 'kernel `plain_k`',
    ),
)
