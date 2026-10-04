"""Tests for py_ci_shared.wrapper_protocol_parity: the static rule, the run-time helper, and their teeth."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import py_ci_shared.wrapper_protocol_parity as gate
from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.wrapper_protocol_parity import assert_wrapper_protocol_parity, find_wrapper_protocol_parity

BOM = b"\xef\xbb\xbf"
CANARY = Path(__file__).resolve().parent / "canary" / "wrapper_protocol_parity"


def _canary(tmp_path: Path, case: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for p in (CANARY / case).rglob("*.canary"):
        (tmp_path / p.name[: -len(".canary")]).write_bytes(p.read_bytes())
    return tmp_path


def _one(tmp_path: Path, src: str, **kwargs) -> list:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "m.py").write_bytes(src.encode())
    return find_wrapper_protocol_parity(tmp_path, use_git=False, **kwargs)


def test_reports_load_top_jobs_copying_cache_clear_but_not_refresh(tmp_path):
    found = find_wrapper_protocol_parity(_canary(tmp_path, "violation"), use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("seed.py", 17, gate.RULE)]
    assert found[0].message.startswith("load_top_jobs copies cache_clear from _load_top_jobs_cached but not refresh;")
    with pytest.raises(AssertionError, match="1 wrapper-protocol-parity finding"):
        assert_wrapper_protocol_parity(tmp_path, use_git=False)


def test_negative_control_the_fixed_wrapper_is_not_reported(tmp_path):
    # the fix sets `refresh` to a partial of the wrapper, not a copy: any assignment counts
    assert find_wrapper_protocol_parity(_canary(tmp_path, "clean"), use_git=False) == []


def test_an_unknown_inner_is_taken_to_have_the_whole_protocol(tmp_path):
    src = "from lib import magic\n\n@magic\ndef _inner():\n    pass\n\ndef outer():\n    pass\n\nouter.cache_clear = _inner.cache_clear\n"
    found = _one(tmp_path / "a", src)
    assert len(found) == 1 and "but not has_value, refresh;" in found[0].message
    # told what `magic` provides, only that is required
    assert _one(tmp_path / "b", src, providers={"magic": {"cache_clear"}}) == []


def test_what_the_module_reads_off_the_inner_counts_as_having_it(tmp_path):
    src = "import functools\n\n@functools.lru_cache\ndef _inner():\n    pass\n\ndef outer():\n    return _inner.refresh()\n\nouter.cache_clear = _inner.cache_clear\n"
    found = _one(tmp_path, src)
    assert len(found) == 1 and "but not refresh;" in found[0].message


def test_custom_protocols_and_setattr(tmp_path):
    src = "def outer():\n    pass\n\nouter.open = inner.open\nsetattr(outer, 'close', inner.close)\n"
    assert _one(tmp_path / "a", src, protocols=[{"open", "close"}]) == []
    found = _one(tmp_path / "b", src.replace("setattr(outer, 'close', inner.close)\n", ""), protocols=[{"open", "close"}])
    assert len(found) == 1 and "but not close;" in found[0].message
    # a copy of an attribute outside every protocol is not a wrapper relation
    assert _one(tmp_path / "c", "outer.__doc__ = inner.__doc__\n") == []


def test_a_call_built_inner_resolves_its_decorator(tmp_path):
    src = "loader = ttl_cached('k', 1.0)(_raw)\n\ndef outer():\n    pass\n\nouter.cache_clear = loader.cache_clear\n"
    found = _one(tmp_path, src)
    assert len(found) == 1 and found[0].message.startswith("outer copies cache_clear from loader but not refresh;")


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = find_wrapper_protocol_parity(_canary(tmp_path / "p", "violation"), use_git=False)
    bom = find_wrapper_protocol_parity(_canary(tmp_path / "b", "bom"), use_git=False)
    assert plain and [(f.path, f.line, f.message) for f in bom] == [(f.path, f.line, f.message) for f in plain]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _canary(tmp_path, "violation")
    (root / "broken.py").write_bytes(b"def broken(:\n")
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_wrapper_protocol_parity(root, use_git=False)
    assert len(find_wrapper_protocol_parity(root, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_wrapper_protocol_parity(tmp_path, use_git=False)


# ---- teeth ---------------------------------------------------------------------------------------------------------


def test_teeth_reverting_the_core_condition_loses_the_motivating_case(tmp_path, monkeypatch):
    root = _canary(tmp_path, "violation")
    assert find_wrapper_protocol_parity(root, use_git=False)
    source = Path(gate.__file__).read_text(encoding="utf-8")
    old = "            missing = sorted(inner_has - wrapper_has)\n"
    assert source.count(old) == 1, "the substitution no longer matches the gate's source"
    mutant = types.ModuleType("py_ci_shared._wrapper_protocol_parity_mutant")
    mutant.__package__ = "py_ci_shared"
    monkeypatch.setitem(sys.modules, mutant.__name__, mutant)
    exec(compile(source.replace(old, "            missing: list[str] = []\n"), gate.__file__, "exec"), mutant.__dict__)
    assert mutant.find_wrapper_protocol_parity(root, use_git=False) == [], "the mutant still bites"
