"""Shared check: a string constant is compared by value, not by identity.

``x is SOME_SQL`` holds only while CPython keeps the two strings as one object: it depends on
interning, and on every caller passing the defining module's own object rather than an equal copy
(built, reloaded, read from a config). production_scrapers decided insert-versus-upsert that way
until 2026-09-12 and needed an import-time guard, and a load-bearing SQL comment, to keep the two
constants from ever interning into one; an equal copy of the insert statement silently lost the claim
guard. ruff's F632 catches ``is`` against a LITERAL, not against a name bound to one.

The rule: an ``is`` / ``is not`` where either side is a name (or attribute) bound at module level, in
any of the scanned files, to a string or bytes value -- a literal, an f-string, or a ``+`` of those.
Sentinel objects (``_MISSING = object()``), ``None`` and booleans are untouched.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path


def _stringish(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes))
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _stringish(node.left) or _stringish(node.right)
    return False


def _parse(path: Path) -> "ast.Module | None":
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def string_constant_names(files: Iterable[Path]) -> set[str]:
    """Names bound at module level to a string or bytes value in any of *files*."""
    names: set[str] = set()
    for path in files:
        tree = _parse(path)
        if tree is None:
            continue
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else []
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            if value is not None and _stringish(value):
                names |= {t.id for t in targets if isinstance(t, ast.Name)}
    return names


def _name(node: ast.AST) -> "str | None":
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def find_identity_comparisons(files: Iterable[Path], *, root: "Path | None" = None, names: "set[str] | None" = None) -> list[str]:
    files = list(files)
    consts = names if names is not None else string_constant_names(files)
    problems: list[str] = []
    for path in files:
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or not any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
                continue
            if any(_name(side) in consts for side in (node.left, *node.comparators)):
                rel = path.relative_to(root).as_posix() if root else path.as_posix()
                problems.append(f"{rel}:{node.lineno}: `{ast.unparse(node)[:100]}` compares a string constant by identity -- use ==")
    return problems


def assert_no_identity_comparisons(files: Iterable[Path], *, root: "Path | None" = None, min_files: int = 1) -> None:
    import pytest

    files = list(files)
    if len(files) < min_files:
        pytest.fail(f"only {len(files)} file(s) scanned; expected at least {min_files} -- this would check nothing")
    problems = find_identity_comparisons(files, root=root)
    if problems:
        pytest.fail(f"{len(problems)} identity comparison(s) against a string constant:\n  " + "\n  ".join(problems))
