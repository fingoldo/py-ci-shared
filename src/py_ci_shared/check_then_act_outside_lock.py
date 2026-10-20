"""A membership test or ``get`` on a shared dict, and a later write to the same dict, with no lock held across both.

In code that runs on several threads, ``if key not in self._items:`` followed by ``self._items[key] = value`` is a race: another
thread can insert the key between the test and the write, so the work is done twice, or a value is overwritten after a reader
already used the first one. Taking the lock around only the write does not help: the test was already decided outside it.

Scope, deliberately narrow. A key is ``self.X`` (an attribute of the method's receiver) or a module-level dict ``D`` (bound at
module scope to a dict literal, ``dict()``, ``OrderedDict()``, ``defaultdict()`` or ``WeakValueDictionary()``). Within one
function, a check is a membership test (``in`` / ``not in``) or a ``.get(...)`` call on the key, and an act is a subscript
write to the same key (plain, augmented or annotated assignment). An act is a finding when an earlier-or-same-line check on
the same key shares no lock-with block with it. A function is judged only when the module constructs a Lock, RLock or
Condition somewhere, or the function itself holds one: a module with no locks is single-threaded or unguarded by design, and
``module_cache_thread_safety`` owns that case.

Not judged: a check and an act in the same lock-with block (both are then under one lock), a key that is not a dict in this
module (a parameter, a call result), and anything across a function call.

Usage::

    from py_ci_shared.check_then_act_outside_lock import assert_no_check_then_act_outside_lock

    def test_no_check_then_act():
        assert_no_check_then_act_outside_lock(_py_files(REPO / "src"))
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ._core.node_index import function_nodes
from ._core import Finding, ParsedFile, child_nodes, scan_python
from .lock_held_across_io import is_lock_like, module_lock_names

__all__ = ["RULE", "assert_no_check_then_act_outside_lock", "find_check_then_act_outside_lock"]

RULE = "check-then-act-outside-lock"
_DICT_CONSTRUCTORS = frozenset({"dict", "OrderedDict", "defaultdict", "WeakValueDictionary"})
_Key = tuple[str, str]  # ("self", attr) or ("module", name)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_WITH = (ast.With, ast.AsyncWith)


@dataclass(frozen=True)
class _Access:
    key: _Key
    line: int
    locks: frozenset[int]  # ids of the lock-with blocks that enclose the access


def _is_dict_value(value: ast.AST) -> bool:
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
        return name in _DICT_CONSTRUCTORS
    return False


def _module_dicts(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _is_dict_value(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _is_dict_value(node.value) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def _key_of(node: ast.AST, module_dicts: frozenset[str]) -> Optional[_Key]:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return ("self", node.attr)
    if isinstance(node, ast.Name) and node.id in module_dicts:
        return ("module", node.id)
    return None


def _render(key: _Key) -> str:
    return f"self.{key[1]}" if key[0] == "self" else key[1]


def _record(node: ast.AST, locks: frozenset[int], module_dicts: frozenset[str], checks: list[_Access], acts: list[_Access]) -> None:
    """Sort one node into a check (a membership test or ``get`` on a key) or an act (a subscript write to a key)."""
    if isinstance(node, ast.Compare):
        for op, container in zip(node.ops, node.comparators):
            key = _key_of(container, module_dicts)
            if key is not None and isinstance(op, (ast.In, ast.NotIn)):
                checks.append(_Access(key, node.lineno, locks))
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        key = _key_of(node.func.value, module_dicts)
        if key is not None:
            checks.append(_Access(key, node.lineno, locks))
    elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Subscript):
                key = _key_of(target.value, module_dicts)
                if key is not None:
                    acts.append(_Access(key, node.lineno, locks))


def _accesses(fn: ast.AST, module_dicts: frozenset[str], lock_names: frozenset[str]) -> tuple[list[_Access], list[_Access], bool]:
    """Checks, acts and whether the function holds a lock itself, for the statements of *fn* (nested scopes excluded)."""
    checks: list[_Access] = []
    acts: list[_Access] = []
    holds_lock = False

    def visit(node: ast.AST, locks: frozenset[int]) -> None:
        nonlocal holds_lock
        for child in child_nodes(node):
            if isinstance(child, _SCOPES):
                continue
            inner = locks
            if isinstance(child, _WITH) and any(is_lock_like(item.context_expr, lock_names) for item in child.items):
                inner = locks | {id(child)}
                holds_lock = True
            _record(child, inner, module_dicts, checks, acts)
            visit(child, inner)

    visit(fn, frozenset())
    return checks, acts, holds_lock


def _findings_in_function(fn: ast.AST, rel: str, module_dicts: frozenset[str], lock_names: frozenset[str], module_has_lock: bool) -> list[Finding]:
    checks, acts, holds_lock = _accesses(fn, module_dicts, lock_names)
    if not (module_has_lock or holds_lock):
        return []
    out: list[Finding] = []
    name = getattr(fn, "name", "<function>")
    for act in acts:
        for check in checks:
            if check.key == act.key and check.line <= act.line and not (check.locks & act.locks):
                out.append(
                    Finding(
                        rel,
                        act.line,
                        RULE,
                        f"{_render(act.key)} is tested at line {check.line} and written here in {name} with no lock held across both: "
                        "another thread can insert or remove the key in between; do the test and the write in one with-block on one lock",
                    )
                )
                break
    return out


def _findings_in(parsed: ParsedFile) -> list[Finding]:
    module_dicts = _module_dicts(parsed.tree)
    lock_names = module_lock_names(parsed.tree)
    module_has_lock = bool(lock_names)
    out: list[Finding] = []
    for fn in function_nodes(parsed.tree):
        out.extend(_findings_in_function(fn, parsed.rel, module_dicts, lock_names, module_has_lock))
    return out


def find_check_then_act_outside_lock(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every check-then-act pair on a shared dict in *files* that no common lock covers, sorted by path and line.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for an unreadable file (unless
    *allow_unparsed*).
    """
    scan = scan_python(list(files), min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_no_check_then_act_outside_lock(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any check-then-act on a shared dict that no single lock covers (see the module docstring)."""
    found = find_check_then_act_outside_lock(files, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(
            f"{len(found)} check-then-act-outside-lock finding(s): a test and a write on a shared dict need one lock across both:\n  "
            + "\n  ".join(f.render() for f in found)
        )
