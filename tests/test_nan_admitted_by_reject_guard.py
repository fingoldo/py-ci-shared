"""Tests for py_ci_shared.nan_admitted_by_reject_guard."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.nan_admitted_by_reject_guard import (
    RULE_LOOP,
    RULE_RETURN,
    assert_nan_admitted_by_reject_guard,
    find_nan_admitted_by_reject_guard,
)

BOM = b"\xef\xbb\xbf"

INSERT_TOP = """
import numpy as np

def _insert_top(losses, worst):
    kept = []
    for loss in losses:
        if loss >= worst:
            continue
        kept.append(np.float64(loss))
    return kept
"""


def _find(root, **kw):
    return find_nan_admitted_by_reject_guard(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else textwrap.dedent(data).encode())
    return tmp_path


def _hits(tmp_path: Path, src: str):
    return [(f.line, f.rule) for f in _find(_corpus(tmp_path, {"m.py": src}))]


def test_reports_the_mlframe_insert_top_shape(tmp_path):
    """mlframe 584c9d880: `loss >= worst: continue` keeps a NaN loss."""
    assert _hits(tmp_path, INSERT_TOP) == [(7, RULE_LOOP)]


def test_negative_control_the_negated_accepting_form_is_clean(tmp_path):
    assert _hits(tmp_path, INSERT_TOP.replace("if loss >= worst:", "if not loss < worst:")) == []


def test_an_earlier_isnan_on_the_same_name_is_clean(tmp_path):
    src = INSERT_TOP.replace("        if loss >= worst:", "        if np.isnan(loss):\n            continue\n        if loss >= worst:")
    assert _hits(tmp_path, src) == []


def test_an_isnan_on_another_name_does_not_excuse_it(tmp_path):
    src = INSERT_TOP.replace("        if loss >= worst:", "        if np.isnan(worst):\n            continue\n        if loss >= worst:")
    assert [r for _, r in _hits(tmp_path, src)] == [RULE_LOOP]


def test_self_comparison_idiom_is_clean(tmp_path):
    src = INSERT_TOP.replace("        if loss >= worst:", "        if loss != loss:\n            continue\n        if loss >= worst:")
    assert _hits(tmp_path, src) == []


def test_return_reject_has_its_own_rule_and_either_side_may_be_the_score(tmp_path):
    src = """
    import math

    def f(score, cutoff):
        if cutoff > score:
            return None
        return math.inf
    """
    assert _hits(tmp_path, src) == [(5, RULE_RETURN)]


def test_integer_counters_without_float_evidence_are_not_reported(tmp_path):
    src = """
    def f(items, limit):
        for value in items:
            if value > limit:
                break
    """
    assert _hits(tmp_path, src) == []


def test_an_if_with_an_else_is_not_a_bare_reject(tmp_path):
    src = INSERT_TOP.replace("            continue\n", "            continue\n        else:\n            kept.append(0.0)\n")
    assert _hits(tmp_path, src) == []


def test_non_score_names_and_multi_op_chains_are_not_reported(tmp_path):
    src = """
    import numpy as np

    def f(xs, worst, n, cap):
        for i in xs:
            if i >= worst:
                continue
            if n > cap:
                break
            if 0 < i < worst:
                continue
        return np.nan
    """
    assert _hits(tmp_path, src) == []


def test_marker_suppresses(tmp_path):
    src = INSERT_TOP.replace("if loss >= worst:", "if loss >= worst:  # nan-ok: losses are finite by construction")
    assert _hits(tmp_path, src) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    src = textwrap.dedent(INSERT_TOP).encode()
    plain = [(f.line, f.rule) for f in _find(_corpus(tmp_path / "a", {"m.py": src}))]
    bom = [(f.line, f.rule) for f in _find(_corpus(tmp_path / "b", {"m.py": BOM + src}))]
    assert bom == plain and plain


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)


def test_assert_names_the_fix_and_passes_clean_code(tmp_path):
    with pytest.raises(AssertionError, match=r"if not loss < worst"):
        assert_nan_admitted_by_reject_guard(_corpus(tmp_path / "bad", {"m.py": INSERT_TOP}), use_git=False)
    assert_nan_admitted_by_reject_guard(_corpus(tmp_path / "good", {"m.py": b"x = 1\n"}), use_git=False)


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": INSERT_TOP})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_nan_admitted_by_reject_guard(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_nan_admitted_by_reject_guard(root, baseline_path=baseline, use_git=False)
    (root / "n.py").write_text(textwrap.dedent(INSERT_TOP), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_nan_admitted_by_reject_guard(root, baseline_path=baseline, use_git=False)
