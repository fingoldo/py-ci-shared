"""Module-level import cycles, and the cycles that only load when one particular side is imported first.

Two rules over the package's top-level import graph (imports inside a function body, and under ``if TYPE_CHECKING:``, do
not run at load and are ignored):

* ``import-cycle``: a strongly connected component of more than one module. A cycle resolves only while every member
  binds what the others need before the back-edge runs, so a later reorder turns it into ``ImportError: cannot import
  name X from partially initialized module Y``. Existing cycles are ratcheted by the baseline.
* ``import-order``: the load is SIMULATED once per cycle member imported first (ancestor packages first, as Python
  does). A ``from Y import n`` that runs while ``Y`` is still executing and has not yet bound ``n`` fails for that
  order. This is the shape a size split produces: the parent re-exports the sibling's names at its BOTTOM while the
  sibling imports the parent at its TOP, so importing the parent works and importing the sibling first raises. The
  process that happens to import the working side first hides it until some other entry point imports the other side.

The simulation is static: an import under ``try``/``if`` is assumed to run (and one under a ``try`` catching
``ImportError`` survives a failure, as it does at runtime), bindings under ``if`` count as bound, and a
module that defines ``__getattr__`` binds every name. A submodule named in ``from pkg import sub`` is imported, not
looked up. Only imports that resolve to a module in the scanned corpus take part.

Usage from a repository's meta tests::

    from py_ci_shared.import_cycles import assert_no_import_cycles

    def test_no_import_cycles(request):
        assert_no_import_cycles(REPO / "src" / "mypkg", baseline_path=HERE / "_import_cycles_baseline.json", request=request)
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ScanResult, module_of, package_of, resolve_relative, scan_python
from ._core.node_index import walk as _fast_walk
from ._gate_run import enforce_findings

__all__ = ["REFRESH_FLAG", "ImportEvent", "assert_no_import_cycles", "build_import_graph", "find_import_cycles"]

REFRESH_FLAG = "--refresh-import-cycles-baseline"
GATE = "import-cycles"
RULE_CYCLE = "import-cycle"
RULE_ORDER = "import-order"


@dataclass(frozen=True)
class ImportEvent:
    """One top-level import statement's effect on module *target*: ``names`` is None for ``import target``."""

    line: int
    target: str
    names: Optional[tuple[str, ...]] = None
    guarded: bool = False


@dataclass
class _Module:
    name: str
    rel: str
    is_package: bool
    events: list[ImportEvent] = field(default_factory=list)
    bindings: list[tuple[int, str]] = field(default_factory=list)
    has_getattr: bool = False


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def _load_time_statements(body: Iterable[ast.stmt]) -> Iterable[ast.stmt]:
    """Statements that run when the module is imported: into if/try/with blocks, never into def/class bodies,
    ``if TYPE_CHECKING:`` or ``if __name__ == "__main__":``."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, ast.If):
            if not (_is_type_checking(stmt.test) or _is_main_guard(stmt.test)):
                yield from _load_time_statements(stmt.body)
            yield from _load_time_statements(stmt.orelse)
        elif isinstance(stmt, ast.Try) or type(stmt).__name__ == "TryStar":
            for block in (stmt.body, stmt.orelse, stmt.finalbody):  # type: ignore[attr-defined]
                yield from _load_time_statements(block)
            for handler in stmt.handlers:  # type: ignore[attr-defined]
                yield from _load_time_statements(handler.body)
        elif isinstance(stmt, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
            yield from _load_time_statements(stmt.body)
            yield from _load_time_statements(getattr(stmt, "orelse", []))


_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any((isinstance(t, ast.Name) and t.id in _IMPORT_ERRORS) or (isinstance(t, ast.Attribute) and t.attr in _IMPORT_ERRORS) for t in types)


def _guarded_lines(tree: ast.Module) -> set[int]:
    """Lines of statements inside a ``try`` whose handler catches ImportError: a failed import there is survived."""
    out: set[int] = set()
    for node in _fast_walk(tree):
        if isinstance(node, ast.Try) and any(_catches_import_error(h) for h in node.handlers):
            for stmt in node.body:
                out.update(getattr(n, "lineno", 0) for n in _fast_walk(stmt))
    return out


def _target_names(target: ast.expr) -> Iterable[str]:
    for node in _fast_walk(target):
        if isinstance(node, ast.Name):
            yield node.id


def _import_bindings(stmt: ast.stmt) -> Iterable[str]:
    if isinstance(stmt, ast.Import):
        return [alias.asname or alias.name.split(".", 1)[0] for alias in stmt.names]
    if isinstance(stmt, ast.ImportFrom):
        return [alias.asname or alias.name for alias in stmt.names]  # "*" is kept: _Simulation reads it as "binds all"
    return []


def _bindings(stmt: ast.stmt) -> Iterable[str]:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    targets: list[ast.expr] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AugAssign) or (isinstance(stmt, ast.AnnAssign) and stmt.value is not None):
        targets = [stmt.target]
    elif isinstance(stmt, (ast.For, ast.AsyncFor)):
        targets = [stmt.target]
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in stmt.items if item.optional_vars is not None]
    else:
        return _import_bindings(stmt)
    return [n for t in targets for n in _target_names(t)]


def _chain(dotted: str) -> list[str]:
    parts = dotted.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _events(mod: _Module, tree: ast.Module, package: str, known: Mapping[str, Any]) -> list[ImportEvent]:
    out: list[ImportEvent] = []
    guarded = _guarded_lines(tree)
    for stmt in _load_time_statements(tree.body):
        g = stmt.lineno in guarded
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                out.extend(ImportEvent(stmt.lineno, m, None, g) for m in _chain(alias.name) if m in known)
        elif isinstance(stmt, ast.ImportFrom):
            base = resolve_relative(stmt.module, stmt.level, package)
            if base is None:
                continue
            out.extend(ImportEvent(stmt.lineno, m, None, g) for m in _chain(base)[:-1] if m in known)
            if base not in known:
                continue
            names: list[str] = []
            for alias in stmt.names:
                sub = f"{base}.{alias.name}"
                if alias.name == "*":
                    continue
                if sub in known:
                    out.append(ImportEvent(stmt.lineno, sub, None, g))
                else:
                    names.append(alias.name)
            out.append(ImportEvent(stmt.lineno, base, tuple(names), g))
    return out


def build_import_graph(scan: ScanResult, src_root: Path, package: str) -> dict[str, _Module]:
    """``{module: _Module}`` for every parsed file; *package* is the dotted name of *src_root* ('' for a src/ layout)."""
    known: dict[str, tuple[Any, ...]] = {}
    for f in scan:
        known[module_of(f.path, src_root, package)] = (f, package_of(f.path, src_root, package))
    graph: dict[str, _Module] = {}
    for name, (f, pkg) in known.items():
        mod = _Module(name, f.rel, f.path.name == "__init__.py")
        mod_pkg = name if mod.is_package else pkg
        mod.events = _events(mod, f.tree, mod_pkg, known)
        for stmt in _load_time_statements(f.tree.body):
            mod.bindings.extend((stmt.lineno, n) for n in _bindings(stmt))
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "__getattr__":
                mod.has_getattr = True
        graph[name] = mod
    return graph


class _Tarjan:
    """Iterative Tarjan (a deep package would overflow the recursive form)."""

    def __init__(self, edges: Mapping[str, set[str]]) -> None:
        self.edges = edges
        self.index: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.stack: list[str] = []
        self.on_stack: set[str] = set()
        self.out: list[list[str]] = []

    def _visit(self, node: str, work: list[tuple[str, list[str]]]) -> None:
        self.index[node] = self.low[node] = len(self.index)
        self.stack.append(node)
        self.on_stack.add(node)
        work.append((node, sorted(self.edges.get(node, ()))))

    def _close(self, node: str) -> None:
        comp: list[str] = []
        while True:
            w = self.stack.pop()
            self.on_stack.discard(w)
            comp.append(w)
            if w == node:
                break
        self.out.append(sorted(comp))

    def run(self) -> list[list[str]]:
        for root in sorted(self.edges):
            if root in self.index:
                continue
            work: list[tuple[str, list[str]]] = []
            self._visit(root, work)
            while work:
                node, pending = work[-1]
                if pending:
                    nxt = pending.pop(0)
                    if nxt not in self.index:
                        self._visit(nxt, work)
                    elif nxt in self.on_stack:
                        self.low[node] = min(self.low[node], self.index[nxt])
                    continue
                work.pop()
                if work:
                    self.low[work[-1][0]] = min(self.low[work[-1][0]], self.low[node])
                if self.low[node] == self.index[node]:
                    self._close(node)
        return self.out


def _sccs(edges: Mapping[str, set[str]]) -> list[list[str]]:
    return _Tarjan(edges).run()


class _Simulation:
    """Replays the load of one start module, restricted to one component, and records the first failing from-import."""

    def __init__(self, graph: Mapping[str, _Module], members: set[str]) -> None:
        self.graph = graph
        self.members = members
        self.position: dict[str, int] = {}
        self.done: set[str] = set()
        self.imported_subs: dict[str, set[str]] = {}
        self.failure: Optional[tuple[_Module, ImportEvent, str, int, list[str]]] = None

    def _missing(self, mod: _Module, before: int, wanted: Iterable[str]) -> list[str]:
        names = {n for line, n in mod.bindings if line < before}
        if "*" in names:
            return []  # a star import above binds names this scan cannot list
        return sorted(set(wanted) - names - self.imported_subs.get(mod.name, set()))

    def _enter(self, event: ImportEvent) -> bool:
        """Load *event*'s target if it has not started; False when a failure propagates past this statement."""
        if event.target in self.done or event.target in self.position:
            return True
        started = set(self.position)
        self.load(event.target)
        if self.failure is None:
            return True
        if not event.guarded:
            return False
        self.failure = None  # the ImportError is caught here; Python drops the half-run modules
        for half in [m for m in self.position if m not in started]:
            del self.position[half]
        return True

    def _check_names(self, mod: _Module, event: ImportEvent, target: _Module) -> None:
        if not event.names or event.guarded or target.has_getattr or event.target not in self.position:
            return
        at = self.position[event.target]
        missing = self._missing(target, at, event.names)
        if missing:
            self.failure = (mod, event, event.target, at, missing)

    def load(self, name: str) -> None:
        if self.failure is not None or name in self.done or name in self.position or name not in self.members:
            return
        mod = self.graph[name]
        for event in mod.events:
            self.position[name] = event.line
            target = self.graph.get(event.target)
            if target is None or event.target not in self.members:
                continue
            if not self._enter(event):
                return
            parent, _, leaf = event.target.rpartition(".")
            if parent:
                self.imported_subs.setdefault(parent, set()).add(leaf)
            self._check_names(mod, event, target)
            if self.failure is not None:
                return
        self.position.pop(name, None)
        self.done.add(name)


def _is_implicit_parent(name: str, event: ImportEvent) -> bool:
    """``import a.b.c`` inside ``a.b.x`` also names ``a`` and ``a.b``: they are already loading, so that is no edge. Nor is
    ``from . import sibling`` inside ``a.b.x``: it reaches the submodule ``a.b.sibling`` (its own edge) through the
    package, and asks the package for no name it binds. ``from . import NAME`` of a name ``a.b`` binds stays an edge."""
    return not event.names and name.startswith(event.target + ".")


def _ancestors_in(name: str, members: set[str]) -> list[str]:
    return [m for m in _chain(name)[:-1] if m in members]


def find_import_cycles(
    src_root: Union[str, Path],
    *,
    package: Optional[str] = None,
    exclude_parts: Iterable[str] = (),
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)`` for the package at *src_root*.

    *package* is the dotted name of *src_root*; it defaults to the directory name when *src_root* holds an
    ``__init__.py`` and to ``""`` otherwise (a ``src/`` directory holding several top-level packages).
    """
    root = Path(src_root)
    if package is None:
        package = root.name if (root / "__init__.py").is_file() else ""
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    graph = build_import_graph(scan, root, package)
    edges = {name: {e.target for e in mod.events if e.target != name and not _is_implicit_parent(name, e)} for name, mod in graph.items()}
    everything = set(graph)
    findings: list[Finding] = []
    for comp in _sccs(edges):
        if len(comp) < 2:
            continue
        display = " -> ".join([*comp, comp[0]])
        first = graph[comp[0]]
        findings.append(Finding(first.rel, 1, RULE_CYCLE, f"{len(comp)}-module import cycle: {display}", key=f"{RULE_CYCLE}::{' | '.join(comp)}"))
        failures: dict[tuple[str, int, str, str], list[str]] = {}
        for start in comp:
            sim = _Simulation(graph, everything)
            for step in [*_ancestors_in(start, everything), start]:  # Python runs every ancestor package first
                sim.load(step)
            if sim.failure is None:
                continue
            mod, event, target, at, missing = sim.failure
            failures.setdefault((mod.rel, event.line, target, ", ".join(missing)), []).append(f"{start}@{at}")
        for (rel, line, target, missing_s), starts in sorted(failures.items()):
            which = ", ".join(s.split("@")[0] for s in starts)
            paused = starts[0].split("@")[1]
            message = (
                f"`from {target} import {missing_s}` runs while {target} is still executing (at its line {paused}) and has not bound "
                f"{missing_s} yet: raises ImportError when {which} is imported first"
            )
            findings.append(Finding(rel, line, RULE_ORDER, message, key=f"{RULE_ORDER}::{rel}::{target}::{missing_s}"))
    findings.sort(key=lambda f: (f.rule, f.path, f.line))
    return findings, scan


def assert_no_import_cycles(
    src_root: Union[str, Path],
    *,
    package: Optional[str] = None,
    exclude_parts: Iterable[str] = (),
    baseline_path: Optional[Union[str, Path]] = None,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on an import cycle or an order-dependent from-import not accepted by *baseline_path*.

    Without a baseline every finding fails. A missing baseline fails; it is written only on refresh
    (``REFRESH_FLAG``, or ``PY_CI_SHARED_REFRESH=import-cycles``). Unparsed files and a walk under *min_files* fail.
    """
    findings, scan = find_import_cycles(src_root, package=package, exclude_parts=exclude_parts, use_git=use_git)
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance=(
            "break the cycle (move the shared names to a leaf module both import) or, for import-order, bind the "
            "names before the import that closes the cycle"
        ),
    )


if __name__ == "__main__":  # pragma: no cover - manual corpus run
    found, result = find_import_cycles(sys.argv[1], package=sys.argv[2] if len(sys.argv) > 2 else None)
    for item in found:
        print(item.render())  # noqa: T201
    print(f"{len(found)} finding(s), {result.parsed_count} parsed, {len(result.unparsed)} unparsed")  # noqa: T201
