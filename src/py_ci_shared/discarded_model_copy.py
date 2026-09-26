"""A config rebuilt with ``model_copy(update=...)`` into a local must reach something, or the override is lost.

``cfg2 = cfg.model_copy(update={"enabled": True})`` makes a new object; the caller's ``cfg`` is untouched. When the local
copy is never returned, stored on an object or container, or passed to a call, the effective config exists only inside
that function and every later consumer reads the original. The shipped instance was discovery auto-enabled on a copy
that post-processing never saw, so the cross-target ensemble and the lag failsafe were skipped.

The check flags a function-scope ``name = <expr>.model_copy(update=...)`` whose ``name`` is afterwards neither returned
(or yielded), nor assigned into an attribute or subscript, nor used as a call argument. A copy intentionally scoped to one
statement is listed in ``allowed`` as ``path::function`` with a reason.

Usage from a repository's meta tests::

    from py_ci_shared.discarded_model_copy import assert_no_discarded_model_copy

    def test_no_discarded_model_copy():
        assert_no_discarded_model_copy(files=..., repo_root=REPO_ROOT, allowed={})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import ScanResult, scan_python
from ._core.node_index import walk as _fast_walk

__all__ = ["DiscardedCopy", "find_discarded_model_copies", "assert_no_discarded_model_copy"]

_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]

#: Calls that only DISPLAY a value. Passing the copy to one of these does not hand the override to any consumer.
_LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log", "fatal"})
_SINK_FUNCTIONS = frozenset({"print", "repr", "str", "len", "id", "type", "isinstance", "hash", "bool"})


class DiscardedCopy:
    """One finding: the file, the function (qualified: ``Class.method``, ``outer.<locals>.inner``) and the line."""

    __slots__ = ("function", "lineno", "name", "path")

    def __init__(self, path: str, function: str, lineno: int, name: str) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.name = name

    @property
    def key(self) -> str:
        """``path::function``: what an ``allowed`` entry names, so an entry covers one function in one file."""
        return f"{self.path}::{self.function}"

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.function}(): {self.name} = ...model_copy(update=...) reaches nothing"


def _is_update_copy(node: Optional[ast.AST]) -> bool:
    """``<expr>.model_copy(update=...)``."""
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "model_copy" and any(k.arg == "update" for k in node.keywords)
    )


def _mentions(node: ast.AST | None, name: str) -> bool:
    """Whether ``name`` is loaded anywhere under ``node``."""
    return node is not None and any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load) for n in _fast_walk(node))


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_sink_call(call: ast.Call) -> bool:
    """``print(x)``, ``log.debug('%s', x)``, ``logging.info(x)``, ``self.logger.warning(x)``: displays, not consumers."""
    if isinstance(call.func, ast.Name):
        return call.func.id in _SINK_FUNCTIONS
    if isinstance(call.func, ast.Attribute) and call.func.attr in _LOG_METHODS:
        receiver = _dotted(call.func.value).rsplit(".", 1)[-1].lower()
        return "log" in receiver
    return False


def _hands_on(node: ast.AST, name: str) -> bool:
    """Whether this one node returns/yields ``name``, stores it into an attribute or subscript, or passes it to a call."""
    if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
        return _mentions(node.value, name)
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(isinstance(t, (ast.Attribute, ast.Subscript)) for t in targets) and _mentions(node.value, name)
    if isinstance(node, ast.Call):
        if _is_sink_call(node):
            return False
        if any(_mentions(a, name) for a in node.args) or any(_mentions(k.value, name) for k in node.keywords):
            return True
        # ``name.method(...)`` hands the copy to its own method (e.g. ``cfg2.apply()``): the object is used.
        return isinstance(node.func, ast.Attribute) and node.func.attr != "model_copy" and _mentions(node.func.value, name)
    return False


def _escapes(func: ast.AST, name: str, _seen: frozenset[str] = frozenset()) -> bool:
    """Whether ``name`` (or a local it is aliased to) reaches anything inside ``func`` (see :func:`_hands_on`)."""
    seen = _seen | {name}
    for node in _fast_walk(func):
        if _hands_on(node, name):
            return True
        # ``alias = name`` (or ``alias = name if c else other``) forwards the question to the alias.
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and not _is_update_copy(node.value) and _mentions(node.value, name):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id not in seen and _escapes(func, t.id, seen) for t in targets):
                return True
    return False


def _own_scope(func: _FunctionNode) -> Iterator[ast.AST]:
    """Every node of *func* that belongs to its own scope: nested functions, lambdas and classes are their own."""
    stack: list[ast.AST] = [*func.args.defaults, *[d for d in func.args.kw_defaults if d is not None], *func.body]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _functions(tree: ast.Module) -> Iterator[tuple[str, _FunctionNode]]:
    def visit(node: ast.AST, prefix: str) -> Iterator[tuple[str, _FunctionNode]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                yield qual, child
                yield from visit(child, f"{qual}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                yield from visit(child, f"{prefix}{child.name}.")
            else:
                yield from visit(child, prefix)

    return visit(tree, "")


def _copies(func: _FunctionNode) -> Iterator[tuple[int, str]]:
    """``(line, local name)`` for each ``name = / name: T = / (name := ) <x>.model_copy(update=...)`` in *func*'s own scope."""
    for node in _own_scope(func):
        if isinstance(node, ast.Assign) and _is_update_copy(node.value):
            yield from ((node.lineno, t.id) for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and _is_update_copy(node.value) and isinstance(node.target, ast.Name):
            yield node.lineno, node.target.id


def _find_in_scan(scan: ScanResult) -> list[DiscardedCopy]:
    out: list[DiscardedCopy] = []
    for parsed in scan:
        for qual, func in _functions(parsed.tree):
            out.extend(DiscardedCopy(parsed.rel, qual, line, name) for line, name in _copies(func) if not _escapes(func, name))
    return sorted(out, key=lambda d: (d.path, d.lineno))


def find_discarded_model_copies(files: Iterable[Path], repo_root: Path) -> list[DiscardedCopy]:
    """Every local ``model_copy(update=...)`` result that is never returned, stored or passed on (logging it does not
    count). A copy is reported once, under the function whose scope it lives in. Unparsable files are not in this list;
    :func:`assert_no_discarded_model_copy` fails on them."""
    return _find_in_scan(scan_python([Path(p) for p in files], root=Path(repo_root)))


def assert_no_discarded_model_copy(files: Iterable[Path], repo_root: Path, allowed: Mapping[str, str], min_files: int = 1) -> None:
    """Fail on a discarded ``model_copy(update=...)`` outside ``allowed`` (``"path::function"`` -> reason; ``path`` is
    relative to *repo_root*, ``function`` qualified as in the finding); stale, unreasoned or bare-name entries fail too,
    as do unparsable files and fewer than *min_files* parsed files."""
    scan = scan_python([Path(p) for p in files], root=Path(repo_root), min_files=min_files)
    scan.check_floor()
    empty = sorted(k for k, v in allowed.items() if not str(v).strip())
    if empty:
        raise AssertionError(f"allowed entries need a reason: {empty}")
    bare = sorted(k for k in allowed if "::" not in k)
    if bare:
        raise AssertionError(f"allowed entries must name 'path::function' so they cover one function in one file, not every same-named one: {bare}")
    found = _find_in_scan(scan)
    bad = [d for d in found if d.key not in allowed]
    stale = sorted(set(allowed) - {d.key for d in found})
    msgs = []
    if scan.unparsed:
        msgs.append(f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked: " + "; ".join(p.render() for p in scan.unparsed))
    if bad:
        msgs.append("config copies built with model_copy(update=...) that no caller ever receives (the override is lost): " + "; ".join(map(repr, bad)))
    if stale:
        msgs.append(f"allowed entries with no discarded copy left: {stale}")
    if msgs:
        raise AssertionError("\n".join(msgs))
