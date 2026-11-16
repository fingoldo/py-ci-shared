"""Canary fragment of fit_at_test_module_import (audit 50-T5). The loader globs ``_gate_teeth_canaries_cuda_*.py``, so this
file is named for that glob even though the gate is not a CUDA gate; each fragment defines one CANARY."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "fit_at_test_module_import",
    lambda d: _gate("fit_at_test_module_import").assert_no_fit_at_test_module_import(d),
    token="fit(",
    tokens=("fit(", "fit_transform("),
)
