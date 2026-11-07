"""Tests for py_ci_shared.possibly_unbound_local."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.possibly_unbound_local import (
    RULE_IF,
    RULE_MATCH,
    RULE_TRY,
    assert_possibly_unbound_local,
    find_possibly_unbound_local,
)

BOM = b"\xef\xbb\xbf"


def _find(root, **kw):
    return find_possibly_unbound_local(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _rules(tmp_path: Path, src: str):
    root = _corpus(tmp_path, {"m.py": textwrap.dedent(src).encode()})
    return [(f.line, f.rule) for f in _find(root)]


MUON = """
def maybe_newton_schulz_triton(verdict, shape):
    if verdict is None:
        device_name = "gpu"
        speedup = 2.0
        verdict = (device_name, speedup)
    print(f"dispatch {device_name} {speedup}")
    return verdict
"""


def test_reports_the_muon_dispatcher_shape(tmp_path):
    """mlframe 169f17000: names bound only in the `verdict is None` branch and read by a log line after it."""
    found = _find(_corpus(tmp_path, {"m.py": textwrap.dedent(MUON).encode()}))
    assert {(f.line, f.rule) for f in found} == {(7, RULE_IF)}
    assert {"device_name", "speedup"} == {f.message.split("`")[1] for f in found}


def test_negative_control_binding_before_the_branch_is_clean(tmp_path):
    src = MUON.replace("    if verdict is None:", "    device_name = speedup = None\n    if verdict is None:")
    assert _rules(tmp_path, src) == []


def test_else_arm_that_raises_or_returns_binds_the_name(tmp_path):
    src = """
    def f(x):
        if x:
            y = 1
        else:
            raise ValueError(x)
        return y

    def g(x):
        if x:
            z = 1
        else:
            return None
        return z

    def h(x):
        if x:
            w = 1
        else:
            w = 2
        return w
    """
    assert _rules(tmp_path, src) == []


def test_try_arms_report_try_rule(tmp_path):
    src = """
    def f(p):
        try:
            h = open(p)
        except OSError:
            pass
        return h
    """
    assert _rules(tmp_path, src) == [(7, RULE_TRY)]


def test_try_import_fallback_and_reraise_are_clean(tmp_path):
    src = """
    try:
        from numba import njit
    except ImportError:
        njit = None

    def f(p):
        try:
            h = open(p)
        except OSError:
            raise
        else:
            k = 1
        finally:
            print("done")
        return h, k
    """
    assert _rules(tmp_path, src) == []


def test_module_level_try_import_without_fallback_is_reported(tmp_path):
    """pyutilz core/array_summary.py: `njit` bound only in a try-import, then used."""
    src = """
    try:
        from numba import njit
    except ImportError:
        pass

    @njit
    def f():
        return 1

    x = njit
    """
    assert [r for _, r in _rules(tmp_path, src)] == [RULE_TRY]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match needs 3.10")
def test_match_without_irrefutable_case_reports_match_rule(tmp_path):
    src = """
    def f(k):
        match k:
            case 1:
                c = "one"
            case 2:
                c = "two"
        return c

    def g(k):
        match k:
            case 1:
                d = "one"
            case _:
                d = "other"
        return d
    """
    assert _rules(tmp_path, src) == [(8, RULE_MATCH)]


def test_loop_targets_and_loop_bound_names_are_not_reported(tmp_path):
    src = """
    def f(items):
        for i in items:
            last = i
        return i, last

    def g():
        while True:
            v = 1
            break
        return v
    """
    assert _rules(tmp_path, src) == []


def test_except_name_is_unbound_after_the_handler_and_del_clears(tmp_path):
    src = """
    def f(p):
        try:
            p()
        except OSError as e:
            print(e)
        q = 1
        del q
        return p
    """
    assert _rules(tmp_path, src) == []


def test_comprehension_variable_and_nested_function_reads_are_ignored(tmp_path):
    src = """
    def f(xs, flag):
        if flag:
            x = 1
        ys = [x for x in xs]
        def inner():
            return x
        return ys, inner
    """
    assert _rules(tmp_path, src) == []


def test_global_declared_name_is_ignored(tmp_path):
    src = """
    def f(flag):
        global G
        if flag:
            G = 1
        return G
    """
    assert _rules(tmp_path, src) == []


def test_augmented_assignment_reads_the_name(tmp_path):
    src = """
    def f(flag):
        if flag:
            n = 0
        n += 1
        return n
    """
    assert _rules(tmp_path, src) == [(5, RULE_IF)]


def test_marker_suppresses(tmp_path):
    src = """
    def f(flag):
        if flag:
            n = 0
        return n  # unbound-ok: flag is constant per process
    """
    assert _rules(tmp_path, src) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _find(_corpus(tmp_path / "a", {"m.py": textwrap.dedent(MUON).encode()}))
    bom = _find(_corpus(tmp_path / "b", {"m.py": BOM + textwrap.dedent(MUON).encode()}))
    assert [(f.line, f.rule) for f in bom] == [(f.line, f.rule) for f in plain] and plain


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)


def test_assert_names_the_fix_and_passes_clean_code(tmp_path):
    with pytest.raises(AssertionError, match=r"bind the name before the branch"):
        assert_possibly_unbound_local(_corpus(tmp_path / "bad", {"m.py": textwrap.dedent(MUON).encode()}), use_git=False)
    assert_possibly_unbound_local(_corpus(tmp_path / "good", {"m.py": b"x = 1\n"}), use_git=False)


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": textwrap.dedent(MUON).encode()})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_possibly_unbound_local(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_possibly_unbound_local(root, baseline_path=baseline, use_git=False)
    (root / "n.py").write_bytes(textwrap.dedent(MUON).encode())
    with pytest.raises(pytest.fail.Exception):
        assert_possibly_unbound_local(root, baseline_path=baseline, use_git=False)
