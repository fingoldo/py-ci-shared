"""Tests for py_ci_shared.external_fact_tables."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from py_ci_shared._core import CoreError, clear_parse_cache
from py_ci_shared.external_fact_tables import (
    RULE,
    UNDECLARED_RULE,
    assert_external_fact_tables_current,
    find_expiring_fact_tables,
    find_external_fact_table_problems,
    find_undeclared_fact_tables,
    parse_declarations,
)

BOM = b"\xef\xbb\xbf"
TODAY = dt.date(2026, 10, 3)
TABLE = '_PRICING = {\n    "a": (1.0, 2.0),\n    "b": (3.0, 4.0),\n    "c": (5.0, 6.0),\n}\n'


def _write(root: Path, rel: str, text: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    clear_parse_cache()


def _problems(root: Path, tables: dict, **kw: object) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.message) for f in find_external_fact_table_problems(root, tables, today=TODAY, **kw)]  # type: ignore[arg-type]


def test_reports_the_seeded_violation(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/prov.py", "import os\n\n" + TABLE)
    assert _problems(tmp_path, {"pkg/prov.py:_PRICING": 45}) == [
        ("pkg/prov.py", 3, "_PRICING: cites no source and no check date: add `# Source: <url> (verified YYYY-MM-DD)` above it")
    ]
    assert find_external_fact_table_problems(tmp_path, {"pkg/prov.py:_PRICING": 45}, today=TODAY)[0].rule == RULE


@pytest.mark.parametrize(
    "citation",
    [
        "# Source: https://api-docs.example.com/pricing (fetched 2026-09-26).\n",
        "# Pricing per 1M tokens.\n# Source: https://example.com/p\n# verified 2026-09-01, re-checked 2026-09-30\n",
        "# Current lineup (docs.x.ai/docs/models, 2026-09-26).\n",
    ],
)
def test_a_cited_dated_table_within_its_limit_passes(tmp_path: Path, citation: str) -> None:
    _write(tmp_path, "prov.py", citation + TABLE)
    assert _problems(tmp_path, {"prov.py:_PRICING": 45}) == []


def test_a_citation_inside_the_literal_before_the_first_entry_counts(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "prov.py",
        '_PRICING: dict = {\n    # Current lineup (https://x.example/models, 2026-09-26).\n    "a": 1.0,\n    # 2025-01-01 legacy\n    "b": 2.0,\n}\n',
    )
    assert _problems(tmp_path, {"prov.py:_PRICING": 45}) == []
    _write(tmp_path, "prov.py", '_PRICING: dict = {\n    "a": 1.0,\n    # https://x.example/models 2026-09-26\n    "b": 2.0,\n}\n')
    assert [m for _, _, m in _problems(tmp_path, {"prov.py:_PRICING": 45})] == [
        "_PRICING: cites no source and no check date: add `# Source: <url> (verified YYYY-MM-DD)` above it"
    ]


def test_a_comment_block_separated_by_a_blank_line_or_code_does_not_count(tmp_path: Path) -> None:
    _write(tmp_path, "prov.py", "# Source: https://example.com/p (verified 2026-09-26)\n\n" + TABLE)
    _write(tmp_path, "other.py", "# Source: https://example.com/p (verified 2026-09-26)\nX = 1\n" + TABLE)
    assert len(_problems(tmp_path, {"prov.py:_PRICING": 45, "other.py:_PRICING": 45})) == 2


@pytest.mark.parametrize(
    ("citation", "message"),
    [
        ("# Source: https://example.com/p\n", "_PRICING: cites https://example.com/p with no check date: add (verified YYYY-MM-DD)"),
        ("# from the same pricing page, 2026-09-26\n", "_PRICING: cites no source URL next to its check date"),
        ("# https://example.com/p 2026-10-09\n", "_PRICING: check date 2026-10-09 is in the future"),
        (
            "# https://example.com/p checked 2026-08-01\n",
            "_PRICING: last checked 2026-08-01 (63 days ago, limit 45): re-verify against https://example.com/p and update the date",
        ),
    ],
)
def test_each_citation_problem(tmp_path: Path, citation: str, message: str) -> None:
    _write(tmp_path, "prov.py", citation + TABLE)
    assert [m for _, _, m in _problems(tmp_path, {"prov.py:_PRICING": 45})] == [message]


def test_the_limit_is_per_table_and_the_boundary_day_passes(tmp_path: Path) -> None:
    _write(tmp_path, "prov.py", "# https://example.com/p 2026-09-03\n" + TABLE)
    assert _problems(tmp_path, {"prov.py:_PRICING": 30}) == []  # exactly 30 days
    assert len(_problems(tmp_path, {"prov.py:_PRICING": 29})) == 1
    assert _problems(tmp_path, {"prov.py:_PRICING": {"max_age_days": 30}}) == []


def test_a_declared_table_that_is_gone_or_in_a_missing_file_is_a_finding(tmp_path: Path) -> None:
    _write(tmp_path, "prov.py", "# https://example.com/p 2026-09-30\n_RENAMED = {}\n")
    assert [m for _, _, m in _problems(tmp_path, {"prov.py:_PRICING": 45})] == [
        "declared fact table _PRICING no longer exists at module level; update the declaration"
    ]
    found = _problems(tmp_path, {"gone.py:_PRICING": 45})
    assert len(found) == 1 and found[0][0] == "gone.py" and "cannot be read" in found[0][2]


def test_tables_bound_under_a_module_level_guard_are_found(tmp_path: Path) -> None:
    _write(tmp_path, "prov.py", "import sys\nif sys.version_info >= (3, 9):\n    # https://example.com/p 2026-09-30\n    _PRICING = {}\n")
    assert _problems(tmp_path, {"prov.py:_PRICING": 45}) == []


@pytest.mark.parametrize("bad", [{"prov.py": 45}, {"prov.py:_P": 0}, {"prov.py:_P": True}, {"prov.py:_P": "45"}, {":_P": 4}, {"prov.py:not-a-name": 4}])
def test_malformed_declarations_raise(bad: dict) -> None:
    with pytest.raises(CoreError):
        parse_declarations(bad)


def test_no_declaration_fails_the_floor(tmp_path: Path) -> None:
    with pytest.raises(CoreError, match="0 table"):
        find_external_fact_table_problems(tmp_path, {}, today=TODAY)
    assert find_external_fact_table_problems(tmp_path, {}, today=TODAY, min_tables=0) == []


def test_expiring_tables_are_listed_and_printed_never_failed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _write(tmp_path, "prov.py", "# Source: https://example.com/p (verified 2026-09-01)\n" + TABLE)
    tables = {"prov.py:_PRICING": 35}  # 32 of 35 days
    assert find_expiring_fact_tables(tmp_path, tables, today=TODAY) == [
        "prov.py:2: _PRICING expires on 2026-10-06 (checked 2026-09-01, limit 35 days); re-verify against https://example.com/p"
    ]
    assert find_expiring_fact_tables(tmp_path, tables, warn_days=2, today=TODAY) == []
    assert find_expiring_fact_tables(tmp_path, {"prov.py:_PRICING": 31}, today=TODAY) == []  # already stale: a failure, not a warning
    assert_external_fact_tables_current(tmp_path, tables, today=TODAY)
    assert "external-fact-tables early warning: prov.py:2: _PRICING expires on 2026-10-06" in capsys.readouterr().out
    assert_external_fact_tables_current(tmp_path, tables, today=TODAY, warn_days=0)
    assert capsys.readouterr().out == ""


def test_assert_names_every_problem(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", TABLE)
    _write(tmp_path, "b.py", "# https://example.com/p 2026-09-30\n" + TABLE)
    with pytest.raises(AssertionError, match=r"(?s)1 external fact table.*a.py:1: \[external-fact-table\] _PRICING"):
        assert_external_fact_tables_current(tmp_path, {"a.py:_PRICING": 45, "b.py:_PRICING": 45}, today=TODAY)


def test_undeclared_advisory_lists_numeric_fact_like_tables_not_declared(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/prov.py", TABLE + '_CONTEXT_WINDOW = {"a": 1, "b": 2, "c": 3}\n_NAMES = {"a": 1, "b": 2, "c": 3}\n_COST = {"a": 1}\n')
    _write(tmp_path, "tests/test_prov.py", TABLE)
    found = find_undeclared_fact_tables(tmp_path, {"pkg/prov.py:_PRICING": 45}, use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("pkg/prov.py", 6, UNDECLARED_RULE)]
    assert found[0].message.startswith("_CONTEXT_WINDOW looks like a table of external facts")


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path: Path) -> None:
    _write(tmp_path / "a", "prov.py", TABLE)
    _write(tmp_path / "b", "prov.py", TABLE, bom=True)
    assert _problems(tmp_path / "a", {"prov.py:_PRICING": 45}) == _problems(tmp_path / "b", {"prov.py:_PRICING": 45}) != []


def test_an_unparsable_declared_file_is_a_finding_by_name(tmp_path: Path) -> None:
    _write(tmp_path, "prov.py", "_PRICING = {\n")
    found = _problems(tmp_path, {"prov.py:_PRICING": 45})
    assert len(found) == 1 and found[0][0] == "prov.py" and "unparsable" in found[0][2]


def test_allow_unparsed_skips_an_unparsable_declared_file(tmp_path: Path) -> None:
    _write(tmp_path, "prov.py", "_PRICING = {\n")
    _write(tmp_path, "ok.py", "# https://example.com/p 2026-09-30\n" + TABLE)
    tables = {"prov.py:_PRICING": 45, "ok.py:_PRICING": 45}
    with pytest.raises(AssertionError, match=r"prov\.py"):
        assert_external_fact_tables_current(tmp_path, tables, today=TODAY)
    assert_external_fact_tables_current(tmp_path, tables, today=TODAY, allow_unparsed=True)
    with pytest.raises(CoreError, match="expected at least 3"):
        assert_external_fact_tables_current(tmp_path, tables, today=TODAY, min_files=3)
