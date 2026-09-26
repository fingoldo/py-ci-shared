"""Unit tests for the function-complexity ratchet (ruff C901 numbers, keyed by path::qualname)."""

from __future__ import annotations

import json
import re
from pathlib import Path


import pytest

pytest.importorskip("ruff")

from py_ci_shared.function_complexity import assert_complexity_does_not_grow, complexity_problems, function_complexities


def _branchy(name: str, n: int, indent: str = "") -> str:
    """A function whose ruff complexity is exactly ``n + 1`` (one ``if`` per branch)."""
    body = "".join(f"{indent}    if x == {i}:\n{indent}        return {i}\n" for i in range(n))
    return f"{indent}def {name}(x):\n{body}{indent}    return -1\n"


def _repo(tmp_path: Path, files: dict) -> list:
    out = []
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        out.append(p)
    return out


def test_measures_over_limit_functions_with_qualnames(tmp_path):
    files = _repo(
        tmp_path,
        {
            "pkg/a.py": _branchy("simple", 3)
            + "\nclass K:\n"
            + _branchy("method", 10, "    ")
            + "\n"
            + "def outer():\n"
            + _branchy("inner", 8, "    ")
            + "    return inner\n"
        },
    )
    got, keys = function_complexities(files, tmp_path, limit=5)
    # ruff counts a nested function's branches into its parent as well, so ``outer`` is over the limit too.
    assert got == {"pkg/a.py::K.method": 11, "pkg/a.py::outer.<locals>.inner": 9, "pkg/a.py::outer": 10}
    assert "pkg/a.py::simple" in keys


def test_ratchet_rules():
    keys = {"a.py::grew", "a.py::shrank", "a.py::fixed", "a.py::new", "a.py::same"}
    got = {"a.py::grew": 40, "a.py::shrank": 30, "a.py::new": 27, "a.py::same": 33}
    base = {"a.py::grew": 35, "a.py::shrank": 32, "a.py::fixed": 29, "a.py::gone": 50, "a.py::same": 33}
    problems = "\n".join(complexity_problems(got, keys, base, limit=25))
    assert "grew: complexity 40, over its ceiling of 35" in problems
    assert "shrank: complexity 30, under its ceiling of 32" in problems
    assert "fixed: now within the limit of 25" in problems
    assert "gone: in the baseline but gone" in problems
    assert "new: complexity 27, over the limit of 25" in problems
    assert "same" not in problems


def test_assert_passes_on_a_matching_baseline_and_refreshes(tmp_path):
    files = _repo(tmp_path, {f"m{i}.py": _branchy(f"f{i}", 2) for i in range(3)} | {"big.py": _branchy("big", 30)})
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.skip.Exception):
        assert_complexity_does_not_grow(files, tmp_path, baseline, limit=25, min_functions=3, refresh=True, grow=True)
    assert json.loads(baseline.read_text()) == {"big.py::big": 31}
    assert_complexity_does_not_grow(files, tmp_path, baseline, limit=25, min_functions=3, refresh=False)
    baseline.write_text(json.dumps({}))
    with pytest.raises(pytest.fail.Exception, match=re.escape("big.py::big: complexity 31, over the limit of 25")):
        assert_complexity_does_not_grow(files, tmp_path, baseline, limit=25, min_functions=3, refresh=False)


def test_a_seeding_refresh_needs_growth_allowed(tmp_path):
    files = _repo(tmp_path, {"big.py": _branchy("big", 30)})
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match=r"ALLOW_GROW|refresh-grow|grow"):
        assert_complexity_does_not_grow(files, tmp_path, baseline, limit=25, min_functions=1, refresh=True, grow=False)
    assert not baseline.exists()
