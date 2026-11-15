"""Canary of cuda_kernel_integer_width (loaded by tests/_gate_teeth_canaries_more.py; replaces the entry of tests/_gate_teeth_canaries.py).

One token per seeded shape: both long shapes, the int index product, the wide-variable product (53-T10) and a long behind an empty marker (53-T9).
"""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_kernel_integer_width",
    lambda d: _gate("cuda_kernel_integer_width").assert_cuda_kernel_integer_width(d, use_git=False, include_advisory=True),
    tokens=(
        '(long)i', 'unsigned long off', 'int idx = n_rows * n_cols', 'size_t base = n_rows * n_cols', 'long lane',
    ),
)
