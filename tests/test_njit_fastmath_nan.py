"""Tests for py_ci_shared.njit_fastmath_nan."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.njit_fastmath_nan import RULE, assert_njit_fastmath_nan, find_njit_fastmath_nan

BOM = b"\xef\xbb\xbf"

HEAD = "import numpy as np\nfrom numba import njit\n"
VIOLATION = HEAD + "\n\n@njit(fastmath=True)\ndef f(x):\n    return np.nan if np.isnan(x).any() else 0.0\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _hits(tmp_path: Path, source: str) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_njit_fastmath_nan(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_njit_fastmath_nan(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 6, RULE)
    assert "'f'" in finding.message
    with pytest.raises(AssertionError, match=r"m.py:6"):
        assert_njit_fastmath_nan(root, use_git=False)


@pytest.mark.parametrize(
    "decorator",
    ["@njit(fastmath=True)", '@njit(fastmath={"nnan", "nsz"})', '@njit(fastmath={"fast"})', "@njit(cache=True, fastmath=_FULL)"],
)
def test_every_full_fastmath_spelling_is_reported(tmp_path, decorator):
    src = HEAD + '_FULL = {"nnan"}\n\n' + decorator + "\ndef f(x):\n    return x != x\n"
    assert _hits(tmp_path, src) == [6]


def test_module_constant_naming_a_safe_set_is_not_reported(tmp_path):
    src = HEAD + '_SAFE = {"nsz", "arcp", "contract", "afn", "reassoc"}\n\n@njit(fastmath=_SAFE)\ndef f(x):\n    return np.isnan(x)\n'
    assert _hits(tmp_path, src) == []


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    assert _hits(tmp_path, HEAD + "\n@njit(fastmath=True)\ndef f(x):\n    return x + 1.0\n") == []
    assert _hits(tmp_path, HEAD + "\n@njit\ndef f(x):\n    return np.isnan(x)\n") == []
    assert _hits(tmp_path, HEAD + "\n@njit(fastmath=False)\ndef f(x):\n    return np.isnan(x)\n") == []


def test_marker_on_decorator_or_def_line_suppresses(tmp_path):
    src = HEAD + "\n@njit(fastmath=True)  # fastmath-nan-ok: inputs prefiltered\ndef f(x):\n    return np.isnan(x)\n"
    assert _hits(tmp_path, src) == []


def test_non_numba_njit_is_ignored(tmp_path):
    assert _hits(tmp_path, "from other import njit\n\n@njit(fastmath=True)\ndef f(x):\n    return x != x\n") == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"m.py": BOM + VIOLATION.encode()})
    assert [f.line for f in find_njit_fastmath_nan(root, use_git=False)] == [6]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_njit_fastmath_nan(root, use_git=False)
    assert find_njit_fastmath_nan(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_njit_fastmath_nan(tmp_path, use_git=False)
