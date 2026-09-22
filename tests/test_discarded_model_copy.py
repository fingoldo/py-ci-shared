"""Unit tests for the discarded-model_copy check. Real files on disk, no mocking, matching this package's convention."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.discarded_model_copy import assert_no_discarded_model_copy, find_discarded_model_copies


def _found(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return [f"{d.function}:{d.name}" for d in find_discarded_model_copies([p], tmp_path)]


def test_a_copy_used_only_for_a_local_read_is_found(tmp_path):
    body = """
        def auto_enable(cfg):
            eff = cfg.model_copy(update={"enabled": True})
            if eff.enabled:
                print("on")
    """
    assert _found(tmp_path, body) == ["auto_enable:eff"]


@pytest.mark.parametrize("use", ["return eff", "ctx.cfg = eff", "store[0] = eff", "run(config=eff)", "yield eff", "eff.apply()"])
def test_a_copy_that_reaches_something_is_not_flagged(tmp_path, use):
    body = f"""
        def f(cfg, ctx, store, run):
            eff = cfg.model_copy(update={{"enabled": True}})
            {use}
    """
    assert _found(tmp_path, body) == []


def test_a_copy_forwarded_through_an_alias_is_followed(tmp_path):
    body = """
        def f(cfg, run, flag):
            base = cfg
            if flag:
                base = cfg.model_copy(update={"x": 1})
            local = base
            run(local)
    """
    assert _found(tmp_path, body) == []


def test_a_plain_model_copy_without_update_is_not_a_finding(tmp_path):
    body = """
        def f(cfg):
            snapshot = cfg.model_copy()
            if snapshot.n:
                return None
    """
    assert _found(tmp_path, body) == []


def test_assert_allows_a_reasoned_entry_and_rejects_stale_or_empty_ones(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(textwrap.dedent("""
        def probe(cfg):
            eff = cfg.model_copy(update={"n": 1})
            if eff.n:
                return None
    """), encoding="utf-8")
    with pytest.raises(AssertionError, match="probe"):
        assert_no_discarded_model_copy([p], tmp_path, {})
    assert_no_discarded_model_copy([p], tmp_path, {"probe": "a one-line diagnostic read"})
    with pytest.raises(AssertionError, match="no discarded copy left"):
        assert_no_discarded_model_copy([p], tmp_path, {"probe": "x", "gone": "y"})
    with pytest.raises(AssertionError, match="need a reason"):
        assert_no_discarded_model_copy([p], tmp_path, {"probe": ""})
