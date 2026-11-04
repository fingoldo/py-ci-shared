"""Tests for py_ci_shared.ml_api_contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.ml_api_contracts import RULES, assert_ml_api_contracts, find_ml_api_contracts

BOM = b"\xef\xbb\xbf"

VIOLATION = (
    "class Est(BaseEstimator):\n    def __init__(self, depth=3):\n        self.depth = max(1, int(depth))\n\n\n"
    "def refit(model, y_true, proba):\n    n_estimators = model.best_iteration\n    return n_estimators, proba.argmax(axis=1) == y_true\n"
)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _hits(tmp_path: Path, source: str) -> list[tuple[int, str]]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [(f.line, f.rule) for f in find_ml_api_contracts(root, use_git=False)]


def test_reports_each_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    found = find_ml_api_contracts(root, use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 3, RULES[2]), ("m.py", 7, RULES[0]), ("m.py", 8, RULES[1])]
    with pytest.raises(AssertionError, match=r"m\.py:7"):
        assert_ml_api_contracts(root, use_git=False)


def test_keyword_and_catboost_getter_forms_are_reported(tmp_path):
    src = "def f(m):\n    return fit(iterations=m.get_best_iteration(), num_boost_round=m.best_iteration)\n"
    assert _hits(tmp_path, src) == [(2, RULES[0])]


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    plus_one = "def f(m):\n    n_estimators = m.best_iteration + 1\n    return n_estimators\n"
    assert _hits(tmp_path, plus_one) == []
    lightgbm = "def f(m):\n    n_estimators = m.best_iteration_\n    return n_estimators\n"
    assert _hits(tmp_path, lightgbm) == []
    other_name = "def f(m):\n    stop_at = m.best_iteration\n    return stop_at\n"
    assert _hits(tmp_path, other_name) == []
    mapped = "def f(proba, y_true, classes):\n    return classes[proba.argmax(axis=1)] == y_true\n"
    assert _hits(tmp_path, mapped) == []
    other = "def f(proba, y_pred):\n    return proba.argmax(axis=1) == y_pred\n"
    assert _hits(tmp_path, other) == []
    plain = "class Est(BaseEstimator):\n    def __init__(self, depth=3):\n        self.depth = depth\n        self.cache_ = {}\n"
    assert _hits(tmp_path, plain) == []
    not_estimator = "class Box:\n    def __init__(self, depth=3):\n        self.depth = int(depth)\n"
    assert _hits(tmp_path, not_estimator) == []


def test_marker_suppresses(tmp_path):
    src = "def f(m):\n    n_estimators = m.best_iteration  # ml-api-ok: already converted upstream\n"
    assert _hits(tmp_path, src) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"m.py": BOM + VIOLATION.encode()})
    assert len(find_ml_api_contracts(root, use_git=False)) == 3


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_ml_api_contracts(root, use_git=False)
    assert find_ml_api_contracts(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_ml_api_contracts(tmp_path, use_git=False)
