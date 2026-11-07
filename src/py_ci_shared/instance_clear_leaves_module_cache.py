"""A ``clear``/``reset``/``release`` method resets only ``self`` while the class's other methods use a module-level cache.

mlframe 29f1e5c8d: the xgb shim's ``clear_cache()`` reset its instance pointers only, so the next ``fit`` hit the module-level LRU and reused the same ``QuantileDMatrix``, keeping the C++ memory the
docstring promised to free. The asymmetry is between methods of one class: the work methods read and fill a process-wide container, the "forget state" method touches ``self`` alone.

Per class: the module-level mutable containers whose name carries a cache-like token (``cache``, ``memo``, ``lru``, ``store``, ``pool``, ``registry``, ``interned``) that some non-clearing method uses,
directly or through a ``self.`` call or a module function it calls (two hops). A method whose name starts with a clearing verb that references ``self`` and reaches none of those containers is reported:

* ``instance-clear-leaves-module-cache``: ``clear``, ``reset``, ``invalidate``, ``flush``, ``forget``, ``purge`` (state the caller expects to be empty afterwards).
* ``instance-release-leaves-module-cache``: ``release``, ``free``, ``dispose``, ``drop``, ``evict`` (memory the caller expects to be returned).

A warn-once set or a registry that is intentionally process-wide is not named like a cache and is not matched; one that is, takes the marker. Suppression: ``# cache-scope-ok: <reason>`` on the ``def`` line.

Usage in a consumer's meta test::

    from py_ci_shared.instance_clear_leaves_module_cache import assert_instance_clear_leaves_module_cache

    def test_clear_methods_clear_the_module_cache():
        assert_instance_clear_leaves_module_cache("src", baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python

__all__ = [
    "MARKER",
    "RULE_CLEAR",
    "RULE_RELEASE",
    "assert_instance_clear_leaves_module_cache",
    "find_instance_clear_leaves_module_cache",
]

RULE_CLEAR = "instance-clear-leaves-module-cache"
RULE_RELEASE = "instance-release-leaves-module-cache"
MARKER = "cache-scope-ok:"

_CLEAR_VERBS = ("clear", "reset", "invalidate", "flush", "forget", "purge")
_RELEASE_VERBS = ("release", "free", "dispose", "drop", "evict")
_CACHE_TOKENS = frozenset({"cache", "cached", "memo", "memoize", "lru", "store", "pool", "registry", "interned"})
_CONTAINER_CALLS = frozenset({"dict", "list", "set", "OrderedDict", "defaultdict", "deque", "WeakValueDictionary", "WeakKeyDictionary", "WeakSet", "Counter"})
_HOPS = 2


def _is_container(value: ast.AST) -> bool:
    if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
        return True
    if isinstance(value, ast.Call):
        return (getattr(value.func, "id", None) or getattr(value.func, "attr", None)) in _CONTAINER_CALLS
    return False


def _module_caches(tree: ast.Module) -> "set[str]":
    out: "set[str]" = set()
    for node in tree.body:
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is not None and _is_container(value):
            out |= {t.id for t in targets if isinstance(t, ast.Name) and set(t.id.lower().strip("_").split("_")) & _CACHE_TOKENS}
    return out


def _verb_rule(name: str) -> Optional[str]:
    lowered = name.lower().lstrip("_")
    if lowered.startswith(_CLEAR_VERBS):
        return RULE_CLEAR
    return RULE_RELEASE if lowered.startswith(_RELEASE_VERBS) else None


def _names(fn: ast.AST) -> "set[str]":
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}


def _self_calls(fn: ast.AST) -> "set[str]":
    return {
        c.func.attr
        for c in ast.walk(fn)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) and c.func.value.id in ("self", "cls")
    }


def _reaches(start: ast.AST, methods: "Mapping[str, ast.AST]", functions: "Mapping[str, ast.AST]", caches: "set[str]") -> "set[str]":
    """The module caches *start* uses directly or through ``self.`` calls and module functions, within :data:`_HOPS`."""
    found: "set[str]" = set()
    frontier = [start]
    for _ in range(_HOPS + 1):
        nxt: "list[ast.AST]" = []
        for node in frontier:
            found |= _names(node) & caches
            nxt.extend(methods[m] for m in _self_calls(node) if m in methods)
            nxt.extend(functions[n] for n in _names(node) if n in functions)
        frontier = nxt
    return found


def _marked(lines: "list[str]", number: int) -> bool:
    line = lines[number - 1] if 0 < number <= len(lines) else ""
    return "#" in line and MARKER in line[line.index("#") :]


def _class_findings(cls: ast.ClassDef, rel: str, lines: "list[str]", functions: "Mapping[str, ast.AST]", caches: "set[str]") -> "list[Finding]":
    methods = {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    used: "set[str]" = set()
    for name, node in methods.items():
        if _verb_rule(name) is None:
            used |= _reaches(node, methods, functions, caches)
    out: "list[Finding]" = []
    for name, node in methods.items():
        rule = _verb_rule(name)
        if rule is None or not used or "self" not in _names(node) or _marked(lines, node.lineno):
            continue
        if not _reaches(node, methods, functions, caches) & used:
            out.append(
                Finding(
                    rel,
                    node.lineno,
                    rule,
                    f"`{cls.name}.{name}` resets `self` but reaches none of the module-level caches its sibling methods use ({', '.join(sorted(used))}): clear them here too, or mark `# cache-scope-ok: <reason>`",
                )
            )
    return out


def _findings_in(tree: ast.Module, rel: str, lines: "list[str]") -> "list[Finding]":
    caches = _module_caches(tree)
    if not caches:
        return []
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out: "list[Finding]" = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            out.extend(_class_findings(node, rel, lines, functions, caches))
    return out


def find_instance_clear_leaves_module_cache(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every clearing method under *root* that leaves a module-level cache its siblings use, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code
    must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_instance_clear_leaves_module_cache(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any clearing method that leaves a module cache, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_instance_clear_leaves_module_cache(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "clear the module-level cache in the method too, or mark its `def` line `# cache-scope-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="instance_clear_leaves_module_cache", refresh_command="PY_CI_SHARED_REFRESH=instance_clear_leaves_module_cache")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} clearing method(s) that leave a module-level cache; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
