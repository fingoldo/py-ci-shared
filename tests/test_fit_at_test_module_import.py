"""fit_at_test_module_import (audit 50-T5): a test module must not train a model while it is being imported.

Every CI shard imports every test module to collect it, so one full fit at module level costs every shard that fit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import UnparsedFilesError
from py_ci_shared.fit_at_test_module_import import (
    DEFAULT_HEAVY_CALLS,
    RULE,
    assert_no_fit_at_test_module_import,
    find_fit_at_test_module_import,
)


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))


def _rules(root: Path, **kw: object) -> list[tuple[str, int]]:
    return [(f.path, f.line) for f in find_fit_at_test_module_import(root, **kw)]  # type: ignore[arg-type]


def test_module_level_fit_is_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_model.py",
        "from sklearn.linear_model import LogisticRegression\n"
        "import numpy as np\n"
        "MODEL = LogisticRegression().fit(np.zeros((10, 2)), np.arange(10) % 2)\n"
        "def test_uses_model():\n"
        "    assert MODEL is not None\n",
    )
    found = find_fit_at_test_module_import(tmp_path)
    assert [(f.path, f.line, f.rule) for f in found] == [("test_model.py", 3, RULE)]
    assert ".fit(" in found[0].message


def test_class_body_fit_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "test_cls.py", "class TestX:\n    model = Thing().fit_transform([1], [2])\n")
    assert _rules(tmp_path) == [("test_cls.py", 2)]


@pytest.mark.parametrize("call", DEFAULT_HEAVY_CALLS)
def test_every_default_heavy_call_is_flagged(tmp_path: Path, call: str) -> None:
    _write(tmp_path, "test_x.py", f"OUT = thing.{call}(X)\n")
    assert _rules(tmp_path) == [("test_x.py", 1)]


def test_calls_inside_function_bodies_are_not_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_fn.py",
        "import pytest\n"
        "@pytest.fixture(scope='module')\n"
        "def model():\n"
        "    return LogisticRegression().fit(X, y)\n"
        "def test_a():\n"
        "    m = LogisticRegression().fit(X, y)\n"
        "LAZY = lambda: Thing().fit(X, y)\n"
        "async def test_b():\n"
        "    await Thing().fit(X, y)\n",
    )
    assert _rules(tmp_path) == []


def test_decorator_and_default_argument_run_at_import(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_defaults.py",
        "def test_a(m=Thing().fit(X, y)):\n    pass\n",
    )
    assert _rules(tmp_path) == [("test_defaults.py", 1)]


def test_waiver_on_the_call_line_suppresses(tmp_path: Path) -> None:
    _write(tmp_path, "test_w.py", "M = Thing().fit(X, y)  # import-fit-ok: shared tiny model, 10 rows\n")
    assert _rules(tmp_path) == []


def test_waiver_on_a_later_line_of_a_multiline_call_suppresses(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_w2.py",
        "M = (Thing()\n     .fit(X, y))  # import-fit-ok: ten rows, under a millisecond\n",
    )
    assert _rules(tmp_path) == []


def test_heavy_calls_are_configurable(tmp_path: Path) -> None:
    _write(tmp_path, "test_c.py", "A = thing.fit(X)\nB = thing.train_model(X)\n")
    assert _rules(tmp_path, heavy_calls=("train_model",)) == [("test_c.py", 2)]


def test_only_test_modules_are_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "helpers.py", "M = Thing().fit(X, y)\n")
    _write(tmp_path, "test_real.py", "def test_ok():\n    pass\n")
    _write(tmp_path, "conftest.py", "CFG = Thing().fit(X, y)\n")
    _write(tmp_path, "sub/fixture_test.py", "CFG2 = Thing().fit(X, y)\n")
    assert sorted(p for p, _ in _rules(tmp_path)) == ["conftest.py", "sub/fixture_test.py"]


def test_assert_raises_naming_the_call(tmp_path: Path) -> None:
    _write(tmp_path, "test_bad.py", "M = Thing().fit(X, y)\n")
    with pytest.raises(AssertionError, match=r"test_bad\.py:1: \[fit-at-test-module-import\]"):
        assert_no_fit_at_test_module_import(tmp_path)


def test_assert_passes_on_clean_tree(tmp_path: Path) -> None:
    _write(tmp_path, "test_ok.py", "import pytest\n@pytest.fixture\ndef m():\n    return Thing().fit(X, y)\n")
    assert_no_fit_at_test_module_import(tmp_path)


def test_unparsable_test_module_fails_the_assert(tmp_path: Path) -> None:
    _write(tmp_path, "test_ok.py", "def test_a():\n    pass\n")
    _write(tmp_path, "test_broken.py", "def (:\n")
    with pytest.raises(UnparsedFilesError):
        assert_no_fit_at_test_module_import(tmp_path)


def test_empty_tree_fails_the_floor(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        assert_no_fit_at_test_module_import(tmp_path)
