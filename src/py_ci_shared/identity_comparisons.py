"""Shared check: a string constant is compared by value, not by identity.

``x is SOME_SQL`` holds only while CPython keeps the two strings as one object: it depends on
interning, and on every caller passing the defining module's own object rather than an equal copy
(built, reloaded, read from a config). production_scrapers decided insert-versus-upsert that way
until 2026-09-12 and needed an import-time guard, and a load-bearing SQL comment, to keep the two
constants from ever interning into one; an equal copy of the insert statement silently lost the claim
guard. ruff's F632 catches ``is`` against a LITERAL, not against a name bound to one.

The rule: an ``is`` / ``is not`` where either side is a name (or attribute) bound at module level, in
the module it comes from (or at class level), to a string or bytes value -- a literal, an f-string, or a ``+``/``%`` of those.
Sentinel objects (``_MISSING = object()``), ``None`` and booleans are untouched, and so is an ``is`` whose other
side is ``None``/``True``/``False``/``...``, a member of an ``Enum`` class (its class-level values are members, not
strings), or a name imported from a module outside the scan (``p.kind is inspect.Parameter.VAR_KEYWORD``).
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional

from ._core import ImportAliases, ParsedFile, ScanResult, relative_posix, scan_python
from ._core.node_index import walk as _fast_walk

_TRY_TYPES: tuple[type, ...] = (ast.Try,) + ((getattr(ast, "TryStar"),) if hasattr(ast, "TryStar") else ())


def _stringish(node: ast.AST, known: "set[str] | frozenset[str]" = frozenset()) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes))
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Name):
        return node.id in known
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _stringish(node.left, known) or _stringish(node.right, known)
    return False


def _parse(path: Path) -> "ast.Module | None":
    """Kept for callers; parsing goes through ``_core`` (BOM-safe, cached). None for a file that cannot be parsed."""
    scan = scan_python([Path(path)])
    return scan.files[0].tree if scan.files else None


def _scope_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements that bind in this scope: *body* and the bodies of its ``if``/``try``/``with``/loops, not nested defs."""
    stack = list(body)
    while stack:
        node = stack.pop(0)
        yield node
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
        elif isinstance(node, _TRY_TYPES):
            stack.extend(getattr(node, "body", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))


def _bound_strings(body: list[ast.stmt]) -> set[str]:
    """Names bound in this scope to a string/bytes value: ``X = "a"``, ``X: str = f"..."``, ``A, B = "a", "b"``, and
    ``Y = X + "suffix"`` where ``X`` is one of them."""
    names: set[str] = set()
    for node in _scope_statements(body):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names |= _pairs(target, node.value, names)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            names |= _pairs(node.target, node.value, names)
    return names


def _pairs(target: ast.AST, value: ast.AST, known: set[str]) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id} if _stringish(value, known) else set()
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
        out: set[str] = set()
        for t, v in zip(target.elts, value.elts):
            out |= _pairs(t, v, known)
        return out
    return set()


_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})


def _is_enum_class(node: ast.ClassDef) -> bool:
    """A class deriving from an ``enum`` base (by its last dotted part): its class-level assignments are members."""
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else base.id if isinstance(base, ast.Name) else ""
        if name in _ENUM_BASES:
            return True
    return False


class _ModuleConstants:
    """One module's string constants: module-level names, and class-level ones per class (not per Enum class)."""

    def __init__(self, tree: ast.Module) -> None:
        self.module = _bound_strings(tree.body)
        self.classes: dict[str, set[str]] = {}
        for node in _fast_walk(tree):
            if isinstance(node, ast.ClassDef) and not _is_enum_class(node):
                self.classes.setdefault(node.name, set()).update(_bound_strings(node.body))

    @property
    def class_names(self) -> set[str]:
        return set().union(*self.classes.values()) if self.classes else set()


def string_constant_names(files: Iterable[Path]) -> set[str]:
    """Names bound at module level (including under a module-level ``if``/``try``, and tuple targets) or at class level
    to a string or bytes value in any of *files*."""
    names: set[str] = set()
    for parsed in scan_python([Path(p) for p in files]):
        consts = _ModuleConstants(parsed.tree)
        names |= consts.module | consts.class_names
    return names


def _name(node: ast.AST) -> "str | None":
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _module_names(rel: str) -> list[str]:
    """Every dotted name a file might be imported as: ``src/pkg/mod.py`` -> ``src.pkg.mod``, ``pkg.mod``, ``mod``."""
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return [".".join(parts[i:]) for i in range(len(parts)) if parts[i:]]


def _common_root(paths: list[Path]) -> Optional[Path]:
    if not paths:
        return None
    try:
        return Path(os.path.commonpath([str(p.resolve().parent) for p in paths]))
    except ValueError:
        return None


class _Index:
    """String constants of every scanned module, reachable by the dotted names the module can be imported as."""

    def __init__(self, parsed: list[ParsedFile], base: Optional[Path]) -> None:
        self.by_file: dict[Path, _ModuleConstants] = {}
        self.by_module: dict[str, _ModuleConstants] = {}
        for p in parsed:
            consts = _ModuleConstants(p.tree)
            self.by_file[p.path] = consts
            for name in _module_names(relative_posix(p.path.resolve(), base.resolve()) if base is not None else p.path.name):
                self.by_module.setdefault(name, consts)
        self.all_class_names: set[str] = set().union(*(c.class_names for c in self.by_file.values())) if self.by_file else set()

    def is_string_constant(self, node: ast.AST, here: _ModuleConstants, aliases: ImportAliases) -> bool:
        if isinstance(node, ast.Name):
            if node.id in here.module or node.id in here.class_names:
                return True
            return self._qualified(aliases.qualified_name(node))
        if isinstance(node, ast.Attribute):
            if self._qualified(aliases.qualified_name(node)):
                return True
            # `self.SQL` / `Cls.SQL` / `obj.SQL`: an attribute read through an instance cannot be resolved statically, so
            # it is matched against the CLASS constants of the scanned modules (not their module-level names).
            return node.attr in here.class_names or node.attr in self.all_class_names
        return False

    def is_external(self, node: ast.AST, aliases: ImportAliases) -> bool:
        """A dotted read rooted at an import of a module outside the scan (``inspect.Parameter.VAR_KEYWORD``)."""
        head: ast.AST = node
        while isinstance(head, ast.Attribute):
            head = head.value
        if not (isinstance(head, ast.Name) and aliases.is_imported(head.id)):
            return False
        qualified = aliases.qualified_name(node) or ""
        parts = qualified.split(".")
        return not any(".".join(parts[:i]) in self.by_module for i in range(1, len(parts) + 1))

    def _qualified(self, qualified: Optional[str]) -> bool:
        if not qualified or "." not in qualified:
            return False
        module, _, name = qualified.rpartition(".")
        consts = self.by_module.get(module)
        return consts is not None and name in consts.module


def _find(scan: ScanResult, base: Optional[Path], names: "set[str] | None", root: "Path | None") -> list[str]:
    index = _Index(scan.files, base)
    problems: list[str] = []
    for parsed in scan:
        here = index.by_file[parsed.path]
        aliases = ImportAliases.from_tree(parsed.tree)
        for node in _fast_walk(parsed.tree):
            if not isinstance(node, ast.Compare) or not any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
                continue
            sides = (node.left, *node.comparators)
            if any(isinstance(side, ast.Constant) and (side.value is None or side.value is Ellipsis or isinstance(side.value, bool)) for side in sides):
                continue
            if names is not None:
                hit = any(_name(side) in names for side in sides)
            else:
                hit = any(index.is_string_constant(side, here, aliases) for side in sides) and not any(index.is_external(side, aliases) for side in sides)
            if hit:
                rel = relative_posix(parsed.path, root) if root else parsed.path.as_posix()
                problems.append(f"{rel}:{node.lineno}: `{ast.unparse(node)[:100]}` compares a string constant by identity -- use ==")
    return problems


def find_identity_comparisons(files: Iterable[Path], *, root: "Path | None" = None, names: "set[str] | None" = None) -> list[str]:
    """``path:line: ...`` for each ``is``/``is not`` against a string constant.

    A name counts when it resolves to a string constant of the module it comes from: defined in the same file, imported
    (``from queries import SQL``, ``queries.SQL``) from a scanned module that defines it as a string, or read as an
    attribute that is a class-level string constant. A same-named sentinel in another module (``MISSING = object()``
    here, ``MISSING = "missing"`` there) is not a string constant. With *names*, those names count wherever they appear.
    Files that cannot be parsed are not in this list; :func:`assert_no_identity_comparisons` fails on them.
    """
    paths = [Path(p) for p in files]
    base = root if root is not None else _common_root(paths)
    return _find(scan_python(paths), base, names, root)


def assert_no_identity_comparisons(files: Iterable[Path], *, root: "Path | None" = None, min_files: int = 1) -> None:
    """Fail on an identity comparison against a string constant, on a file that cannot be parsed, and when fewer than
    *min_files* files parsed."""
    import pytest

    paths = [Path(p) for p in files]
    scan = scan_python(paths, min_files=min_files)
    if scan.parsed_count < min_files:
        pytest.fail(f"only {scan.parsed_count} file(s) scanned; expected at least {min_files} -- this would check nothing")
    problems = [f"{relative_posix(u.path, root) if root else u.path.as_posix()}:{u.line}: {u.kind}: {u.message}" for u in scan.unparsed]
    problems += _find(scan, root if root is not None else _common_root(paths), None, root)
    if problems:
        pytest.fail(f"{len(problems)} identity comparison(s) against a string constant, or unparsable file(s):\n  " + "\n  ".join(problems))
