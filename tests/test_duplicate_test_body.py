"""Tests for py_ci_shared.duplicate_test_body."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.duplicate_test_body import assert_no_duplicate_test_bodies, find_duplicate_test_bodies

BOM = b"\xef\xbb\xbf"

FIRST = (
    "def test_prev_zero_returns_true():\n"
    "    result = keys_changed_enough({'x': 5}, {'x': 0}, threshold=0.5, strict=True)\n"
    "    assert result is True\n"
    "    assert keys_changed_enough({'x': 5}, {'x': 0}, threshold=0.5, strict=False) is True\n"
)
SECOND = FIRST.replace("test_prev_zero_returns_true", "test_zero_to_nonzero_is_changed").replace(
    ":\n    result", ':\n    """Docstrings are ignored."""\n    result', 1
)
TWINS = FIRST + "\n" + SECOND


def _corpus(tmp_path: Path, files: dict) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return tmp_path


def _find(tmp_path: Path, src: str, **kw):
    _corpus(tmp_path, {"test_a.py": src})
    return find_duplicate_test_bodies(tmp_path, use_git=False, **kw)


def test_reports_the_seeded_violation(tmp_path):
    found = _find(tmp_path, TWINS)
    assert [(f.path, f.line, f.rule) for f in found] == [("test_a.py", 6, "duplicate-test-body")]
    assert found[0].message.startswith("test_zero_to_nonzero_is_changed repeats the body, decorators and signature of test_prev_zero_returns_true (line 1)")


def test_one_changed_literal_is_not_a_twin(tmp_path):
    assert _find(tmp_path, FIRST + "\n" + SECOND.replace("strict=True", "strict=None")) == []


def test_a_different_decorator_or_signature_is_not_a_twin(tmp_path):
    assert _find(tmp_path, FIRST + "\n@pytest.mark.slow\n" + SECOND) == []
    assert _find(tmp_path, FIRST + "\n" + SECOND.replace("_changed():", "_changed(tmp_path):")) == []


def test_non_test_functions_and_short_bodies_are_ignored(tmp_path):
    assert _find(tmp_path, TWINS.replace("def test_", "def helper_")) == []
    short = "def test_a():\n    assert f(1)\n\ndef test_b():\n    assert f(1)\n"
    assert _find(tmp_path, short) == []
    assert [f.line for f in _find(tmp_path, short, min_dump_chars=10)] == [4]


def _indented(src: str) -> str:
    return "".join("    " + line if line.strip() else line for line in src.splitlines(True))


def test_scope_is_the_class_and_the_file(tmp_path):
    body = _indented(FIRST)
    assert _find(tmp_path, "class A:\n" + body + "\nclass B:\n" + body) == []
    same = "class A:\n" + body + "\n" + body.replace("test_prev", "test_other") + "\n"
    assert [f.line for f in _find(tmp_path, same)] == [7]
    _corpus(tmp_path, {"test_a.py": FIRST, "test_b.py": FIRST})
    assert find_duplicate_test_bodies(tmp_path, use_git=False) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _find(tmp_path / "p", TWINS)
    _corpus(tmp_path / "b", {"test_a.py": BOM + TWINS.encode()})
    assert find_duplicate_test_bodies(tmp_path / "b", use_git=False) == plain != []


def test_crlf_source_gives_the_same_finding(tmp_path):
    _corpus(tmp_path, {"test_a.py": TWINS.replace("\n", "\r\n").encode()})
    assert [f.line for f in find_duplicate_test_bodies(tmp_path, use_git=False)] == [6]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _corpus(tmp_path, {"test_a.py": TWINS, "broken.py": "def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_duplicate_test_bodies(tmp_path, use_git=False)
    assert len(find_duplicate_test_bodies(tmp_path, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_duplicate_test_bodies(tmp_path, use_git=False)


def test_assert_names_the_twin_and_a_baseline_accepts_it(tmp_path):
    _corpus(tmp_path, {"test_a.py": TWINS})
    with pytest.raises(AssertionError, match="test_zero_to_nonzero_is_changed"):
        assert_no_duplicate_test_bodies(tmp_path, use_git=False)
    base = tmp_path / "base.json"
    found = find_duplicate_test_bodies(tmp_path, use_git=False)
    base.write_text(
        json.dumps({"schema": 1, "gate": "duplicate_test_body", "entries": {f.key: {"count": 1, "note": "accepted"} for f in found}}), encoding="utf-8"
    )
    assert_no_duplicate_test_bodies(tmp_path, use_git=False, baseline_path=base)
    _corpus(tmp_path, {"test_b.py": TWINS})
    with pytest.raises(pytest.fail.Exception, match=r"test_b.py"):
        assert_no_duplicate_test_bodies(tmp_path, use_git=False, baseline_path=base)
