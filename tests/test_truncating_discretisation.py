"""Tests for py_ci_shared.truncating_discretisation."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.truncating_discretisation import RULE, assert_truncating_discretisation, find_truncating_discretisation

BOM = b"\xef\xbb\xbf"

VIOLATION = "import numpy as np\n\n\ndef prep(y):\n    if len(np.unique(y)) <= 32:\n        y = y.astype(np.int64)\n    return y\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_truncating_discretisation(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_truncating_discretisation(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 6, RULE)
    with pytest.raises(AssertionError, match=r"m\.py:6"):
        assert_truncating_discretisation(root, use_git=False)


@pytest.mark.parametrize(
    "test, cast",
    [("y.nunique() <= 32", "y.astype(int)"), ("np.unique(y).size < 10", 'y.astype("int32")'), ("y.nunique() <= 8", "y.astype(np.uint8)")],
)
def test_other_spellings_are_reported(tmp_path, test, cast):
    src = f"def prep(y):\n    if {test}:\n        z = {cast}\n    return z\n"
    assert _lines(tmp_path, src) == [3]


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    checked = VIOLATION.replace("<= 32:", "<= 32 and np.all(y == np.round(y)):")
    assert _lines(tmp_path, checked) == []
    dtype_guard = VIOLATION.replace("<= 32:", "<= 32 and np.issubdtype(y.dtype, np.integer):")
    assert _lines(tmp_path, dtype_guard) == []
    not_distinct = VIOLATION.replace("len(np.unique(y)) <= 32", "len(y) <= 32")
    assert _lines(tmp_path, not_distinct) == []
    other_array = VIOLATION.replace("y = y.astype", "y = w.astype")
    assert _lines(tmp_path, other_array) == []
    float_cast = VIOLATION.replace("np.int64", "np.float64")
    assert _lines(tmp_path, float_cast) == []


def test_marker_suppresses(tmp_path):
    src = VIOLATION.replace("np.int64)\n", "np.int64)  # truncating-ok: labels\n")
    assert _lines(tmp_path, src) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"m.py": BOM + VIOLATION.encode()})
    assert [f.line for f in find_truncating_discretisation(root, use_git=False)] == [6]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_truncating_discretisation(root, use_git=False)
    assert find_truncating_discretisation(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_truncating_discretisation(tmp_path, use_git=False)
