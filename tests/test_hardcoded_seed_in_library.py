"""Tests for py_ci_shared.hardcoded_seed_in_library."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.hardcoded_seed_in_library import RULE, assert_hardcoded_seed_in_library, find_hardcoded_seed_in_library

BOM = b"\xef\xbb\xbf"
VIOLATION = "from sklearn.model_selection import train_test_split\n\n\ndef split(X, random_state=None):\n    return train_test_split(X, random_state=42)\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _found(tmp_path: Path, source: str) -> list[str]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.message for f in find_hardcoded_seed_in_library(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_hardcoded_seed_in_library(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 5, RULE)
    assert finding.message == "split: random_state=42 hard-codes a seed in a function that takes its own random_state"
    with pytest.raises(AssertionError, match=r"m\.py:5"):
        assert_hardcoded_seed_in_library(root, use_git=False)


def test_negative_control_passing_the_callers_seed_is_clean(tmp_path):
    assert _found(tmp_path, VIOLATION.replace("random_state=42", "random_state=random_state")) == []


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("np.random.default_rng(7)", id="default-rng"),
        pytest.param("np.random.RandomState(7)", id="random-state"),
        pytest.param("np.random.seed(7)", id="np-seed"),
        pytest.param("random.seed(7)", id="stdlib-seed"),
        pytest.param("torch.manual_seed(7)", id="torch-manual-seed"),
        pytest.param("model(seed=7)", id="seed-keyword"),
        pytest.param("model(random_state=-1)", id="negative-literal"),
    ],
)
def test_every_seeding_shape_is_reported(tmp_path, call):
    src = f"import random\nimport numpy as np\nimport torch\n\n\ndef f(model, seed=None):\n    return {call}\n"
    assert len(_found(tmp_path, src)) == 1


def test_from_imports_are_resolved(tmp_path):
    src = "from numpy.random import default_rng as make\n\n\ndef f(rng=None):\n    return make(3)\n"
    assert len(_found(tmp_path, src)) == 1


@pytest.mark.parametrize("param", ["random_state", "seed", "rng"])
def test_each_own_parameter_name_enables_the_check(tmp_path, param):
    assert len(_found(tmp_path, f"def f(model, {param}=None):\n    return model(seed=1)\n")) == 1


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    if rng is None:\n        rng = np.random.default_rng(0)\n    return rng\n", id="if-is-none"),
        pytest.param("    if not rng:\n        rng = np.random.default_rng(0)\n    return rng\n", id="if-not"),
        pytest.param("    rng = rng or np.random.default_rng(0)\n    return rng\n", id="or-fallback"),
        pytest.param("    return np.random.default_rng(0) if rng is None else rng\n", id="ifexp-is-none"),
        pytest.param("    return rng if rng is not None else np.random.default_rng(0)\n", id="ifexp-is-not-none"),
        pytest.param("    if rng is not None:\n        return rng\n    else:\n        return np.random.default_rng(0)\n", id="else-of-is-not-none"),
    ],
)
def test_a_literal_used_only_when_the_caller_gave_no_seed_is_clean(tmp_path, body):
    assert _found(tmp_path, "import numpy as np\n\n\ndef f(rng=None):\n" + body) == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    if rng is not None:\n        return np.random.default_rng(0)\n    return None\n", id="literal-when-the-seed-IS-given"),
        pytest.param("    if other is None:\n        return np.random.default_rng(0)\n    return rng\n", id="guard-on-another-name"),
        pytest.param("    return np.random.default_rng(0) or rng\n", id="literal-first-in-or"),
    ],
)
def test_a_literal_used_when_the_caller_did_give_a_seed_is_reported(tmp_path, body):
    assert len(_found(tmp_path, "import numpy as np\n\n\ndef f(other, rng=None):\n" + body)) == 1


def test_a_function_without_its_own_seed_parameter_may_hard_code_one(tmp_path):
    assert _found(tmp_path, "def f(model):\n    return model(random_state=42)\n") == []


def test_a_non_literal_or_boolean_or_none_seed_is_not_reported(tmp_path):
    src = "def f(model, seed=None, base=3):\n    a = model(seed=base + 1)\n    b = model(seed=None)\n    c = model(seed=True)\n    return a, b, c\n"
    assert _found(tmp_path, src) == []


def test_a_nested_function_inherits_the_enclosing_seed_parameter(tmp_path):
    src = "def outer(model, seed=None):\n    def inner():\n        return model(seed=5)\n    return inner\n"
    assert _found(tmp_path, src) == ["outer.<locals>.inner: seed=5 hard-codes a seed in a function that takes its own seed"]


def test_a_method_does_not_inherit_a_class_level_scope_from_another_function(tmp_path):
    src = "class C:\n    def a(self, seed=None):\n        return seed\n\n    def b(self, model):\n        return model(seed=3)\n"
    assert _found(tmp_path, src) == []


def test_tests_and_benchmarks_are_excluded_by_default_but_not_lookalike_directories(tmp_path):
    files = {p: VIOLATION.encode() for p in ("tests/t.py", "pkg/tests/t.py", "pkg/_benchmarks/b.py", "contests/c.py", "lib/m.py")}
    root = _corpus(tmp_path, files)
    assert sorted(f.path for f in find_hardcoded_seed_in_library(root, use_git=False)) == ["contests/c.py", "lib/m.py"]
    assert len(find_hardcoded_seed_in_library(root, use_git=False, exclude=())) == 5


def test_the_suppression_comment_silences_the_statement_only(tmp_path):
    ok = VIOLATION.replace("random_state=42)", "random_state=42)  # seed-ok: fixed validation split on purpose")
    assert _found(tmp_path / "a", ok) == []
    other = VIOLATION.replace("def split(X, random_state=None):", "def split(X, random_state=None):  # seed-ok: header is not the statement")
    assert len(_found(tmp_path / "b", other)) == 1


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"m.py": VIOLATION.encode()})
    bom = _corpus(tmp_path / "bom", {"m.py": BOM + VIOLATION.encode()})
    assert find_hardcoded_seed_in_library(bom, use_git=False) == find_hardcoded_seed_in_library(plain, use_git=False) != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_hardcoded_seed_in_library(root, use_git=False)
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        assert_hardcoded_seed_in_library(root, use_git=False)
    assert find_hardcoded_seed_in_library(root, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_hardcoded_seed_in_library(tmp_path, use_git=False)
    root = _corpus(tmp_path / "one", {"a.py": b"x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_hardcoded_seed_in_library(root, min_files=2, use_git=False)
