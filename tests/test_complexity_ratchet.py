"""complexity_ratchet: ruff's C901 number from the AST, and the no-new / no-growth / lock-in-the-gain ratchet over it."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import REFRESH_GROW_ENV_VAR
from py_ci_shared.complexity_ratchet import (
    assert_complexity_does_not_grow,
    complexities_by_line,
    complexity_problems,
    function_complexities,
    function_complexity,
)

REPO = Path(__file__).resolve().parents[1]


def _cx(src: str) -> int:
    tree = ast.parse(textwrap.dedent(src))
    return function_complexity(tree.body[0])


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("def f():\n    return 1\n", 1),
        ("def f(a):\n    if a:\n        return 1\n    elif a > 1:\n        return 2\n    else:\n        return 3\n", 3),
        ("def f(xs):\n    for x in xs:\n        pass\n    else:\n        pass\n", 2),
        ("def f(a):\n    while a:\n        a -= 1\n", 2),
        (
            "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n    except OSError:\n        pass\n    else:\n        pass\n    finally:\n        pass\n",
            4,
        ),
        ("def f(a, b):\n    return a and b or not a\n", 1),  # boolean operators add nothing in ruff's count
        ("def f(xs):\n    return [x for x in xs if x]\n", 1),  # nor do comprehensions
        ("def f(a):\n    with open(a) as h:\n        if h:\n            pass\n", 2),
        ("def f():\n    def g(a):\n        if a:\n            pass\n    return g\n", 3),  # a nested def counts 1 plus its body
        ("async def f(xs):\n    async for x in xs:\n        pass\n", 2),
    ],
)
def test_the_number_matches_ruffs_rules(src, expected):
    assert _cx(src) == expected


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match needs Python 3.10")
def test_an_irrefutable_last_case_counts_like_an_else():
    src = "def f(x):\n    match x:\n        case 1:\n            pass\n        case 2:\n            pass\n        case _:\n            pass\n"
    assert _cx(src) == 3
    assert _cx(src.replace("case _:", "case 3:")) == 4


def test_methods_and_nested_functions_are_keyed_by_qualname(tmp_path):
    (tmp_path / "m.py").write_text("class C:\n    def m(self, a):\n        if a:\n            pass\n        def inner():\n            pass\n", encoding="utf-8")
    values = function_complexities([tmp_path / "m.py"], tmp_path)
    assert values == {"m.py::C.m": 3, "m.py::C.m.<locals>.inner": 1}


def test_parity_with_ruff_on_this_package():
    """Every function in src/ gets the number ruff reports for it (max-complexity 0 makes ruff report them all)."""
    pytest.importorskip("ruff")
    files = sorted((REPO / "src").rglob("*.py"))
    mine = complexities_by_line(files, REPO)
    cmd = [sys.executable, "-m", "ruff", "check", "src", "--isolated", "--no-cache", "--select", "C901"]
    cmd += ["--config", "lint.mccabe.max-complexity=0", "--output-format", "json", "--exit-zero"]
    out = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=True).stdout
    theirs = {}
    for d in json.loads(out):
        rel = Path(d["filename"]).resolve().relative_to(REPO).as_posix()
        theirs[(rel, d["location"]["row"])] = int(re.search(r"\((\d+) > 0\)", d["message"]).group(1))
    assert len(theirs) > 500, "ruff reported too few functions; the comparison would prove nothing"
    diff = {k: (mine.get(k), theirs.get(k)) for k in set(mine) | set(theirs) if mine.get(k) != theirs.get(k)}
    assert not diff, f"(ours, ruff's) differ for {len(diff)} function(s): {sorted(diff.items())[:10]}"


def test_problems_new_grown_shrunk_and_gone():
    baseline = {"a.py::grew": 12, "a.py::shrank": 15, "a.py::fixed": 14, "a.py::gone": 11, "a.py::same": 13}
    values = {"a.py::grew": 13, "a.py::shrank": 12, "a.py::fixed": 9, "a.py::same": 13, "a.py::new": 11, "a.py::small": 10}
    problems = "\n".join(complexity_problems(values, baseline, limit=10))
    assert "a.py::grew: complexity 13, over its recorded 12" in problems
    assert "a.py::shrank: complexity 12, under its recorded 15" in problems
    assert "a.py::fixed: complexity 9, now within the limit" in problems
    assert "a.py::gone: in the baseline but gone" in problems
    assert "a.py::new: complexity 11, over the limit of 10" in problems
    assert "same" not in problems and "small" not in problems
    relaxed = complexity_problems(values, baseline, limit=10, fail_on_shrink=False)
    assert len(relaxed) == 2, relaxed


BRANCHY = "def branchy(a, b, c):\n    if a:\n        return 1\n    if b:\n        return 2\n    if c:\n        return 3\n    return 0\n"
SIMPLE = "def simple(a):\n    return a\n"


def _repo(tmp_path, *sources: str) -> list[Path]:
    files = []
    for i, src in enumerate(sources):
        f = tmp_path / f"m{i}.py"
        f.write_text(src, encoding="utf-8")
        files.append(f)
    return files


def _run(files, tmp_path, **kw):
    return assert_complexity_does_not_grow(files, tmp_path, tmp_path / "b.json", limit=2, min_functions=1, **kw)


def test_a_new_complex_function_fails_and_a_baselined_one_passes(tmp_path):
    files = _repo(tmp_path, BRANCHY)
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"m0\.py::branchy: complexity 4, over the limit of 2"):
        _run(files, tmp_path)
    (tmp_path / "b.json").write_text(json.dumps({"m0.py::branchy": 4}), encoding="utf-8")
    _run(files, tmp_path)


def test_a_missing_baseline_and_an_unparsable_file_fail(tmp_path):
    files = _repo(tmp_path, SIMPLE)
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        _run(files, tmp_path)
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    files.append(tmp_path / "broken.py")
    with pytest.raises(pytest.fail.Exception, match="not measured"):
        _run(files, tmp_path)


def test_the_floor_fails_an_empty_walk(tmp_path):
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="only 0 function"):
        assert_complexity_does_not_grow([], tmp_path, tmp_path / "b.json")


def test_refresh_is_shrink_only_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(REFRESH_GROW_ENV_VAR, raising=False)
    files = _repo(tmp_path, SIMPLE)
    (tmp_path / "b.json").write_text(json.dumps({"m0.py::gone": 5}), encoding="utf-8")
    with pytest.raises(pytest.skip.Exception):
        _run(files, tmp_path, refresh=True)
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == {}
    files = _repo(tmp_path, BRANCHY)
    with pytest.raises(pytest.fail.Exception, match=r"m0.py::branchy  \(new: 4\)[\s\S]*PY_CI_SHARED_REFRESH_ALLOW_GROW"):
        _run(files, tmp_path, refresh=True)
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == {}
    with pytest.raises(pytest.skip.Exception):
        _run(files, tmp_path, refresh=True, grow=True)
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == {"m0.py::branchy": 4}


def test_seeding_a_missing_baseline_needs_the_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv(REFRESH_GROW_ENV_VAR, raising=False)
    files = _repo(tmp_path, BRANCHY)
    with pytest.raises(pytest.fail.Exception, match="does not exist, and seeding it would accept 1"):
        _run(files, tmp_path, refresh=True)
    assert not (tmp_path / "b.json").exists()
    monkeypatch.setenv(REFRESH_GROW_ENV_VAR, "1")
    with pytest.raises(pytest.skip.Exception):
        _run(files, tmp_path, refresh=True)
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == {"m0.py::branchy": 4}
