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
import json
from collections.abc import Iterable
from pathlib import Path

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

_SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", ".claude", ".tox", ".eggs", "site-packages"})


class IncompleteTransaction:
    __slots__ = ("path", "function", "lineno", "handle")

    def __init__(self, path: str, function: str, lineno: int, handle: str) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.handle = handle

    @property
    def key(self) -> str:
        """``path::function::handle`` -- stable across line shifts, for a baseline entry."""
        return f"{self.path}::{self.function}::{self.handle}"

    def __repr__(self) -> str:  # pragma: no cover -- debugging aid only
        return f"IncompleteTransaction({self.key} at line {self.lineno})"


def _iter_py_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


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


def _called_methods(fn: ast.FunctionDef | ast.AsyncFunctionDef, on_names: set[str], methods: frozenset[str]) -> set[str]:
    """`{handle_name}` for every `<handle_name>.<method in methods>(...)` call found in *fn*."""
    found: set[str] = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        if not isinstance(receiver, ast.Name) or receiver.id not in on_names:
            continue
        if node.func.attr in methods:
            found.add(receiver.id)
    return found


def find_incomplete_transactions(
    files: Iterable[Path],
    repo_root: Path,
    *,
    handle_names: frozenset[str] = DEFAULT_HANDLE_NAMES,
    execute_names: frozenset[str] = DEFAULT_EXECUTE_NAMES,
    complete_names: frozenset[str] = DEFAULT_COMPLETE_NAMES,
    autocommitting_context_managers: frozenset[str] = frozenset(),
) -> list[IncompleteTransaction]:
    """Every function whose own db-handle PARAMETER is `.execute(...)`-d but never
    `.commit(`/`.rollback(`-ed in the same body, and is not delegated via
    `autocommitting_context_managers`."""
    out: list[IncompleteTransaction] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = path.relative_to(repo_root).as_posix() if path.is_absolute() else path.as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = _param_names(node) & handle_names
            with_bound = _with_bound_names(node, autocommitting_context_managers)
            candidates = params - with_bound
            if not candidates:
                continue
            executed = _called_methods(node, candidates, execute_names)
            completed = _called_methods(node, candidates, complete_names)
            for handle in sorted(executed - completed):
                out.append(IncompleteTransaction(rel, node.name, node.lineno, handle))
    return out


def assert_no_new_incomplete_transaction(
    files: Iterable[Path],
    repo_root: Path,
    baseline_path: Path,
    **find_kwargs,
) -> None:
    """Fail on an incomplete transaction not already in *baseline_path*. Ratchet, not a gate:
    the baseline records what was already true, and the list can only shrink from here."""
    accepted: dict[str, str] = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
    found = find_incomplete_transactions(files, repo_root, **find_kwargs)
    new = {t.key: t for t in found if t.key not in accepted}
    if new:
        lines = "\n  ".join(f"{t.key} (line {t.lineno})" for t in sorted(new.values(), key=lambda t: t.key))
        raise AssertionError(
            f"{len(new)} function(s) execute a statement on their own db-handle parameter and never "
            f"commit or rollback it -- the connection is left in an open transaction on return:\n  {lines}\n"
            "Add `<handle>.commit()` (or `.rollback()`), or if the caller genuinely owns the transaction, "
            "record it in the baseline with a reason."
        )
    stale = sorted(k for k in accepted if k not in {t.key for t in found})
    if stale:
        raise AssertionError(f"these baseline entries no longer describe an incomplete transaction -- remove them: {stale}")
