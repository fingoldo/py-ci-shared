"""Tests for py_ci_shared.verified_then_reopened."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.verified_then_reopened import assert_verified_then_reopened, find_reopened_after_verify

BOM = b"\xef\xbb\xbf"
PRE_FIX = "import pickle\n\ndef safe_load(path):\n    verify_sidecar(path)\n    with open(path, 'rb') as handle:\n        return pickle.load(handle)\n"
POST_FIX = "import pickle\n\ndef safe_load(path):\n    payload = open(path, 'rb').read()\n    verify_bytes(payload)\n    return pickle.loads(payload)\n"


def _find(tmp_path: Path, src, **kw):
    (tmp_path / "m.py").write_bytes(src if isinstance(src, bytes) else src.encode("utf-8"))
    return find_reopened_after_verify(tmp_path, use_git=False, **kw)


def test_reports_the_seeded_violation(tmp_path):
    found = _find(tmp_path, PRE_FIX)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 5, "verified-then-reopened")]
    assert found[0].message.startswith("safe_load: verify_sidecar(path) verifies the path at line 4, then open(path) opens it again")


def test_reading_once_is_clean(tmp_path):
    assert _find(tmp_path, POST_FIX) == []


def test_other_path_sink_before_verify_and_rebinding_are_clean(tmp_path):
    other = "def f(path, other):\n    verify_sidecar(path)\n    return open(other, 'rb')\n"
    assert _find(tmp_path, other) == []
    before = "def f(path):\n    data = open(path, 'rb')\n    verify_sidecar(path)\n    return data\n"
    assert _find(tmp_path, before) == []
    rebound = "def f(path):\n    verify_sidecar(path)\n    path = fetch()\n    return open(path, 'rb')\n"
    assert _find(tmp_path, rebound) == []


def test_method_sinks_attribute_paths_and_aliases(tmp_path):
    assert [f.line for f in _find(tmp_path, "def f(p):\n    check_checksum(p)\n    return p.read_bytes()\n")] == [3]
    assert [f.line for f in _find(tmp_path, "import gzip\ndef f(self):\n    self._verify(self.path)\n    return gzip.open(self.path)\n")] == [4]
    assert [f.line for f in _find(tmp_path, "def f(p):\n    ok = verify(p)\n    return np.load(p)\n")] == [3]


def test_nested_function_bodies_are_not_mixed_into_the_outer_one(tmp_path):
    src = "def outer(path):\n    verify_sidecar(path)\n    def later():\n        return open(path)\n    return later\n"
    assert _find(tmp_path, src) == []


def test_custom_regex_and_sinks(tmp_path):
    src = "def f(path):\n    audit(path)\n    return slurp(path)\n"
    assert _find(tmp_path, src) == []
    assert [f.line for f in _find(tmp_path, src, verify_name_regex="audit", sink_names={"slurp"})] == [3]


def test_bom_and_crlf_are_reported_like_the_plain_file(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    assert [f.line for f in _find(tmp_path / "b", BOM + PRE_FIX.encode())] == [5]
    assert [f.line for f in _find(tmp_path / "c", PRE_FIX.replace("\n", "\r\n").encode())] == [5]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    (tmp_path / "ok.py").write_text(PRE_FIX, encoding="utf-8")
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_reopened_after_verify(tmp_path, use_git=False)
    assert len(find_reopened_after_verify(tmp_path, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_reopened_after_verify(tmp_path, use_git=False)


def test_assert_and_baseline(tmp_path):
    (tmp_path / "m.py").write_text(PRE_FIX, encoding="utf-8")
    with pytest.raises(AssertionError, match="safe_load"):
        assert_verified_then_reopened(tmp_path, use_git=False)
    base = tmp_path / "base.json"
    found = find_reopened_after_verify(tmp_path, use_git=False)
    base.write_text(
        json.dumps({"schema": 1, "gate": "verified_then_reopened", "entries": {f.key: {"count": 1, "note": "ok"} for f in found}}), encoding="utf-8"
    )
    assert_verified_then_reopened(tmp_path, use_git=False, baseline_path=base)
    (tmp_path / "n.py").write_text(PRE_FIX, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"n\.py"):
        assert_verified_then_reopened(tmp_path, use_git=False, baseline_path=base)


def test_a_size_check_is_not_verification_so_its_reopen_is_clean(tmp_path):
    truncate = "import os\n\ndef truncate_to(path, size):\n    verify_size(path, size)\n    with open(path, 'r+b') as handle:\n        handle.truncate(size)\n"
    assert _find(tmp_path, truncate) == []


def test_a_reopen_marked_with_a_reason_is_accepted(tmp_path):
    marked = PRE_FIX.replace("with open(path, 'rb') as handle:", "with open(path, 'rb') as handle:  # reopen-ok: sidecar is a size only")
    assert _find(tmp_path, marked) == []


def test_a_reopen_marker_without_a_reason_does_not_suppress(tmp_path):
    bare = PRE_FIX.replace("with open(path, 'rb') as handle:", "with open(path, 'rb') as handle:  # reopen-ok:")
    assert [f.line for f in _find(tmp_path, bare)] == [5]
