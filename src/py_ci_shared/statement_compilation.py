"""Shared check and helper: a test that mocks the statement constructor never compiles the statement.

glossum's 2026-09-01 audit, 08-H1 (HIGH): a partial-column ``INSERT`` into ``word_senses`` was rejected by
PostgreSQL on NOT NULL before ``ON CONFLICT`` could fire, so the CEFR import could never write a row. The test
covering that path patched ``sqlalchemy.dialects.postgresql.insert`` with a ``MagicMock``, so no statement was ever
built and nothing could have caught it. Mocking the session or the engine keeps the statement real; mocking the
constructor is the specific move that removes compilation from the test.

Two parts, usable separately:

* ``find_mocked_statement_constructors`` -- an AST walk over a test tree for ``patch`` / ``mocker.patch`` /
  ``patch.object`` whose target is a statement constructor (``insert``, ``pg_insert``, ``update``, ``delete``,
  ``select``, ``merge``, ``upsert``).
* ``compile_pg`` -- compile a statement for the PostgreSQL dialect, so a test asserts against real SQL rather than
  a mock's ``call_args``. Imports SQLAlchemy lazily, so this module costs nothing where it is unused.

Two exclusions keep it quiet, both from the audit: a test about call ROUTING (did the code choose the upsert path
at all?) legitimately replaces the constructor and says so with ``@pytest.mark.routing``; and a patch whose
replacement is an autospec of the real constructor preserves the signature, so the statement is still built.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# The names that BUILD a statement. Mocking one of these means no SQL is ever compiled; mocking `session`,
# `engine` or `connection` does not, and is the normal way to keep a unit test off the database.
STATEMENT_CONSTRUCTORS: frozenset[str] = frozenset({"insert", "pg_insert", "update", "delete", "select", "merge", "upsert"})
ROUTING_MARKER = "routing"
_PATCH_NAMES = frozenset({"patch", "patch.object", "mocker.patch", "mocker.patch.object", "monkeypatch.setattr"})
_AUTOSPEC_KEYWORDS = frozenset({"autospec", "spec", "spec_set", "new_callable"})


def _dotted(node: ast.expr) -> str:
    """``mocker.patch.object`` for the call's func expression, as written."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _target_name(call: ast.Call) -> str:
    """The symbol a patch call replaces: the tail of ``"a.b.insert"``, or the second argument of ``patch.object``."""
    if not call.args:
        return ""
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.rsplit(".", 1)[-1]
    if len(call.args) > 1:
        second = call.args[1]
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            return second.value
    return ""


def _is_autospecced(call: ast.Call) -> bool:
    """True if the replacement keeps the real signature, so the statement is still built."""
    for keyword in call.keywords:
        if keyword.arg in _AUTOSPEC_KEYWORDS:
            return True
        if keyword.arg == "new" and isinstance(keyword.value, ast.Call) and "autospec" in _dotted(keyword.value.func):
            return True
    return False


_Marked = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _routing_marked(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    """True if a function carries ``@pytest.mark.routing``: a test about which path was chosen, not about SQL."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _dotted(target).endswith(f"mark.{ROUTING_MARKER}"):
            return True
    return False


def _enclosing_marked(tree: ast.Module) -> set[int]:
    """Line numbers covered by a routing-marked function or class."""
    covered: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _Marked) and _routing_marked(node):
            covered |= set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return covered


def find_mocked_statement_constructors(
    test_files: Iterable[Path],
    root: Path,
    *,
    constructors: frozenset[str] = STATEMENT_CONSTRUCTORS,
) -> list[str]:
    """``path:line: patches <name>`` for every patch of a statement constructor in *test_files*."""
    problems: list[str] = []
    for path in test_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        marked = _enclosing_marked(tree)
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _dotted(node.func) not in _PATCH_NAMES:
                continue
            name = _target_name(node)
            if name not in constructors or node.lineno in marked or _is_autospecced(node):
                continue
            problems.append(
                f"{rel}:{node.lineno}: patches the statement constructor `{name}`, so no statement is built and "
                f"nothing is compiled: the test passes against SQL the database would reject. Patch the session or "
                f"the engine and assert on the compiled statement, or mark the test @pytest.mark.routing if it is "
                f"only about which path was chosen."
            )
    return problems


def assert_no_mocked_statement_constructors(test_files: Iterable[Path], root: Path, **kwargs: Any) -> None:
    """Fail on any test that replaces a statement constructor with a mock."""
    import pytest

    problems = find_mocked_statement_constructors(test_files, root, **kwargs)
    if problems:
        pytest.fail(f"{len(problems)} test(s) mock away the statement they are testing:\n  " + "\n  ".join(problems))


def compile_pg(statement: Any, *, literal_binds: bool = False) -> str:
    """*statement* compiled for the PostgreSQL dialect, as text.

    A statement compiled for SQLAlchemy's default dialect does not exercise the PostgreSQL-specific grammar a test
    usually means to assert on (``ON CONFLICT``, ``RETURNING``, the JSONB operators), so a test that compiles
    without naming a dialect can pass on SQL the database would reject.
    """
    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": literal_binds}))
