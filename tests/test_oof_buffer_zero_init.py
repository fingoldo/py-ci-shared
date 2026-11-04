"""Tests for py_ci_shared.oof_buffer_zero_init."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.oof_buffer_zero_init import RULE, assert_oof_buffer_zero_init, find_oof_buffer_zero_init

BOM = b"\xef\xbb\xbf"

VIOLATION = (
    "import numpy as np\n\n\ndef oof_predict(model, X, y, cv):\n    oof = np.zeros(len(y))\n    for tr, va in cv.split(X):\n"
    "        oof[va] = model.fit(X[tr], y[tr]).predict(X[va])\n    return oof\n"
)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_oof_buffer_zero_init(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_oof_buffer_zero_init(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 5, RULE)
    assert "'oof'" in finding.message and "np.zeros" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:5"):
        assert_oof_buffer_zero_init(root, use_git=False)


def test_a_splitter_parameter_iterated_directly_is_reported(tmp_path):
    src = VIOLATION.replace("cv.split(X)", "folds")
    assert _lines(tmp_path, src.replace("X, y, cv", "X, y, folds")) == [5]


def test_np_empty_is_reported_even_with_a_coverage_check(tmp_path):
    src = VIOLATION.replace("np.zeros", "np.empty").replace("    return oof", "    assert not np.isnan(oof).any()\n    return oof")
    assert _lines(tmp_path, src) == [5]


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    nan_init = VIOLATION.replace("np.zeros(len(y))", "np.full(len(y), np.nan)")
    assert _lines(tmp_path, nan_init) == []
    local_kfold = VIOLATION.replace("cv.split(X)", "KFold(3).split(X)")
    assert _lines(tmp_path, local_kfold) == []
    named = VIOLATION.replace("cv.split(X)", "kf.split(X)").replace("    oof =", "    kf = StratifiedKFold(3)\n    oof =")
    assert _lines(tmp_path, named) == []
    covered = VIOLATION.replace("    return oof", "    assert not np.isnan(oof).any()\n    return oof")
    assert _lines(tmp_path, covered) == []
    unrelated = VIOLATION.replace("oof", "buf")
    assert _lines(tmp_path, unrelated) == []


def test_marker_suppresses(tmp_path):
    src = VIOLATION.replace("np.zeros(len(y))", "np.zeros(len(y))  # oof-zero-ok: full partition by contract")
    assert _lines(tmp_path, src) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"m.py": BOM + VIOLATION.encode()})
    assert [f.line for f in find_oof_buffer_zero_init(root, use_git=False)] == [5]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_oof_buffer_zero_init(root, use_git=False)
    assert find_oof_buffer_zero_init(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_oof_buffer_zero_init(tmp_path, use_git=False)
