"""Tests for py_ci_shared.nested_numba_parallel_guard."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.nested_numba_parallel_guard import (
    RULE_EXECUTOR,
    RULE_JOBLIB,
    RULE_THREAD,
    assert_nested_numba_parallel_guard,
    find_nested_numba_parallel_guard,
)

BOM = b"\xef\xbb\xbf"

KERNEL = """
from numba import njit, prange

@njit(parallel=True, cache=True)
def kernel(x):
    for i in prange(len(x)):
        x[i] += 1
    return x
"""


def _find(root, **kw):
    return find_nested_numba_parallel_guard(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else textwrap.dedent(data).encode())
    return tmp_path


def _hits(tmp_path: Path, files: dict, **kw):
    return [(f.path, f.line, f.rule) for f in _find(_corpus(tmp_path, files), **kw)]


def test_per_feature_edges_shape_four_hops_across_modules(tmp_path):
    """mlframe: the pool is four hops from the prange kernel, in other modules."""
    files = {
        "kern.py": KERNEL + "\ndef mdlp_recurse(x):\n    return kernel(x)\n",
        "edges.py": "from kern import mdlp_recurse\n\ndef mdlp_bin_edges(x):\n    return mdlp_recurse(x)\n\ndef edges_fayyad_irani(x):\n    return mdlp_bin_edges(x)\n",
        "fan.py": """
            from concurrent.futures import ThreadPoolExecutor
            from edges import edges_fayyad_irani

            def per_feature_edges(cols):
                with ThreadPoolExecutor(4) as pool:
                    return list(pool.map(edges_fayyad_irani, cols))
        """,
    }
    found = _find(_corpus(tmp_path, files))
    assert [(f.path, f.line, f.rule) for f in found] == [("fan.py", 6, RULE_EXECUTOR)]
    assert "per_feature_edges" in found[0].message
    assert "mdlp_bin_edges -> mdlp_recurse -> kernel" in found[0].message


def test_each_pool_kind_has_its_own_rule(tmp_path):
    files = {
        "m.py": KERNEL
        + """
def work(x):
    return kernel(x)

def a(c):
    return ThreadPoolExecutor(2).map(work, c)

def b(x):
    threading.Thread(target=work, args=(x,)).start()

def c(cs):
    return Parallel(n_jobs=2, prefer="threads")(delayed(work)(x) for x in cs)
"""
    }
    assert sorted(r for _, _, r in _hits(tmp_path, files)) == sorted([RULE_EXECUTOR, RULE_THREAD, RULE_JOBLIB])


def test_negative_control_the_guard_around_the_kernel_call_silences_the_path(tmp_path):
    files = {
        "m.py": KERNEL
        + """
def work(x):
    with parallel_kernel_entry():
        return kernel(x)

def a(c):
    return ThreadPoolExecutor(2).map(work, c)
"""
    }
    assert _hits(tmp_path, files) == []


def test_a_guard_in_a_sibling_function_does_not_cover_an_unguarded_one(tmp_path):
    """mlframe: `_hermite_prewarp` guarded its replay and the walk treated its unguarded neighbour as covered."""
    files = {
        "m.py": KERNEL
        + """
def guarded(x):
    with parallel_kernel_entry():
        return kernel(x)

def unguarded(x):
    return kernel(x)

def a(c):
    return ThreadPoolExecutor(2).map(unguarded, c)
"""
    }
    assert [r for _, _, r in _hits(tmp_path, files)] == [RULE_EXECUTOR]


def test_a_custom_guard_name_is_honoured(tmp_path):
    files = {"m.py": KERNEL + "\ndef a(c):\n    with my_lock():\n        kernel(c)\n    ThreadPoolExecutor(2)\n"}
    assert _hits(tmp_path / "custom", files, guard_name="my_lock") == []
    assert len(_hits(tmp_path / "default", files)) == 1


def test_a_dispatch_table_member_is_reached(tmp_path):
    files = {
        "m.py": KERNEL
        + """
BUILDERS = {"a": kernel}

def build(name, x):
    return BUILDERS[name](x)

def fan(c):
    return ThreadPoolExecutor(2).map(build, c)
"""
    }
    assert [r for _, _, r in _hits(tmp_path, files)] == [RULE_EXECUTOR]


def test_a_non_parallel_njit_is_not_a_kernel(tmp_path):
    files = {"m.py": "from numba import njit\n\n@njit(cache=True)\ndef k(x):\n    return x\n\ndef fan(c):\n    return ThreadPoolExecutor(2).map(k, c)\n"}
    assert _hits(tmp_path, files) == []


def test_depth_bounds_the_walk(tmp_path):
    files = {
        "m.py": KERNEL + "\ndef h1(x):\n    return kernel(x)\n\ndef h2(x):\n    return h1(x)\n\ndef fan(c):\n    return ThreadPoolExecutor(2).map(h2, c)\n"
    }
    assert len(_hits(tmp_path / "deep", files, max_depth=4)) == 1
    assert _hits(tmp_path / "shallow", files, max_depth=2) == []


def test_marker_on_the_pool_line_or_def_line_suppresses(tmp_path):
    body = KERNEL + "\ndef w(x):\n    return kernel(x)\n\n"
    on_pool = body + "def a(c):\n    return ThreadPoolExecutor(1).map(w, c)  # parallel-guarded: one worker, caller blocks\n"
    on_def = body + "def b(c):  # parallel-guarded: watchdog\n    return ThreadPoolExecutor(1).map(w, c)\n"
    assert _hits(tmp_path / "p", {"m.py": on_pool}) == []
    assert _hits(tmp_path / "d", {"m.py": on_def}) == []


def test_tests_and_benchmarks_dirs_are_skipped(tmp_path):
    files = {"tests/m.py": KERNEL + "\ndef f(c):\n    return ThreadPoolExecutor(2).map(kernel, c)\n", "ok.py": "x = 1\n"}
    assert _hits(tmp_path, files) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    src = textwrap.dedent(KERNEL + "\ndef f(c):\n    return ThreadPoolExecutor(2).map(kernel, c)\n").encode()
    plain = _hits(tmp_path / "a", {"m.py": src})
    bom = _hits(tmp_path / "b", {"m.py": BOM + src})
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
    bad = {"m.py": KERNEL + "\ndef f(c):\n    return ThreadPoolExecutor(2).map(kernel, c)\n"}
    with pytest.raises(AssertionError, match=r"with parallel_kernel_entry\(\)"):
        assert_nested_numba_parallel_guard(_corpus(tmp_path / "bad", bad), use_git=False)
    assert_nested_numba_parallel_guard(_corpus(tmp_path / "good", {"m.py": b"x = 1\n"}), use_git=False)


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    src = KERNEL + "\ndef f(c):\n    return ThreadPoolExecutor(2).map(kernel, c)\n"
    root = _corpus(tmp_path / "src", {"m.py": src})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_nested_numba_parallel_guard(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_nested_numba_parallel_guard(root, baseline_path=baseline, use_git=False)
    (root / "n.py").write_text(textwrap.dedent(src), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_nested_numba_parallel_guard(root, baseline_path=baseline, use_git=False)
