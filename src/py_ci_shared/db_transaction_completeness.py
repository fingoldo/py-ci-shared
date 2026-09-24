"""Shared check: a function that runs a statement on a database-handle parameter must also
close the transaction it opened -- a ``commit()`` or ``rollback()`` on that same name,
somewhere in its own body.

WHERE THIS CAME FROM
---------------------
2026-09-12, the ``new_scraper`` monorepo. ``load_prev_hashes`` ran a plain ``SELECT`` via
``db.execute(...)`` / ``db.fetchall()`` and returned, with no ``db.commit()``. The connection
class defaults to ``autocommit=False``, so the SELECT left it sitting in an open transaction.
Its one caller runs the read, then hands off to worker threads for up to fourteen minutes on
their OWN connections -- so the connection the SELECT ran on then sat idle-in-transaction well
past the server's ``idle_in_transaction_session_timeout`` (120s) and was killed by the server
every single cycle. The failure was invisible in the test suite (a mock records the call and
returns whatever `fetchall` was told to), invisible in the diff (a missing line, not a wrong
one), and ran in production for an unmeasured span before a human noticed the scraper's own log
alternating "server closed the connection unexpectedly" with "Reconnected after transient
error" every cycle.

The sibling read helper in the same codebase, ``fetch_column``, commits after its own SELECT for
exactly this reason -- so the fix pattern already existed, once, next to the bug that didn't
follow it.

WHAT THIS CHECKS, AND WHAT IT CANNOT
-------------------------------------
For every function definition in the scanned files: if the function's OWN parameter list names
a database-handle-shaped argument (matched by name, see *handle_names*), and the function's body
calls ``<that name>.<execute_name>(...)`` for some *execute_name* in *execute_names*, then the
same body must also call ``<that name>.<complete_name>(...)`` for some *complete_name* in
*complete_names* -- ``.commit(`` or ``.rollback(`` by default.

It is a proxy, an AST walk, not a data-flow analysis -- the same honesty ``effect_assertion_parity``
states about its own check. Two shapes it cannot see:

* A function that delegates the open transaction to its CALLER on purpose (returns the handle,
  or is itself called inside a caller's own ``with ... as db:`` block that commits after
  several such calls). These are real and not bugs; use *known_delegated_commits* to record them,
  the same "genuinely consumed a different way" escape ``config_field_consumption`` grants.
* A handle wrapped in a context manager that commits on ``__exit__`` (``with borrowed() as db:``
  where ``borrowed`` itself commits) -- the call site never needs its own `.commit()`, and this
  check does not try to know what a context manager does on the way out. Add the WRAPPING
  function's own name to *autocommitting_context_managers* so a `with <name>(...) as db:` block
  is treated as already complete.

A ratchet, not a gate on first use, the same shape ``effect_assertion_parity``/``baseline_ratchet``
already use: existing gaps go in a JSON baseline with a reason; the list can only shrink.

Usage::

    from py_ci_shared.db_transaction_completeness import assert_no_new_incomplete_transaction

    def test_no_new_incomplete_db_transaction():
        assert_no_new_incomplete_transaction(
            files=sorted(REPO.rglob("*.py")),
            repo_root=REPO,
            baseline_path=Path(__file__).with_name("_db_transaction_baseline.json"),
        )
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import DEFAULT_EXCLUDE, Baseline, ScanResult, iter_files, refresh_requested, scan_python

__all__ = [
    "DEFAULT_HANDLE_NAMES",
    "DEFAULT_EXECUTE_NAMES",
    "DEFAULT_COMPLETE_NAMES",
    "IncompleteTransaction",
    "find_incomplete_transactions",
    "assert_no_new_incomplete_transaction",
]

#: Parameter names treated as "this is a database handle". Matched by exact name -- narrow on
#: purpose, so a `db`-named list comprehension variable elsewhere in a function is not mistaken
#: for the parameter.
DEFAULT_HANDLE_NAMES = frozenset({"db", "conn", "connection", "cur", "cursor"})

DEFAULT_EXECUTE_NAMES = frozenset({"execute", "executemany", "execute_values"})

DEFAULT_COMPLETE_NAMES = frozenset({"commit", "rollback"})

#: Kept for callers that imported it; enumeration now goes through ``_core.iter_files`` and ``DEFAULT_EXCLUDE``.
_SKIP_DIRS = DEFAULT_EXCLUDE

#: Methods on a handle that return a cursor bound to the handle's transaction (psycopg/sqlite3/DB-API).
_CURSOR_FACTORIES = frozenset({"cursor"})

REFRESH_FLAG = "--refresh-db-transaction-baseline"


class IncompleteTransaction:
    __slots__ = ("function", "handle", "lineno", "path", "qualname")

    def __init__(self, path: str, function: str, lineno: int, handle: str, qualname: Optional[str] = None) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.handle = handle
        self.qualname = qualname or function

    @property
    def key(self) -> str:
        """``path::qualname::handle`` -- stable across line shifts, and ``A.run``/``B.run`` stay apart."""
        return f"{self.path}::{self.qualname}::{self.handle}"

    def __repr__(self) -> str:  # pragma: no cover -- debugging aid only
        return f"IncompleteTransaction({self.key} at line {self.lineno})"


def _iter_py_files(root: Path) -> Iterable[Path]:
    return iter_files(root, ("*.py",), exclude=DEFAULT_EXCLUDE)


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = fn.args
    names = {a.arg for a in args.posonlyargs} | {a.arg for a in args.args} | {a.arg for a in args.kwonlyargs}
    if args.vararg:
        names.add(args.vararg.arg)
    return names


def _with_bound_names(fn: ast.FunctionDef | ast.AsyncFunctionDef, autocommitting: frozenset[str]) -> set[str]:
    """Names bound by a ``with <autocommitting-call>(...) as name:`` inside *fn* -- these are
    treated as already-committed on exit, so a bare-handle check for them is suppressed."""
    bound: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in autocommitting and item.optional_vars is not None and isinstance(item.optional_vars, ast.Name):
                bound.add(item.optional_vars.id)
    return bound


def _cursor_of(value: Optional[ast.AST], handles: set[str]) -> Optional[str]:
    """The handle *value* opens a cursor on (``conn.cursor(...)``), else None."""
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr in _CURSOR_FACTORIES
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id in handles
    ):
        return value.func.value.id
    return None


def _cursor_aliases(fn: ast.FunctionDef | ast.AsyncFunctionDef, handles: set[str]) -> dict[str, str]:
    """``{cursor name: handle}`` for ``cur = conn.cursor()`` and ``with conn.cursor() as cur:`` in *fn*.

    A statement run on the cursor runs in the handle's transaction, so it needs the handle's commit just the same.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            handle = _cursor_of(node.value, handles)
            if handle is not None:
                aliases.update({t.id: handle for t in targets if isinstance(t, ast.Name)})
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                handle = _cursor_of(item.context_expr, handles)
                if handle is not None and isinstance(item.optional_vars, ast.Name):
                    aliases[item.optional_vars.id] = handle
    return aliases


def _called_methods(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, on_names: set[str], methods: frozenset[str], aliases: Optional[dict[str, str]] = None
) -> set[str]:
    """`{handle_name}` for every `<handle_name or its cursor>.<method in methods>(...)` call found in *fn*."""
    aliases = aliases or {}
    found: set[str] = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in methods:
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name):
            if receiver.id in on_names:
                found.add(receiver.id)
            elif aliases.get(receiver.id) in on_names:
                found.add(aliases[receiver.id])
        else:
            handle = _cursor_of(receiver, on_names)  # conn.cursor().execute(...)
            if handle is not None:
                found.add(handle)
    return found


def _functions_with_qualnames(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                out.append((qual, child))
                visit(child, f"{qual}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def _find_in_scan(
    scan: ScanResult,
    *,
    handle_names: frozenset[str],
    execute_names: frozenset[str],
    complete_names: frozenset[str],
    autocommitting_context_managers: frozenset[str],
) -> list[IncompleteTransaction]:
    out: list[IncompleteTransaction] = []
    for parsed in scan:
        for qual, node in _functions_with_qualnames(parsed.tree):
            params = _param_names(node) & handle_names
            with_bound = _with_bound_names(node, autocommitting_context_managers)
            candidates = params - with_bound
            if not candidates:
                continue
            aliases = _cursor_aliases(node, candidates)
            executed = _called_methods(node, candidates, execute_names, aliases)
            completed = _called_methods(node, candidates, complete_names)
            out.extend(IncompleteTransaction(parsed.rel, node.name, node.lineno, handle, qual) for handle in sorted(executed - completed))
    return out


def find_incomplete_transactions(
    files: Iterable[Path],
    repo_root: Path,
    *,
    handle_names: frozenset[str] = DEFAULT_HANDLE_NAMES,
    execute_names: frozenset[str] = DEFAULT_EXECUTE_NAMES,
    complete_names: frozenset[str] = DEFAULT_COMPLETE_NAMES,
    autocommitting_context_managers: frozenset[str] = frozenset(),
) -> list[IncompleteTransaction]:
    """Every function whose own db-handle PARAMETER (or a cursor opened on it) is `.execute(...)`-d but never
    `.commit(`/`.rollback(`-ed in the same body, and is not delegated via `autocommitting_context_managers`.

    Files that cannot be read or parsed are not in this list; :func:`assert_no_new_incomplete_transaction`
    reports them as failures."""
    scan = scan_python(list(files), root=repo_root)
    return _find_in_scan(
        scan,
        handle_names=handle_names,
        execute_names=execute_names,
        complete_names=complete_names,
        autocommitting_context_managers=autocommitting_context_managers,
    )


def assert_no_new_incomplete_transaction(
    files: Iterable[Path],
    repo_root: Path,
    baseline_path: Path,
    *,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Any = None,
    **find_kwargs: Any,
) -> None:
    """Fail on an incomplete transaction not already in *baseline_path*. Ratchet, not a gate:
    the baseline records what was already true, and the list can only shrink from here.

    Also fails when fewer than *min_files* files parsed, when any file could not be parsed, and when the baseline
    is missing (create it with ``--refresh-db-transaction-baseline`` or ``PY_CI_SHARED_REFRESH=db-transaction``).
    """
    import pytest

    scan = scan_python(list(files), root=repo_root, min_files=min_files)
    found = _find_in_scan(
        scan,
        handle_names=find_kwargs.pop("handle_names", DEFAULT_HANDLE_NAMES),
        execute_names=find_kwargs.pop("execute_names", DEFAULT_EXECUTE_NAMES),
        complete_names=find_kwargs.pop("complete_names", DEFAULT_COMPLETE_NAMES),
        autocommitting_context_managers=find_kwargs.pop("autocommitting_context_managers", frozenset()),
    )
    if find_kwargs:
        raise TypeError(f"unexpected keyword argument(s): {sorted(find_kwargs)}")
    scan.assert_ok()
    do_refresh = refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request)
    outcome = Baseline(baseline_path, gate="db-transaction", refresh_command=f"pytest {REFRESH_FLAG}").enforce(
        [t.key for t in found],
        refresh=do_refresh,
        describe={t.key: f"{t.key} (line {t.lineno})" for t in found},
        guidance=(
            "these functions execute a statement on their own db-handle parameter and never commit or rollback it -- "
            "the connection is left in an open transaction on return. Add `<handle>.commit()` (or `.rollback()`), or if the "
            "caller genuinely owns the transaction, record it in the baseline with a reason."
        ),
    )
    if outcome.refreshed:
        pytest.skip(outcome.message)
    if not outcome.ok or outcome.stale:
        raise AssertionError(outcome.message)
