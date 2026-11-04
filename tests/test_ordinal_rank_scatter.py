"""Tests for py_ci_shared.ordinal_rank_scatter."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.ordinal_rank_scatter import RULE, assert_ordinal_rank_scatter, find_ordinal_rank_scatter

BOM = b"\xef\xbb\xbf"

VIOLATION = (
    "import numpy as np\n\n\ndef f(x):\n    order = np.argsort(x, kind='mergesort')\n    ranks = np.empty(len(x))\n"
    "    ranks[order] = np.arange(len(x))\n    return ranks\n"
)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_ordinal_rank_scatter(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_ordinal_rank_scatter(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 7, RULE)
    assert "'ranks'" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:7"):
        assert_ordinal_rank_scatter(root, use_git=False)


def test_direct_argsort_index_and_arange_arithmetic_are_reported(tmp_path):
    src = "import numpy as np\n\n\ndef f(x):\n    r = np.empty(len(x))\n    r[np.argsort(x)] = np.arange(len(x)) + 1.0\n    return r\n"
    assert _lines(tmp_path, src) == [6]


def test_nested_function_is_reported_once(tmp_path):
    src = "import numpy as np\n\n\ndef outer(x):\n    def inner(y):\n        o = np.argsort(y)\n        rk = np.empty(len(y))\n        rk[o] = np.arange(len(y))\n        return rk\n    return inner(x)\n"
    assert _lines(tmp_path, src) == [8]


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    average = "from scipy.stats import rankdata\n\n\ndef f(x):\n    return rankdata(x)\n"
    assert _lines(tmp_path, average) == []
    tie_aware = VIOLATION.replace("def f(x):", "def f(x):\n    # average the tied ranks afterwards\n    _ = np.unique(x)")
    assert _lines(tmp_path, tie_aware) == []
    not_rank = VIOLATION.replace("ranks", "slots")
    assert _lines(tmp_path, not_rank) == []
    not_arange = VIOLATION.replace("np.arange(len(x))", "np.zeros(len(x))")
    assert _lines(tmp_path, not_arange) == []


def test_marker_suppresses_a_deliberate_ordinal_tiebreak(tmp_path):
    src = VIOLATION.replace("np.arange(len(x))\n", "np.arange(len(x))  # ordinal-rank-ok: stable tiebreak wanted\n")
    assert _lines(tmp_path, src) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"m.py": BOM + VIOLATION.encode()})
    assert [f.line for f in find_ordinal_rank_scatter(root, use_git=False)] == [7]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_ordinal_rank_scatter(root, use_git=False)
    assert find_ordinal_rank_scatter(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_ordinal_rank_scatter(tmp_path, use_git=False)
