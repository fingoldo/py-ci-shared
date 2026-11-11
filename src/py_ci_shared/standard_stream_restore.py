"""Code that swaps ``sys.stdout``/``sys.stderr`` must restore them only while its own stream is still installed.

The usual silencer saves the current stream, installs its own, and puts the saved one back in ``finally``. The put-back
is unconditional, and that is the bug: by the time it runs, the saved stream may be closed. Two ways it happens:

* Another swap overlapped this one (a second thread, an interleaved generator, an ``ExitStack``). The inner block's exit
  reinstalls the outer block's devnull, which the outer block then closes.
* Under pytest, the saved stream was a ``capsys`` CaptureIO. The test that owned it ends and pytest closes it, and a
  late restore (from a thread, a generator, a callback) makes that closed object ``sys.stderr`` for the rest of the
  process.

From then on every print raises "I/O operation on closed file". In mlframe the test harness prints at every test's
setup, so after one such leak every later test on the xdist worker errored before running: 13 of 23 in one rerun, none
of them related to the test that leaked.

``contextlib.redirect_stdout`` and ``redirect_stderr`` restore unconditionally as well.

The safe shape restores only if its own stream is still the one installed::

    sys.stderr = devnull
    try:
        ...
    finally:
        if sys.stderr is devnull:
            sys.stderr = old

What is reported is a RESTORE: an assignment to ``sys.stdout``/``sys.stderr`` (``setattr(sys, "stdout", ...)`` and
``import sys as _sys`` included) whose value was saved from that stream earlier in the module (``old = sys.stderr``,
``self._old = sys.stdout``, ``_before = (sys.stdout, sys.stderr)``, then ``sys.stderr = old`` / ``= self._old`` /
``sys.stdout, sys.stderr = _before``). Installing a stream is not reported: a module-level
``sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`` in a CLI script restores nothing, so it cannot put back a closed
stream. A value whose origin the parse cannot see (a parameter, a call's result) is not reported either; that is the
price of not flagging every process-lifetime wrapper.

A restore is safe when, in the same function (nested functions excluded), an enclosing ``if``/``while`` test, or an
earlier ``if`` that exits (``return``/``raise``/``break``/``continue``), compares the same stream by identity with
something other than ``None`` (``if sys.stderr is devnull:``), or reads a name the function computed from such a
comparison (``leaked = [s is not b for s, b in zip((sys.stdout, sys.stderr), saved)]; if leaked: ...``). An identity
check elsewhere in the function, or in a nested function, does not guard it.

Every ``contextlib.redirect_stdout``/``redirect_stderr`` call is reported. ``allow`` takes ``path:line`` or, stable
across edits above the site, ``path::function`` (the qualified name, ``<module>`` at top level).
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core.scan import scan_roots_excluding
from ._core import ScanResult, nodes_of
from ._core.node_index import parent_map, tree_memo, walk
from ._core.scopes import iter_scopes

__all__ = [
    "Finding",
    "assert_standard_streams_restored_only_if_still_ours",
    "find_unconditional_stream_restores",
]

_STREAMS = ("stdout", "stderr")
_REDIRECTS = frozenset({"redirect_stdout", "redirect_stderr"})


class Finding:
    """One unsafe swap: where it is and what it does."""

    def __init__(self, path: Path, lineno: int, what: str, function: str = "<module>") -> None:
        """Record the site; *function* is the enclosing qualified name, the key ``path::function`` allow entries use."""
        self.path = path
        self.lineno = lineno
        self.what = what
        self.function = function

    def __str__(self) -> str:
        """Render as ``path:line  what``, the form an editor can jump to."""
        return f"{self.path.as_posix()}:{self.lineno}  {self.what}"


_Scope = Union[ast.Module, ast.FunctionDef, ast.AsyncFunctionDef]
_EXITS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def _sys_names(tree: ast.Module) -> frozenset[str]:
    """``sys`` and every name ``import sys as X`` binds in the module."""
    names = {"sys"}
    for node in nodes_of(tree, ast.Import):
        names.update(alias.asname for alias in node.names if alias.name == "sys" and alias.asname)
    return frozenset(names)


def _key(node: ast.AST) -> str:
    """A stable name for a saved-value location: ``old``, ``self._old``; a subscript is keyed by its container."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _key(node.value)
        return f"{base}.{node.attr}" if base else ""
    if isinstance(node, ast.Subscript):
        return _key(node.value)
    return ""


def _scope_parent(tree: ast.Module, scope: ast.AST, parent_all: dict[int, Optional[ast.AST]]) -> Callable[[ast.AST], Optional[ast.AST]]:
    """Parent lookup that stops at *scope*: the guard chain never climbs out of the function or module it is checking."""

    def up(node: ast.AST) -> Optional[ast.AST]:
        if node is scope:
            return None
        return parent_all.get(id(node)) or tree

    return up


_PAIR_NODES = (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.Call)  # the only nodes _pairs ever yields pairs for


def _pairs(node: ast.AST, sys_names: frozenset[str]) -> list[tuple[ast.AST, ast.AST]]:
    """(target, value) pairs an assignment makes, element-wise for ``a, b = x, y``; ``setattr(sys, "stdout", v)``
    yields a synthetic ``sys.stdout`` target."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr" and len(node.args) == 3:
        obj, attr, value = node.args
        if isinstance(obj, ast.Name) and obj.id in sys_names and isinstance(attr, ast.Constant) and attr.value in _STREAMS:
            return [(ast.copy_location(ast.Attribute(value=obj, attr=attr.value, ctx=ast.Store()), node), value)]
        return []
    if isinstance(node, ast.Assign):
        raw = [(t, node.value) for t in node.targets]
    elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
        raw = [(node.target, node.value)]
    else:
        return []
    out: list[tuple[ast.AST, ast.AST]] = []
    for target, value in raw:
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
            out.extend(zip(target.elts, value.elts))
        elif isinstance(target, (ast.Tuple, ast.List)):
            out.extend((element, value) for element in target.elts)
        else:
            out.append((target, value))
    return out


def _stream(node: ast.AST, sys_names: frozenset[str]) -> str:
    """``"stdout"``/``"stderr"`` when *node* is ``sys.stdout``/``sys.stderr`` (through any alias of ``sys``), else ""."""
    if isinstance(node, ast.Attribute) and node.attr in _STREAMS and isinstance(node.value, ast.Name) and node.value.id in sys_names:
        return node.attr
    return ""


def _saved_locations(tree: ast.Module, sys_names: frozenset[str]) -> frozenset[str]:
    """Keys of every location assigned ``sys.<stream>`` itself, or a tuple/list display holding one."""
    saved: set[str] = set()
    for node in nodes_of(tree, ast.Assign, ast.AnnAssign, ast.NamedExpr):
        for target, value in _pairs(node, sys_names):
            elements = value.elts if isinstance(value, (ast.Tuple, ast.List)) else [value]
            if any(_stream(e, sys_names) for e in elements) and not _stream(target, sys_names) and _key(target):
                saved.add(_key(target))
    return frozenset(saved)


def _guard_names(own: list[ast.AST], sys_names: frozenset[str]) -> frozenset[str]:
    """Names this function binds from an expression that compares by identity and mentions a standard stream."""
    names: set[str] = set()
    for node in own:
        if not isinstance(node, _PAIR_NODES):
            continue  # _pairs yields nothing for any other node: skip the call (it dominated the gate)
        for target, value in _pairs(node, sys_names):
            walked = list(walk(value))
            compares = any(isinstance(n, ast.Compare) and any(isinstance(op, (ast.Is, ast.IsNot)) for op in n.ops) for n in walked)
            if compares and any(_stream(n, sys_names) for n in walked) and isinstance(target, ast.Name):
                names.add(target.id)
    return frozenset(names)


def _guards(test: ast.AST, stream: str, sys_names: frozenset[str], guard_names: frozenset[str]) -> bool:
    """Does an ``if``/``while`` test check, by identity against a non-None value, that *stream* is still ours?"""
    for node in walk(test):
        if isinstance(node, ast.Name) and node.id in guard_names:
            return True
        if isinstance(node, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
            sides = [node.left, *node.comparators]
            if any(_stream(side, sys_names) == stream for side in sides) and not any(isinstance(side, ast.Constant) and side.value is None for side in sides):
                return True
    return False


def _guarded(node: ast.AST, stream: str, up: Callable[[ast.AST], Optional[ast.AST]], sys_names: frozenset[str], guard_names: frozenset[str]) -> bool:
    """Is *node* under an enclosing guard, or after an exiting guard ``if`` in an enclosing statement list?"""
    child, cur = node, up(node)
    while cur is not None:
        if isinstance(cur, (ast.If, ast.While, ast.IfExp)) and child is not cur.test and _guards(cur.test, stream, sys_names, guard_names):
            return True
        for field in ("body", "orelse", "finalbody"):
            block = getattr(cur, field, None)
            if isinstance(block, list) and child in block:
                for earlier in block[: block.index(child)]:
                    if (
                        isinstance(earlier, ast.If)
                        and earlier.body
                        and isinstance(earlier.body[-1], _EXITS)
                        and _guards(earlier.test, stream, sys_names, guard_names)
                    ):
                        return True
        child, cur = cur, up(cur)
    return False


def _scopes(tree: ast.Module) -> list[tuple[_Scope, str]]:
    """The module and every function, with qualified names (``Cls.method``, ``outer.<locals>.inner``), from the shared scope index."""
    out: list[tuple[_Scope, str]] = [(tree, "<module>")]
    qual_of: dict[int, str] = {}
    for scope in iter_scopes(tree):
        if scope.node is None:
            continue
        parent = scope.parent
        if parent is None or parent.node is None:
            prefix = ""
        elif isinstance(parent.node, ast.ClassDef):
            prefix = f"{qual_of[id(parent)]}."
        else:
            prefix = f"{qual_of[id(parent)]}.<locals>."
        qual_of[id(scope)] = f"{prefix}{scope.node.name}"
        if not isinstance(scope.node, ast.ClassDef):
            out.append((scope.node, qual_of[id(scope)]))
    return out


def _own_by_scope(tree: ast.Module) -> dict[int, list[ast.AST]]:
    """``id(scope) -> the nodes of its body for this gate`` (the module keyed by the tree): the shared index's own nodes, where a class
    body also counts as part of the scope it is written in, so a class's nodes (and those of classes in it) are added here."""

    def class_body(cls: ast.AST) -> list[ast.AST]:
        out = list(index_own[id(cls)])
        for node in index_own[id(cls)]:
            if isinstance(node, ast.ClassDef):
                out.extend(class_body(node))
        return out

    out: dict[int, list[ast.AST]] = {}
    index_own: dict[int, list[ast.AST]] = {}
    scopes = iter_scopes(tree)
    for scope in scopes:
        index_own[id(tree) if scope.node is None else id(scope.node)] = scope.nodes
    for scope in scopes:
        if scope.node is not None and isinstance(scope.node, ast.ClassDef):
            continue
        key = id(tree) if scope.node is None else id(scope.node)
        nodes = list(scope.nodes)
        for node in scope.nodes:
            if isinstance(node, ast.ClassDef):
                nodes.extend(class_body(node))
        out[key] = nodes
    return out


def _module_findings(path: Path, tree: ast.Module) -> list[Finding]:
    findings: list[Finding] = []
    sys_names = _sys_names(tree)
    saved = _saved_locations(tree, sys_names)
    parent_all = parent_map(tree)  # shared per tree; the scope boundary is kept by _scope_parent
    own_of = tree_memo(tree, "standard_stream_restore.own_by_scope", lambda: _own_by_scope(tree))
    for scope, qualname in _scopes(tree):
        own = list(own_of[id(tree) if scope is tree else id(scope)])
        up = _scope_parent(tree, scope, parent_all)
        guard_names = _guard_names(own, sys_names)
        for node in own:
            if not isinstance(node, _PAIR_NODES):
                continue
            for target, value in _pairs(node, sys_names):
                stream = _stream(target, sys_names)
                if stream and _key(value) in saved and not _guarded(node, stream, up, sys_names, guard_names):
                    what = f"restores sys.{stream} from a saved value with no check that its own stream is still installed"
                    findings.append(Finding(path, getattr(node, "lineno", 0), what, qualname))
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if name in _REDIRECTS:
                    findings.append(Finding(path, getattr(node, "lineno", 0), f"contextlib.{name} restores unconditionally", qualname))
    return sorted(findings, key=lambda f: (f.lineno, f.what))


def _findings(scan: ScanResult) -> list[Finding]:
    findings: list[Finding] = []
    for parsed in scan:
        findings.extend(_module_findings(parsed.path, parsed.tree))
    return findings


def find_unconditional_stream_restores(roots: Sequence[Path], exclude: Iterable[str] = ()) -> list[Finding]:
    """Scan every ``*.py`` under ``roots`` and return each unsafe standard-stream swap.

    ``exclude`` holds path fragments to skip, matched against the POSIX form of each file's path. A missing root raises
    ``py_ci_shared._core.CorpusError``; files that cannot be parsed are not in this list, and
    :func:`assert_standard_streams_restored_only_if_still_ours` fails on them.
    """
    return _findings(scan_roots_excluding(roots, exclude))


def assert_standard_streams_restored_only_if_still_ours(
    roots: Sequence[Path],
    exclude: Iterable[str] = (),
    allow: Iterable[str] = (),
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Raise ``AssertionError`` listing every unsafe swap of ``sys.stdout``/``sys.stderr`` not explicitly allowed.

    ``allow`` holds ``path:line`` or ``path::function`` strings for sites a reader has judged safe (``path`` in POSIX
    form, as the failure prints it). Also fails when fewer than *min_files* files parsed (a typo'd root is not a
    clean tree) and, unless *allow_unparsed*, when any file could not be parsed.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    scan = scan_roots_excluding(roots, exclude)
    scan.min_files = min_files
    newline = chr(10)
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if scan.unparsed and not allow_unparsed:
        problems.append(
            f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked:" + "".join(f"{newline}  {u.render()}" for u in scan.unparsed)
        )
    findings = [f for f in _findings(scan) if not {f"{f.path.as_posix()}:{f.lineno}", f"{f.path.as_posix()}::{f.function}"} & allowed]
    if findings:
        listing = (newline + "  ").join(str(f) for f in findings)
        problems.append(
            newline.join(
                [
                    f"{len(findings)} swap(s) of sys.stdout/sys.stderr that can restore a closed stream.",
                    "  Restore only while your own stream is still installed, or every later print raises",
                    "  'I/O operation on closed file':",
                    "      if sys.stderr is devnull:",
                    "          sys.stderr = old",
                    f"  {listing}",
                ]
            )
        )
    if problems:
        raise AssertionError(newline.join(problems))
