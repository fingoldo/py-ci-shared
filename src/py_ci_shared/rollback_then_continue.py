"""``rollback()`` in a loop's ``except`` handler, then on to the next item as if only that item failed.

``session.rollback()`` undoes the WHOLE open transaction, not the failing statement. In a loop that issues writes per
item and commits in batches, a handler that rolls back and lets the loop continue silently discards every earlier
item's already-issued INSERT/UPDATE, while their success counters (and any accumulated batch list) stay as they were.
The shipped instance: two corpus importers reported thousands of senses imported that were never in the database.

Flagged: an ``except`` handler that calls ``.rollback()`` (any receiver, awaited or not), on a ``try`` inside a
``for``/``while`` body (not inside a nested function), where neither the handler nor the ``try``'s ``finally`` resets
state or leaves the loop: no assignment (an accumulator reset), no ``.clear()``/``.pop()``, no ``raise``, ``break`` or
``return``. Also exempt, because the rollback then loses only the failing item: a ``try`` whose body opens a
savepoint (``begin_nested()``) or commits its own item (a ``.commit()`` in it, unless under a batching ``if`` such
as ``i % 100 == 0`` or ``len(batch) >= n``), and a loop that writes nothing (no ``add``/``merge``/``delete``/``flush``/``executemany``/``execute_values``/
``bulk_*``, no ``insert()``/``update()``/``delete()`` construct, no ``execute`` of a literal INSERT/UPDATE/DELETE/
TRUNCATE/DDL statement): a read-only probe that rolls back to clear an aborted transaction is correct. This is a
deliberately narrow, high-precision heuristic: false negatives are accepted.

Usage::

    from py_ci_shared.rollback_then_continue import assert_no_rollback_then_continue

    def test_no_rollback_then_continue():
        assert_no_rollback_then_continue(REPO / "src", baseline_path=HERE / "_rollback_continue_baseline.json")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ParsedFile, ScanResult
from ._gate_report import enclosing_functions, line_has_marker, report, scan_tree, skip_set

__all__ = ["RULE", "REFRESH_FLAG", "find_rollback_then_continue", "assert_no_rollback_then_continue"]

RULE = "rollback-then-continue"
REFRESH_FLAG = "--refresh-rollback-continue-baseline"
MARKER = "rollback-continue-ok"
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_own(nodes: Sequence[ast.AST]) -> Iterator[ast.AST]:
    """Every node under *nodes*, not descending into nested functions, classes or lambdas."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _calls(nodes: Sequence[ast.AST], attr: str) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr for n in _walk_own(nodes))


def _resets_or_leaves(nodes: Sequence[ast.AST]) -> bool:
    for n in _walk_own(nodes):
        if isinstance(n, (ast.Raise, ast.Break, ast.Return, ast.Assign, ast.AnnAssign)):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("clear", "pop"):
            return True
    return False


#: Unambiguous write methods, on any receiver.
_WRITE_METHODS = frozenset(
    {"add_all", "executemany", "bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings", "copy_records_to_table", "copy_expert"}
)
#: Session methods whose names are also set/dict/list methods (``seen.add(x)``): a write only on a session-like receiver.
_SESSION_METHODS = frozenset({"add", "merge", "delete", "flush"})
_SESSION_RECEIVER = re.compile(r"sess|db|conn|uow|tx|cursor|cur$", re.IGNORECASE)
_WRITE_FUNCS = frozenset({"execute_values", "execute_batch", "insert", "update", "delete", "upsert"})
_WRITE_SQL = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE|UPSERT|TRUNCATE|CREATE|DROP|ALTER)\b", re.IGNORECASE)


def _literal_text(node: ast.expr) -> str:
    """The literal SQL text of an ``execute`` argument: a string, an f-string's constant parts, or ``text("...")``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    if isinstance(node, ast.Call) and node.args:
        return _literal_text(node.args[0])
    return ""


def _receiver_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _receiver_name(node.func)
    return ""


def _writes(nodes: Sequence[ast.AST]) -> bool:
    for n in _walk_own(nodes):
        if not isinstance(n, ast.Call):
            continue
        name = n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ""
        if isinstance(n.func, ast.Attribute) and name in _WRITE_METHODS:
            return True
        if isinstance(n.func, ast.Attribute) and name in _SESSION_METHODS and _SESSION_RECEIVER.search(_receiver_name(n.func.value)):
            return True
        if name in _WRITE_FUNCS:
            return True
        if name == "execute" and n.args and _WRITE_SQL.search(_literal_text(n.args[0])):
            return True
    return False


def _is_batch_condition(test: ast.expr) -> bool:
    """``i % 100 == 0``, ``len(batch) >= n``, ``pending > BATCH_SIZE``: a commit every N items, not every item."""
    for n in ast.walk(test):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "len":
            return True
        if isinstance(n, (ast.Name, ast.Attribute)) and re.search(r"batch|chunk|size|every|pending", _receiver_name(n), re.IGNORECASE):
            return True
    return False


def _commits_per_item(body: Sequence[ast.stmt]) -> bool:
    """The ``try`` body commits its own item: a ``.commit()`` (or a call whose name contains ``commit``) anywhere in it
    except under an ``if`` that batches (``i % N``, ``len(batch) >= N``...), which is the shape that loses earlier items."""
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPES):
            continue
        if isinstance(node, ast.If) and _is_batch_condition(node.test):
            stack.extend(node.orelse)
            continue
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
            if "commit" in name.lower():
                return True
        stack.extend(ast.iter_child_nodes(node))
    return False


_TRY_TYPES: tuple[type, ...] = (ast.Try, *((ast.TryStar,) if hasattr(ast, "TryStar") else ()))


def _loop_trys(tree: ast.AST) -> Iterator[tuple[Any, Any]]:
    """``(outermost enclosing loop, try)`` for every ``try`` (and ``try``/``except*``) inside a loop body, in the loop's function."""
    outer: dict[int, tuple[Any, Any]] = {}
    for loop in ast.walk(tree):
        if not isinstance(loop, (ast.For, ast.AsyncFor, ast.While)):
            continue
        for node in _walk_own(loop.body):
            if isinstance(node, _TRY_TYPES):
                outer.setdefault(id(node), (loop, node))  # ast.walk is breadth-first: the first loop seen is the outermost
    yield from outer.values()


def _file_findings(parsed: ParsedFile) -> list[Finding]:
    lines = parsed.source.splitlines()
    functions = enclosing_functions(parsed.tree)
    out: list[Finding] = []
    for loop, node in _loop_trys(parsed.tree):
        if _calls(node.body, "begin_nested") or _commits_per_item(node.body) or not _writes(loop.body):
            continue
        for handler in node.handlers:
            if not _calls(handler.body, "rollback") or line_has_marker(lines, handler.lineno, MARKER):
                continue
            if _resets_or_leaves(handler.body) or _resets_or_leaves(node.finalbody):
                continue
            where = functions.get(id(handler), "<module>")
            out.append(
                Finding(
                    parsed.rel, handler.lineno, RULE, f"{where}: except handler rolls the whole transaction back inside a loop and carries on without resetting"
                )
            )
    return out


def _collect(root: Union[str, Path], *, skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    findings = [f for parsed in scan for f in _file_findings(parsed)]
    return sorted(findings, key=lambda f: (f.path, f.line)), scan


def find_rollback_then_continue(
    root: Union[str, Path], *, skip_dir_names: Iterable[str] = (), include_tests: bool = False, use_git: Optional[bool] = None
) -> list[Finding]:
    """Every rollback-then-continue handler under *root*, plus one ``unparsed-file`` finding per unparsable file."""
    findings, scan = _collect(root, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    return findings + scan.unparsed_findings()


def assert_no_rollback_then_continue(
    root: Union[str, Path],
    *,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a rollback-then-continue handler (new against *baseline_path* when given), on fewer than *min_files*
    parsed files, and on any unparsable file. Refresh with ``--refresh-rollback-continue-baseline``."""
    findings, scan = _collect(root, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    report(
        findings,
        gate="rollback-then-continue",
        flag=REFRESH_FLAG,
        guidance="give each item a savepoint (begin_nested), or reset the batch accumulators / re-raise after rollback()",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
