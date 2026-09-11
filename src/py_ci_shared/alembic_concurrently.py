"""Shared check: an Alembic migration runs ``CONCURRENTLY`` only inside ``autocommit_block()``.

``CREATE``/``DROP INDEX CONCURRENTLY`` cannot run inside a transaction block, and Alembic wraps every
migration in one. A migration that forgets ``with op.get_context().autocommit_block():`` imports, parses
and passes every offline test, then fails with ``cannot run inside a transaction block`` the moment it is
applied to a real database. Both spellings count: a raw ``op.execute("... CONCURRENTLY ...")`` and
``op.create_index(..., postgresql_concurrently=True)``.

The check is on the AST, not the text, so formatting does not matter; a baseline lets a repo adopt it
over an existing violation, and an entry that no longer occurs fails, so the baseline drains. (glossum;
the same failure, outside Alembic, is why social's runbooks are one CONCURRENTLY statement per file.)

Usage::

    from py_ci_shared.alembic_concurrently import assert_concurrently_is_in_autocommit_blocks

    def test_no_concurrently_outside_autocommit_block():
        assert_concurrently_is_in_autocommit_blocks(REPO_ROOT / "alembic" / "versions")
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


def _uses_concurrently(node: ast.Call) -> bool:
    if any(kw.arg == "postgresql_concurrently" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in node.keywords):
        return True
    return any(isinstance(a, ast.Constant) and isinstance(a.value, str) and "CONCURRENTLY" in a.value.upper() for a in node.args)


def _in_autocommit_block(stack: "list[ast.With]") -> bool:
    return any(
        isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Attribute) and item.context_expr.func.attr == "autocommit_block"
        for with_node in stack
        for item in with_node.items
    )


def unguarded_concurrently_lines(tree: ast.AST) -> list[int]:
    """Line numbers of CONCURRENTLY calls not lexically inside an ``autocommit_block()`` ``with``."""
    out: list[int] = []

    def walk(node: ast.AST, stack: "list[ast.With]") -> None:
        if isinstance(node, ast.With):
            stack = [*stack, node]
        elif isinstance(node, ast.Call) and _uses_concurrently(node) and not _in_autocommit_block(stack):
            out.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            walk(child, stack)

    walk(tree, [])
    return out


def find_unguarded_concurrently(versions_dir: Path) -> set[str]:
    """``{"<migration file name>:<line>"}`` for every unguarded CONCURRENTLY call."""
    out: set[str] = set()
    for py in sorted(versions_dir.glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        out.update(f"{py.name}:{line}" for line in unguarded_concurrently_lines(tree))
    return out


def assert_concurrently_is_in_autocommit_blocks(versions_dir: Path, *, baseline: "Path | None" = None) -> None:
    """Fail on an unguarded call not in *baseline*, on a stale baseline entry, and on an empty versions dir."""
    import pytest

    if not any(versions_dir.glob("*.py")):
        pytest.fail(f"no migrations found in {versions_dir}; the check is reading nothing")
    found = find_unguarded_concurrently(versions_dir)
    allowed: set[str] = set(json.loads(baseline.read_text(encoding="utf-8"))) if baseline is not None and baseline.is_file() else set()
    problems = []
    new = sorted(found - allowed)
    if new:
        problems.append(
            "CONCURRENTLY index operations outside op.get_context().autocommit_block(); applied to a real database these "
            "fail with 'cannot run inside a transaction block':\n    " + "\n    ".join(new)
        )
    stale = sorted(allowed - found)
    if stale:
        problems.append(f"baseline entries that no longer occur (delete them from {baseline}):\n    " + "\n    ".join(stale))
    if problems:
        pytest.fail("\n  ".join(problems))
