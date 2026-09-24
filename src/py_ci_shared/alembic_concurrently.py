"""Shared check: an Alembic migration runs ``CONCURRENTLY`` only inside ``autocommit_block()``.

``CREATE``/``DROP INDEX CONCURRENTLY`` cannot run inside a transaction block, and Alembic wraps every
migration in one. A migration that forgets ``with op.get_context().autocommit_block():`` imports, parses
and passes every offline test, then fails with ``cannot run inside a transaction block`` the moment it is
applied to a real database. Both spellings count: a raw ``op.execute("... CONCURRENTLY ...")`` and
``op.create_index(..., postgresql_concurrently=True)``.

The check is on the AST, not the text, so formatting does not matter. The SQL may be a literal, an f-string,
a concatenation, a ``sqltext=``/``text(...)`` argument or a name bound to such a string in the same file. A
baseline lets a repo adopt it over an existing violation; its keys are the file name plus the call's normalised
source (never a line number), and an entry that no longer occurs fails, so the baseline drains. A migration that
cannot be read or parsed fails the check rather than being skipped. (glossum;
the same failure, outside Alembic, is why social's runbooks are one CONCURRENTLY statement per file.)

Usage::

    from py_ci_shared.alembic_concurrently import assert_concurrently_is_in_autocommit_blocks

    def test_no_concurrently_outside_autocommit_block():
        assert_concurrently_is_in_autocommit_blocks(REPO_ROOT / "alembic" / "versions")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import Baseline, Finding, ScanResult, refresh_requested, scan_python

RULE = "alembic-concurrently"
REFRESH_FLAG = "--refresh-alembic-concurrently-baseline"

_Scope = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _string_parts(node: ast.AST, names: "dict[str, list[str]]") -> Iterable[str]:
    """Every string that flows into *node* without crossing another call (that call is judged on its own)."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, ast.Constant) and isinstance(cur.value, str):
            yield cur.value
        elif isinstance(cur, ast.Name):
            yield from names.get(cur.id, ())
        elif not isinstance(cur, ast.Call) or cur is node:
            stack.extend(ast.iter_child_nodes(cur))


def _truthy(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant):
        return bool(value.value)
    return True  # a computed flag may be true; the check cannot prove it is not


def _uses_concurrently(node: ast.Call, names: "Optional[dict[str, list[str]]]" = None) -> bool:
    bound = names or {}
    if any(kw.arg == "postgresql_concurrently" and _truthy(kw.value) for kw in node.keywords):
        return True
    for arg in [*node.args, *(kw.value for kw in node.keywords)]:
        if isinstance(arg, ast.Call):
            continue
        if any("CONCURRENTLY" in text.upper() for text in _string_parts(arg, bound)):
            return True
    return False


def _string_bindings(tree: ast.AST) -> "dict[str, list[str]]":
    """``name -> [string fragments]`` for every ``name = <str expr>`` / ``name: T = <str expr>`` in the file."""
    out: "dict[str, list[str]]" = {}
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: Optional[ast.expr] = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None:
            continue
        texts = [n.value for n in ast.walk(value) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for target in targets:
            if isinstance(target, ast.Name) and texts:
                out.setdefault(target.id, []).extend(texts)
    return out


def _is_autocommit_ctx(expr: ast.expr) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    return (isinstance(func, ast.Attribute) and func.attr == "autocommit_block") or (isinstance(func, ast.Name) and func.id == "autocommit_block")


def _in_autocommit_block(stack: "list[Any]") -> bool:
    return any(_is_autocommit_ctx(item.context_expr) for with_node in stack for item in with_node.items)


def _unguarded_calls(tree: ast.AST) -> list[ast.Call]:
    names = _string_bindings(tree)
    out: list[ast.Call] = []

    def walk(node: ast.AST, stack: "list[Any]") -> None:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            stack = [*stack, node]
        elif isinstance(node, _Scope):
            # A function defined inside the block runs whenever it is called, usually after the block has exited.
            stack = []
        elif isinstance(node, ast.Call) and _uses_concurrently(node, names) and not _in_autocommit_block(stack):
            out.append(node)
        for child in ast.iter_child_nodes(node):
            walk(child, stack)

    walk(tree, [])
    return out


def unguarded_concurrently_lines(tree: ast.AST) -> list[int]:
    """Line numbers of CONCURRENTLY calls not inside an ``autocommit_block()`` ``with`` of the same scope."""
    return [call.lineno for call in _unguarded_calls(tree)]


def collect_unguarded_concurrently(versions_dir: Path) -> "tuple[list[Finding], ScanResult]":
    """``(findings, scan)``: one Finding per unguarded call (key: file name + normalised call), unparsed files in the scan."""
    versions_dir = Path(versions_dir)
    scan = scan_python(sorted(versions_dir.glob("*.py")), root=versions_dir, min_files=1)
    findings = [Finding(f.rel, call.lineno, RULE, ast.unparse(call)) for f in scan for call in _unguarded_calls(f.tree)]
    return findings, scan


def find_unguarded_concurrently(versions_dir: Path) -> set[str]:
    """``{"<migration file name>:<line>"}`` for every unguarded CONCURRENTLY call, plus
    ``"<file>:<line>: unparsable: <why>"`` for every migration that could not be read or parsed."""
    findings, scan = collect_unguarded_concurrently(versions_dir)
    return {f"{f.path}:{f.line}" for f in findings} | {p.render() for p in scan.unparsed}


def assert_concurrently_is_in_autocommit_blocks(versions_dir: Path, *, baseline: "Path | None" = None, request: Any = None) -> None:
    """Fail on an unguarded call not in *baseline*, on a stale or missing baseline, on an unparsable migration and on
    an empty versions dir. Rewrite the baseline with ``--refresh-alembic-concurrently-baseline`` (or
    ``PY_CI_SHARED_REFRESH=alembic-concurrently``)."""
    import pytest

    findings, scan = collect_unguarded_concurrently(versions_dir)
    if not scan.files and not scan.unparsed:
        pytest.fail(f"no migrations found in {versions_dir}; the check is reading nothing")
    problems: list[str] = []
    if scan.unparsed:
        problems.append("migrations that could not be read or parsed, so they were not checked:\n    " + "\n    ".join(p.render() for p in scan.unparsed))
    guidance = (
        "CONCURRENTLY index operations outside op.get_context().autocommit_block(); applied to a real database these "
        "fail with 'cannot run inside a transaction block'"
    )
    if baseline is None:
        if findings:
            problems.append(guidance + ":\n    " + "\n    ".join(f.render() for f in findings))
    else:
        outcome = Baseline(baseline, gate=RULE, refresh_command=f"pytest {REFRESH_FLAG}").enforce(
            findings, refresh=refresh_requested(REFRESH_FLAG, request), guidance=guidance
        )
        if outcome.refreshed and not problems:
            pytest.skip(outcome.message)
        if not outcome.ok or outcome.stale:
            problems.append(outcome.message)
    if problems:
        pytest.fail("\n  ".join(problems))
