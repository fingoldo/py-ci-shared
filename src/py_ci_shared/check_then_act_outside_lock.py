"""A membership test or ``get`` on a shared dict, and a later write to the same dict, with no lock held across both.

In code that runs on several threads, ``if key not in self._items:`` followed by ``self._items[key] = value`` is a race: another
thread can insert the key between the test and the write, so the work is done twice, or a value is overwritten after a reader
already used the first one. Taking the lock around only the write does not help: the test was already decided outside it.

Scope, deliberately narrow. A key is ``self.X`` (an attribute of the method's receiver) or a module-level dict ``D`` (bound at
module scope to a dict literal, ``dict()``, ``OrderedDict()``, ``defaultdict()`` or ``WeakValueDictionary()``). Within one
function, a check is a membership test (``in`` / ``not in``) or a ``.get(...)`` call on the key, and an act is a subscript
write to the same key (plain, augmented or annotated assignment), a ``del key[k]``, or a ``update``/``pop``/``popitem`` call on it
(``cls._x`` counts like ``self._x``). An act is a finding when an earlier-or-same-line check on
the same key shares no lock-with block with it. A function is judged only when the module constructs a Lock, RLock or
Condition somewhere, or the function itself holds one: a module with no locks is single-threaded or unguarded by design, and
``module_cache_thread_safety`` owns that case.

Not judged: a helper documented as called with the lock held (``*_unlocked``/``*_locked``, or a docstring "caller holds ..."); an idempotent
``d.pop(k, default)``; a removal that sits under an ``if`` inside a lock block whose earlier check was also under a lock (it re-decides under the lock); a check and an act in the same lock-with block (both are then under one lock), a key that is not a dict in this
module (a parameter, a call result), and anything across a function call.

Usage::

    from py_ci_shared.check_then_act_outside_lock import assert_no_check_then_act_outside_lock

    def test_no_check_then_act():
        assert_no_check_then_act_outside_lock(_py_files(REPO / "src"))
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ._core.node_index import nodes_of, parent_map, tree_memo
from ._core import Finding, ParsedFile, child_nodes, scan_python
from .lock_held_across_io import is_lock_like, module_lock_names

__all__ = ["RULE", "assert_no_check_then_act_outside_lock", "find_check_then_act_outside_lock"]

RULE = "check-then-act-outside-lock"
_DICT_CONSTRUCTORS = frozenset({"dict", "OrderedDict", "defaultdict", "WeakValueDictionary"})
_Key = tuple[str, str]  # ("self", attr) or ("module", name)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_WITH = (ast.With, ast.AsyncWith)
#: The node types :func:`_record` sorts; every other node is only a link in the walk, so it is not passed to it.
_RELEVANT = (ast.Compare, ast.Call, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)
#: Dict methods that write after a membership test: ``if k in c: c.pop(k)``; ``setdefault`` is atomic and returns the winner, so it is not an act.
_MUTATORS = frozenset({"update", "pop", "popitem"})


@dataclass(frozen=True)
class _Access:
    key: _Key
    line: int
    locks: frozenset[int]  # ids of the lock-with blocks that enclose the access
    guarded: bool = False  # a removal under a lock, itself inside an `if` of that block: it re-decides under the lock


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
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"}:
        return ("self", node.attr)
    if isinstance(node, ast.Name) and node.id in module_dicts:
        return ("module", node.id)
    return None


def _render(key: _Key) -> str:
    return f"self.{key[1]}" if key[0] == "self" else key[1]


def _record_compare(node: ast.Compare, locks: frozenset[int], module_dicts: frozenset[str], checks: list[_Access]) -> None:
    for op, container in zip(node.ops, node.comparators):
        key = _key_of(container, module_dicts)
        if key is not None and isinstance(op, (ast.In, ast.NotIn)):
            checks.append(_Access(key, node.lineno, locks))


def _record_call(node: ast.Call, locks: frozenset[int], module_dicts: frozenset[str], checks: list[_Access], acts: list[_Access], guarded: bool) -> None:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return
    key = _key_of(func.value, module_dicts)
    if key is None:
        return
    if func.attr == "get":
        checks.append(_Access(key, node.lineno, locks))
    elif func.attr in _MUTATORS:
        idempotent = func.attr == "pop" and (len(node.args) >= 2 or bool(node.keywords))  # `d.pop(k, None)` cannot fail or double-act
        if not idempotent:
            acts.append(_Access(key, node.lineno, locks, guarded and bool(locks)))


def _record_write(
    node: Union[ast.Delete, ast.Assign, ast.AugAssign, ast.AnnAssign], locks: frozenset[int], module_dicts: frozenset[str], acts: list[_Access], guarded: bool
) -> None:
    """A ``del d[k]`` (guarded when it re-decides under the lock) or a subscript assignment."""
    if isinstance(node, ast.Delete):
        keys = (_key_of(t.value, module_dicts) for t in node.targets if isinstance(t, ast.Subscript))
        acts.extend(_Access(key, node.lineno, locks, guarded and bool(locks)) for key in keys if key is not None)
        return
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Subscript):
            key = _key_of(target.value, module_dicts)
            if key is not None:
                acts.append(_Access(key, node.lineno, locks))


def _record(node: ast.AST, locks: frozenset[int], module_dicts: frozenset[str], checks: list[_Access], acts: list[_Access], guarded: bool = False) -> None:
    """Sort one node into a check (a membership test or ``get`` on a key) or an act (a subscript write, ``del`` or mutator call on a key)."""
    if isinstance(node, ast.Compare):
        _record_compare(node, locks, module_dicts, checks)
    elif isinstance(node, ast.Call):
        _record_call(node, locks, module_dicts, checks, acts, guarded)
    elif isinstance(node, (ast.Delete, ast.Assign, ast.AugAssign, ast.AnnAssign)):
        _record_write(node, locks, module_dicts, acts, guarded)


def _accesses(fn: ast.AST, module_dicts: frozenset[str], lock_names: frozenset[str]) -> tuple[list[_Access], list[_Access], bool]:
    """Checks, acts and whether the function holds a lock itself, for the statements of *fn* (nested scopes excluded)."""
    checks: list[_Access] = []
    acts: list[_Access] = []
    holds_lock = False

    def visit(node: ast.AST, locks: frozenset[int], guarded: bool = False) -> None:
        nonlocal holds_lock
        for child in child_nodes(node):
            if isinstance(child, _SCOPES):
                continue
            inner = locks
            branch = guarded or (isinstance(node, ast.If) and child is not node.test and bool(locks))
            if isinstance(child, _WITH) and any(is_lock_like(item.context_expr, lock_names) for item in child.items):
                inner = locks | {id(child)}
                holds_lock = True
            if isinstance(child, _RELEVANT):
                _record(child, inner, module_dicts, checks, acts, branch)
            visit(child, inner, branch)

    visit(fn, frozenset())
    return checks, acts, holds_lock


_CALLER_HOLDS = re.compile(r"caller\s+(?:must\s+)?(?:holds?|has|owns)|with\s+the\s+lock\s+held|lock\s+must\s+be\s+held", re.IGNORECASE)


def _caller_holds_lock(fn: ast.AST) -> bool:
    """A helper that documents (name ``*_unlocked``/``*_locked`` or docstring "caller holds the lock") that its caller owns the lock."""
    name = getattr(fn, "name", "")
    if name.endswith(("_unlocked", "_locked")):
        return True
    doc = ast.get_docstring(fn) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) else None  # type: ignore[arg-type]
    return bool(doc and _CALLER_HOLDS.search(doc))


def _findings_in_function(fn: ast.AST, rel: str, module_dicts: frozenset[str], lock_names: frozenset[str], module_has_lock: bool) -> list[Finding]:
    if _caller_holds_lock(fn):
        return []
    checks, acts, holds_lock = _accesses(fn, module_dicts, lock_names)
    if not (module_has_lock or holds_lock):
        return []
    out: list[Finding] = []
    name = getattr(fn, "name", "<function>")
    for act in acts:
        for check in checks:
            if act.guarded and check.locks:
                continue  # `with L: e = d.get(k)` ... `with L: if e.refcount == 0: del d[k]`: the removal re-decides under the lock
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


def _functions_with_with(tree: ast.Module) -> frozenset[int]:
    """Ids of the functions that contain a ``with`` anywhere in their body, nested functions included: one pass over the ``with``
    statements, climbing the shared parent map, instead of a walk of every function's subtree."""
    parents = parent_map(tree)
    out: set[int] = set()
    for node in nodes_of(tree, ast.With, ast.AsyncWith):
        cur = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(id(cur))
            cur = parents.get(id(cur))
    return frozenset(out)


def _findings_in(parsed: ParsedFile) -> list[tuple[Finding, str]]:
    module_dicts = _module_dicts(parsed.tree)
    lock_names = module_lock_names(parsed.tree)
    module_has_lock = bool(lock_names)
    out: list[tuple[Finding, str]] = []
    with_fns = tree_memo(parsed.tree, "check_then_act.functions_with_with", lambda: _functions_with_with(parsed.tree))
    for fn in nodes_of(parsed.tree, ast.FunctionDef, ast.AsyncFunctionDef):  # the index query: same defs, same order as function_nodes
        if not module_has_lock and id(fn) not in with_fns:
            continue  # no lock anywhere in the module and no `with` in this function: no finding is possible
        name = getattr(fn, "name", "<function>")
        out.extend((f, name) for f in _findings_in_function(fn, parsed.rel, module_dicts, lock_names, module_has_lock))
    return out


def _validate_exempt(exempt: Mapping[tuple[str, str], str]) -> None:
    for key, reason in exempt.items():
        if not (isinstance(key, tuple) and len(key) == 2 and all(isinstance(part, str) and part for part in key)):
            raise ValueError(f"exemption key must be (path suffix, function name), got {key!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"exemption {key!r} needs a non-empty reason")


def _unexempt_and_matched(found: list[tuple[Finding, str]], exempt: Mapping[tuple[str, str], str]) -> tuple[list[Finding], set[tuple[str, str]]]:
    """The findings no exemption covers, and the exemption keys that covered at least one finding."""
    unexempt: list[Finding] = []
    matched: set[tuple[str, str]] = set()
    for finding, name in found:
        hit = next((key for key in exempt if finding.path.replace("\\", "/").endswith(key[0]) and key[1] == name), None)
        if hit is None:
            unexempt.append(finding)
        else:
            matched.add(hit)
    return unexempt, matched


def _scan(files: Iterable[Union[str, Path]], min_files: int, allow_unparsed: bool, use_git: Optional[bool]) -> list[tuple[Finding, str]]:
    scan = scan_python(list(files), min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[tuple[Finding, str]] = []
    for parsed in scan:
        out.extend(_findings_in(parsed))
    return sorted(out, key=lambda pair: (pair[0].path, pair[0].line))


def find_check_then_act_outside_lock(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exempt: Optional[Mapping[tuple[str, str], str]] = None,
) -> list[Finding]:
    """Every check-then-act pair on a shared dict in *files* that no common lock covers, sorted by path and line.

    *exempt* maps ``(path suffix, function name)`` to the reason the site is accepted, and drops the matching findings.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for an unreadable file (unless
    *allow_unparsed*).
    """
    exempt = exempt or {}
    _validate_exempt(exempt)
    unexempt, _matched = _unexempt_and_matched(_scan(files, min_files, allow_unparsed, use_git), exempt)
    return unexempt


def assert_no_check_then_act_outside_lock(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exempt: Optional[Mapping[tuple[str, str], str]] = None,
) -> None:
    """Fail on any check-then-act on a shared dict that no single lock covers and no *exempt* entry accepts.

    An exemption that matches no finding also fails, so an exempt site that gets fixed (or moves) is removed rather than left
    as a silent allowance.
    """
    exempt = exempt or {}
    _validate_exempt(exempt)
    unexempt, matched = _unexempt_and_matched(_scan(files, min_files, allow_unparsed, use_git), exempt)
    if unexempt:
        raise AssertionError(
            f"{len(unexempt)} check-then-act-outside-lock finding(s): a test and a write on a shared dict need one lock across both:\n"
            + "\n".join(f.render() for f in unexempt)
        )
    stale = sorted(set(exempt) - matched)
    if stale:
        raise AssertionError(f"exemptions match no finding, remove them: {stale}")
