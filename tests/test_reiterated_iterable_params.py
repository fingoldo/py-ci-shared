from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.reiterated_iterable_params import RULE, assert_no_reiterated_iterable_params, find_reiterated_iterable_params


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


HEADER = "from typing import Iterable, Iterator, Optional, Sequence, Union\nimport collections.abc as cabc\n"


def _write(root: Path, rel: str, body: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + (HEADER + textwrap.dedent(body)).encode("utf-8"))

    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _messages(root: Path, **kw: object) -> list[str]:
    return [f.message for f in find_reiterated_iterable_params(root, use_git=False, **kw) if f.rule == RULE]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "body",
    [
        "def f(p: Iterable[str]):\n    for a in p:\n        pass\n    for b in p:\n        pass\n",
        "def f(p: Iterable[str]):\n    xs = [a for a in p]\n    return set(p)\n",
        "def f(p: 'Iterable[str]'):\n    ok = all(p)\n    return ','.join(p)\n",
        "def f(p: Optional[Iterable[str]]):\n    first = sorted(p)\n    return 'x' in p\n",
        "def f(p: Iterable[str] | None):\n    a = list(p)\n    b = tuple(p)\n",
        "def f(p: Union[Iterator[int], None]):\n    out = []\n    out.extend(p)\n    return max(p)\n",
        "def f(p: cabc.Iterable):\n    g(*p)\n    yield from p\n",
        "def f(rows, p: Iterable[str]):\n    for r in rows:\n        if r in p:\n            pass\n",
        "def f(rows, p: Iterable[str]):\n    return [(r, c) for r in rows for c in p]\n",
        "def f(p: Iterable[str], k):\n    if k:\n        a = list(p)\n    return set(p)\n",
    ],
)
def test_each_consumption_shape_is_flagged(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "m.py", body)
    assert len(_messages(tmp_path)) == 1


def test_message_names_the_function_parameter_and_count(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "class C:\n    def m(self, cols: Iterable[str]):\n        a = list(cols)\n        b = set(cols)\n        return sorted(cols)\n")
    assert _messages(tmp_path) == ["C.m(cols: Iterable[str]) is consumed 3 times on one path"]


@pytest.mark.parametrize(
    "body",
    [
        "def f(p: Iterable[str]):\n    p = list(p)\n    a = set(p)\n    return sorted(p)\n",
        "def f(p: Sequence[str]):\n    a = set(p)\n    return sorted(p)\n",
        "def f(p: list):\n    a = set(p)\n    return sorted(p)\n",
        "def f(p: Iterable[str], k):\n    if k:\n        return list(p)\n    return set(p)\n",
        "def f(p: Iterable[str], k):\n    if k:\n        a = list(p)\n    else:\n        a = set(p)\n    return a\n",
        "def f(p: Iterable[str]):\n    if not p:\n        return None\n    return list(p)\n",
        "def f(p: Iterable[str]):\n    for a in p:\n        print(a)\n",
        "def f(p: Iterable[str]):\n    try:\n        return list(p)\n    except TypeError:\n        raise\n",
        "def f(p: Iterable[str]):\n    def inner():\n        return list(p)\n    return set(p)\n",
        "def f(p: Iterable[str]):  # reiterable-ok: every caller passes a list\n    a = set(p)\n    return list(p)\n",
        "def f(p):\n    a = set(p)\n    return list(p)\n",
    ],
)
def test_negative_controls(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "m.py", body)
    assert _messages(tmp_path) == []


def test_tests_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "tests/helpers.py", "def f(p: Iterable[str]):\n    a = set(p)\n    return list(p)\n")
    assert _messages(tmp_path) == []
    assert len(_messages(tmp_path, include_tests=True)) == 1


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(p: Iterable[str]):\n    a = set(p)\n    return list(p)\n", bom=True)
    assert len(_messages(tmp_path)) == 1


def test_unparsable_and_empty_corpus_fail(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_reiterated_iterable_params(tmp_path, use_git=False)
    _write(tmp_path, "ok.py", "x = 1\n")
    (tmp_path / "bad.py").write_text("def (:\n", encoding="utf-8")
    assert [f.rule for f in find_reiterated_iterable_params(tmp_path, use_git=False)] == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_no_reiterated_iterable_params(tmp_path, use_git=False)


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", "def f(p: Iterable[str]):\n    return list(p)\n")
    assert_no_reiterated_iterable_params(src, use_git=False)
    _write(src, "m.py", "def f(p: Iterable[str]):\n    a = set(p)\n    return list(p)\n")
    with pytest.raises(pytest.fail.Exception, match="consumed 2 times"):
        assert_no_reiterated_iterable_params(src, use_git=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_reiterated_iterable_params(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_reiterated_iterable_params(src, baseline_path=baseline, refresh=True, use_git=False)
    assert_no_reiterated_iterable_params(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", "def f(p: Iterable[str]):\n    a = set(p)\n    return list(p)\ndef g(p: Iterable[str]):\n    a = set(p)\n    return list(p)\n")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_no_reiterated_iterable_params(src, baseline_path=baseline, refresh=False, use_git=False)
