"""Shared check: no module is reloaded or dropped from ``sys.modules`` without a restore in the same scope.

``importlib.reload(m)``, ``del sys.modules[name]`` and ``sys.modules.pop(name)`` rebind a module's top-level
names. Every file that did ``from m import Cls`` at load keeps the OLD ``Cls``; a lazy import inside a
function gets the NEW one. The two disagree on class identity, which breaks ``isinstance``, class-attribute
caches and idempotent-install markers in unrelated later tests, and shows up as an intermittent failure
far from its cause (mlframe's 2026-05-22 fit-cache incident).

Three repos carried a copy: mlframe and glossum scanned tests with an AST, scope-aware check; autopsia
banned the primitives outright in production code. Both halves are here:

* ``find_unpaired_reloads`` -- in tests, each primitive needs a restore reachable from its OWN function or
  fixture: a ``sys.modules[...] = saved`` / ``sys.modules.update(...)``, a module ``__dict__`` swap,
  ``addfinalizer(...)``, a ``finally`` that reloads again after the patch is undone, a requested fixture
  that restores, an autouse fixture that restores, or ``subprocess.run`` (process isolation). Whole-file
  matching was the earlier heuristic and passed a file whose restore sat in an unrelated function.
* ``find_reloads_in_code`` -- outside tests there is no fixture to restore anything, so any use is flagged.

A module that owns a mutable singleton (cache, registry, lock) is not repaired by a ``__dict__`` restore,
because importers captured the old object; name such modules in ``singleton_modules`` and an unpaired
site in a file that names one is reported with that warning, since only a subprocess repairs it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

_PRIMITIVE_TOKENS = ("importlib.reload", "del sys.modules", "sys.modules.pop")


@dataclass(frozen=True)
class ReloadSite:
    path: str
    line: int
    primitive: str
    singleton: bool = False

    def __str__(self) -> str:
        return f"{self.path}:{self.line} ({self.primitive})" + ("  [reloads a singleton-owning module: needs a subprocess]" if self.singleton else "")


def _is_sys_modules(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys"


def reload_primitive(node: ast.AST) -> "str | None":
    """The primitive *node* is, if any."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "reload" and isinstance(func.value, ast.Name) and func.value.id == "importlib":
            return "importlib.reload"
        if isinstance(func, ast.Attribute) and func.attr == "pop" and _is_sys_modules(func.value):
            return "sys.modules.pop"
    if isinstance(node, ast.Delete) and any(isinstance(t, ast.Subscript) and _is_sys_modules(t.value) for t in node.targets):
        return "del sys.modules"
    return None


def _has_restore(scope: ast.AST) -> bool:
    for sub in ast.walk(scope):
        if isinstance(sub, ast.Assign) and any(isinstance(t, ast.Subscript) and _is_sys_modules(t.value) for t in sub.targets):
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            f = sub.func
            if f.attr == "update" and _is_sys_modules(f.value):
                return True
            if f.attr in ("update", "clear") and isinstance(f.value, ast.Attribute) and f.value.attr == "__dict__":
                return True
            if f.attr == "addfinalizer":
                return True
            if f.attr == "run" and isinstance(f.value, ast.Name) and f.value.id == "subprocess":
                return True
        if isinstance(sub, ast.Try) and any(reload_primitive(n) == "importlib.reload" for fin in sub.finalbody for n in ast.walk(fin)):
            return True
    return False


def _functions(tree: ast.AST) -> Iterator["ast.FunctionDef | ast.AsyncFunctionDef"]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _fixture_flags(func: "ast.FunctionDef | ast.AsyncFunctionDef") -> "tuple[bool, bool]":
    is_fixture = is_autouse = False
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (isinstance(target, ast.Attribute) and target.attr == "fixture") or (isinstance(target, ast.Name) and target.id == "fixture"):
            is_fixture = True
        if isinstance(dec, ast.Call) and any(kw.arg == "autouse" and isinstance(kw.value, ast.Constant) and kw.value.value for kw in dec.keywords):
            is_autouse = True
    return is_fixture, is_autouse


def _enclosing(tree: ast.AST, target: ast.AST) -> "ast.FunctionDef | ast.AsyncFunctionDef | None":
    line = getattr(target, "lineno", 0)
    best = None
    for func in _functions(tree):
        if func.lineno <= line <= (func.end_lineno or func.lineno) and (best is None or func.lineno > best.lineno):
            best = func
    return best


def _parse(path: Path) -> "tuple[str, ast.AST] | None":
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not any(tok in src for tok in _PRIMITIVE_TOKENS):
        return None
    try:
        return src, ast.parse(src)
    except SyntaxError:
        return None


def find_unpaired_reloads(tests_dir: Path, *, stub_only_files: Iterable[str] = (), singleton_modules: Iterable[str] = ()) -> list[ReloadSite]:
    """Reload sites under *tests_dir* with no restore reachable from their own scope (paths relative to it).

    ``stub_only_files`` exempts files that reload only stub modules of their own; ``singleton_modules`` marks
    sites in a file that names one of those modules.
    """
    exempt = set(stub_only_files)
    singletons = tuple(singleton_modules)
    out: list[ReloadSite] = []
    for py in sorted(tests_dir.rglob("*.py")):
        rel = py.relative_to(tests_dir).as_posix()
        if "__pycache__" in py.parts or rel in exempt:
            continue
        parsed = _parse(py)
        if parsed is None:
            continue
        src, tree = parsed
        restoring_fixtures: set[str] = set()
        autouse_restore = False
        for func in _functions(tree):
            is_fixture, is_autouse = _fixture_flags(func)
            if is_fixture and _has_restore(func):
                restoring_fixtures.add(func.name)
                autouse_restore = autouse_restore or is_autouse
        singleton = any(mod in src for mod in singletons)
        for node in ast.walk(tree):
            prim = reload_primitive(node)
            if prim is None:
                continue
            scope = _enclosing(tree, node)
            safe = autouse_restore or (scope is not None and (_has_restore(scope) or bool({a.arg for a in scope.args.args} & restoring_fixtures)))
            if not safe:
                out.append(ReloadSite(rel, getattr(node, "lineno", 0), prim, singleton))
    return out


def find_reloads_in_code(roots: Iterable[Path], repo_root: Path, *, allowed: Iterable[tuple[str, int]] = ()) -> list[ReloadSite]:
    """Every primitive under production *roots* (no fixture can restore there), minus an explicit allowlist."""
    ok = set(allowed)
    out: list[ReloadSite] = []
    for root in roots:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            parsed = _parse(py)
            if parsed is None:
                continue
            rel = py.relative_to(repo_root).as_posix()
            for node in ast.walk(parsed[1]):
                prim = reload_primitive(node)
                if prim is not None and (rel, getattr(node, "lineno", 0)) not in ok:
                    out.append(ReloadSite(rel, getattr(node, "lineno", 0), prim))
    return out


def assert_no_unpaired_reloads(tests_dir: Path, *, stub_only_files: Iterable[str] = (), singleton_modules: Iterable[str] = ()) -> None:
    import pytest

    exempt = list(stub_only_files)
    stale = [rel for rel in exempt if not (tests_dir / rel).exists()]
    if stale:
        pytest.fail(f"stub-only allowlist entries that no longer exist (an exemption for nothing): {stale}")
    sites = find_unpaired_reloads(tests_dir, stub_only_files=exempt, singleton_modules=singleton_modules)
    if sites:
        pytest.fail(
            f"{len(sites)} reload site(s) with no snapshot/restore in their own function or fixture. Restore in the same "
            "function (finally / addfinalizer), a requested fixture or an autouse one, or isolate it in a subprocess:\n  "
            + "\n  ".join(str(s) for s in sites[:30])
        )
