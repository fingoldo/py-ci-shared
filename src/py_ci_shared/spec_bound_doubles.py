"""Shared check: a test double reaching duck-typed production code must be spec-bound.

2026-09-05 glossum agent wave, findings 02-F1 through 02-F5. ``advisory_lock.claim_lock``
resolves the session it runs lock SQL against through ``getattr(target, "session", None)``
-- the indirection exists because one caller swaps its session mid-flight. A bare
``AsyncMock()`` AUTO-CREATES that attribute, so every lock statement went to a different
child mock whose ``scalar()`` was a truthy Mock: the lock always appeared acquired, the
conflict never raised, and a test named "exits early on lock conflict" never reached one.
Five tests across four files were affected, and one of them fell through into the batch
loop it was protecting and span for over two hours, growing to 82 GiB.

The production-side alternative was considered and rejected there: making the resolver
follow ``.session`` only when it is a real session would break legitimate hand-written
holder fakes, and no attribute test can distinguish a Mock from a holder. The rule belongs
on the test side, which is what this enforces.

SCOPE, deliberately narrow
--------------------------
``Mock()``/``AsyncMock()`` with no spec is the right tool for a commit coroutine, a context
manager or an HTTP client, and a file-wide ban flagged five files whose doubles never reach
a lock. Two shapes are reported instead:

* a spec-less mock bound to a name matching ``name_hints`` (e.g. anything with "session" in
  it) -- plainly, by tuple unpacking, or as ``with patch(...) as session`` -- and
* a spec-less mock reaching one of ``entry_points`` -- the functions that take the duck-typed
  object -- directly or through any name bound to one in the same scope (``m = AsyncMock();
  claim_lock(m)``).

``spec=None`` is spec-less, and the mock classes resolve through imports (``AsyncMock as AM``).

Usage::

    from py_ci_shared.spec_bound_doubles import assert_doubles_are_spec_bound

    def test_no_bare_mock_reaches_the_lock():
        assert_doubles_are_spec_bound(
            files=lock_driving_tests,
            entry_points={"claim_lock", "try_claim_lock"},
            name_hints={"session"},
            repo_root=REPO,
        )
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional

from ._core import ImportAliases, SourceError, parse_file, read_source, relative_posix
from ._core.node_index import walk as _fast_walk

DEFAULT_MOCK_NAMES = frozenset({"Mock", "MagicMock", "AsyncMock", "NonCallableMock"})
#: ``patch(...)`` spellings whose ``as`` target is a MagicMock unless given a spec.
_PATCHERS = frozenset({"patch", "patch.object", "patch.multiple"})
_SPEC_KEYWORDS = ("spec", "spec_set", "autospec")
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _truthy_keyword(call: ast.Call, names: Iterable[str]) -> bool:
    wanted = set(names)
    return any(kw.arg in wanted and not (isinstance(kw.value, ast.Constant) and not kw.value.value) for kw in call.keywords)


def _callee(call: ast.Call, aliases: Optional[ImportAliases]) -> set[str]:
    func = call.func
    names = {func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")}
    if aliases is not None:
        qualified = aliases.qualified_name(call)
        if qualified:
            names.add(qualified.rsplit(".", 1)[-1])
    return names


def is_bare_mock(node: ast.AST, mock_names: frozenset[str] = DEFAULT_MOCK_NAMES, aliases: Optional[ImportAliases] = None) -> bool:
    """True for ``Mock()`` / ``AsyncMock()`` with no positional spec and no truthy ``spec``/``spec_set``
    (``spec=None`` is bare). With *aliases*, ``AsyncMock as AM`` is recognised too."""
    if not isinstance(node, ast.Call):
        return False
    if not (_callee(node, aliases) & mock_names):
        return False
    return not node.args and not _truthy_keyword(node, ("spec", "spec_set"))


def _is_bare_patch(node: ast.AST, aliases: ImportAliases) -> bool:
    """``patch("x")`` / ``patch.object(o, "a")`` whose replacement is an unspecced MagicMock."""
    if not isinstance(node, ast.Call):
        return False
    qualified = aliases.qualified_name(node) or ""
    tail = ".".join(qualified.split(".")[-2:]) if qualified.endswith((".object", ".multiple")) else qualified.rsplit(".", 1)[-1]
    if tail not in _PATCHERS:
        return False
    if any(kw.arg in ("new", "new_callable") for kw in node.keywords):
        return False
    return not _truthy_keyword(node, _SPEC_KEYWORDS)


def _scopes(tree: ast.Module) -> Iterator[list[ast.AST]]:
    """The nodes of the module scope and of every function, each without its nested scopes, in source order."""
    bodies: list[list[ast.stmt]] = [tree.body]
    bodies += [n.body for n in _fast_walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for body in bodies:
        nodes: list[ast.AST] = []
        stack: list[ast.AST] = [n for n in body if not isinstance(n, _NESTED)]
        while stack:
            node = stack.pop()
            nodes.append(node)
            stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _NESTED))
        yield sorted(nodes, key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))


def _bindings(node: ast.AST) -> list[tuple[ast.expr, ast.expr]]:
    """``(target, value)`` pairs a statement binds, tuple unpacking paired element by element."""
    pairs: list[tuple[ast.expr, ast.expr]] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            pairs.extend(_pair(target, node.value))
    elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
        pairs.extend(_pair(node.target, node.value))
    return pairs


def _pair(target: ast.expr, value: ast.expr) -> list[tuple[ast.expr, ast.expr]]:
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
        out: list[tuple[ast.expr, ast.expr]] = []
        for t, v in zip(target.elts, value.elts):
            out.extend(_pair(t, v))
        return out
    return [(target, value)]


def _name_of(target: ast.expr) -> Optional[str]:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _unbound_lines(tree: ast.Module, entry_points: frozenset[str], name_hints: frozenset[str], mock_names: frozenset[str]) -> list[int]:
    aliases = ImportAliases.from_tree(tree)
    hints = frozenset(h.lower() for h in name_hints)
    lines: set[int] = set()

    def hinted(name: str) -> bool:
        return any(h in name.lower() for h in hints)

    for nodes in _scopes(tree):
        bare: set[str] = set()
        for node in nodes:
            bound = list(_bindings(node))
            if isinstance(node, (ast.With, ast.AsyncWith)):
                bound += [(item.optional_vars, item.context_expr) for item in node.items if item.optional_vars is not None]
            for target, value in bound:
                is_bare = is_bare_mock(value, mock_names, aliases) or (isinstance(node, (ast.With, ast.AsyncWith)) and _is_bare_patch(value, aliases))
                name = _name_of(target)
                if name is None:
                    continue
                if is_bare:
                    bare.add(name)
                    if hinted(name):
                        lines.add(value.lineno)
                else:
                    bare.discard(name)
            if isinstance(node, ast.Call) and _callee(node, aliases) & entry_points:
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    if is_bare_mock(arg, mock_names, aliases):
                        lines.add(arg.lineno)
                    elif isinstance(arg, ast.Name) and arg.id in bare and not hinted(arg.id):
                        lines.add(arg.lineno)
    return sorted(lines)


def find_unbound_doubles(
    path: Path,
    *,
    entry_points: frozenset[str],
    name_hints: frozenset[str],
    mock_names: frozenset[str] = DEFAULT_MOCK_NAMES,
) -> list[int]:
    """Lines where a spec-less mock is used as the duck-typed object: bound to a hinted name (plain, tuple-unpacked or
    ``with patch(...) as session``), or reaching an entry point directly or through any name bound to one. Raises
    ``SourceError`` for a file that cannot be read or parsed: an empty answer for it would read as clean."""
    return _unbound_lines(parse_file(path), frozenset(entry_points), frozenset(name_hints), mock_names)


def files_driving(files: Iterable[Path], entry_points: Iterable[str]) -> list[Path]:
    """The test files that mention one of the duck-typed entry points. A file that cannot be read is kept, so the
    check reports it rather than dropping it."""
    wanted = tuple(entry_points)
    out = []
    for path in files:
        try:
            text = read_source(path)
        except SourceError:
            out.append(path)
            continue
        if any(name in text for name in wanted):
            out.append(path)
    return out


def assert_doubles_are_spec_bound(
    *,
    files: Iterable[Path],
    entry_points: Iterable[str],
    name_hints: Iterable[str],
    repo_root: Path,
    min_subjects: int = 1,
) -> None:
    """Fail when a spec-less mock is used where production duck-types on an attribute, or a subject file cannot
    be parsed."""
    import pytest

    entries = frozenset(entry_points)
    hints = frozenset(h.lower() for h in name_hints)
    subjects = files_driving(files, entries)

    if len(subjects) < min_subjects:
        pytest.fail(f"only {len(subjects)} test file(s) mention {sorted(entries)} -- expected at least " f"{min_subjects}. The scan lost its subject.")

    problems: list[str] = []
    for path in subjects:
        rel = relative_posix(path, repo_root)
        try:
            lines = find_unbound_doubles(path, entry_points=entries, name_hints=hints)
        except SourceError as exc:
            problems.append(f"{rel}:{exc.line or 1}: {exc.kind}: {exc.message} - its doubles were not checked")
            continue
        if lines:
            problems.append(
                f"{rel} builds a spec-less mock at line(s) {lines} and hands it to duck-typed "
                f"code; a bare Mock auto-creates whatever attribute that code resolves through, "
                f"which silently routes the call to a different object"
            )

    if problems:
        pytest.fail(f"{len(problems)} file(s) with unbound doubles:\n  " + "\n  ".join(problems))
