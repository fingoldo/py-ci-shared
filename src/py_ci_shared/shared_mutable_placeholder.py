"""A mutable placeholder built once in an enclosing scope and handed out by reference from a function.

``_EMPTY = Rows()`` at module level, then ``def fetch(): return _EMPTY``: every caller receives the same object, so the first
caller that appends to it or fills it changes what every later caller sees. A fresh value per call (``return Rows()``, or a
local ``rows = Rows(); return rows``) does not have the defect, and neither does a copy on return (``return list(_EMPTY)``,
``return _EMPTY.copy()``, ``copy.copy(_EMPTY)``), since the return is then no longer the name itself.

A placeholder is a name assigned, directly in a module or function body, to a list, dict or set literal, to a builtin
collection constructor (``list()``, ``dict()``, ``set()``, ``deque()``, ``defaultdict()``, ``OrderedDict()``, ``Counter()``,
``bytearray()``), or to a call of a capitalised name (a class such as ``Rows()``), except a few immutable value types
(``Path``, ``Decimal``, ``frozenset``, ...) and the synchronisation primitives (``threading.RLock()``), which are shared on purpose. A ``return NAME`` is a finding when ``NAME`` is a placeholder of an enclosing
scope (module, or a function a nested function sits in), and the returning function does not rebind the name itself.

Also shared on purpose, and so not placeholders: a call of a class whose name ends in ``Executor``, ``Pool``, ``Client``, ``Session``,
``Engine``, ``Queue``, ``Logger``, ``ContextVar``, ``Local`` or ``Manager`` (``_EXEC = ThreadPoolExecutor(4)``, ``_CLIENT = Client()``).
A singleton the gate cannot tell, such as a module cache dict handed out by an accessor, is marked
``# shared-mutable-placeholder-ok: <reason>`` on the assignment (or the line above it) or on the ``return`` line (or the line above it).

Not judged: a function that rebinds the name (a parameter, a local assignment, a ``global`` declaration does count as the
module's name and is judged), a return of anything other than the bare name, and a placeholder whose value is a factory call
with a lowercase name (``make_rows()``), since the gate cannot tell that it is shared.

Usage::

    from py_ci_shared.shared_mutable_placeholder import assert_no_shared_mutable_placeholders

    def test_no_shared_mutable_placeholders():
        assert_no_shared_mutable_placeholders(_py_files(REPO / "src"))
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, ParsedFile, scan_python
from ._gate_report import line_has_marker
from ._core.node_index import nodes_of
from ._core.scopes import iter_scopes

__all__ = ["RULE", "assert_no_shared_mutable_placeholders", "find_shared_mutable_placeholders"]

RULE = "shared-mutable-placeholder"
MARKER = "shared-mutable-placeholder-ok:"
_REASONED = re.compile(r"#\s*shared-mutable-placeholder-ok:\s*\S")
#: Class-name suffixes of objects that are one shared instance by design (a pool, a client, an engine, a queue, a context variable).
_SINGLETON_SUFFIXES = ("Executor", "Pool", "Client", "Session", "Engine", "Queue", "Logger", "ContextVar", "Local", "Manager")
_BUILTIN_COLLECTIONS = frozenset({"list", "dict", "set", "deque", "defaultdict", "OrderedDict", "Counter", "bytearray"})
#: Synchronisation primitives are handed out to be shared: a lock returned by reference is the intended object.
_SYNC_PRIMITIVES = frozenset({"Lock", "RLock", "Condition", "Semaphore", "BoundedSemaphore", "Event", "Barrier"})
_IMMUTABLE_CONSTRUCTORS = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath", "Decimal", "Fraction", "frozenset", "Enum", "Pattern"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_Scope = Union[ast.Module, ast.FunctionDef, ast.AsyncFunctionDef]


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def is_placeholder_value(value: ast.AST) -> bool:
    """True when the expression builds a mutable collection (see the module docstring)."""
    if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if not isinstance(value, ast.Call):
        return False
    name = _callee(value)
    if name in _BUILTIN_COLLECTIONS:
        return True
    if name in _SYNC_PRIMITIVES:
        return False
    return bool(name) and name[0].isupper() and name not in _IMMUTABLE_CONSTRUCTORS and not name.endswith(_SINGLETON_SUFFIXES)


def _marked(lines: list[str], first: int, last: int) -> bool:
    """True when the marker sits on the line above *first* or on any line from *first* to *last*."""
    for n in range(first - 1, last + 1):
        if n == first - 1 and not (0 < n <= len(lines) and lines[n - 1].lstrip().startswith("#")):
            continue  # the line above counts only as a comment line of its own, not as another statement's trailing comment
        if line_has_marker(lines, n, MARKER) and _REASONED.search(lines[n - 1]):
            return True
    return False


def _placeholders_in(direct: list[ast.AST], lines: list[str]) -> frozenset[str]:
    names: set[str] = set()
    for node in direct:
        if isinstance(node, ast.Assign) and is_placeholder_value(node.value):
            found = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and node.value is not None and is_placeholder_value(node.value) and isinstance(node.target, ast.Name):
            found = [node.target.id]
        else:
            continue
        if not _marked(lines, node.lineno, getattr(node, "end_lineno", None) or node.lineno):
            names.update(found)
    return frozenset(names)


def _bound_in(fn: _Scope, direct: list[ast.AST]) -> frozenset[str]:
    """Names *fn* binds itself (parameters, assignments, nested definitions), minus those it declares ``global``."""
    args = fn.args  # type: ignore[union-attr]
    params = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
    params += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
    bound: set[str] = set(params)
    declared_global: set[str] = set()
    for node in direct:
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Global):
            declared_global.update(node.names)
    return frozenset(bound - declared_global)


def _returns_in(fn: _Scope, direct: list[ast.AST], rel: str, visible: frozenset[str], scope_name: str, lines: list[str]) -> list[Finding]:
    return [
        Finding(
            rel,
            node.lineno,
            RULE,
            f"{node.value.id} is a mutable placeholder built once in {scope_name} and returned by reference from "
            f"{getattr(fn, 'name', '<function>')}: every caller shares one object; build the value inside the function or return a copy",
        )
        for node in direct
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name) and node.value.id in visible and not _marked(lines, node.lineno, node.lineno)
    ]


def _findings_in(parsed: ParsedFile) -> list[Finding]:
    """Each scope's own nodes come from the shared per-tree scope index, so the placeholder, binding and return checks share one collection."""
    if not any(isinstance(r.value, ast.Name) for r in nodes_of(parsed.tree, ast.Return)):
        return []  # a finding is a ``return NAME``; a module with none has nothing to judge
    out: list[Finding] = []
    lines = parsed.source.splitlines()
    # per scope: names an inner function can see, and the name findings in its functions are reported against
    context: dict[int, tuple[frozenset[str], str]] = {}
    for scope in iter_scopes(parsed.tree):
        if scope.parent is None:
            enclosing, scope_name = _placeholders_in(scope.nodes, lines), "the module"
        else:
            parent_enclosing, parent_name = context[id(scope.parent)]
            node = scope.node
            assert node is not None, "only the module scope has no node, and it is handled above"
            if isinstance(node, ast.ClassDef):
                enclosing, scope_name = parent_enclosing, parent_name
            else:
                # with no placeholder in an enclosing scope nothing is visible here: no binding walk and no return to report
                visible = parent_enclosing - _bound_in(node, scope.nodes) if parent_enclosing else frozenset()
                if visible:
                    out.extend(_returns_in(node, scope.nodes, parsed.rel, visible, parent_name, lines))
                enclosing, scope_name = visible | _placeholders_in(scope.nodes, lines), node.name
        context[id(scope)] = (enclosing, scope_name)
    return out


def find_shared_mutable_placeholders(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every ``return NAME`` of an enclosing scope's mutable placeholder in *files*, sorted by path and line.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for an unreadable file (unless
    *allow_unparsed*).
    """
    scan = scan_python(list(files), min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_no_shared_mutable_placeholders(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any function that returns an enclosing scope's mutable placeholder by reference (see the module docstring)."""
    found = find_shared_mutable_placeholders(files, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(
            f"{len(found)} shared-mutable-placeholder finding(s): a shared object returned by reference is mutated by every caller:\n  "
            + "\n  ".join(f.render() for f in found)
        )
