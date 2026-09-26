"""A broad ``except`` must not cache a process-lifetime "unavailable" verdict.

An availability probe wrapped in ``except Exception`` and memoised into a module-level flag turns the first
exception of the run into the answer for the rest of it. The exceptions such probes actually see are not
facts about the machine: another process holding the device, an allocation failing at that instant, a
driver reset, a fault raised out of a device-count call under contention. Only ``ImportError`` is a genuine
absence -- a library that is not installed will not become installed -- and that one is correct to cache.

Three instances in one repository, each silent and each paid for the whole run:

* a metrics argsort probe, giving back a measured ~10% end-to-end win at 200k rows;
* a feature-selection cluster probe, putting an entire pair loop on the CPU for every later ``fit()``;
* a transformer probe, doing the same one package over -- found by this check rather than by review.

Scope. Only a name that is BOTH assigned at module scope and declared ``global`` in the function doing the
caching is reported: a local ``ok = False`` is a per-call verdict and harmless, and on a ~3500-module
repository that distinction alone takes the report from 29 to 7. The name must also read as an
availability or failure flag, which is a heuristic and is why ``allow`` exists -- a deliberate latch, such
as a circuit breaker that a caller re-arms, is a legitimate answer rather than a defect.

Known blind spot: a probe that stores its verdict in a dict (``result["available"] = False``) rather than a
module global is not reported. The dict is usually local, so it cannot be distinguished from a per-call
result without following it to its caller.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import DEFAULT_EXCLUDE, ImportAliases, ScanResult, iter_files, scan_python
from ._core.node_index import walk as _fast_walk

__all__ = [
    "Finding",
    "assert_no_latched_availability_flags",
    "find_latched_availability_flags",
]

# Substrings that make a boolean module global read as an availability or failure verdict.
_FLAG_MARKERS = ("AVAILABLE", "FAILED", "USABLE", "SUPPORTED", "PRESENT", "WORKS", "BROKEN")

# The exception types that make a handler broad enough to swallow a transient fault, however they are spelled.
_BROAD = frozenset({"Exception", "BaseException"})
_BROAD_QUALIFIED = _BROAD | {f"builtins.{name}" for name in _BROAD}

_TRY_TYPES: tuple[type, ...] = (ast.Try,) + ((getattr(ast, "TryStar"),) if hasattr(ast, "TryStar") else ())
_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


class Finding:
    """One boolean module global pinned inside a broad ``except``."""

    def __init__(self, path: Path, lineno: int, flag: str, function: str) -> None:
        """Record the site."""
        self.path = path
        self.lineno = lineno
        self.flag = flag
        self.function = function

    def __str__(self) -> str:
        """Render as ``path:line  flag (in function)``."""
        return f"{self.path.as_posix()}:{self.lineno}  {self.flag} (in {self.function})"


def _module_level_names(tree: ast.Module) -> set[str]:
    """Names bound at module scope, including annotated ones and those under a module-level ``if``/``try``.

    The annotated form is the one these flags actually use -- ``_GPU_AVAILABLE: bool | None = None`` -- so
    missing it makes the whole check report nothing on the code it was written for.
    """
    names: set[str] = set()
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Assign):
            names |= {n.id for t in node.targets for n in _fast_walk(t) if isinstance(n, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.If, ast.With, ast.AsyncWith)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
        elif isinstance(node, _TRY_TYPES):
            stack.extend(getattr(node, "body", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))
    return names


def _is_broad(handler: ast.ExceptHandler, aliases: Optional[ImportAliases] = None) -> bool:
    """Whether this handler catches broadly enough to swallow a transient fault (``Exception``, ``BaseException``,
    ``builtins.Exception``, a bare ``except``, or a tuple containing one of them)."""
    if handler.type is None:
        return True
    resolver = aliases or ImportAliases()
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(resolver.qualified_name(t) in _BROAD_QUALIFIED for t in types)


def _looks_like_a_flag(name: str) -> bool:
    """Whether the name reads as an availability or failure verdict."""
    upper = name.upper()
    return any(marker in upper for marker in _FLAG_MARKERS)


def _own_scope(function: _FunctionNode) -> Iterator[ast.AST]:
    """Nodes of *function*'s own scope: a nested def or class is scanned on its own, with its own ``global``s."""
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _qualified_functions(tree: ast.Module) -> Iterator[tuple[str, _FunctionNode]]:
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


def _bool_targets(stmt: ast.AST) -> list[ast.Name]:
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, bool):
        return [t for t in stmt.targets if isinstance(t, ast.Name)]
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, bool) and isinstance(stmt.target, ast.Name):
        return [stmt.target]
    return []


def _handler_statements(handler: ast.ExceptHandler) -> Iterator[ast.AST]:
    """Statements run by *handler*, not those inside a function defined in it."""
    stack: list[ast.AST] = list(handler.body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _scan(roots: Sequence[Path], exclude: Iterable[str]) -> ScanResult:
    excluded = tuple(exclude)
    files: list[Path] = []
    for root in roots:
        files.extend(p for p in iter_files(Path(root), ("*.py",), exclude=DEFAULT_EXCLUDE) if not any(fragment in p.as_posix() for fragment in excluded))
    return scan_python(files)


def _findings(scan: ScanResult) -> list[Finding]:
    findings: list[Finding] = []
    for parsed in scan:
        tree = parsed.tree
        aliases = ImportAliases.from_tree(tree)
        module_names = _module_level_names(tree)
        for qualname, function in _qualified_functions(tree):
            own = list(_own_scope(function))
            declared_global = {n for node in own if isinstance(node, ast.Global) for n in node.names}
            if not declared_global:
                continue
            for node in own:
                if not isinstance(node, _TRY_TYPES):
                    continue
                for handler in getattr(node, "handlers", []):
                    if not _is_broad(handler, aliases):
                        continue
                    for stmt in _handler_statements(handler):
                        findings.extend(
                            Finding(parsed.path, target.lineno, target.id, qualname)
                            for target in _bool_targets(stmt)
                            if target.id in declared_global and target.id in module_names and _looks_like_a_flag(target.id)
                        )
    return sorted(findings, key=lambda f: (f.path.as_posix(), f.lineno))


def find_latched_availability_flags(roots: Sequence[Path], exclude: Iterable[str] = ()) -> list[Finding]:
    """Return every boolean availability flag pinned to a constant inside a broad ``except`` (``try``/``except*`` alike),
    each reported once, under the function whose own scope holds it. Unparsable files are not in this list; the assert
    fails on them."""
    return _findings(_scan(roots, exclude))


def assert_no_latched_availability_flags(roots: Sequence[Path], exclude: Iterable[str] = (), allow: Iterable[str] = (), *, min_files: int = 1) -> None:
    """Raise ``AssertionError`` listing every latched availability flag that is not explicitly allowed.

    ``allow`` holds flag names. A deliberate latch belongs there with a reason -- a circuit breaker a caller
    re-arms is doing exactly what it should. What does not belong there is a probe: narrow its handler to
    ``ImportError``, warn on anything else, and leave the cache unset so the next call re-probes.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    scan = _scan(roots, exclude)
    scan.min_files = min_files
    newline = chr(10)
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if scan.unparsed:
        problems.append(
            f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked:" + "".join(f"{newline}  {u.render()}" for u in scan.unparsed)
        )
    findings = [f for f in _findings(scan) if f.flag not in allowed]
    if findings:
        listing = (newline + "  ").join(str(f) for f in findings)
        problems.append(
            newline.join(
                [
                    f"{len(findings)} availability flag(s) pinned for the process inside a broad `except`.",
                    "  The first exception of the run becomes the answer for the rest of it, and the exceptions these",
                    "  probes see are moments, not facts: contention, an allocation failing, a driver reset.",
                    "  Cache only ImportError; warn on anything else and leave the flag unset so the next call re-probes.",
                    f"  {listing}",
                ]
            )
        )
    if problems:
        raise AssertionError(newline.join(problems))
