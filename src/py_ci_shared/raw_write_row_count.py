"""Raw UPDATE or DELETE statements that run without a row-count check (report only; a shrink-only baseline)

A raw ``UPDATE``/``DELETE`` sent through ``execute`` (or ``exec_driver_sql``, or ``text(...)``) in a function that never reads
``rowcount``: the caller cannot tell whether any row changed, so a ``WHERE`` that matches nothing passes silently (50-F3).
The nearest correct code is the same statement whose result the function reads ``rowcount`` from. Migrations, test code and ``batch_*`` scripts are excluded:
their bulk statements are intended and run once.

Usage in a consumer's meta test::

    from py_ci_shared.raw_write_row_count import assert_raw_write_row_count

    def test_raw_write_row_count():
        assert_raw_write_row_count("src")
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from ._core.node_index import walk

__all__ = ["RULE", "assert_raw_write_row_count", "find_raw_write_row_count"]

RULE = "raw-write-row-count"


_RUNNERS = frozenset({"execute", "exec_driver_sql"})
_WRITE = re.compile(r"^\s*(UPDATE|DELETE)\b", re.IGNORECASE)
# Test code builds fixtures with raw writes on purpose, and its rowcount is not what a test asserts: the gate judges production code (50-F3).
_EXCLUDED_PARTS = frozenset({"alembic", "migrations", "tests"})


def _sql_text(node: ast.expr) -> Optional[str]:
    """The SQL text of a string, an f-string's literal parts, or the first argument of ``text(...)``; None otherwise."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
    if isinstance(node, ast.Call) and node.args:
        callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if callee == "text":
            return _sql_text(node.args[0])
    return None


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases) -> list[Finding]:
    """The findings in one parsed file. ``aliases.qualified_name(node)`` resolves ``np.x`` to ``numpy.x``."""
    if _EXCLUDED_PARTS & set(Path(rel).parts) or Path(rel).name.startswith("batch_"):
        return []
    out: list[Finding] = []
    seen: set[tuple[int, int]] = set()
    scopes: list[ast.AST] = [tree, *(n for n in walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    for scope in scopes:
        if any(isinstance(n, ast.Attribute) and n.attr == "rowcount" for n in walk(scope)):
            continue
        for call in walk(scope):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            callee = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
            if callee not in _RUNNERS:
                continue
            sql = _sql_text(call.args[0])
            match = _WRITE.match(sql) if sql is not None else None
            if match and (call.lineno, call.col_offset) not in seen:
                seen.add((call.lineno, call.col_offset))
                out.append(Finding(rel, call.lineno, RULE, f"raw {match.group(1).upper()} runs without a row-count check: nothing says whether a row changed"))
    return out


def find_raw_write_row_count(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree)))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_raw_write_row_count(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    report_only: bool = False,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept.

    *report_only* prints every finding to stderr and never fails: the first step before the gate blocks (50-F3)."""
    found = find_raw_write_row_count(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if report_only:
        for f in found:
            sys.stderr.write(f.render() + "\n")
        return
    guidance = "read rowcount from the result, or check it, so a statement that matches nothing is seen"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="raw_write_row_count", refresh_command="PY_CI_SHARED_REFRESH=raw_write_row_count")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} raw-write-row-count finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
