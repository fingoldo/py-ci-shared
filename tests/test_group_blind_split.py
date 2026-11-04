"""Tests for py_ci_shared.group_blind_split."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.group_blind_split import RULE, assert_group_blind_split, find_group_blind_split

BOM = b"\xef\xbb\xbf"

VIOLATION = "from sklearn.model_selection import KFold\n\n\ndef make_folds(X, y, groups):\n    return list(KFold(5).split(X))\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str, **kw) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_group_blind_split(root, use_git=False, **kw)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_group_blind_split(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 5, RULE)
    assert "make_folds" in finding.message and "KFold" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:5"):
        assert_group_blind_split(root, use_git=False)


def test_train_test_split_in_a_branch_that_ignores_groups_is_reported(tmp_path):
    src = (
        "from sklearn.model_selection import train_test_split\n\n\ndef f(X, y, groups, val_size):\n"
        "    if isinstance(val_size, float):\n        return train_test_split(X, y, test_size=val_size)\n    return None\n"
    )
    assert _lines(tmp_path, src) == [6]


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    fallback = (
        "from sklearn.model_selection import GroupKFold, KFold\n\n\ndef f(X, y, groups):\n    if groups is None:\n        return KFold(5)\n"
        "    return GroupKFold(5)\n"
    )
    assert _lines(tmp_path, fallback) == []
    passes = "from sklearn.model_selection import train_test_split\n\n\ndef f(X, y, groups):\n    return train_test_split(X, groups, y)\n"
    assert _lines(tmp_path, passes) == []
    keyword = "def f(X, y, groups):\n    return train_test_split(X, y, groups=groups)\n"
    assert _lines(tmp_path, keyword) == []
    no_groups = VIOLATION.replace(", groups", "")
    assert _lines(tmp_path, no_groups) == []


def test_marker_and_allowed_entry_suppress(tmp_path):
    marked = VIOLATION.replace("KFold(5).split(X))", "KFold(5).split(X))  # group-blind-ok: diagnostic folds")
    assert _lines(tmp_path, marked) == []
    assert _lines(tmp_path, VIOLATION, allowed={"m.py::make_folds": "diagnostic folds"}) == []


def test_custom_parameter_and_splitter_names(tmp_path):
    src = "def f(X, patient):\n    return MySplit(3)\n"
    assert _lines(tmp_path, src) == []
    assert _lines(tmp_path, src, group_params={"patient"}, splitters={"MySplit"}) == [2]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"m.py": BOM + VIOLATION.encode()})
    assert [f.line for f in find_group_blind_split(root, use_git=False)] == [5]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_group_blind_split(root, use_git=False)
    assert find_group_blind_split(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_group_blind_split(tmp_path, use_git=False)
