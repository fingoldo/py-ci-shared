"""required_call_kwargs: a configured call must carry one of its configured keywords (asyncpg pools without command_timeout starve)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.required_call_kwargs import (
    ASYNCPG,
    RULE,
    assert_required_call_kwargs,
    find_required_call_kwargs,
)

BOM = b"\xef\xbb\xbf"
NL = chr(10)


def _write(root: Path, rel: str, lines: list, *, bom: bool = False) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (NL.join(lines) + NL).encode("utf-8")
    path.write_bytes((BOM if bom else b"") + data)
    return path


def _find(tmp_path: Path, lines: list, **kwargs) -> list:
    _write(tmp_path, "mod.py", lines)
    return find_required_call_kwargs(tmp_path, use_git=False, **kwargs)


def test_the_bare_create_pool_call_is_reported_by_name_and_line(tmp_path):
    """The incident: asyncpg.create_pool(dsn, min_size, max_size) with no command_timeout."""
    found = _find(
        tmp_path,
        [
            "import asyncpg",  # 1
            "",  # 2
            "async def open_pool(dsn):",  # 3
            "    return await asyncpg.create_pool(dsn, min_size=1, max_size=4)",  # 4
        ],
    )
    assert [(f.path, f.line, f.rule) for f in found] == [("mod.py", 4, RULE)]
    assert "asyncpg.create_pool" in found[0].message
    assert "command_timeout" in found[0].message


def test_a_bare_connect_call_is_reported_for_its_timeout_keyword(tmp_path):
    found = _find(tmp_path, ["import asyncpg", "async def f(dsn):", "    return await asyncpg.connect(dsn)"])
    assert [(f.line, f.rule) for f in found] == [(3, RULE)]
    assert "asyncpg.connect" in found[0].message
    assert "timeout" in found[0].message


def test_an_aliased_module_import_is_resolved(tmp_path):
    found = _find(tmp_path, ["import asyncpg as apg", "async def f(dsn):", "    return await apg.create_pool(dsn)"])
    assert [(f.line, f.rule) for f in found] == [(3, RULE)]
    assert "asyncpg.create_pool" in found[0].message


def test_a_directly_imported_name_is_resolved(tmp_path):
    found = _find(tmp_path, ["from asyncpg import connect as open_conn", "async def f(dsn):", "    return await open_conn(dsn)"])
    assert [(f.line, f.rule) for f in found] == [(3, RULE)]
    assert "asyncpg.connect" in found[0].message


def test_the_keyword_present_is_clean(tmp_path):
    found = _find(
        tmp_path,
        [
            "import asyncpg as apg",
            "async def open_pool(dsn):",
            "    return await apg.create_pool(dsn, min_size=1, max_size=4, command_timeout=30)",
            "async def open_one(dsn):",
            "    return await apg.connect(dsn, timeout=10)",
        ],
    )
    assert found == []


def test_forwarded_kwargs_count_as_present(tmp_path):
    """A ``**kw`` forwarder may carry the keyword, so the gate cannot call it missing."""
    found = _find(tmp_path, ["import asyncpg as apg", "async def open_one(dsn, **kw):", "    return await apg.connect(dsn, **kw)"])
    assert found == []


def test_any_one_of_the_keywords_satisfies_the_rule(tmp_path):
    rules = [{"call": "pkg.make", "any_of": ["alpha", "beta"]}]
    assert _find(tmp_path, ["import pkg", "pkg.make(1, beta=2)"], rules=rules) == []
    assert [f.line for f in _find(tmp_path, ["import pkg", "pkg.make(1, gamma=2)"], rules=rules)] == [2]


def test_a_caller_supplied_rule_table_replaces_the_preset(tmp_path):
    rules = [{"call": "stanza.Pipeline", "any_of": ["package"]}]
    found = _find(
        tmp_path,
        ["import stanza", 'stanza.Pipeline("ka", processors="tokenize")', "asyncpg_stub = None", "import asyncpg", "asyncpg.create_pool(1)"],
        rules=rules,
    )
    assert [(f.line, "stanza.Pipeline" in f.message) for f in found] == [(2, True)]


def test_the_preset_is_the_two_asyncpg_rows():
    assert ASYNCPG == [
        {"call": "asyncpg.create_pool", "any_of": ("command_timeout",)},
        {"call": "asyncpg.connect", "any_of": ("timeout",)},
    ]


def test_an_unrelated_method_of_the_same_name_is_not_reported(tmp_path):
    """Only the configured qualified callee is checked: a local object's ``create_pool`` is not asyncpg's."""
    assert _find(tmp_path, ["class Pool:", "    def create_pool(self, dsn):", "        return dsn", "Pool().create_pool('x')"]) == []


def test_a_suppression_with_a_reason_is_accepted(tmp_path):
    found = _find(
        tmp_path,
        [
            "import asyncpg",
            "async def probe(dsn):",
            "    # call-kwarg-ok: one-shot probe, the server timeout is the bound",
            "    return await asyncpg.connect(dsn)",
        ],
    )
    assert found == []


def test_a_suppression_without_a_reason_is_itself_a_finding(tmp_path):
    found = _find(tmp_path, ["import asyncpg", "async def probe(dsn):", "    return await asyncpg.connect(dsn)  # call-kwarg-ok:"])
    assert [f.line for f in found] == [3]
    assert "reason" in found[0].message


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    lines = ["import asyncpg", "async def open_pool(dsn):", "    return await asyncpg.create_pool(dsn)"]
    plain = find_required_call_kwargs(_write(tmp_path / "plain", "mod.py", lines).parent, use_git=False)
    bom = find_required_call_kwargs(_write(tmp_path / "bom", "mod.py", lines, bom=True).parent, use_git=False)
    assert [(f.path, f.line, f.message) for f in bom] == [(f.path, f.line, f.message) for f in plain]
    assert len(bom) == 1


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _write(tmp_path, "broken.py", ["def broken(:"])
    _write(tmp_path, "ok.py", ["x = 1"])  # one parsed file clears the floor, so only the unparsed check is under test
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_required_call_kwargs(tmp_path, use_git=False)
    assert find_required_call_kwargs(tmp_path, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_required_call_kwargs(tmp_path, use_git=False)


def test_the_assert_fails_the_build_on_a_finding(tmp_path):
    _write(tmp_path, "mod.py", ["import asyncpg", "asyncpg.create_pool('x')"])
    with pytest.raises(AssertionError, match="required-call-kwargs"):
        assert_required_call_kwargs(tmp_path, use_git=False)
