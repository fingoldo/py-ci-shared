"""Tests for py_ci_shared.ktc_sweep_tolerances."""

from __future__ import annotations

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.ktc_sweep_tolerances import RULE, assert_ktc_sweep_tolerances, find_ktc_sweep_tolerances

CALL = "def tune(x):\n    return sweep_backend_grid(x, repeats=3, equiv_rtol={r}, equiv_atol={a}){m}\n"


def _write(tmp_path, text, name="m.py"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _find(root, **kw):
    return find_ktc_sweep_tolerances(root, use_git=False, **kw)


def test_loose_literal_is_reported_with_rule_and_values(tmp_path):
    found = _find(_write(tmp_path, CALL.format(r="5e-2", a="1e-12", m="")))
    assert [f.rule for f in found] == [RULE] and "equiv_rtol=0.05" in found[0].message and "tune" in found[0].message


def test_tight_zero_and_computed_tolerances_pass(tmp_path):
    assert _find(_write(tmp_path, CALL.format(r="0.0", a="1e-12", m=""))) == []
    assert _find(_write(tmp_path, CALL.format(r="max(1e-9, 8 * eps)", a="float('inf')", m=""))) == []


def test_cap_is_a_parameter(tmp_path):
    root = _write(tmp_path, CALL.format(r="1e-4", a="1e-12", m=""))
    assert _find(root) and not _find(root, max_value=1e-3)


def test_call_and_kwarg_names_are_parameters(tmp_path):
    root = _write(tmp_path, "def f():\n    mod.other_sweep(tol=0.5)\n")
    assert not _find(root)
    assert len(_find(root, call_names=("other_sweep",), kwarg_names=("tol",))) == 1


def test_marker_with_reason_suppresses_but_empty_reason_does_not(tmp_path):
    assert not _find(_write(tmp_path, CALL.format(r="5e-2", a="1e-12", m="  # ktc-ok: rank vs percentile binner")))
    assert _find(_write(tmp_path, CALL.format(r="5e-2", a="1e-12", m="  # ktc-ok:")))


def test_allow_table_needs_a_reason(tmp_path):
    root = _write(tmp_path, CALL.format(r="5e-2", a="1e-12", m=""))
    assert not _find(root, allow={"m.py::tune": "different algorithms"})
    assert _find(root, allow={"m.py::tune": " "})


def test_exclude_path_part(tmp_path):
    (tmp_path / "legacy").mkdir()
    _write(tmp_path / "legacy", CALL.format(r="5e-2", a="1e-12", m=""))
    _write(tmp_path, "x = 1\n")
    assert not _find(tmp_path, exclude=("legacy",))
    assert _find(tmp_path)


def test_floor_and_unparsed(tmp_path):
    _write(tmp_path, "x = 1\n")
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=5)
    _write(tmp_path, "def (:\n", name="b.py")
    with pytest.raises(UnparsedFilesError):
        _find(tmp_path)
    assert _find(tmp_path, allow_unparsed=True) == []


def test_assert_message_names_file_line_rule(tmp_path):
    root = _write(tmp_path, CALL.format(r="5e-2", a="1e-12", m=""))
    with pytest.raises(AssertionError, match=r"m\.py:2: \[ktc-loose-sweep-tolerance\]"):
        assert_ktc_sweep_tolerances(root, use_git=False)
