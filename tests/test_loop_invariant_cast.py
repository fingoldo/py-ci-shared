"""Tests for py_ci_shared.loop_invariant_cast."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.loop_invariant_cast import RULE, assert_loop_invariant_cast, find_loop_invariant_cast

BOM = b"\xef\xbb\xbf"

VIOLATION = "def score_all(baselines, y):\n    for name in baselines:\n        y_int = y.astype('int64')\n        print(name, y_int)\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_loop_invariant_cast(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_loop_invariant_cast(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 3, RULE)
    assert "y.astype" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:3"):
        assert_loop_invariant_cast(root, use_git=False)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    for name in names:\n        x_int = np.ascontiguousarray(x, dtype=np.int64)\n        print(name, x_int)\n", id="ascontiguousarray"),
        pytest.param("    for name in names:\n        x_int = np.asarray(x)\n        print(name, x_int)\n", id="asarray"),
        pytest.param("    for name in names:\n        x_int = np.array(x, dtype=np.int64)\n        print(name, x_int)\n", id="array"),
        pytest.param("    while names:\n        x_int = x.copy()\n        print(x_int)\n        names.pop()\n", id="method-copy-in-while"),
        pytest.param(
            "    for name in names:\n        for inner in range(3):\n            x_int = x.astype('float64')\n            print(name, inner, x_int)\n",
            id="nested-loop",
        ),
    ],
)
def test_the_cast_shapes_are_reported(tmp_path, body):
    assert _lines(tmp_path, "import numpy as np\n\n\ndef f(x, names):\n" + body) != []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    x_int = x.astype('int64')\n    for name in names:\n        print(name, x_int)\n", id="already-hoisted"),
        pytest.param("    for x in names:\n        x_int = x.astype('int64')\n        print(x_int)\n", id="name-is-the-loop-target"),
        pytest.param("    for name in names:\n        x = x + 1\n        x_int = x.astype('int64')\n        print(name, x_int)\n", id="reassigned-in-function"),
        pytest.param(
            "    for name in names:\n        x_int = x.astype('int64')\n        x_int[0] = 1\n        print(name, x_int)\n",
            id="subscript-mutated-private-scratch",
        ),
        pytest.param(
            "    for name in names:\n        x_int = x.copy()\n        rng.shuffle(x_int)\n        print(name, x_int)\n",
            id="shuffle-mutated-private-scratch",
        ),
        pytest.param(
            "    import copy as _cp\n    for name in names:\n        y = _cp.copy(x)\n        print(name, y)\n",
            id="module-aliased-copy-not-a-method",
        ),
        pytest.param("    for name in names:\n        print(name, x.astype('int64'))\n", id="not-assigned-to-a-name"),
    ],
)
def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path, body):
    assert _lines(tmp_path, "import numpy as np\n\n\ndef f(x, names, rng=None):\n" + body) == []


def test_pandas_deep_kwarg_copy_is_still_reported(tmp_path):
    """``.copy(deep=True)`` takes no POSITIONAL arg (``deep`` is keyword-only by convention), so the
    positional-arg guard that excludes ``copy.copy(x)``-style namespace calls does not exclude this -- a
    pandas ``.copy(deep=True)`` inside a loop is still a genuine redundant-copy candidate."""
    body = "    for name in names:\n        x_int = x.copy(deep=True)\n        print(name, x_int)\n"
    assert _lines(tmp_path, "def f(x, names):\n" + body) != []


def test_the_suppression_comment_on_the_statement_silences_it(tmp_path):
    suppressed = VIOLATION.replace("y.astype('int64')\n", "y.astype('int64')  # loop-invariant-ok: deliberate per-iteration re-copy\n")
    assert _lines(tmp_path, suppressed) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"m.py": VIOLATION.encode()})
    bom = _corpus(tmp_path / "bom", {"m.py": BOM + VIOLATION.encode()})
    assert find_loop_invariant_cast(bom, use_git=False) == find_loop_invariant_cast(plain, use_git=False) != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_loop_invariant_cast(root, use_git=False)
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        assert_loop_invariant_cast(root, use_git=False)
    assert find_loop_invariant_cast(root, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_loop_invariant_cast(tmp_path, use_git=False)
    root = _corpus(tmp_path / "two", {"a.py": b"x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_loop_invariant_cast(root, min_files=2, use_git=False)
    assert find_loop_invariant_cast(root, min_files=1, use_git=False) == []


def test_exclude_skips_paths_by_fragment(tmp_path):
    root = _corpus(tmp_path, {"bench/m.py": VIOLATION.encode(), "lib/m.py": VIOLATION.encode()})
    found = find_loop_invariant_cast(root, use_git=False, exclude=("bench/",))
    assert [f.path for f in found] == ["lib/m.py"]


def test_a_baseline_accepts_the_known_finding_and_rejects_a_new_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": VIOLATION.encode()})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.fail.Exception):
        assert_loop_invariant_cast(root, baseline_path=baseline, use_git=False)
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten"):
        assert_loop_invariant_cast(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_loop_invariant_cast(root, baseline_path=baseline, use_git=False)
    (root / "n.py").write_bytes(VIOLATION.encode())
    with pytest.raises(pytest.fail.Exception):
        assert_loop_invariant_cast(root, baseline_path=baseline, use_git=False)
