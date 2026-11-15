"""Canary of cuda_coalesced_reads (loaded by tests/_gate_teeth_canaries_more.py; replaces the entry of tests/_gate_teeth_canaries.py).

One token per seeded rule and form (53-T4); seed.py:43 is the second kernel of the same name, reported at its own line (53-T5).
"""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "cuda_coalesced_reads",
    lambda d: _gate("cuda_coalesced_reads").assert_cuda_coalesced_reads(d, use_git=False),
    tokens=(
        'the kernel strided indexes', 'the kernel indirect_k', 'the kernel tx_k', 'the kernel ptr_row_k', 'the kernel gather_k', 'the kernel macro_k', 'seed.py:43:',
    ),
)
