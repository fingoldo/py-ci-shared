"""A monkeypatch of a name that its module only re-exports changes nothing: the code under test keeps its own binding.

``from mod import NAME`` copies the object into the importing module at import time. A test that patches ``mod.NAME``
replaces the attribute on ``mod``, and every reader that bound the name itself still sees the original. The test passes,
because the code under test never saw the fake, and the assertion it wrote either held by accident or was never checked.

Flagged: a ``monkeypatch.setattr(X, "NAME", ...)``, a ``unittest.mock.patch.object(X, "NAME", ...)`` or a
``patch("pkg.mod.NAME")`` in a test, where ``X`` is a first-party module (``pkg.mod``) and, in that module, ``NAME`` is
inert: it is bound ONLY by import statements (no ``def``, ``class``, assignment, ``for``/``with`` target, ``global``
rebinding or star import at module level), no function body of the module reads it as a bare name, and no function body
in any scanned package module reads it as an attribute of the module object (``mod.NAME``, ``getattr(mod, "NAME")``).

Example, the defect::

    # config_base.py        LIMIT = 100
    # reexport.py           from config_base import LIMIT      <- LIMIT is only imported here
    # service.py            from reexport import LIMIT         <- service keeps its own copy
    #                       def cap(n): return min(n, LIMIT)
    # test_service.py       monkeypatch.setattr(reexport, "LIMIT", 5)   <- reported: changes nothing

The nearest correct code is ``service.py`` reading ``reexport.LIMIT`` at call time (``import reexport`` and
``min(n, reexport.LIMIT)``), which makes ``reexport.LIMIT`` a live patch target and is not reported. Patching
``service.LIMIT`` (the name where it is read) is the other correct form.

Conservative by design: a name the module also rebinds, reads or exposes dynamically (``globals()``, ``vars()``) is live,
so a doubt errs toward silence, never toward a false finding. A patch target that is not a first-party module, or a name
the module does not bind at all, is not judged here.

A route that must stay as it is (a re-export kept for a third party that reads it) is accepted through the baseline,
which can only shrink: ``assert_patch_target_read_at_call(test_paths, package_roots, baseline_path=...)``.

Usage::

    from py_ci_shared.patch_target_read_at_call import assert_patch_target_read_at_call

    def test_no_inert_patches():
        assert_patch_target_read_at_call(["tests"], ["src"])
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, ParsedFile, child_nodes, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "assert_patch_target_read_at_call", "find_inert_patches"]

RULE = "patch-target-read-at-call"

_MOCK_PATCH = "unittest.mock.patch"
_MOCK_PATCH_OBJECT = "unittest.mock.patch.object"
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_DYNAMIC_NAMESPACE_CALLS = frozenset({"globals", "vars"})
_MATCH_BINDERS = tuple(getattr(ast, n) for n in ("MatchAs", "MatchStar") if hasattr(ast, n))  # 3.10+ only


@dataclass(frozen=True)
class _Module:
    name: str
    tree: ast.Module
    aliases: ImportAliases


@dataclass
class _Facts:
    """What one first-party module says about its own module-level names and how its functions reach them."""

    module_bindings: dict[str, tuple[bool, bool]] = field(default_factory=dict)  # name -> (bound by import, bound otherwise)
    star_import: bool = False
    global_names: set[str] = field(default_factory=set)
    function_loads: set[str] = field(default_factory=set)  # bare names read in a function body
    function_attrs: set[str] = field(default_factory=set)  # attribute names read in a function body
    function_qualified: set[str] = field(default_factory=set)  # dotted targets read in a function body (``pkg.mod.NAME``)
    dynamic_namespace: bool = False  # globals()/vars() in a function body: any name may be read


def _module_name(rel: str) -> str:
    """``pkg/sub/mod.py`` -> ``pkg.sub.mod``; ``pkg/sub/__init__.py`` -> ``pkg.sub``."""
    parts = rel[: -len(".py")].split("/") if rel.endswith(".py") else rel.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


def _is_package_init(rel: str) -> bool:
    return rel.endswith("__init__.py")


def _module_scope(tree: ast.Module) -> Iterator[ast.AST]:
    """Every node that executes at module scope: function, class and lambda bodies are other scopes and are not entered."""
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(child_nodes(node))


def _names_bound(node: ast.AST) -> tuple[list[str], bool, list[str]]:
    """``(import-bound names, star import, other-bound names)`` for one module-scope node."""
    if isinstance(node, ast.Import):
        return [a.asname or a.name.split(".", 1)[0] for a in node.names], False, []
    if isinstance(node, ast.ImportFrom):
        if any(a.name == "*" for a in node.names):
            return [], True, []
        return [a.asname or a.name for a in node.names], False, []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [], False, [node.name]
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return [], False, [node.id]
    if isinstance(node, ast.ExceptHandler) and node.name:
        return [], False, [node.name]
    if isinstance(node, _MATCH_BINDERS) and node.name:
        return [], False, [node.name]
    return [], False, []


def _record_module_scope(facts: _Facts, tree: ast.Module) -> None:
    for node in _module_scope(tree):
        by_import, star, otherwise = _names_bound(node)
        facts.star_import = facts.star_import or star
        for name in by_import:
            _, other = facts.module_bindings.get(name, (False, False))
            facts.module_bindings[name] = (True, other)
        for name in otherwise:
            imported, _ = facts.module_bindings.get(name, (False, False))
            facts.module_bindings[name] = (imported, True)


def _record_function_reads(facts: _Facts, module: _Module, func: ast.AST) -> None:
    for node in shared_walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            facts.function_loads.add(node.id)
            facts.dynamic_namespace = facts.dynamic_namespace or node.id in _DYNAMIC_NAMESPACE_CALLS
        elif isinstance(node, ast.Attribute):
            facts.function_attrs.add(node.attr)
            qualified = module.aliases.qualified_name(node)
            if qualified is not None:
                facts.function_qualified.add(qualified)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            target = module.aliases.qualified_name(node.args[0])
            attr_node = node.args[1]
            if target is not None and isinstance(attr_node, ast.Constant) and isinstance(attr_node.value, str):
                facts.function_qualified.add(f"{target}.{attr_node.value}")


def _facts_of(module: _Module) -> _Facts:
    facts = _Facts()
    _record_module_scope(facts, module.tree)
    for node in shared_walk(module.tree):
        if isinstance(node, ast.Global):
            facts.global_names.update(node.names)
    for func in shared_walk(module.tree):
        if isinstance(func, _FUNCTIONS):
            _record_function_reads(facts, module, func)
    return facts


def _is_inert(name: str, owner: _Module, facts: _Facts, all_facts: dict[str, _Facts]) -> bool:
    """True when patching ``owner.name`` reaches no code: bound only by import here, and never read through the module object."""
    if facts.star_import or facts.dynamic_namespace:
        return False
    by_import, otherwise = facts.module_bindings.get(name, (False, False))
    if not by_import or otherwise:
        return False
    if name in facts.global_names or name in facts.function_loads or name in facts.function_attrs:
        return False
    dotted = f"{owner.name}.{name}"
    return not any(dotted in other.function_qualified for other in all_facts.values())


def _head(node: ast.AST) -> Optional[str]:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_monkeypatch(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "monkeypatch") or (isinstance(node, ast.Attribute) and node.attr == "monkeypatch")


def _module_expr(node: ast.AST, aliases: ImportAliases) -> Optional[str]:
    """The dotted module a patch target expression names, when its head name is an import (``mod``, ``pkg.mod``)."""
    head = _head(node)
    if head is None or not aliases.is_imported(head):
        return None
    return aliases.qualified_name(node)


def _split_dotted(text: str) -> Optional[tuple[str, str]]:
    module, dot, name = text.rpartition(".")
    return (module, name) if dot and module and name else None


def _str_const(node: Optional[ast.AST]) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _patch_target(call: ast.Call, aliases: ImportAliases) -> Optional[tuple[str, str]]:
    """``(module, NAME)`` for a patch call that names a module attribute, or ``None`` for any other call."""
    func = call.func
    args = call.args
    if isinstance(func, ast.Attribute) and func.attr == "setattr" and _is_monkeypatch(func.value) and args:
        name = _str_const(args[1]) if len(args) >= 2 else None
        if name is not None:
            module = _module_expr(args[0], aliases)
            return (module, name) if module is not None else None
        dotted = _str_const(args[0])
        return _split_dotted(dotted) if dotted is not None else None
    qualified = aliases.qualified_name(func)
    if qualified == _MOCK_PATCH_OBJECT and len(args) >= 2:
        name = _str_const(args[1])
        module = _module_expr(args[0], aliases)
        return (module, name) if name is not None and module is not None else None
    if qualified == _MOCK_PATCH and args:
        dotted = _str_const(args[0])
        return _split_dotted(dotted) if dotted is not None else None
    return None


def _index(package_roots: Iterable[Union[str, Path]], *, allow_unparsed: bool, use_git: Optional[bool]) -> dict[str, _Module]:
    """Every first-party module under the package roots, by dotted name. Each root must parse at least one file."""
    modules: dict[str, _Module] = {}
    for root in package_roots:
        root_path = Path(root)
        # A root that is itself a package (holds __init__.py) names its modules under its own name: ``dashboard/data.py`` is
        # ``dashboard.data``, the name the code imports it by. A plain source root (``src``) names them from its contents.
        prefix = root_path.name if (root_path / "__init__.py").is_file() else ""
        scan = scan_python(root_path, min_files=1, use_git=use_git)
        scan.assert_ok(allow_unparsed=allow_unparsed)
        for parsed in scan:
            name = ".".join(p for p in (prefix, _module_name(parsed.rel)) if p)
            if not name or name in modules:
                continue
            package = name if _is_package_init(parsed.rel) else name.rpartition(".")[0]
            modules[name] = _Module(name, parsed.tree, ImportAliases.from_tree(parsed.tree, package=package or None))
    return modules


def _findings_in(parsed: ParsedFile, modules: dict[str, _Module], facts: dict[str, _Facts]) -> list[Finding]:
    out: list[Finding] = []
    aliases = ImportAliases.from_tree(parsed.tree)
    for node in shared_walk(parsed.tree):
        if not isinstance(node, ast.Call):
            continue
        target = _patch_target(node, aliases)
        if target is None:
            continue
        module_name, name = target
        owner = modules.get(module_name)
        if owner is None or not _is_inert(name, owner, facts[module_name], facts):
            continue
        out.append(
            Finding(
                parsed.rel,
                node.lineno,
                RULE,
                f"patch of {module_name}.{name} changes nothing: {name!r} is only imported into {module_name}, and no function "
                f"reads it through the module; the code under test keeps its own binding. Patch the name where it is read, or "
                f"have the code read {module_name}.{name} at call time",
            )
        )
    return out


def find_inert_patches(
    test_paths: Iterable[Union[str, Path]],
    package_roots: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every inert patch in the tests under *test_paths*, sorted by path and line.

    *package_roots* are the directories whose modules are first-party; a module's dotted name is its path relative to its
    root. Raises ``EmptyScanError`` when fewer than *min_files* test files parsed, and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*) in the tests or the packages: a gate that cannot read the code must
    not pass it.
    """
    modules = _index(package_roots, allow_unparsed=allow_unparsed, use_git=use_git)
    facts = {name: _facts_of(module) for name, module in modules.items()}
    scan = scan_python(list(test_paths), min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed, modules, facts))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_patch_target_read_at_call(
    test_paths: Iterable[Union[str, Path]],
    package_roots: Iterable[Union[str, Path]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    grow: Optional[bool] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any inert patch, or with *baseline_path* on any inert patch the baseline does not accept."""
    found = find_inert_patches(test_paths, package_roots, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "patch the name where the code under test reads it, or read it as module.NAME at call time"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="patch_target_read_at_call", refresh_command="PY_CI_SHARED_REFRESH=patch_target_read_at_call")
        baseline.enforce(found, refresh=refresh, grow=grow, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} patch-target-read-at-call finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
