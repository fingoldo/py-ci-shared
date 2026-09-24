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
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from ._core import DEFAULT_EXCLUDE, SourceError, iter_files, parse_source

__all__ = [
    "ModuleIndex",
    "assert_all_from_imports_resolve",
    "find_unresolved_from_imports",
]

_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError"})
_BROAD_ERRORS = frozenset({"Exception", "BaseException"})


def _module_level_nodes(body: "list[ast.stmt]") -> Iterator[ast.AST]:
    """Every node in the MODULE scope: module statements and compound-statement bodies, never the inside of a
    ``def``/``lambda``/``class`` body (their decorators, defaults and bases are module-scope expressions)."""
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend([*node.decorator_list, *node.args.defaults])
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend([*node.decorator_list, *node.bases, *(k.value for k in node.keywords)])
            continue
        if isinstance(node, ast.Lambda):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _is_dynamic_module(tree: ast.Module) -> bool:
    """Does the module build names at import time in a way a parse cannot see? A module-level ``def __getattr__``,
    ``globals()[...] = ...`` / ``globals().update(...)`` / ``vars()[...] = ...`` outside a function, an ``exec`` at import
    time, or ``setattr(sys.modules[...], ...)``. A class's ``__getattr__`` method is not one."""
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name == "__getattr__":
            return True
    for node in _module_level_nodes(tree.body):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Call) and getattr(target.value.func, "id", "") in ("globals", "vars"):
                    return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name == "exec":
                return True
            if (
                name == "update"
                and isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Call)
                and getattr(func.value.func, "id", "") in ("globals", "vars")
            ):
                return True
            if name == "setattr" and node.args and "sys.modules" in ast.unparse(node.args[0]):
                return True
    return False


class ModuleIndex:
    """Maps a dotted module name to the set of top-level names it binds, by parsing only."""

    def __init__(self, roots: Sequence[Path], package_roots: Sequence[Path] | None = None) -> None:
        self._names: dict[str, set[str]] = {}
        self._dynamic: set[str] = set()
        self._packages: set[str] = set()
        self._package_roots = [Path(p) for p in (package_roots or roots)]
        #: ``(path, "line: kind: message")`` for every file under the roots that could not be read or parsed. Its
        #: module is known to exist but its names are not, so imports from it are not judged.
        self.unparsed: list[tuple[Path, str]] = []
        for root in roots:
            for path in iter_files(Path(root), ("*.py",), exclude=DEFAULT_EXCLUDE):
                dotted = self._dotted(path)
                if dotted is None:
                    continue
                if path.name == "__init__.py":
                    self._packages.add(dotted)
                try:
                    _, tree = parse_source(path)
                except SourceError as exc:
                    self.unparsed.append((path, f"{exc.line or 1}: {exc.kind}: {exc.message}"))
                    self._dynamic.add(dotted)
                    self._names.setdefault(dotted, set())
                    continue
                if _is_dynamic_module(tree):
                    self._dynamic.add(dotted)
                self._names[dotted] = _bound_names(tree)

        # A package's SUBMODULES are importable names too: `from a.b import c` is valid whenever a/b/c.py
        # exists, even though b/__init__.py binds no name `c`. Without this every subpackage facade reads
        # as thousands of undefined names. Parents are registered on the way, which also covers IMPLICIT
        # NAMESPACE packages -- a directory with no __init__.py is importable since 3.3, and treating one
        # as absent reports every module under it as missing.
        for dotted in list(self._names):
            parts = dotted.split(".")
            for depth in range(len(parts) - 1, 0, -1):
                parent = ".".join(parts[:depth])
                self._names.setdefault(parent, set()).add(parts[depth])

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
        """True when the module builds names at runtime (or could not be parsed), so its full surface is unknown."""
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
    """Names a SINGLE node binds in the scope it runs in. Shared by the top level and by conditional bodies.

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
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return _assigned_names(node.target)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return _assigned_names(node.target)
    if isinstance(node, ast.withitem) and node.optional_vars is not None:
        return _assigned_names(node.optional_vars)
    if isinstance(node, ast.ExceptHandler) and node.name:
        return {node.name}
    if type(node).__name__ == "TypeAlias":  # `type X = ...`, Python 3.12+
        return _assigned_names(node.name)  # type: ignore[attr-defined]
    if type(node).__name__ in ("MatchAs", "MatchStar") and getattr(node, "name", None):
        return {node.name}  # type: ignore[attr-defined]
    if type(node).__name__ == "MatchMapping" and getattr(node, "rest", None):
        return {node.rest}  # type: ignore[attr-defined]
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".")[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        # A star import republishes an unknown set; mark the module unresolvable rather than reporting
        # every consumer of a name it may well provide.
        return {"*" if alias.name == "*" else (alias.asname or alias.name) for alias in node.names}
    return set()


def _bound_names(tree: ast.Module) -> set[str]:
    """Every top-level name a module binds: module statements, version guards and import fallbacks, ``with``/``for``/
    walrus/``match`` targets and ``type`` aliases at import time, and names a function declares ``global``. Locals of
    a function or class body defined under a guard are NOT module names."""
    out: set[str] = set()
    for node in _module_level_nodes(tree.body):
        out |= _names_bound_by(node)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            out.update(node.names)
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


def _under_prefix(target: str, prefixes: "tuple[str, ...]") -> bool:
    """*target* is one of *prefixes* or a module below one: ``pkg`` covers ``pkg.a``, never ``pkg_other``."""
    return any(target == p or target.startswith(p.rstrip(".") + ".") for p in prefixes)


def _judge_import(
    path: Path,
    tree: ast.Module,
    node: ast.ImportFrom,
    index: "ModuleIndex",
    importing: str,
    prefixes: tuple,
    guarded: "set[int] | None" = None,
) -> str | None:
    """One `from X import Y`, judged. Returns a problem line or None.

    ``guarded`` is this file's ``_guarded_import_ids`` when the caller computed it once for the whole file; without
    it the guard shape is re-derived here, which walks the tree again per import.
    """
    target = _resolve_relative(importing, node, is_package=index.is_package(importing))
    if not target or not _under_prefix(target, prefixes):
        return None
    if id(node) in guarded if guarded is not None else _absence_is_expected(tree, node):
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
    return f"{path.as_posix()}:{node.lineno}: '{target}' does not define " + ", ".join(f"'{m}'" for m in missing) + f" ({scope})"


def find_unresolved_from_imports(
    scan_roots: Sequence[Path],
    index: ModuleIndex,
    *,
    resolvable_prefixes: Iterable[str],
) -> list[str]:
    """Every `from X import Y` under ``scan_roots`` where X is known and does not bind Y.

    Only imports whose target is one of ``resolvable_prefixes`` or below one (dotted boundary) are judged -- a
    third-party target is not in the index and its absence proves nothing. A file under the scan roots or in the
    index that cannot be read or parsed is reported as ``<path>:<line>: unparsable: ...``: its imports, or its
    names, are unknown.
    """
    prefixes = tuple(resolvable_prefixes)
    problems: list[str] = []
    seen_unparsed: set[str] = set()
    for root in scan_roots:
        for path in iter_files(Path(root), ("*.py",), exclude=DEFAULT_EXCLUDE):
            importing = index._dotted(path) or ""
            try:
                _, tree = parse_source(path)
            except SourceError as exc:
                seen_unparsed.add(str(path.resolve()))
                problems.append(f"{path.as_posix()}:{exc.line or 1}: {exc.kind}: {exc.message}")
                continue
            imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            if imports:
                guarded = _guarded_import_ids(tree)
                for node in imports:
                    problem = _judge_import(path, tree, node, index, importing, prefixes, guarded)
                    if problem:
                        problems.append(problem)
    for path, detail in index.unparsed:
        if str(path.resolve()) not in seen_unparsed:
            problems.append(f"{path.as_posix()}:{detail}")
    return problems


def _names_in(node: "ast.AST | None") -> set[str]:
    """Exception names an ``except`` type or a ``raises``/``suppress`` argument mentions (tuples included)."""
    if node is None:
        return set()
    return {n.id if isinstance(n, ast.Name) else n.attr for n in ast.walk(node) if isinstance(n, (ast.Name, ast.Attribute))}


def _handler_expects_import_failure(handler: ast.ExceptHandler) -> bool:
    return handler.type is None or bool(_names_in(handler.type) & (_IMPORT_ERRORS | _BROAD_ERRORS))


def _with_expects_import_failure(node: "ast.With | ast.AsyncWith") -> bool:
    """``with pytest.raises(ImportError)`` (a tuple too) asserts the failure; ``with suppress(ImportError)`` (or a broad
    exception) tolerates it."""
    for item in node.items:
        call = item.context_expr
        if not isinstance(call, ast.Call):
            continue
        name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""
        mentioned = set().union(*(_names_in(a) for a in call.args)) if call.args else set()
        if name == "raises" and mentioned & _IMPORT_ERRORS:
            return True
        if name == "suppress" and mentioned & (_IMPORT_ERRORS | _BROAD_ERRORS):
            return True
    return False


def _guard_bodies(node: ast.AST) -> "list[ast.stmt] | None":
    if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
        if any(_handler_expects_import_failure(h) for h in node.handlers):  # type: ignore[attr-defined]
            return node.body  # type: ignore[attr-defined, no-any-return]
    elif isinstance(node, (ast.With, ast.AsyncWith)) and _with_expects_import_failure(node):
        return node.body
    return None


def _guarded_import_ids(tree: ast.Module) -> "set[int]":
    """``id()`` of every ImportFrom whose failure the surrounding code handles or asserts, from ONE walk of *tree*.

    :func:`_absence_is_expected` answers the same question for a single import by walking the whole tree, so asking
    it per import is quadratic in the file (measured: 170 s of a 235 s scan of mlframe's src).
    """
    guarded: "set[int]" = set()
    for node in ast.walk(tree):
        bodies = _guard_bodies(node)
        if bodies is not None:
            guarded.update(id(inner) for stmt in bodies for inner in ast.walk(stmt) if isinstance(inner, ast.ImportFrom))
    return guarded


def _absence_is_expected(tree: ast.Module, target: ast.ImportFrom) -> bool:
    """True when the surrounding code already handles, or asserts, that this import fails.

    Legitimate shapes that a naive scan reports as defects:

    * ``try: from x import y`` with an ``ImportError``/``ModuleNotFoundError``/``Exception``/``BaseException`` handler
      (a tuple of them too) -- an optional dependency or an optional frozen baseline snapshot, where absence is the
      documented fallback path. ``with contextlib.suppress(ImportError)`` is the same shape.
    * ``with pytest.raises(ImportError)`` (or a tuple including it) -- a test whose whole point is that the name is
      NOT importable. Reporting that one would be actively wrong: the finding IS the contract.
    """
    for node in ast.walk(tree):
        bodies = _guard_bodies(node)
        if bodies is not None and any(inner is target for stmt in bodies for inner in ast.walk(stmt)):
            return True
    return False


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
    """Fail on any `from X import Y` naming something X does not define, and on a file that cannot be parsed.

    ``allowlist`` entries are matched as substrings of the reported line, for the rare genuinely-dynamic
    target this module's own heuristics cannot see.
    """
    import pytest

    index = ModuleIndex(package_roots, package_roots)
    problems = [p for p in find_unresolved_from_imports(scan_roots, index, resolvable_prefixes=resolvable_prefixes) if not any(a in p for a in allowlist)]
    if problems:
        pytest.fail(
            f"{len(problems)} unresolved `from X import Y`:\n  "
            + "\n  ".join(problems)
            + "\n\nA module-scope one is an ImportError at COLLECTION -- with pytest-split every shard collects "
            "the whole tree, so one missing name fails all of them. One inside a function waits for that branch."
        )
