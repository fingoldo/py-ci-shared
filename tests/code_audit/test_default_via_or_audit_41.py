"""default_via_or false positives from audit 41-T5: a test double's `or True` idiom and a chain of `.get()` string lookups."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.code_audit.default_via_or import scan_default_via_or_trap

from ._helpers import _write

_NO_TRAP_SOURCES = {
    "test_double_idiom.py": "def make(seen):\n    return lambda name: seen.append(name) or True\n",
}


@pytest.mark.parametrize("name", sorted(_NO_TRAP_SOURCES))
def test_audit_41_t5_idioms_are_not_traps(tmp_path: Path, name: str):
    """A None-returning call (`list.append`) as a record-and-return stub has no falsy sentinel to rewrite, so it is not reported."""
    _write(tmp_path, name, _NO_TRAP_SOURCES[name])
    assert scan_default_via_or_trap(tmp_path) == []


def test_audit_41_t5_numeric_fallback_on_get_is_still_flagged(tmp_path: Path):
    """The `.get()` exemption covers string chains only: `kwargs.get("timeout") or 120` still rewrites a real 0."""
    _write(tmp_path, "numeric_get.py", 'def f(kwargs):\n    return kwargs.get("timeout") or 120\n')
    assert len(scan_default_via_or_trap(tmp_path)) == 1
