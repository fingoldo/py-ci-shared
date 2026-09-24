"""Shared check and helper: a test that mocks the statement constructor never compiles the statement.

glossum's 2026-09-01 audit, 08-H1 (HIGH): a partial-column ``INSERT`` into ``word_senses`` was rejected by
PostgreSQL on NOT NULL before ``ON CONFLICT`` could fire, so the CEFR import could never write a row. The test
covering that path patched ``sqlalchemy.dialects.postgresql.insert`` with a ``MagicMock``, so no statement was ever
built and nothing could have caught it. Mocking the session or the engine keeps the statement real; mocking the
constructor is the specific move that removes compilation from the test.

Two parts, usable separately:

* ``find_mocked_statement_constructors`` -- an AST walk over a test tree for ``patch`` / ``mock.patch`` /
  ``mocker.patch`` / ``patch.object`` / ``monkeypatch.setattr`` (however imported or aliased) whose target is a
  statement constructor (``insert``, ``pg_insert``, ``update``, ``delete``, ``select``, ``merge``, ``upsert``).
  A method on a class (``httpx.AsyncClient.delete``) and targets under HTTP/OS modules are not constructors.
* ``compile_pg`` -- compile a statement for the PostgreSQL dialect, so a test asserts against real SQL rather than
  a mock's ``call_args``. Imports SQLAlchemy lazily, so this module costs nothing where it is unused.

Two exclusions keep it quiet, both from the audit: a test about call ROUTING (did the code choose the upsert path
at all?) legitimately replaces the constructor and says so with ``@pytest.mark.routing`` (on the test, its class,
or a module/class ``pytestmark``); and a patch whose replacement is an autospec of the real constructor
(``autospec``/``spec``/``spec_set`` with a truthy value) preserves the signature, so the statement is still built.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import ImportAliases, ScanResult, scan_python

# The names that BUILD a statement. Mocking one of these means no SQL is ever compiled; mocking `session`,
# `engine` or `connection` does not, and is the normal way to keep a unit test off the database.
STATEMENT_CONSTRUCTORS: frozenset[str] = frozenset({"insert", "pg_insert", "update", "delete", "select", "merge", "upsert"})
ROUTING_MARKER = "routing"
# Patch callables, by their import-resolved dotted name (`from unittest import mock as m; m.patch` -> `unittest.mock.patch`).
_PATCH_NAMES = frozenset(
    {
        "patch",
        "patch.object",
        "mock.patch",
        "mock.patch.object",
        "unittest.mock.patch",
        "unittest.mock.patch.object",
        "mocker.patch",
        "mocker.patch.object",
        "monkeypatch.setattr",
    }
)
_AUTOSPEC_KEYWORDS = frozenset({"autospec", "spec", "spec_set"})
#: Patch targets under these modules are HTTP/OS calls that happen to be named ``delete``/``update``, not SQL.
DEFAULT_EXCLUDED_PREFIXES: tuple[str, ...] = ("requests.", "httpx.", "aiohttp.", "urllib3.", "urllib.", "boto3.", "botocore.", "os.", "shutil.")


def _dotted(node: ast.expr) -> str:
    """``mocker.patch.object`` for the call's func expression, as written."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_patch_call(call: ast.Call, aliases: ImportAliases) -> bool:
    if _dotted(call.func) in _PATCH_NAMES:
        return True
    return aliases.qualified_name(call) in _PATCH_NAMES


def _target(call: ast.Call) -> str:
    """The full dotted symbol a patch call replaces: ``"a.b.insert"``, or ``<object>.<attr>`` for ``patch.object``."""
    if not call.args:
        return ""
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if len(call.args) > 1:
        second = call.args[1]
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            return f"{_dotted(first) or '<object>'}.{second.value}"
    return ""


def _target_name(call: ast.Call) -> str:
    """The symbol a patch call replaces: the tail of ``"a.b.insert"``, or the second argument of ``patch.object``."""
    return _target(call).rsplit(".", 1)[-1]


def _is_method_target(target: str) -> bool:
    """``httpx.AsyncClient.delete`` / ``patch.object(Session, "delete")``: a method on a class, not a module-level
    statement constructor (classes are CapWords; modules and constructor functions are not)."""
    parts = target.split(".")
    return len(parts) >= 2 and parts[-2][:1].isupper()


def _truthy(value: ast.expr) -> bool:
    return not (isinstance(value, ast.Constant) and not value.value)


def _is_autospecced(call: ast.Call) -> bool:
    """True if the replacement keeps the real signature: ``autospec``/``spec``/``spec_set`` with a truthy value, or
    ``new=create_autospec(...)``. ``new_callable=MagicMock`` and ``autospec=False`` do not."""
    for keyword in call.keywords:
        if keyword.arg in _AUTOSPEC_KEYWORDS and _truthy(keyword.value):
            return True
        if keyword.arg in ("new", "new_callable") and isinstance(keyword.value, ast.Call) and "autospec" in _dotted(keyword.value.func):
            return True
        if keyword.arg == "new_callable" and "autospec" in _dotted(keyword.value):
            return True
    return False


_Marked = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _is_routing_mark(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return _dotted(target).endswith(f"mark.{ROUTING_MARKER}")


def _pytestmark_is_routing(body: list[ast.stmt]) -> bool:
    """``pytestmark = pytest.mark.routing`` (or a list/tuple holding it) in a module or class body."""
    for stmt in body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets):
            value: ast.expr = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "pytestmark" and stmt.value is not None:
            value = stmt.value
        else:
            continue
        items = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        if any(_is_routing_mark(item) for item in items):
            return True
    return False


def _routing_marked(node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]) -> bool:
    """True if a function or class carries ``@pytest.mark.routing`` (or a class sets a routing ``pytestmark``)."""
    if any(_is_routing_mark(decorator) for decorator in node.decorator_list):
        return True
    return isinstance(node, ast.ClassDef) and _pytestmark_is_routing(node.body)


def _enclosing_marked(tree: ast.Module) -> set[int]:
    """Line numbers covered by a routing-marked function or class, decorators included (a stacked ``@patch`` sits
    above the ``def`` line); every line when the module sets a routing ``pytestmark``."""
    if _pytestmark_is_routing(tree.body):
        return set(range(1, max((getattr(n, "end_lineno", 0) or 0) for n in ast.walk(tree)) + 2))
    covered: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _Marked) and _routing_marked(node):
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            covered |= set(range(start, (node.end_lineno or node.lineno) + 1))
    return covered


def _scan(test_files: Iterable[Path], root: Path, min_files: int) -> ScanResult:
    return scan_python([Path(p) for p in test_files], root=root, min_files=min_files)


def find_mocked_statement_constructors(
    test_files: Iterable[Path],
    root: Path,
    *,
    constructors: frozenset[str] = STATEMENT_CONSTRUCTORS,
    module_prefixes: Optional[Iterable[str]] = None,
    excluded_prefixes: Iterable[str] = DEFAULT_EXCLUDED_PREFIXES,
    min_files: int = 1,
) -> list[str]:
    """``path:line: patches <name>`` for every patch of a statement constructor in *test_files*.

    *module_prefixes*, when given, limits the check to string targets under those modules (``("myapp.",
    "sqlalchemy.")``); *excluded_prefixes* drops targets under modules that are not SQL at all. A file that cannot be
    read or parsed is reported, and so is a corpus where fewer than *min_files* files parsed.
    """
    scan = _scan(test_files, root, min_files)
    problems = [f"{p.rel}:{p.line}: {p.kind}: {p.message} - not checked" for p in scan.unparsed]
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    wanted = tuple(module_prefixes) if module_prefixes is not None else None
    excluded = tuple(excluded_prefixes)
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        marked = _enclosing_marked(parsed.tree)
        for node in ast.walk(parsed.tree):
            if not isinstance(node, ast.Call) or not _is_patch_call(node, aliases):
                continue
            target = _target(node)
            name = target.rsplit(".", 1)[-1]
            if name not in constructors or node.lineno in marked or _is_autospecced(node) or _is_method_target(target):
                continue
            if target.startswith(excluded) or (wanted is not None and not target.startswith(wanted)):
                continue
            problems.append(
                f"{parsed.rel}:{node.lineno}: patches the statement constructor `{name}`, so no statement is built and "
                f"nothing is compiled: the test passes against SQL the database would reject. Patch the session or "
                f"the engine and assert on the compiled statement, or mark the test @pytest.mark.routing if it is "
                f"only about which path was chosen."
            )
    return problems


def assert_no_mocked_statement_constructors(test_files: Iterable[Path], root: Path, **kwargs: Any) -> None:
    """Fail on any test that replaces a statement constructor with a mock (and on unparsable files or an empty
    corpus; see :func:`find_mocked_statement_constructors`)."""
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
