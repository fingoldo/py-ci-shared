"""Tests for py_ci_shared.module_state_test_reset."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.module_state_test_reset import assert_module_state_test_reset, find_module_state_test_reset

BOM = b"\xef\xbb\xbf"
FILLS = b"_CACHE = {}\n\n\ndef remember(key, value):\n    _CACHE[key] = value\n"
IDLE_TEST = b"def test_something():\n    assert True\n"


def _corpus(tmp_path: Path, production: bytes, test: bytes = IDLE_TEST) -> tuple[Path, Path]:
    (tmp_path / "pkg").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pkg" / "mod.py").write_bytes(production)
    (tmp_path / "tests" / "test_mod.py").write_bytes(test)
    return tmp_path / "pkg", tmp_path / "tests"


def _find(tmp_path: Path, production: str, test: bytes = IDLE_TEST) -> list:
    root, tests = _corpus(tmp_path, production.encode(), test)
    return find_module_state_test_reset(root, tests, use_git=False)


def test_reports_the_seeded_violation(tmp_path):
    root, tests = _corpus(tmp_path, FILLS)
    (finding,) = find_module_state_test_reset(root, tests, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("mod.py", 1, "module-state-no-test-reset")
    assert "`_CACHE`" in finding.message


def test_negative_control_a_test_that_mentions_the_name_is_enough(tmp_path):
    assert _find(tmp_path, FILLS.decode(), b"import mod\n\n\ndef test_x():\n    mod._CACHE.clear()\n") == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    root, tests = _corpus(tmp_path, BOM + FILLS)
    assert len(find_module_state_test_reset(root, tests, use_git=False)) == 1


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root, tests = _corpus(tmp_path, b"def broken(:\n")
    (root / "ok.py").write_bytes(b"x = 1\n")
    with pytest.raises(UnparsedFilesError, match=r"mod.py"):
        find_module_state_test_reset(root, tests, use_git=False)
    assert find_module_state_test_reset(root, tests, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    with pytest.raises(EmptyScanError):
        find_module_state_test_reset(tmp_path / "pkg", tmp_path / "tests", use_git=False)


@pytest.mark.parametrize(
    "body",
    [
        "_S = set()\n\n\ndef f(x):\n    _S.add(x)\n",
        "_L = []\n\n\ndef f(x):\n    _L.append(x)\n",
        "_D = dict()\n\n\ndef f():\n    _D.update(a=1)\n",
        "_D = {}\n\n\ndef f(k):\n    del _D[k]\n",
        "_D = {}\n\n\ndef f(k):\n    return _D.setdefault(k, 1)\n",
        "from collections import defaultdict\n_D = defaultdict(list)\n\n\ndef f(k):\n    _D[k].append(1)\n    _D[k] = []\n",
        "_L = []\n\n\ndef f():\n    global _L\n    _L = [1]\n",
        "_N = {}\n\n\nasync def f(k):\n    _N[k] = 1\n",
    ],
)
def test_each_way_a_function_writes_the_state_is_seen(tmp_path, body):
    assert len(_find(tmp_path, body)) == 1


@pytest.mark.parametrize(
    "body",
    [
        "_TABLE = {'a': 1}\n\n\ndef f(k):\n    return _TABLE[k]\n",
        "_TABLE = {}\n_TABLE['a'] = 1\n",
        "_NAME = 'x'\n\n\ndef f():\n    global _NAME\n    _NAME = 'y'\n",
        "def f():\n    _L = []\n    _L.append(1)\n",
        "_L = []\n\n\ndef f(_L):\n    return len(_L)\n",
    ],
)
def test_state_nothing_writes_from_a_function_is_not_reported(tmp_path, body):
    assert _find(tmp_path, body) == []


def test_a_monkeypatch_by_string_counts_as_mentioning_the_name(tmp_path):
    test = b"def test_x(monkeypatch):\n    monkeypatch.setattr('pkg.mod._CACHE', {})\n    monkeypatch.setattr(mod, '_CACHE', {})\n"
    assert _find(tmp_path, FILLS.decode(), test) == []


def test_production_directories_named_in_exclude_are_skipped(tmp_path):
    root, tests = _corpus(tmp_path, b"x = 1\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "tool.py").write_bytes(FILLS)
    assert find_module_state_test_reset(root, tests, use_git=False) == []
    assert len(find_module_state_test_reset(root, tests, exclude=(), use_git=False)) == 1


def test_assert_fails_with_the_finding_and_passes_through_a_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root, tests = _corpus(tmp_path, FILLS)
    with pytest.raises(AssertionError, match=r"mod.py:1"):
        assert_module_state_test_reset(root, tests, use_git=False)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_module_state_test_reset(root, tests, baseline_path=baseline, refresh=True, use_git=False)
    assert_module_state_test_reset(root, tests, baseline_path=baseline, use_git=False)
