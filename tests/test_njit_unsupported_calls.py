"""Tests for py_ci_shared.njit_unsupported_calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.njit_unsupported_calls import RULE, assert_njit_unsupported_calls, find_njit_unsupported_calls

BOM = b"\xef\xbb\xbf"

HEAD = "import numpy as np\nfrom numba import njit\n\n\n"
VIOLATION = HEAD + "@njit\ndef med(x):\n    return np.median(x)\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str, **kw) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_njit_unsupported_calls(root, use_git=False, **kw)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_njit_unsupported_calls(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 7, RULE)
    assert "numpy.median" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:7"):
        assert_njit_unsupported_calls(root, use_git=False)


def test_cast_warn_logger_and_other_decorators_are_reported(tmp_path):
    src = (
        "import logging\nimport warnings\nfrom typing import cast\nimport numba\nimport torch\n\nlogger = logging.getLogger('x')\n\n\n"
        "@numba.jit(nopython=True)\ndef a(x):\n    return cast(float, x)\n\n\n"
        "@numba.vectorize\ndef b(x):\n    warnings.warn('x')\n    return x\n\n\n"
        "@torch.jit.script\ndef c(x):\n    logger.info('x')\n    return x\n"
    )
    assert _lines(tmp_path, src) == [12, 17, 23]


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    assert _lines(tmp_path, HEAD + "def med(x):\n    return np.median(x)\n") == []
    assert _lines(tmp_path, HEAD + "@njit\ndef m(x):\n    return np.mean(x)\n") == []
    other = "import numpy as np\nfrom other import njit\n\n@njit\ndef m(x):\n    return np.median(x)\n"
    assert _lines(tmp_path, other) == []


def test_marker_suppresses_and_tables_are_configurable(tmp_path):
    marked = VIOLATION.replace("np.median(x)", "np.median(x)  # njit-ok: pinned numba supports it")
    assert _lines(tmp_path, marked) == []
    assert _lines(tmp_path, VIOLATION, unsupported={}) == []
    assert _lines(tmp_path, HEAD + "@njit\ndef m(x):\n    return np.mean(x)\n", extra={"numpy.mean": "toy"}) == [7]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"m.py": BOM + VIOLATION.encode()})
    assert [f.line for f in find_njit_unsupported_calls(root, use_git=False)] == [7]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_njit_unsupported_calls(root, use_git=False)
    assert find_njit_unsupported_calls(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_njit_unsupported_calls(tmp_path, use_git=False)
