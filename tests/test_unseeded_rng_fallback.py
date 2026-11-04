"""Tests for py_ci_shared.unseeded_rng_fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.unseeded_rng_fallback import RULE, assert_unseeded_rng_fallback, find_unseeded_rng_fallback

BOM = b"\xef\xbb\xbf"

VIOLATION = "import numpy as np\n\n\ndef pick_folds(n, rng=None):\n    if rng is None:\n        rng = np.random.default_rng()\n    return rng.permutation(n)\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str, **kw) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_unseeded_rng_fallback(root, use_git=False, **kw)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_unseeded_rng_fallback(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 6, RULE)
    assert "pick_folds" in finding.message and "numpy.random.default_rng" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:6"):
        assert_unseeded_rng_fallback(root, use_git=False)


@pytest.mark.parametrize(
    "header, call",
    [
        ("import numpy as np", "np.random.default_rng()"),
        ("import numpy as numpy_alias", "numpy_alias.random.default_rng()"),
        ("from numpy.random import default_rng", "default_rng()"),
        ("from numpy.random import default_rng as mk", "mk(None)"),
        ("from numpy.random import default_rng", "default_rng(seed=None)"),
        ("import numpy as np", "np.random.RandomState()"),
        ("import random", "random.Random()"),
        ("from random import Random", "Random(None)"),
        ("import numpy as np", "np.random.seed()"),
        ("import random", "random.seed()"),
    ],
)
def test_every_entropy_spelling_and_alias_is_reported(tmp_path, header, call):
    src = f"{header}\n\n\ndef f():\n    return {call}\n"
    assert _lines(tmp_path, src) == [5]


@pytest.mark.parametrize(
    "header, call",
    [
        ("import numpy as np", "np.random.default_rng(seed)"),
        ("import numpy as np", "np.random.default_rng(np.random.SeedSequence(seed))"),
        ("import numpy as np", "np.random.default_rng(seed=seed)"),
        ("import numpy as np", "np.random.default_rng(*args)"),
        ("import numpy as np", "np.random.RandomState(0)"),
        ("import random", "random.Random(seed)"),
        ("import numpy as np", "np.random.seed(seed)"),
        ("import numpy as np", "other.default_rng()"),
    ],
)
def test_negative_control_seeded_and_unrelated_calls_are_not_reported(tmp_path, header, call):
    src = f"{header}\n\n\ndef f(seed=0, args=()):\n    return {call}\n"
    assert _lines(tmp_path, src) == []


def test_module_level_calls_are_not_reported(tmp_path):
    assert _lines(tmp_path, "import numpy as np\n\nRNG = np.random.default_rng()\n") == []


def test_one_finding_per_call_in_a_nested_function(tmp_path):
    src = "import numpy as np\n\n\ndef outer():\n    def inner():\n        return np.random.default_rng()\n    return inner\n"
    assert _lines(tmp_path, src) == [6]


def test_marker_on_the_line_or_the_line_above_suppresses(tmp_path):
    same = VIOLATION.replace("default_rng()", "default_rng()  # rng-entropy-ok: jitter")
    assert _lines(tmp_path, same) == []
    above = VIOLATION.replace("        rng = np.random", "        # rng-entropy-ok: jitter\n        rng = np.random")
    assert _lines(tmp_path, above) == []
    wrong_line = VIOLATION.replace("    if rng is None:", "    # rng-entropy-ok: two lines up\n    if rng is None:")
    assert _lines(tmp_path, wrong_line) == [7]


def test_allowed_entry_function_pattern_and_exclude(tmp_path):
    assert _lines(tmp_path, VIOLATION, allowed={"m.py::pick_folds": "entropy by design"}) == []
    assert _lines(tmp_path, VIOLATION, allowed={"m.py::other": "x"}) == [6]
    assert _lines(tmp_path, VIOLATION, allowed_functions=["pick_*"]) == []
    assert _lines(tmp_path, VIOLATION, allowed_functions=["draw_*"]) == [6]
    assert _lines(tmp_path, VIOLATION, exclude=["m.py"]) == []
    assert _lines(tmp_path, VIOLATION, exclude=["bench"]) == [6]


def test_custom_constructors(tmp_path):
    src = "import mylib\n\n\ndef f():\n    return mylib.make_rng()\n"
    assert _lines(tmp_path, src) == []
    assert _lines(tmp_path, src, constructors={"mylib.make_rng"}) == [5]


def test_a_bom_prefixed_and_a_crlf_file_are_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"bom.py": BOM + VIOLATION.encode(), "crlf.py": VIOLATION.replace("\n", "\r\n").encode()})
    assert sorted((f.path, f.line) for f in find_unseeded_rng_fallback(root, use_git=False)) == [("bom.py", 6), ("crlf.py", 6)]


def test_a_crlf_file_still_honours_the_marker(tmp_path):
    src = VIOLATION.replace("default_rng()", "default_rng()  # rng-entropy-ok: jitter").replace("\n", "\r\n")
    root = _corpus(tmp_path, {"m.py": src.encode()})
    assert find_unseeded_rng_fallback(root, use_git=False) == []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_unseeded_rng_fallback(root, use_git=False)
    assert find_unseeded_rng_fallback(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_unseeded_rng_fallback(tmp_path, use_git=False)


def test_baseline_accepts_known_findings_and_rejects_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": VIOLATION.encode()})
    base = tmp_path / "baseline.json"
    with pytest.raises(pytest.fail.Exception):
        assert_unseeded_rng_fallback(root, use_git=False, baseline_path=base)
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten"):
        assert_unseeded_rng_fallback(root, use_git=False, baseline_path=base, refresh=True)
    assert_unseeded_rng_fallback(root, use_git=False, baseline_path=base)
    (root / "n.py").write_bytes(VIOLATION.encode())
    with pytest.raises(pytest.fail.Exception):
        assert_unseeded_rng_fallback(root, use_git=False, baseline_path=base)
