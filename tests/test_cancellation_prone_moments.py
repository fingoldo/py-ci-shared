"""Tests for py_ci_shared.cancellation_prone_moments."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cancellation_prone_moments import RULE, assert_cancellation_prone_moments, find_cancellation_prone_moments

BOM = b"\xef\xbb\xbf"

VIOLATION = (
    "def variance(values):\n"
    "    s1 = 0.0\n"
    "    s2 = 0.0\n"
    "    for v in values:\n"
    "        s1 += v\n"
    "        s2 += v * v\n"
    "    n = len(values)\n"
    "    return s2 / n - (s1 / n) ** 2\n"
)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_cancellation_prone_moments(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_cancellation_prone_moments(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 8, RULE)
    assert "s2 / n - (s1 / n) ** 2" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:8"):
        assert_cancellation_prone_moments(root, use_git=False)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    return np.mean(x**2) - np.mean(x) ** 2\n", id="numpy-one-liner"),
        pytest.param("    return (x**2).mean() - x.mean() ** 2\n", id="method-one-liner"),
        pytest.param("    n = len(x)\n    m = np.sum(x) / n\n    return (np.sum(x * x) - n * m * m) / (n - 1)\n", id="n-mean-squared"),
        pytest.param("    s1 = x.sum()\n    s2 = (x**2).sum()\n    return (s2 - s1 * s1 / len(x)) / (len(x) - 1)\n", id="s1-squared-over-n"),
        pytest.param("    n = len(x)\n    m = np.mean(x)\n    return np.sum(x**3) - 3 * m * np.sum(x**2) + 2 * n * m**3\n", id="third-moment-expansion"),
        pytest.param("    s = np.dot(x, x)\n    t = np.sum(x)\n    return s / len(x) - (t / len(x)) ** 2\n", id="dot-power-sum"),
    ],
)
def test_the_raw_power_sum_shapes_are_reported(tmp_path, body):
    assert _lines(tmp_path, "import numpy as np\n\n\ndef f(x):\n" + body) != []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    return np.sum((x - x.mean()) ** 2) / len(x)\n", id="centred-two-pass"),
        pytest.param("    m = x.mean()\n    d = x - m\n    return np.sum(d * d) / len(x)\n", id="centred-by-name"),
        pytest.param(
            "    m = 0.0\n    m2 = 0.0\n    for k, v in enumerate(x):\n        d = v - m\n        m += d / (k + 1)\n        m2 += d * (v - m)\n    return m2\n",
            id="welford",
        ),
        pytest.param("    return np.var(x) + np.std(x) ** 2\n", id="numpy-var-std"),
        pytest.param("    s2 = np.sum(x * x)\n    return s2 - 1.0\n", id="power-sum-minus-a-constant"),
        pytest.param("    a = np.sum(x)\n    b = np.sum(x)\n    return a**2 - b**2\n", id="no-power-sum-at-all"),
        pytest.param(
            "    a = np.sum(x * x)\n    b = np.sum(x * y)\n    c = np.sum(y * y)\n    return a * c - b * b\n", id="two-by-two-determinant-of-distinct-sums"
        ),
    ],
)
def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path, body):
    assert _lines(tmp_path, "import numpy as np\n\n\ndef f(x, y=None):\n" + body) == []


def test_the_accumulator_form_needs_both_a_power_sum_and_a_squared_mean(tmp_path):
    only_squares = "def f(v):\n    s2 = 0.0\n    for a in v:\n        s2 += a * a\n    return s2 / len(v)\n"
    assert _lines(tmp_path, only_squares) == []


def test_each_function_is_judged_on_its_own_accumulators(tmp_path):
    src = "def a(v):\n    s2 = 0.0\n    for x in v:\n        s2 += x * x\n    return s2\n\n\ndef b(s1, s2, n):\n    return s2 / n - (s1 / n) ** 2\n"
    assert _lines(tmp_path, src) == []


def test_the_suppression_comment_on_the_statement_silences_it(tmp_path):
    suppressed = VIOLATION.replace("(s1 / n) ** 2\n", "(s1 / n) ** 2  # moment-ok: z-scored column, offset is zero\n")
    assert _lines(tmp_path, suppressed) == []
    other_line = VIOLATION.replace("    n = len(values)\n", "    n = len(values)  # moment-ok: not this statement\n")
    assert _lines(tmp_path, other_line) == [8]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"m.py": VIOLATION.encode()})
    bom = _corpus(tmp_path / "bom", {"m.py": BOM + VIOLATION.encode()})
    assert find_cancellation_prone_moments(bom, use_git=False) == find_cancellation_prone_moments(plain, use_git=False) != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_cancellation_prone_moments(root, use_git=False)
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        assert_cancellation_prone_moments(root, use_git=False)
    assert find_cancellation_prone_moments(root, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_cancellation_prone_moments(tmp_path, use_git=False)
    root = _corpus(tmp_path / "two", {"a.py": b"x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_cancellation_prone_moments(root, min_files=2, use_git=False)
    assert find_cancellation_prone_moments(root, min_files=1, use_git=False) == []


def test_exclude_skips_paths_by_fragment(tmp_path):
    root = _corpus(tmp_path, {"bench/m.py": VIOLATION.encode(), "lib/m.py": VIOLATION.encode()})
    found = find_cancellation_prone_moments(root, use_git=False, exclude=("bench/",))
    assert [f.path for f in found] == ["lib/m.py"]


def test_a_baseline_accepts_the_known_finding_and_rejects_a_new_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": VIOLATION.encode()})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.fail.Exception):
        assert_cancellation_prone_moments(root, baseline_path=baseline, use_git=False)
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten"):
        assert_cancellation_prone_moments(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_cancellation_prone_moments(root, baseline_path=baseline, use_git=False)
    (root / "n.py").write_bytes(VIOLATION.encode())
    with pytest.raises(pytest.fail.Exception):
        assert_cancellation_prone_moments(root, baseline_path=baseline, use_git=False)
