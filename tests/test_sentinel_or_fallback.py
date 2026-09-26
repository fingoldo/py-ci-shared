"""Tests for sentinel_or_fallback: every read shape of a declared setting, the harmless tail, custom names, the marker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.sentinel_or_fallback import assert_no_sentinel_or_fallback, find_sentinel_or_fallback


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _found(tmp_path: Path, expr: str, **kw: object) -> list[str]:
    (tmp_path / "m.py").write_text(f"def f(x, d, body, seed, DEFAULT):\n    return {expr}\n", encoding="utf-8")
    findings, _ = find_sentinel_or_fallback(tmp_path, use_git=False, **kw)  # type: ignore[arg-type]
    return [f.message for f in findings]


def test_the_incident_shape_is_reported_once_for_its_replaceable_operand(tmp_path):
    msgs = _found(tmp_path, 'body.get("max_tokens") or body.get("max_completion_tokens") or 0')
    assert msgs == ["`body.get('max_tokens') or body.get('max_completion_tokens')` replaces a falsy max_tokens (0 is a value for it)"]


@pytest.mark.parametrize(
    "expr",
    ["x.timeout or DEFAULT", 'd["seed"] or 42', 'd.get("n_jobs") or -1', 'getattr(x, "limit", None) or 100', "seed or DEFAULT", "x.cfg.temperature or 0.7"],
)
def test_each_read_shape_of_a_declared_setting(tmp_path, expr):
    assert len(_found(tmp_path, expr)) == 1


@pytest.mark.parametrize(
    "expr",
    [
        "x.seed or 0",
        "x.timeout or None",
        'd.get("limit") or 0 or None',
        "x.name or DEFAULT",
        'd["label"] or "x"',
        "DEFAULT or x.timeout",
        "x.timeout and DEFAULT",
        "x.timeout if x.timeout is not None else DEFAULT",
    ],
)
def test_harmless_tails_undeclared_names_last_position_and_other_operators_are_not(tmp_path, expr):
    assert _found(tmp_path, expr) == []


def test_custom_names_replace_the_default_set(tmp_path):
    assert len(_found(tmp_path, "x.budget or DEFAULT", names={"budget"})) == 1
    assert _found(tmp_path, "x.timeout or DEFAULT", names={"budget"}) == []


def test_the_marker_and_the_test_directory_default(tmp_path):
    assert _found(tmp_path, "x.timeout or DEFAULT  # falsy-ok: 0 is rejected by the constructor") == []
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("def t(x, D):\n    return x.seed or D\n", encoding="utf-8")
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    assert find_sentinel_or_fallback(tmp_path, use_git=False)[0] == []
    assert len(find_sentinel_or_fallback(tmp_path, exclude_parts=(), use_git=False)[0]) == 1


def test_bom_unparsable_empty_and_the_ratchet(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_bytes(b"\xef\xbb\xbfdef f(x, D):\n    return x.seed or D\n")
    assert [f.line for f in find_sentinel_or_fallback(src, use_git=False)[0]] == [2]
    bl = tmp_path / "bl.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_sentinel_or_fallback(src, baseline_path=bl, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_sentinel_or_fallback(src, baseline_path=bl, refresh=True, use_git=False)
    assert len(json.loads(bl.read_text(encoding="utf-8"))["entries"]) == 1
    assert_no_sentinel_or_fallback(src, baseline_path=bl, use_git=False)
    (src / "n.py").write_text("def g(x, D):\n    return x.n_jobs or D\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="n_jobs"):
        assert_no_sentinel_or_fallback(src, baseline_path=bl, use_git=False)
    (src / "bad.py").write_text("def (:\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
        assert_no_sentinel_or_fallback(src, use_git=False)
    (tmp_path / "e").mkdir()
    with pytest.raises(pytest.fail.Exception, match="parsed"):
        assert_no_sentinel_or_fallback(tmp_path / "e", use_git=False)
