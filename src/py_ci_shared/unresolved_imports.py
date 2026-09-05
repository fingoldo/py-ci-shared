"""Every ``from X import Y`` must name something X actually defines.

A name that no longer exists behind a `from` import is invisible until the importing module is loaded.
Two shapes, both seen in production repos:

* **At module scope** the failure is an ImportError at COLLECTION. Under `pytest-split` every shard collects
  the whole tree, so ONE missing name fails every shard rather than the single shard that owns the test --
  observed as 39 of 40 shards red on a single removed helper.
* **Inside a function** the failure waits until that branch runs. A `from ..linear_model import X` sitting in
  the `if self.regressor is None:` branch of a public estimator's `fit` broke the DOCUMENTED DEFAULT while
  every test that passed an explicit regressor stayed green.

Resolution is static: the target module is parsed, never imported, so a scan costs no side effects and works
on modules whose imports are expensive or hardware-dependent. That also means a name created dynamically --
by `exec`, by a registry, by a module-level `globals()[...] = ...` facade, or re-exported through a
`__getattr__` -- is invisible to the parse and must not be reported. Such modules are detected and skipped,
with the skip recorded so a reader can tell "checked and clean" from "not checked".
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = [
    "ModuleIndex",
    "assert_all_from_imports_resolve",
    "find_unresolved_from_imports",
]

# A module that builds names at runtime cannot be resolved by parsing it.
_DYNAMIC_MARKERS = ("globals()[", "__getattr__", "exec(", "setattr(sys.modules")


class ModuleIndex:
    """Maps a dotted module name to the set of top-level names it binds, by parsing only."""

    def __init__(self, roots: Sequence[Path], package_roots: Sequence[Path] | None = None) -> None:
        self._names: dict[str, set[str]] = {}
        self._dynamic: set[str] = set()
        self._packages: set[str] = set()
        self._package_roots = [Path(p) for p in (package_roots or roots)]
        for root in roots:
            for path in sorted(Path(root).rglob("*.py")):
                dotted = self._dotted(path)
                if dotted is None:
                    continue
                try:
                    source = path.read_text(encoding="utf-8")
                    tree = ast.parse(source)
                except (SyntaxError, UnicodeDecodeError, OSError):
                    continue
                if any(marker in source for marker in _DYNAMIC_MARKERS):
                    self._dynamic.add(dotted)
                if path.name == "__init__.py":
                    self._packages.add(dotted)
                self._names[dotted] = _bound_names(tree)

        # A package's SUBMODULES are importable names too: `from a.b import c` is valid whenever a/b/c.py
        # exists, even though b/__init__.py binds no name `c`. Without this every subpackage facade reads
        # as thousands of undefined names.
        for dotted in list(self._names):
            parent, _, leaf = dotted.rpartition(".")
            if parent in self._names:
                self._names[parent].add(leaf)

    def _dotted(self, path: Path) -> str | None:
        """The dotted module name for a file, relative to whichever package root contains it."""
        for base in self._package_roots:
            try:
                rel = path.resolve().relative_to(Path(base).resolve())
            except ValueError:
                continue
            parts = list(rel.parts)
            if parts[-1] == "__init__.py":
                parts = parts[:-1]
            else:
                parts[-1] = parts[-1][: -len(".py")]
            return ".".join(parts) if parts else None
        return None

    def knows(self, dotted: str) -> bool:
        """True when this module was parsed and can be resolved against."""
        return dotted in self._names

    def is_package(self, dotted: str) -> bool:
        """True when the dotted name is a package (its file is an ``__init__.py``)."""
        return dotted in self._packages

    def is_dynamic(self, dotted: str) -> bool:
        """True when the module builds names at runtime, so a parse cannot see its full surface."""
        return dotted in self._dynamic

    def names(self, dotted: str) -> set[str]:
        """Top-level names bound by the module."""
        return self._names.get(dotted, set())


def _assigned_names(target: ast.AST) -> set[str]:
    """Every name an assignment target binds, including tuple/list unpacking.

    `a, b, c = factory(...)` is a common way to publish a family of related callables, and reading only
    bare-Name targets reports every one of them as undefined in the module that imports them.
    """
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for element in target.elts:
            out |= _assigned_names(element)
        return out
    if isinstance(target, ast.Starred):
        return _assigned_names(target.value)
    return set()


def _names_bound_by(node: ast.AST) -> set[str]:
    """Names a SINGLE statement binds. Shared by the top level and by conditional bodies.

    A version guard or an ImportError fallback binds names exactly as the top level does, so the two were
    originally written twice; one function keeps them from drifting apart.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        out: set[str] = set()
        for target in node.targets:
            out |= _assigned_names(target)
        return out
    if isinstance(node, ast.AnnAssign):
        return _assigned_names(node.target)
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".")[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        # A star import republishes an unknown set; mark the module unresolvable rather than reporting
        # every consumer of a name it may well provide.
        return {"*" if alias.name == "*" else (alias.asname or alias.name) for alias in node.names}
    return set()


def _bound_names(tree: ast.Module) -> set[str]:
    """Every top-level name a module binds, including inside version guards and import fallbacks."""
    out: set[str] = set()
    for node in tree.body:
        out |= _names_bound_by(node)
        if isinstance(node, (ast.If, ast.Try)):
            for sub in ast.walk(node):
                out |= _names_bound_by(sub)
    return out


def _resolve_relative(importing: str, node: ast.ImportFrom, *, is_package: bool) -> str | None:
    """The absolute dotted target of a possibly-relative ImportFrom, or None if it escapes the tree.

    `level=1` means "the package this module lives in". For a package's own ``__init__`` that IS the
    package; for a plain module it is the parent, so the module's own leaf has to come off first --
    otherwise `from .post import X` inside `pkg/mod.py` resolves to `pkg.mod.post` and every relative
    import in the tree reads as a missing module.
    """
    if not node.level:
        return node.module
    parts = importing.split(".")
    if not is_package:
        parts = parts[:-1]
    up = node.level - 1
    if up:
        if len(parts) <= up:
            return None
        parts = parts[: len(parts) - up]
    return ".".join([*parts, node.module]) if node.module else ".".join(parts)


def _judge_import(path: Path, tree: ast.Module, node: ast.ImportFrom, index: "ModuleIndex", importing: str, prefixes: tuple) -> str | None:
    """One `from X import Y`, judged. Returns a problem line or None."""
    target = _resolve_relative(importing, node, is_package=index.is_package(importing))
    if not target or not target.startswith(prefixes):
        return None
    if not index.knows(target):
        # Inside the package but never parsed: `from pkg.does_not_exist import X` is the defect hunted here.
        return f"{path.as_posix()}:{node.lineno}: module '{target}' does not exist"
    if index.is_dynamic(target) or "*" in index.names(target):
        return None
    available = index.names(target)
    missing = [a.name for a in node.names if a.name != "*" and a.name not in available]
    if not missing:
        return None
    scope = "module scope" if _at_module_scope(tree, node) else "inside a function"
    return f"{path.as_posix()}:{node.lineno}: '{target}' does not define '{missing[0]}' ({scope})"


def find_unresolved_from_imports(
    scan_roots: Sequence[Path],
    index: ModuleIndex,
    *,
    resolvable_prefixes: Iterable[str],
) -> list[str]:
    """Every `from X import Y` under ``scan_roots`` where X is known and does not bind Y.

    Only imports whose target starts with one of ``resolvable_prefixes`` are judged -- a third-party target
    is not in the index and its absence proves nothing.
    """
    prefixes = tuple(resolvable_prefixes)
    problems: list[str] = []
    for root in scan_roots:
        for path in sorted(Path(root).rglob("*.py")):
            importing = index._dotted(path) or ""
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    problem = _judge_import(path, tree, node, index, importing, prefixes)
                    if problem:
                        problems.append(problem)
    return problems


def _at_module_scope(tree: ast.Module, target: ast.ImportFrom) -> bool:
    """True when the import is executed at import time rather than when some branch runs."""
    return any(node is target for node in tree.body)


def assert_all_from_imports_resolve(
    scan_roots: Sequence[Path],
    package_roots: Sequence[Path],
    *,
    resolvable_prefixes: Iterable[str],
    allowlist: Iterable[str] = (),
) -> None:
    """Fail on any `from X import Y` naming something X does not define.

    ``allowlist`` entries are matched as substrings of the reported line, for the rare genuinely-dynamic
    target this module's own heuristics cannot see.
    """
    import pytest

    index = ModuleIndex(package_roots, package_roots)
    problems = [p for p in find_unresolved_from_imports(scan_roots, index, resolvable_prefixes=resolvable_prefixes) if not any(a in p for a in allowlist)]
    if problems:
        pytest.fail(
            f"{len(problems)} unresolved `from X import Y`:\n  " + "\n  ".join(problems) + "\n\nA module-scope one is an ImportError at COLLECTION -- with pytest-split every shard collects "
            "the whole tree, so one missing name fails all of them. One inside a function waits for that branch."
        )
