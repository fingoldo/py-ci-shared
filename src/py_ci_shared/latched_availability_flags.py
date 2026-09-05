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
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = [
    "Finding",
    "assert_no_latched_availability_flags",
    "find_latched_availability_flags",
]

# Substrings that make a boolean module global read as an availability or failure verdict.
_FLAG_MARKERS = ("AVAILABLE", "FAILED", "USABLE", "SUPPORTED", "PRESENT", "WORKS", "BROKEN")

# The exception types that make a handler broad enough to swallow a transient fault.
_BROAD = frozenset({"Exception", "BaseException"})


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
    """Names bound at module scope, including annotated ones.

    The annotated form is the one these flags actually use -- ``_GPU_AVAILABLE: bool | None = None`` -- so
    missing it makes the whole check report nothing on the code it was written for.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """Whether this handler catches broadly enough to swallow a transient fault."""
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in _BROAD
    if isinstance(handler.type, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in _BROAD for e in handler.type.elts)
    return False


def _looks_like_a_flag(name: str) -> bool:
    """Whether the name reads as an availability or failure verdict."""
    upper = name.upper()
    return any(marker in upper for marker in _FLAG_MARKERS)


def find_latched_availability_flags(roots: Sequence[Path], exclude: Iterable[str] = ()) -> list[Finding]:
    """Return every boolean availability flag pinned to a constant inside a broad ``except``."""
    excluded = tuple(exclude)
    findings: list[Finding] = []
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            if any(fragment in path.as_posix() for fragment in excluded):
                continue
            try:
                tree = ast.parse(path.read_bytes().decode("utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            module_names = _module_level_names(tree)
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                declared_global = {n for node in ast.walk(function) for n in (node.names if isinstance(node, ast.Global) else ())}
                for node in ast.walk(function):
                    if not isinstance(node, ast.Try):
                        continue
                    for handler in node.handlers:
                        if not _is_broad(handler):
                            continue
                        for stmt in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
                            if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, bool)):
                                continue
                            for target in stmt.targets:
                                if isinstance(target, ast.Name) and target.id in declared_global and target.id in module_names and _looks_like_a_flag(target.id):
                                    findings.append(Finding(path, stmt.lineno, target.id, function.name))
    return findings


def assert_no_latched_availability_flags(roots: Sequence[Path], exclude: Iterable[str] = (), allow: Iterable[str] = ()) -> None:
    """Raise ``AssertionError`` listing every latched availability flag that is not explicitly allowed.

    ``allow`` holds flag names. A deliberate latch belongs there with a reason -- a circuit breaker a caller
    re-arms is doing exactly what it should. What does not belong there is a probe: narrow its handler to
    ``ImportError``, warn on anything else, and leave the cache unset so the next call re-probes.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    findings = [f for f in find_latched_availability_flags(roots, exclude) if f.flag not in allowed]
    if not findings:
        return
    newline = chr(10)
    listing = (newline + "  ").join(str(f) for f in findings)
    raise AssertionError(
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
