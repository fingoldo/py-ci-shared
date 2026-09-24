"""Unit tests for the discarded-model_copy check. Real files on disk, no mocking, matching this package's convention."""

from __future__ import annotations

import textwrap
from pathlib import Path

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
    p.write_text(
        textwrap.dedent("""
        def probe(cfg):
            eff = cfg.model_copy(update={"n": 1})
            if eff.n:
                return None
    """),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="probe"):
        assert_no_discarded_model_copy([p], tmp_path, {})
    assert_no_discarded_model_copy([p], tmp_path, {"m.py::probe": "a one-line diagnostic read"})
    with pytest.raises(AssertionError, match="no discarded copy left"):
        assert_no_discarded_model_copy([p], tmp_path, {"m.py::probe": "x", "m.py::gone": "y"})
    with pytest.raises(AssertionError, match="need a reason"):
        assert_no_discarded_model_copy([p], tmp_path, {"m.py::probe": ""})


def test_a_nested_copy_is_reported_once_under_its_own_scope(tmp_path):
    body = """
        def outer(cfg):
            def inner(c):
                eff = c.model_copy(update={"n": 1})
                if eff.n:
                    return None
            return inner
    """
    assert _found(tmp_path, body) == ["outer.<locals>.inner:eff"]


def test_allowing_the_inner_function_clears_the_nested_finding(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        textwrap.dedent("""
        def outer(cfg):
            def inner(c):
                eff = c.model_copy(update={"n": 1})
                if eff.n:
                    return None
            return inner
    """),
        encoding="utf-8",
    )
    assert_no_discarded_model_copy([p], tmp_path, {"m.py::outer.<locals>.inner": "diagnostic"})
    with pytest.raises(AssertionError, match="inner"):
        assert_no_discarded_model_copy([p], tmp_path, {"m.py::outer": "wrong scope"})


@pytest.mark.parametrize("assign", ['eff: Cfg = cfg.model_copy(update={"n": 1})', 'if (eff := cfg.model_copy(update={"n": 1})): pass'])
def test_annotated_and_walrus_copies_are_found(tmp_path, assign):
    body = f"""
        def f(cfg):
            {assign}
            if eff.n:
                return None
    """
    assert _found(tmp_path, body) == ["f:eff"]


def test_an_annotated_copy_that_is_returned_is_not_flagged(tmp_path):
    body = """
        def f(cfg):
            eff: Cfg = cfg.model_copy(update={"n": 1})
            return eff
    """
    assert _found(tmp_path, body) == []


@pytest.mark.parametrize("sink", ["log.debug('%s', eff)", "logger.info(eff)", "print(eff)", "self.logger.warning('cfg=%s', eff)"])
def test_logging_the_copy_is_not_an_escape(tmp_path, sink):
    body = f"""
        def f(self, cfg):
            eff = cfg.model_copy(update={{"n": 1}})
            {sink}
            return cfg
    """
    assert _found(tmp_path, body) == ["f:eff"]


def test_allowed_is_scoped_to_one_file(tmp_path):
    src = 'def build(cfg):\n    eff = cfg.model_copy(update={"n": 1})\n    if eff.n:\n        return None\n'
    (tmp_path / "a.py").write_text(src, encoding="utf-8")
    (tmp_path / "b.py").write_text(src, encoding="utf-8")
    files = [tmp_path / "a.py", tmp_path / "b.py"]
    with pytest.raises(AssertionError, match=r"b.py"):
        assert_no_discarded_model_copy(files, tmp_path, {"a.py::build": "diagnostic"})
    with pytest.raises(AssertionError, match="path::function"):
        assert_no_discarded_model_copy(files, tmp_path, {"build": "diagnostic"})
    assert_no_discarded_model_copy(files, tmp_path, {"a.py::build": "d", "b.py::build": "d"})


def test_a_file_outside_the_root_does_not_raise(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    p = other / "m.py"
    p.write_text('def f(cfg):\n    eff = cfg.model_copy(update={"n": 1})\n', encoding="utf-8")
    found = find_discarded_model_copies([p], tmp_path / "elsewhere")
    assert [d.name for d in found] == ["eff"] and found[0].path.endswith("other/m.py")


def test_unparsable_and_bom_files(tmp_path):
    bom = tmp_path / "bom.py"
    bom.write_bytes(b"\xef\xbb\xbf" + b'def f(cfg):\n    eff = cfg.model_copy(update={"n": 1})\n')
    assert [d.function for d in find_discarded_model_copies([bom], tmp_path)] == ["f"]
    broken = tmp_path / "broken.py"
    broken.write_text("def f(:\n", encoding="utf-8")
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\n", encoding="utf-8")
    assert_no_discarded_model_copy([ok], tmp_path, {})
    with pytest.raises(AssertionError, match=r"broken.py"):
        assert_no_discarded_model_copy([ok, broken], tmp_path, {})
