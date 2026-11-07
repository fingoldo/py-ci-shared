"""Tests for py_ci_shared.instance_clear_leaves_module_cache."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.instance_clear_leaves_module_cache import (
    RULE_CLEAR,
    RULE_RELEASE,
    assert_instance_clear_leaves_module_cache,
    find_instance_clear_leaves_module_cache,
)

BOM = b"\xef\xbb\xbf"

XGB_SHIM = """
_DMATRIX_CACHE = {}


class XgbShim:
    def __init__(self):
        self.ptr = None

    def fit(self, key, build):
        if key not in _DMATRIX_CACHE:
            _DMATRIX_CACHE[key] = build()
        self.ptr = _DMATRIX_CACHE[key]
        return self

    def clear_cache(self):
        self.ptr = None
"""


def _find(root, **kw):
    return find_instance_clear_leaves_module_cache(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else textwrap.dedent(data).encode())
    return tmp_path


def _hits(tmp_path: Path, src: str):
    return [(f.line, f.rule) for f in _find(_corpus(tmp_path, {"m.py": src}))]


def test_reports_the_xgb_shim_clear_cache_shape(tmp_path):
    """mlframe 29f1e5c8d: clear_cache() reset the instance pointers while fit() used the module-level LRU."""
    assert _hits(tmp_path, XGB_SHIM) == [(15, RULE_CLEAR)]


def test_negative_control_clearing_the_module_cache_is_clean(tmp_path):
    assert _hits(tmp_path, XGB_SHIM + "        _DMATRIX_CACHE.clear()\n") == []


def test_release_verbs_have_their_own_rule(tmp_path):
    assert _hits(tmp_path, XGB_SHIM.replace("clear_cache", "release")) == [(15, RULE_RELEASE)]


def test_clearing_through_a_self_call_or_a_module_function_counts(tmp_path):
    via_self = XGB_SHIM.replace(
        "    def clear_cache(self):\n        self.ptr = None\n",
        "    def _drop(self):\n        _DMATRIX_CACHE.clear()\n\n    def clear_cache(self):\n        self._drop()\n",
    )
    via_function = "def _wipe():\n    _DMATRIX_CACHE.clear()\n\n" + XGB_SHIM.replace(
        "    def clear_cache(self):\n        self.ptr = None\n", "    def clear_cache(self):\n        self.ptr = None\n        _wipe()\n"
    )
    assert _hits(tmp_path / "a", via_self) == []
    assert _hits(tmp_path / "b", via_function) == []


def test_a_warn_once_set_is_not_a_cache(tmp_path):
    """pyutilz KernelTuningCache.reset: `_UNCONSTRAINED_DIM_WARNED` is process-wide on purpose and not named like a cache."""
    assert _hits(tmp_path, XGB_SHIM.replace("_DMATRIX_CACHE", "_WARNED_ONCE")) == []


def test_a_class_whose_other_methods_do_not_use_the_cache_is_not_reported(tmp_path):
    src = "_CACHE = {}\n\n\nclass A:\n    def run(self):\n        return 1\n\n    def reset(self):\n        self.x = 0\n"
    assert _hits(tmp_path, src) == []


def test_a_clear_method_that_never_touches_self_is_not_reported(tmp_path):
    src = XGB_SHIM.replace("    def clear_cache(self):\n        self.ptr = None\n", "    def clear_cache(self):\n        pass\n")
    assert _hits(tmp_path, src) == []


def test_marker_on_the_def_line_suppresses(tmp_path):
    src = XGB_SHIM.replace("def clear_cache(self):", "def clear_cache(self):  # cache-scope-ok: the LRU is shared by design")
    assert _hits(tmp_path, src) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    src = textwrap.dedent(XGB_SHIM).encode()
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
    with pytest.raises(AssertionError, match=r"clear the module-level cache"):
        assert_instance_clear_leaves_module_cache(_corpus(tmp_path / "bad", {"m.py": XGB_SHIM}), use_git=False)
    assert_instance_clear_leaves_module_cache(_corpus(tmp_path / "good", {"m.py": b"x = 1\n"}), use_git=False)


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": XGB_SHIM})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_instance_clear_leaves_module_cache(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_instance_clear_leaves_module_cache(root, baseline_path=baseline, use_git=False)
    (root / "n.py").write_text(textwrap.dedent(XGB_SHIM), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_instance_clear_leaves_module_cache(root, baseline_path=baseline, use_git=False)
