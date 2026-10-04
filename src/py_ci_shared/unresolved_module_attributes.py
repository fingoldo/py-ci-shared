"""Every attribute read on an imported first-party module (``m.NAME``) must name something that module defines.

``unresolved_imports`` judges ``from X import Y``. The sibling shape ``import X`` followed by ``X.Y`` is invisible to it: a
test did ``import job_details_queries`` and read ``job_details_queries.Q_JOB_PUB_BATCH2`` after another package deleted that
constant, and failed on master for a week. mypy reports it as ``attr-defined``, but those errors usually sit under a
tolerated-error ratchet, so nothing is red when a name disappears.

Module aliases come from ``import a.b``, ``import a.b as x`` and ``from pkg import submodule [as x]`` (when ``submodule`` is a
module file or package that ``pkg`` does not itself bind as a name), at module scope and inside functions. The target is
resolved to a source file under the scanned roots, the sibling directories and the extra ``resolve_roots`` (a monorepo reaches
sibling packages through ``sys.path`` edits, so bare module names are looked up in each of those directories), then PARSED,
never imported. A bare name found in several directories is defined when ANY candidate defines it.

Not judged, and recorded in the report's ``skipped`` list so "checked and clean" differs from "not checked": a target that
defines a module ``__getattr__``, builds names through ``globals()[...] =``/``exec``, star-imports something unresolvable, cannot
be parsed or is a namespace package; and an alias bound twice in one scope (``try: import m / except ImportError: m = None``).
Never reported: dunder attributes, a name tested with ``hasattr(alias, "NAME")`` in the alias's scope, a name assigned or
patched through the alias in the same file (``alias.NAME = ..``, ``setattr``/``monkeypatch.setattr``/``patch.object``) and a
read under ``try/except AttributeError`` or ``pytest.raises(AttributeError)``. ``getattr(alias, "NAME", default)`` is not an
attribute read. Only the first level of ``alias.NAME.other`` is checked.

Usage in a consumer's meta test::

    from py_ci_shared.unresolved_module_attributes import assert_no_unresolved_module_attributes

    def test_no_unresolved_module_attributes():
        assert_no_unresolved_module_attributes("realtime_applications", resolve_roots=["production_scrapers"])
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, SourceError, nodes_of, parse_source, scan_python
from ._core.gate_contract import check_scan
from .unresolved_imports import _bound_names, _is_dynamic_module, _literal_dunder_all, _module_level_nodes, _names_bound_by, _names_in

__all__ = [
    "RULE",
    "AttributeReport",
    "SkippedAlias",
    "assert_no_unresolved_module_attributes",
    "find_unresolved_module_attributes",
    "scan_module_attributes",
]

RULE = "unresolved-module-attributes"
PathLike = Union[str, "os.PathLike[str]"]

_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
#: ``setattr(m, "X", v)``, ``delattr(m, "X")``, ``monkeypatch.setattr(m, "X", v)``, ``patch.object(m, "X")`` define or remove X on purpose
_PATCH_CALLS = frozenset({"setattr", "delattr", "object"})
_SUFFIXES = (".py", ".pyd", ".so")


@dataclass(frozen=True)
class SkippedAlias:
    """An alias whose reads were NOT judged, with why."""

    path: str
    line: int
    alias: str
    module: str
    reason: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: '{self.alias}' ({self.module}) not checked: {self.reason}"


@dataclass
class AttributeReport:
    findings: list[Finding] = field(default_factory=list)
    skipped: list[SkippedAlias] = field(default_factory=list)
    #: attribute reads that were compared against a parsed module
    checked_reads: int = 0
    #: attribute reads on an alias whose module is not first-party (stdlib, third party): nothing to compare against
    external_reads: int = 0


@dataclass(frozen=True)
class _Candidate:
    path: Path
    kind: str  # "module", "package" or "namespace"


@dataclass
class _ModuleInfo:
    names: "Optional[set[str]]"
    skip_reason: str = ""
    #: the names the module's own code binds, without the submodule files a package directory adds
    bound: "set[str]" = field(default_factory=set)


@dataclass(frozen=True)
class _Binding:
    """What one binding of a name in one scope is. ``module`` is the dotted target (relative targets: ``level`` leading dots)."""

    module: str
    level: int = 0
    member: str = ""  # set for ``from module import member``: the alias is ``module.member`` when that is a submodule


def _own_nodes(scope: ast.AST) -> Iterator["tuple[ast.AST, frozenset[str]]"]:
    """Every node of one scope with the comprehension variables shadowing names at that node. A nested def/class/lambda is yielded
    but not entered, except for its decorators, defaults and bases, which run in this scope."""
    if isinstance(scope, ast.Lambda):
        stack: "list[tuple[ast.AST, frozenset[str]]]" = [(scope.body, frozenset())]
    else:
        stack = [(n, frozenset()) for n in getattr(scope, "body", [])]
    while stack:
        node, shadow = stack.pop()
        yield node, shadow
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend((n, shadow) for n in [*node.decorator_list, *node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)])
        elif isinstance(node, ast.ClassDef):
            stack.extend((n, shadow) for n in [*node.decorator_list, *node.bases, *(k.value for k in node.keywords)])
        elif isinstance(node, ast.Lambda):
            stack.extend((n, shadow) for n in node.args.defaults)
        elif isinstance(node, _COMPREHENSIONS):
            bound = frozenset(n.id for g in node.generators for n in ast.walk(g.target) if isinstance(n, ast.Name))
            inner = shadow | bound
            children = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            for gen in node.generators:
                children.extend([gen.iter, *gen.ifs])
            stack.extend((n, inner) for n in children)
        else:
            stack.extend((n, shadow) for n in ast.iter_child_nodes(node))


def _param_names(scope: ast.AST) -> set[str]:
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return set()
    args = scope.args
    names = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    names.update(a.arg for a in (args.vararg, args.kwarg) if a is not None)
    return names


@dataclass
class _Scope:
    node: ast.AST
    parent: "Optional[_Scope]"
    #: name -> every binding of it in this scope; ``None`` stands for a binding that is not a plain import
    bindings: "dict[str, list[Optional[_Binding]]]" = field(default_factory=dict)
    global_names: set[str] = field(default_factory=set)

    @property
    def is_class(self) -> bool:
        return isinstance(self.node, ast.ClassDef)


def _record_import(scope: _Scope, node: "Union[ast.Import, ast.ImportFrom]") -> None:
    """``import a.b`` binds ``a``, ``import a.b as x`` binds ``x`` to ``a.b``, ``from m import n [as x]`` binds ``x`` to ``m.n`` when that is a module."""
    for alias in node.names:
        if isinstance(node, ast.Import):
            target = alias.name if alias.asname else alias.name.split(".")[0]
            scope.bindings.setdefault(alias.asname or target, []).append(_Binding(target))
        elif alias.name != "*":
            scope.bindings.setdefault(alias.asname or alias.name, []).append(_Binding(node.module or "", node.level, alias.name))


def _build_scope(node: ast.AST, parent: "Optional[_Scope]") -> _Scope:
    scope = _Scope(node, parent)
    for name in _param_names(node):
        scope.bindings.setdefault(name, []).append(None)
    for sub, _shadow in _own_nodes(node):
        if isinstance(sub, (ast.Import, ast.ImportFrom)):
            _record_import(scope, sub)
        elif isinstance(sub, (ast.Global, ast.Nonlocal)):
            scope.global_names.update(sub.names)
        else:
            for name in _names_bound_by(sub):
                if name != "*":
                    scope.bindings.setdefault(name, []).append(None)
    return scope


def _scopes_of(tree: ast.Module) -> Iterator[_Scope]:
    """Every scope of the file, outermost first."""
    root = _build_scope(tree, None)
    todo = [root]
    while todo:
        scope = todo.pop(0)
        yield scope
        for sub, _shadow in _own_nodes(scope.node):
            if isinstance(sub, _SCOPE_TYPES):
                todo.append(_build_scope(sub, scope))


def _binder(scope: _Scope, name: str, shadow: "frozenset[str]") -> "Optional[_Scope]":
    """The scope whose binding of *name* a read in *scope* sees, or None when a comprehension variable shadows it or nothing binds it."""
    if name in shadow:
        return None
    current: "Optional[_Scope]" = scope
    first = True
    while current is not None:
        if name in current.global_names:
            while current.parent is not None:
                current = current.parent
            return current if name in current.bindings else None
        if (first or not current.is_class) and name in current.bindings:
            return current
        first = False
        current = current.parent
    return None


class _Resolver:
    """Finds first-party source files for dotted names and caches what each defines, by parsing only."""

    def __init__(self) -> None:
        self._infos: dict[Path, _ModuleInfo] = {}
        self._active: set[Path] = set()

    @staticmethod
    def candidates_in(dirs: Iterable[Path], dotted: str) -> list[_Candidate]:
        out: list[_Candidate] = []
        seen: set[Path] = set()
        parts = dotted.split(".") if dotted else []
        for base in dirs:
            target = base.joinpath(*parts) if parts else base
            found: Optional[_Candidate] = None
            if (target / "__init__.py").is_file():
                found = _Candidate(target / "__init__.py", "package")
            elif parts and target.with_name(target.name + ".py").is_file():
                found = _Candidate(target.with_name(target.name + ".py"), "module")
            elif parts and target.is_dir() and any(target.glob("*.py")):
                found = _Candidate(target, "namespace")
            if found is not None:
                key = found.path.resolve()
                if key not in seen:
                    seen.add(key)
                    out.append(found)
        return out

    def info(self, cand: _Candidate, dirs: Sequence[Path]) -> _ModuleInfo:
        if cand.kind == "namespace":
            return _ModuleInfo(None, "namespace package (may be extended from other directories)")
        key = cand.path.resolve()
        cached = self._infos.get(key)
        if cached is not None:
            return cached
        if key in self._active:
            return _ModuleInfo(None, "circular star import")
        self._active.add(key)
        try:
            result = self._compute(cand, dirs)
        finally:
            self._active.discard(key)
        self._infos[key] = result
        return result

    def _compute(self, cand: _Candidate, dirs: Sequence[Path]) -> _ModuleInfo:
        try:
            _, tree = parse_source(cand.path)
        except SourceError as exc:
            return _ModuleInfo(None, f"target cannot be parsed ({exc.kind})")
        if _is_dynamic_module(tree) or _swaps_module_class(tree):
            return _ModuleInfo(None, "target builds names dynamically (module __getattr__, __class__ swap, globals()[...], exec or setattr on sys.modules)")
        names = set(_bound_names(tree)) - {"*"}
        literal = _literal_dunder_all(tree)
        if literal:
            names |= literal
        for node in _module_level_nodes(tree.body):
            if not (isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)):
                continue
            star = self._star_names(cand, node, dirs)
            if star is None:
                return _ModuleInfo(None, f"target star-imports an unresolvable or dynamic module ({'.' * node.level}{node.module or ''})")
            names |= star
        bound = set(names)
        if cand.kind == "package":
            names |= _submodule_names(cand.path.parent)
        return _ModuleInfo(names, bound=bound)

    def _star_names(self, importer: _Candidate, node: ast.ImportFrom, dirs: Sequence[Path]) -> "Optional[set[str]]":
        if node.level:
            base = importer.path.parent
            for _ in range(node.level - 1):
                base = base.parent
            found = self.candidates_in([base], node.module or "") if node.module else [_Candidate(base / "__init__.py", "package")]
            if not node.module and not found[0].path.is_file():
                return None
        else:
            found = self.candidates_in(dirs, node.module or "")
        if not found:
            return None
        out: set[str] = set()
        for cand in found:
            info = self.info(cand, dirs)
            if info.names is None:
                return None
            literal = _tree_all(cand)
            out |= literal if literal is not None else {n for n in info.names if not n.startswith("_")}
        return out


def _swaps_module_class(tree: ast.Module) -> bool:
    """``sys.modules[__name__].__class__ = Sub`` gives the module a class-level ``__getattr__``/properties, so its names are not all in the parse."""
    for node in _module_level_nodes(tree.body):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "__class__" for t in node.targets):
            return True
    return False


def _tree_all(cand: _Candidate) -> "Optional[set[str]]":
    try:
        _, tree = parse_source(cand.path)
    except SourceError:
        return None
    return _literal_dunder_all(tree)


def _submodule_names(package_dir: Path) -> set[str]:
    out: set[str] = set()
    try:
        entries = list(package_dir.iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.is_dir():
            out.add(entry.name)
        elif entry.name.endswith(_SUFFIXES):
            out.add(entry.name.split(".")[0])
    out.discard("__init__")
    return out


def _anchor(path: Path) -> Path:
    """The directory pytest's default import mode puts on ``sys.path`` for *path*: the first ancestor with no ``__init__.py``."""
    current = path.parent
    while (current / "__init__.py").is_file() and current.parent != current:
        current = current.parent
    return current


def _attribute_guards(tree: ast.Module) -> "tuple[set[int], dict[str, set[str]]]":
    """``(ids of Attribute nodes under try/except AttributeError or pytest.raises(AttributeError), name -> attributes it is given on purpose)``."""
    return _guarded_ids(tree), _given_names(tree)


def _guarded_ids(tree: ast.Module) -> "set[int]":
    guarded: set[int] = set()
    for node in nodes_of(tree, ast.Try, *((ast.TryStar,) if hasattr(ast, "TryStar") else ())):
        if any("AttributeError" in _names_in(h.type) for h in node.handlers):  # type: ignore[attr-defined]
            guarded.update(id(n) for stmt in node.body for n in ast.walk(stmt))  # type: ignore[attr-defined]
    for node in nodes_of(tree, ast.With, ast.AsyncWith):
        for item in node.items:  # type: ignore[attr-defined]
            call = item.context_expr
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "raises":
                if any("AttributeError" in _names_in(a) for a in call.args):
                    guarded.update(id(n) for stmt in node.body for n in ast.walk(stmt))  # type: ignore[attr-defined]
    return guarded


def _given_names(tree: ast.Module) -> "dict[str, set[str]]":
    given: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.value, ast.Name):
            given.setdefault(node.value.id, set()).add(node.attr)
        elif isinstance(node, ast.Call) and len(node.args) >= 2 and isinstance(node.args[0], ast.Name):
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            second = node.args[1]
            if fname in _PATCH_CALLS and isinstance(second, ast.Constant) and isinstance(second.value, str):
                given.setdefault(node.args[0].id, set()).add(second.value)
    return given


def _hasattr_names(scope_node: ast.AST) -> "dict[str, set[str]]":
    """``alias -> names`` tested with ``hasattr(alias, "NAME")`` anywhere inside the scope's subtree."""
    out: dict[str, set[str]] = {}
    for node in ast.walk(scope_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hasattr" and len(node.args) == 2:
            first, second = node.args
            if isinstance(first, ast.Name) and isinstance(second, ast.Constant) and isinstance(second.value, str):
                out.setdefault(first.id, set()).add(second.value)
    return out


def _alias_candidates(binding: _Binding, file: Path, dirs: Sequence[Path], resolver: _Resolver) -> "tuple[str, list[_Candidate]]":
    """``(dotted display name, candidate sources)`` of the module an import binding names; no candidates for a non-first-party or non-module target."""
    if binding.level:
        base = file.parent
        for _ in range(binding.level - 1):
            base = base.parent
        bases = [base]
        prefix = "." * binding.level
    else:
        bases = list(dirs)
        prefix = ""
    if not binding.member:
        return prefix + binding.module, resolver.candidates_in(bases, binding.module)
    dotted = f"{binding.module}.{binding.member}" if binding.module else binding.member
    # `from pkg import name`: a name the package binds wins over a same-named submodule file.
    holders = (
        resolver.candidates_in(bases, binding.module)
        if binding.module
        else [_Candidate(b / "__init__.py", "package") for b in bases if (b / "__init__.py").is_file()]
    )
    for holder in holders:
        info = resolver.info(holder, dirs)
        if info.names is not None and binding.member in info.bound:
            return prefix + dotted, []
    return prefix + dotted, resolver.candidates_in(bases, dotted)


def _single_binding(bindings: "list[Optional[_Binding]]") -> "tuple[Optional[_Binding], bool]":
    """``(the one module a name is bound to, whether it was bound to something else too)``; no binding when it is not purely one import target."""
    imports = {b for b in bindings if b is not None}
    if len(imports) == 1 and None not in bindings:
        return next(iter(imports)), False
    return None, bool(imports)


def _judge_read(node: ast.Attribute, binding: _Binding, exempt: "set[str]", ctx: tuple) -> None:
    """Compare one ``alias.NAME`` read with the names its module defines; *ctx* is the scan state of the file."""
    rel, path, dirs, resolver, report, skipped_seen = ctx
    name = node.value.id  # type: ignore[attr-defined]
    attr = node.attr
    dotted, cands = _alias_candidates(binding, path, dirs, resolver)
    if not cands:
        report.external_reads += 1
        return
    if attr in exempt:
        return
    infos = [resolver.info(c, dirs) for c in cands]
    reasons = [i.skip_reason for i in infos if i.names is None]
    if reasons:
        _skip(report, skipped_seen, rel, node.lineno, name, dotted, reasons[0])
        return
    report.checked_reads += 1
    if not any(attr in (i.names or ()) for i in infos):
        where = ", ".join(sorted({_display(c.path, dirs) for c in cands}))
        report.findings.append(Finding(rel, node.lineno, RULE, f"'{name}.{attr}': module '{dotted}' ({where}) does not define '{attr}'"))


def _scan_file(tree: ast.Module, rel: str, path: Path, dirs: Sequence[Path], resolver: _Resolver, report: AttributeReport) -> None:
    guarded, given = _attribute_guards(tree)
    scopes = list(_scopes_of(tree))
    hasattr_cache: "dict[int, dict[str, set[str]]]" = {}
    skipped_seen: set[tuple[str, str, str]] = set()
    for scope in scopes:
        for node, shadow in _own_nodes(scope.node):
            if not (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name)):
                continue
            name = node.value.id
            attr = node.attr
            if attr.startswith("__") or id(node) in guarded:
                continue
            owner = _binder(scope, name, shadow)
            if owner is None:
                continue
            binding, rebound = _single_binding(owner.bindings[name])
            if rebound:
                _skip(report, skipped_seen, rel, node.lineno, name, "?", "alias rebound in the same scope")
            if binding is None:
                continue
            exempt = hasattr_cache.setdefault(id(owner), _hasattr_names(owner.node)).get(name, set()) | given.get(name, set())
            _judge_read(node, binding, exempt, (rel, path, dirs, resolver, report, skipped_seen))


def _skip(report: AttributeReport, seen: "set[tuple[str, str, str]]", rel: str, line: int, alias: str, module: str, reason: str) -> None:
    key = (alias, module, reason)
    if key not in seen:
        seen.add(key)
        report.skipped.append(SkippedAlias(rel, line, alias, module, reason))


def _display(path: Path, dirs: Sequence[Path]) -> str:
    """A target path relative to the first search directory that holds it, so a message is the same on every machine."""
    resolved = path.resolve()
    for base in dirs:
        if base.resolve() in resolved.parents:
            return resolved.relative_to(base.resolve()).as_posix()
    return path.name


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            out.append(p.resolve())
    return out


def scan_module_attributes(
    roots: "Union[PathLike, Sequence[PathLike]]",
    *,
    resolve_roots: "Iterable[PathLike]" = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> AttributeReport:
    """Findings, skipped aliases and counters for every Python file under *roots*.

    *resolve_roots* are the directories where a bare module name is found (the ones a test puts on ``sys.path``); the scanned
    roots and each file's own import directory are always searched. Raises ``EmptyScanError`` below *min_files* parsed files
    and ``UnparsedFilesError`` for a file that cannot be parsed (unless *allow_unparsed*).
    """
    root_list = [roots] if isinstance(roots, (str, os.PathLike)) else list(roots)
    extra = [Path(r) for r in resolve_roots]
    for missing in (p for p in extra if not p.is_dir()):
        raise FileNotFoundError(f"resolve_roots entry is not a directory: {missing}")
    scan = scan_python(root_list if len(root_list) != 1 else root_list[0], min_files=min_files, use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    resolver = _Resolver()
    report = AttributeReport()
    scan_dirs = [Path(r) for r in root_list if Path(r).is_dir()]
    for parsed in scan:
        dirs = _dedupe([_anchor(parsed.path.resolve()), parsed.path.parent, *extra, *scan_dirs])
        _scan_file(parsed.tree, parsed.rel, parsed.path.resolve(), dirs, resolver, report)
    report.findings.sort(key=lambda f: (f.path, f.line))
    return report


def find_unresolved_module_attributes(
    roots: "Union[PathLike, Sequence[PathLike]]",
    *,
    resolve_roots: "Iterable[PathLike]" = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *roots*, sorted by path and line (see :func:`scan_module_attributes` for the skipped aliases)."""
    return scan_module_attributes(roots, resolve_roots=resolve_roots, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git).findings


def assert_no_unresolved_module_attributes(
    roots: "Union[PathLike, Sequence[PathLike]]",
    *,
    resolve_roots: "Iterable[PathLike]" = (),
    baseline_path: Optional[PathLike] = None,
    refresh: bool = False,
    grow: Optional[bool] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    skipped_report: "Optional[list[SkippedAlias]]" = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept.

    Pass a list as *skipped_report* to receive the aliases that could not be judged.
    """
    report = scan_module_attributes(roots, resolve_roots=resolve_roots, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if skipped_report is not None:
        skipped_report.extend(report.skipped)
    found = report.findings
    guidance = "the module no longer defines that name: restore it, or update the reader (a deleted constant fails only the code that reads it)"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="unresolved_module_attributes", refresh_command="PY_CI_SHARED_REFRESH=unresolved_module_attributes")
        baseline.enforce(found, refresh=refresh, grow=grow, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} unresolved-module-attributes finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
