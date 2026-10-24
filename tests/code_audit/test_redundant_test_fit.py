"""Scanner tests for redundant_test_fit, split verbatim out of the former tests/test_code_audit.py."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

from py_ci_shared.code_audit import (
    scan_redundant_test_fit_calls,
)

from ._helpers import _write

# ---- signature normalisation uses ast.unparse directly (no 3.8 fallback: requires-python >=3.9) ----

def test_call_signature_is_the_unparsed_call_text():
    import ast

    from py_ci_shared.code_audit.redundant_test_fit import _call_signature

    call = ast.parse("_helper(1, k=x + 1)").body[0].value
    assert _call_signature(call) == "_helper(1, k=x + 1)"


# ---- subpackage facade sensor ------------------------------------------


# ---- redundant_test_fit_call ---------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 9), reason="scan_redundant_test_fit_calls needs ast.unparse (python>=3.9)")
def test_redundant_identical_fit_call_across_two_tests_flags(tmp_path: Path):
    """The exact confirmed-real-bug shape (mlframe MRMR biz_value suite): two sibling
    test functions each independently call the SAME deterministic helper with the SAME
    literal seed to check a different assertion on the identical fit result."""
    _write(tmp_path, "test_bad.py", """
def _build_data(seed):
    return seed

def _fit_model(X, seed):
    return X + seed

def test_a():
    X = _build_data(seed=101)
    sel = _fit_model(X, seed=101)
    assert sel

def test_b():
    X = _build_data(seed=101)
    sel = _fit_model(X, seed=101)
    assert sel
""")
    findings = scan_redundant_test_fit_calls(tmp_path)
    checks = {f.check for f in findings}
    assert "redundant_test_fit_call" in checks
    assert findings
    assert all(f.severity == "Low" for f in findings)


@pytest.mark.skipif(sys.version_info < (3, 9), reason="scan_redundant_test_fit_calls needs ast.unparse (python>=3.9)")
def test_a_loop_variable_argument_is_not_a_repeated_call(tmp_path: Path):
    """Two functions each looping over their own data unparse to the same signature - `_resolves(x)` -
    while sharing no call at all. The check is for a deterministic repeat of ONE call, and a loop variable
    takes a different value every iteration."""
    _write(tmp_path, "test_loop.py", """
def _resolves(x):
    return bool(x)

def test_a():
    assert all(_resolves(x) for x in [1, 2, 3])

def test_b():
    bad = [x for x in [4, 5] if not _resolves(x)]
    assert not bad
""")
    assert [f for f in scan_redundant_test_fit_calls(tmp_path) if f.check == "redundant_test_fit_call"] == []


@pytest.mark.skipif(sys.version_info < (3, 9), reason="scan_redundant_test_fit_calls needs ast.unparse (python>=3.9)")
def test_a_module_level_constant_argument_is_still_a_repeated_call(tmp_path: Path):
    """The exemption must key on ITERATION binding, not on "the argument is a name": a constant passed by
    name really is the same value in both callers, which is the genuine duplicate this check exists for."""
    _write(tmp_path, "test_const.py", """
SEED = 101

def _fit(seed):
    return seed

def test_a():
    assert _fit(SEED)

def test_b():
    assert _fit(SEED) == 101
""")
    assert "redundant_test_fit_call" in {f.check for f in scan_redundant_test_fit_calls(tmp_path)}


@pytest.mark.skipif(sys.version_info < (3, 9), reason="scan_redundant_test_fit_calls needs ast.unparse (python>=3.9)")
def test_a_literal_data_factory_is_not_flagged(tmp_path: Path):
    """A helper that fills in a dict literal is the opposite of the expensive fit this scanner hunts: it
    costs microseconds, and its result is a FRESH MUTABLE object each caller then edits. Acting on the
    finding here - caching it, or sharing one fixture - would hand every test the same dict and let one
    test's mutation reach another, so a flag on this shape recommends a bug."""
    _write(tmp_path, "test_factory.py", """
def _item(**over):
    base = {"name": "x", "hits": 13, "denominator": 142}
    base.update(over)
    return base

def test_a():
    assert _item()["hits"] == 13

def test_b():
    assert _item()["denominator"] == 142
""")
    assert [f for f in scan_redundant_test_fit_calls(tmp_path) if f.check == "redundant_test_fit_call"] == []


@pytest.mark.skipif(sys.version_info < (3, 9), reason="scan_redundant_test_fit_calls needs ast.unparse (python>=3.9)")
def test_a_helper_with_no_calls_is_still_flagged(tmp_path: Path):
    """The exemption must key on BUILDING a literal, not merely on containing no expensive-looking call:
    `def _build_data(seed): return seed` is what an expensive builder is reduced to in a scanner test."""
    _write(tmp_path, "test_stub.py", """
def _build_data(seed):
    return seed

def test_a():
    assert _build_data(seed=101) == 101

def test_b():
    assert _build_data(seed=101) is not None
""")
    assert "redundant_test_fit_call" in {f.check for f in scan_redundant_test_fit_calls(tmp_path)}


@pytest.mark.skipif(sys.version_info < (3, 9), reason="scan_redundant_test_fit_calls needs ast.unparse (python>=3.9)")
def test_redundant_call_different_seeds_not_flagged(tmp_path: Path):
    """Different literal args -> genuinely different computations, not a duplicate."""
    _write(tmp_path, "test_ok.py", """
def _build_data(seed):
    return seed

def test_a():
    X = _build_data(seed=101)
    assert X

def test_b():
    X = _build_data(seed=202)
    assert X
""")
    assert scan_redundant_test_fit_calls(tmp_path) == []


@pytest.mark.skipif(sys.version_info < (3, 9), reason="scan_redundant_test_fit_calls needs ast.unparse (python>=3.9)")
def test_redundant_call_same_test_not_flagged(tmp_path: Path):
    """The SAME call appearing twice within one test function (e.g. a sanity re-check)
    is not a cross-test duplication -- only 2+ DIFFERENT test functions count."""
    _write(tmp_path, "test_ok.py", """
def _build_data(seed):
    return seed

def test_a():
    X1 = _build_data(seed=101)
    X2 = _build_data(seed=101)
    assert X1 == X2
""")
    assert scan_redundant_test_fit_calls(tmp_path) == []


@pytest.mark.skipif(sys.version_info < (3, 9), reason="scan_redundant_test_fit_calls needs ast.unparse (python>=3.9)")
def test_redundant_call_already_cached_not_flagged(tmp_path: Path):
    """A helper already decorated with @cache/@lru_cache has already been fixed."""
    _write(tmp_path, "test_ok.py", """
from functools import cache

@cache
def _build_data(seed):
    return seed

def test_a():
    X = _build_data(seed=101)
    assert X

def test_b():
    X = _build_data(seed=101)
    assert X
""")
    assert scan_redundant_test_fit_calls(tmp_path) == []


def test_redundant_call_non_test_file_not_scanned(tmp_path: Path):
    """This scanner only applies to test_*.py / *_test.py files."""
    _write(tmp_path, "helpers.py", """
def _build_data(seed):
    return seed

def test_a():
    X = _build_data(seed=101)
    assert X

def test_b():
    X = _build_data(seed=101)
    assert X
""")
    assert scan_redundant_test_fit_calls(tmp_path) == []


def test_redundant_call_public_function_not_flagged(tmp_path: Path):
    """Only underscore-prefixed local helpers are tracked -- repeated identical calls to a
    public/third-party-style function (no leading underscore) are a normal, cheap pattern."""
    _write(tmp_path, "test_ok.py", """
def build_data(seed):
    return seed

def test_a():
    X = build_data(seed=101)
    assert X

def test_b():
    X = build_data(seed=101)
    assert X
""")
    assert scan_redundant_test_fit_calls(tmp_path) == []


# ---- F63: redundant test fit, on the 3.8 floor --------------------------------------


def test_redundant_test_fit_calls_ignores_different_arguments(tmp_path: Path):
    _write(tmp_path, "test_a.py", """
def _build_data(n):
    return compute(n)


def test_a():
    assert _build_data(1)


def test_b():
    assert _build_data(2)
""")
    assert scan_redundant_test_fit_calls(tmp_path) == []
