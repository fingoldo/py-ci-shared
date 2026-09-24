"""Shared check: no module is reloaded or dropped from ``sys.modules`` without a restore in the same scope.

``importlib.reload(m)``, ``del sys.modules[name]`` and ``sys.modules.pop(name)`` rebind a module's top-level
names. Every file that did ``from m import Cls`` at load keeps the OLD ``Cls``; a lazy import inside a
function gets the NEW one. The two disagree on class identity, which breaks ``isinstance``, class-attribute
caches and idempotent-install markers in unrelated later tests, and shows up as an intermittent failure
far from its cause (mlframe's 2026-05-22 fit-cache incident).

Three repos carried a copy: mlframe and glossum scanned tests with an AST, scope-aware check; autopsia
banned the primitives outright in production code. Both halves are here:

* ``find_unpaired_reloads`` -- in tests, each primitive needs a restore reachable from its OWN function or
  fixture (or an enclosing function): a write-back of a SNAPSHOT (``sys.modules[...] = saved`` /
  ``sys.modules.update(saved)`` / ``mod.__dict__.update(saved)``, where ``saved`` was read from ``sys.modules`` or
  a module ``__dict__``), an ``addfinalizer(...)`` whose callable does one of those or reloads, a ``finally``
  that reloads again after the patch is undone, ``patch.dict(sys.modules)``, a requested restoring fixture (from
  the file or a ``conftest.py``, by argument or ``usefixtures``), or an autouse one whose scope covers the site.
  Installing a fake (``sys.modules["m"] = object()``) is not a restore, and neither is a ``subprocess.run`` next
  to an in-process reload. Whole-file matching was the earlier heuristic and passed a file whose restore sat in
  an unrelated function.
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
from typing import Optional, Union

from ._core import DEFAULT_EXCLUDE, ImportAliases, relative_posix, scan_python

_RELOADERS = frozenset({"importlib.reload", "imp.reload"})
_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]


@dataclass(frozen=True)
class ReloadSite:
    path: str
    line: int
    primitive: str
    singleton: bool = False
    text: str = ""

    def __str__(self) -> str:
        return f"{self.path}:{self.line} ({self.primitive})" + ("  [reloads a singleton-owning module: needs a subprocess]" if self.singleton else "")


def _is_sys_modules(node: ast.AST, aliases: Optional[ImportAliases] = None) -> bool:
    if aliases is not None:
        return aliases.qualified_name(node) == "sys.modules"
    return isinstance(node, ast.Attribute) and node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys"


def reload_primitive(node: ast.AST, aliases: Optional[ImportAliases] = None) -> "str | None":
    """The primitive *node* is, if any. With *aliases* (from the node's module), ``from importlib import reload``,
    ``import importlib as il`` and ``import sys as _sys`` are resolved; without, only the literal spellings match."""
    if isinstance(node, ast.Call):
        func = node.func
        if aliases is not None:
            if aliases.qualified_name(func) in _RELOADERS:
                return "importlib.reload"
        elif isinstance(func, ast.Attribute) and func.attr == "reload" and isinstance(func.value, ast.Name) and func.value.id == "importlib":
            return "importlib.reload"
        if isinstance(func, ast.Attribute) and func.attr == "pop" and _is_sys_modules(func.value, aliases):
            return "sys.modules.pop"
    if isinstance(node, ast.Delete) and any(isinstance(t, ast.Subscript) and _is_sys_modules(t.value, aliases) for t in node.targets):
        return "del sys.modules"
    return None


# ---------------------------------------------------------------------------------------------------------------------
# what counts as a restore


def _mentions_snapshot_source(node: ast.AST, aliases: ImportAliases) -> bool:
    """Does *node* read ``sys.modules`` or a module ``__dict__`` / ``vars(module)``? Such a value is a snapshot."""
    for sub in ast.walk(node):
        if _is_sys_modules(sub, aliases):
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "__dict__":
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "vars" and sub.args:
            return True
    return False


def _bound_names(target: ast.AST) -> Iterator[str]:
    for sub in ast.walk(target):
        if isinstance(sub, ast.Name):
            yield sub.id


def _snapshots(scope: ast.AST, aliases: ImportAliases) -> set[str]:
    """Names bound in *scope* from a ``sys.modules`` or module-``__dict__`` read."""
    out: set[str] = set()
    for sub in ast.walk(scope):
        if isinstance(sub, ast.Assign) and _mentions_snapshot_source(sub.value, aliases):
            for target in sub.targets:
                out.update(_bound_names(target))
        elif isinstance(sub, (ast.AnnAssign, ast.NamedExpr)) and sub.value is not None and _mentions_snapshot_source(sub.value, aliases):
            out.update(_bound_names(sub.target))
    return out


def _uses(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(sub, ast.Name) and sub.id in names for sub in ast.walk(node))


class _Restores:
    """Decides whether a scope undoes a reload: snapshot-sourced writes back, finalizers that do, finally reloads."""

    def __init__(self, tree: ast.AST, aliases: ImportAliases) -> None:
        self.aliases = aliases
        self.defs: dict[str, list[_FuncDef]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.defs.setdefault(node.name, []).append(node)

    def has_restore(self, scope: ast.AST, snapshots: Optional[set[str]] = None, *, in_finalizer: bool = False, depth: int = 0) -> bool:
        snaps = set(snapshots or ()) | _snapshots(scope, self.aliases)
        for sub in ast.walk(scope):
            if in_finalizer and reload_primitive(sub, self.aliases) == "importlib.reload":
                return True
            if isinstance(sub, ast.Assign) and any(isinstance(t, ast.Subscript) and _is_sys_modules(t.value, self.aliases) for t in sub.targets):
                if _uses(sub.value, snaps):
                    return True
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                f = sub.func
                if f.attr == "update" and (_is_sys_modules(f.value, self.aliases) or (isinstance(f.value, ast.Attribute) and f.value.attr == "__dict__")):
                    if any(_uses(arg, snaps) for arg in sub.args):
                        return True
                if f.attr == "addfinalizer" and sub.args and depth < 3 and self._finalizer_restores(sub.args[0], snaps, depth):
                    return True
                if f.attr == "dict" and self.aliases.qualified_name(f) in ("unittest.mock.patch.dict", "mock.patch.dict"):
                    if sub.args and _is_sys_modules(sub.args[0], self.aliases):
                        return True
            if isinstance(sub, ast.Try) and any(reload_primitive(n, self.aliases) == "importlib.reload" for fin in sub.finalbody for n in ast.walk(fin)):
                return True
        return False

    def _finalizer_restores(self, arg: ast.AST, snaps: set[str], depth: int) -> bool:
        if isinstance(arg, ast.Lambda):
            return self.has_restore(arg.body, snaps, in_finalizer=True, depth=depth + 1)
        if isinstance(arg, ast.Name):
            return any(self.has_restore(d, snaps, in_finalizer=True, depth=depth + 1) for d in self.defs.get(arg.id, ()))
        if isinstance(arg, ast.Call):  # functools.partial(sys.modules.update, saved)
            return any(_uses(a, snaps) for a in arg.args) and any(
                isinstance(a, ast.Attribute) and a.attr == "update" and _is_sys_modules(a.value, self.aliases) for a in arg.args
            )
        return False


# ---------------------------------------------------------------------------------------------------------------------
# fixtures


def _fixture_flags(func: _FuncDef, aliases: Optional[ImportAliases] = None) -> "tuple[bool, bool]":
    is_fixture = is_autouse = False
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        qualified = aliases.qualified_name(target) if aliases is not None else None
        if qualified in ("pytest.fixture", "pytest_asyncio.fixture") or (
            (isinstance(target, ast.Attribute) and target.attr == "fixture") or (isinstance(target, ast.Name) and target.id == "fixture")
        ):
            is_fixture = True
        if isinstance(dec, ast.Call) and any(kw.arg == "autouse" and isinstance(kw.value, ast.Constant) and kw.value.value for kw in dec.keywords):
            is_autouse = True
    return is_fixture, is_autouse


def _usefixtures(decorators: "list[ast.expr]", aliases: ImportAliases) -> set[str]:
    out: set[str] = set()
    for dec in decorators:
        for sub in ast.walk(dec):
            if isinstance(sub, ast.Call) and (aliases.qualified_name(sub) or "").endswith("mark.usefixtures"):
                out.update(a.value for a in sub.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return out


@dataclass
class _FixtureScope:
    """Restoring fixtures visible from one file: by name (requestable) and autouse (applied without a request)."""

    restoring: set[str]
    autouse: bool


def _conftest_fixtures(tests_dir: Path, parsed_by_path: "dict[Path, tuple[ast.Module, ImportAliases]]") -> "dict[Path, _FixtureScope]":
    """Per conftest directory: its restoring fixture names, and whether one of them is autouse."""
    out: dict[Path, _FixtureScope] = {}
    for path, (tree, aliases) in parsed_by_path.items():
        if path.name != "conftest.py":
            continue
        restores = _Restores(tree, aliases)
        scope = _FixtureScope(set(), False)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_fixture, is_autouse = _fixture_flags(node, aliases)
                if is_fixture and restores.has_restore(node):
                    scope.restoring.add(node.name)
                    scope.autouse = scope.autouse or is_autouse
        out[path.parent.resolve()] = scope
    return out


def _parents(tree: ast.AST) -> "dict[ast.AST, ast.AST]":
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _chain(node: ast.AST, parents: "dict[ast.AST, ast.AST]") -> "list[ast.AST]":
    """Enclosing functions and classes of *node*, innermost first."""
    out: list[ast.AST] = []
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(cur)
        cur = parents.get(cur)
    return out


def _restoring_fixtures_in(body: "list[ast.stmt]", restores: _Restores, aliases: ImportAliases) -> "tuple[set[str], bool]":
    names: set[str] = set()
    autouse = False
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_fixture, is_autouse = _fixture_flags(node, aliases)
            if is_fixture and restores.has_restore(node):
                names.add(node.name)
                autouse = autouse or is_autouse
    return names, autouse


def _unparsed_site(problem: object) -> ReloadSite:
    return ReloadSite(getattr(problem, "rel"), getattr(problem, "line"), f"{getattr(problem, 'kind')}: {getattr(problem, 'message')}")


def find_unpaired_reloads(
    tests_dir: Path, *, stub_only_files: Iterable[str] = (), singleton_modules: Iterable[str] = (), min_files: int = 0
) -> list[ReloadSite]:
    """Reload sites under *tests_dir* with no restore reachable from their own scope (paths relative to it).

    A site is paired when an enclosing function (any level) restores from a snapshot, requests a restoring fixture
    (argument or ``usefixtures``, defined in the file or a ``conftest.py`` above it), or runs under a restoring
    autouse fixture whose scope covers it (module, its class, or a conftest directory). ``stub_only_files`` exempts
    files that reload only stub modules of their own; ``singleton_modules`` marks sites in a file that names one of
    those modules. A file that cannot be parsed is returned as a site whose primitive says so. A missing
    *tests_dir* raises :class:`py_ci_shared._core.CorpusError`.
    """
    exempt = set(stub_only_files)
    singletons = tuple(singleton_modules)
    scan = scan_python(tests_dir, min_files=min_files, exclude=DEFAULT_EXCLUDE)
    if min_files:
        scan.check_floor()
    parsed_by_path = {f.path: (f.tree, ImportAliases.from_tree(f.tree)) for f in scan}
    conftests = _conftest_fixtures(tests_dir, parsed_by_path)
    root = tests_dir.resolve()
    out: list[ReloadSite] = [_unparsed_site(p) for p in scan.unparsed if p.rel not in exempt]
    for parsed in scan:
        rel = parsed.rel
        if rel in exempt:
            continue
        tree, aliases = parsed_by_path[parsed.path]
        sites = [(node, prim) for node in ast.walk(tree) for prim in [reload_primitive(node, aliases)] if prim is not None]
        if not sites:
            continue
        restores = _Restores(tree, aliases)
        visible: set[str] = set()
        dir_autouse = False
        here = parsed.path.resolve().parent
        while True:
            scope = conftests.get(here)
            if scope is not None:
                visible |= scope.restoring
                dir_autouse = dir_autouse or scope.autouse
            if here == root or here.parent == here:
                break
            here = here.parent
        module_names, module_autouse = _restoring_fixtures_in(tree.body, restores, aliases)
        visible |= module_names
        module_uses: set[str] = set()
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in statement.targets):
                module_uses |= _usefixtures([statement.value], aliases)
        parents = _parents(tree)
        singleton = any(mod in parsed.source for mod in singletons)
        for node, prim in sites:
            chain = _chain(node, parents)
            functions = [c for c in chain if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))]
            classes = [c for c in chain if isinstance(c, ast.ClassDef)]
            requested = set(module_uses)
            class_autouse = False
            class_visible: set[str] = set()
            for cls in classes:
                requested |= _usefixtures(cls.decorator_list, aliases)
                names, autouse = _restoring_fixtures_in(cls.body, restores, aliases)
                class_visible |= names
                class_autouse = class_autouse or autouse
            for func in functions:
                requested |= {a.arg for a in func.args.args + func.args.kwonlyargs}
                requested |= _usefixtures(func.decorator_list, aliases)
            snapshots: set[str] = set()
            for func in reversed(functions):
                snapshots |= _snapshots(func, aliases)
            safe = (
                dir_autouse
                or module_autouse
                or class_autouse
                or bool(requested & (visible | class_visible))
                or any(restores.has_restore(func, snapshots) for func in functions)
            )
            if not safe:
                out.append(ReloadSite(rel, getattr(node, "lineno", 0), prim, singleton, _statement_text(parsed.source, node)))
    return out


def _statement_text(source: str, node: ast.AST) -> str:
    return " ".join((ast.get_source_segment(source, node) or ast.unparse(node)).split())


def find_reloads_in_code(roots: Iterable[Path], repo_root: Path, *, allowed: Iterable["tuple[str, Union[int, str]]"] = ()) -> list[ReloadSite]:
    """Every primitive under production *roots* (no fixture can restore there), minus an explicit allowlist.

    *allowed* entries are ``(repo-relative path, statement text)`` -- the primitive's source, whitespace-collapsed,
    which survives edits above it -- or the older ``(path, line)``. A missing root raises
    :class:`py_ci_shared._core.CorpusError`; an unparsable file is returned as a site whose primitive says so. Use
    :func:`assert_no_reloads_in_code` to also fail on allowlist entries that match nothing.
    """
    return _reloads_in_code(roots, repo_root, allowed=allowed)[0]


def _reloads_in_code(roots: Iterable[Path], repo_root: Path, *, allowed: Iterable["tuple[str, Union[int, str]]"] = ()) -> "tuple[list[ReloadSite], int]":
    """``(sites, parsed file count)`` for :func:`find_reloads_in_code`."""
    ok = set(allowed)
    out: list[ReloadSite] = []
    parsed_count = 0
    for root in roots:
        scan = scan_python(Path(root), min_files=0, root=repo_root, exclude=DEFAULT_EXCLUDE)
        out.extend(s for s in (_unparsed_site(p) for p in scan.unparsed) if (s.path, s.line) not in ok)
        parsed_count += scan.parsed_count
        for parsed in scan:
            aliases = ImportAliases.from_tree(parsed.tree)
            rel = relative_posix(parsed.path, repo_root)
            for node in ast.walk(parsed.tree):
                prim = reload_primitive(node, aliases)
                if prim is None:
                    continue
                line = getattr(node, "lineno", 0)
                text = _statement_text(parsed.source, node)
                if (rel, line) not in ok and (rel, text) not in ok:
                    out.append(ReloadSite(rel, line, prim, text=text))
    return out, parsed_count


def assert_no_reloads_in_code(roots: Iterable[Path], repo_root: Path, *, allowed: Iterable["tuple[str, Union[int, str]]"] = (), min_files: int = 1) -> None:
    """Fail on any primitive under *roots* the allowlist does not name, on an allowlist entry that names nothing, and on
    fewer than *min_files* parsed files (a scan that lost its subject)."""
    import pytest

    allowed = list(allowed)
    roots = list(roots)
    unexcused, parsed_count = _reloads_in_code(roots, repo_root, allowed=())
    present = {(s.path, s.line) for s in unexcused} | {(s.path, s.text) for s in unexcused}
    sites = [s for s in unexcused if (s.path, s.line) not in set(allowed) and (s.path, s.text) not in set(allowed)]
    stale = [entry for entry in allowed if tuple(entry) not in present]
    problems: list[str] = []
    if parsed_count < min_files:
        problems.append(f"only {parsed_count} file(s) parsed under {[str(r) for r in roots]}; expected at least {min_files}. The scan lost its subject.")
    if sites:
        problems.append(f"{len(sites)} module reload/unload site(s) in production code:\n  " + "\n  ".join(f"{s}  {s.text}" for s in sites))
    if stale:
        problems.append(f"{len(stale)} allowlist entr(ies) that match no site (remove them):\n  " + "\n  ".join(map(str, stale)))
    if problems:
        pytest.fail("\n".join(problems), pytrace=False)


def assert_no_unpaired_reloads(tests_dir: Path, *, stub_only_files: Iterable[str] = (), singleton_modules: Iterable[str] = (), min_files: int = 1) -> None:
    """Fail on an unpaired reload site, an unparsable test file, a stale exemption, or fewer than *min_files* parsed files."""
    import pytest

    exempt = list(stub_only_files)
    stale = [rel for rel in exempt if not (tests_dir / rel).exists()]
    if stale:
        pytest.fail(f"stub-only allowlist entries that no longer exist (an exemption for nothing): {stale}")
    sites = find_unpaired_reloads(tests_dir, stub_only_files=exempt, singleton_modules=singleton_modules, min_files=min_files)
    if sites:
        pytest.fail(
            f"{len(sites)} reload site(s) with no snapshot/restore in their own function or fixture. Restore in the same "
            "function (finally / addfinalizer), a requested fixture or an autouse one, or isolate it in a subprocess:\n  "
            + "\n  ".join(str(s) for s in sites[:30])
        )
