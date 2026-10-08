"""Module-level mutable state that production code writes and no test ever resets or replaces.

WHY. A module-level ``_CACHE = {}`` that a function fills is process-lifetime state. The first test that exercises that function leaves entries behind and the
next test sees them: the suite passes in one order and fails in another, or passes alone and fails in the batch. Every such name needs a test-side reset
(``mod._CACHE.clear()``, ``monkeypatch.setattr(mod, "_CACHE", {})``, an autouse fixture) -- and a name NO test file mentions at all certainly has none.

WHAT IT REPORTS. A module-level name bound to a mutable container (a ``{}``/``[]``/``{...}``/``[...]`` literal, a comprehension, or ``dict()``, ``list()``,
``set()``, ``defaultdict()``, ``OrderedDict()``, ``deque()``, ``Counter()``) that a FUNCTION of the same module mutates (``name[k] = v``, ``del name[k]``,
``name.append/add/update/extend/setdefault/pop/clear/...``, ``name += ...``, or ``global name`` followed by a rebinding), and whose name appears in no file of
*tests_root* -- neither as an attribute (``mod.NAME``), a string (``monkeypatch.setattr(mod, "NAME", ...)``) nor an imported name. A name that tests mention is
taken to be handled: whether the mention really resets it is for review; this gate finds the state nobody even looked at.

NOT REPORTED. Names never mutated from a function (a constant table built at import), and mutations at module level (they run once, at import).

Usage in a consumer's meta test::

    from py_ci_shared.module_state_test_reset import assert_module_state_test_reset

    def test_module_state_has_a_test_side_reset():
        assert_module_state_test_reset(ROOT, ROOT / "tests", baseline_path=HERE / "_module_state_baseline.json")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import DEFAULT_EXCLUDE, Baseline, Finding, scan_python

__all__ = ["RULE", "assert_module_state_test_reset", "find_module_state_test_reset"]

RULE = "module-state-no-test-reset"

_CONTAINER_CALLS = frozenset({"dict", "list", "set", "defaultdict", "OrderedDict", "deque", "Counter"})
_MUTATORS = frozenset({"append", "add", "update", "extend", "setdefault", "pop", "popitem", "remove", "discard", "insert", "appendleft", "popleft", "clear"})
_NOT_PRODUCTION = frozenset({"tests", "test", "testing", "scripts", "probes", "benchmarks", "examples", "docs", "migrations"})


def _is_mutable_container(value: ast.expr | None) -> bool:
    """Whether *value* builds an empty-or-not mutable container."""
    if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        return name in _CONTAINER_CALLS
    return False


def _module_containers(tree: ast.Module) -> dict[str, int]:
    """Module-level names bound to a mutable container, with the line of the binding."""
    out: dict[str, int] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and _is_mutable_container(stmt.value):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, stmt.lineno)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and _is_mutable_container(stmt.value):
            out.setdefault(stmt.target.id, stmt.lineno)
    return out


def _mutated_in_functions(tree: ast.Module, names: Iterable[str]) -> set[str]:
    """The *names* that some function in the module writes to."""
    wanted = set(names)
    hit: set[str] = set()
    for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        declared_global: set[str] = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Global):
                declared_global.update(node.names)
        for node in ast.walk(func):
            target: ast.expr | None = None
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
                targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                        target = t.value
                    elif isinstance(t, ast.Name) and t.id in declared_global:
                        target = t
                    elif isinstance(node, ast.AugAssign) and isinstance(t, ast.Name) and t.id in declared_global:
                        target = t
                    if isinstance(target, ast.Name) and target.id in wanted:
                        hit.add(target.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATORS and isinstance(node.func.value, ast.Name):
                if node.func.value.id in wanted:
                    hit.add(node.func.value.id)
    return hit


def _names_tests_mention(tests_root: Union[str, Path], *, use_git: Optional[bool]) -> set[str]:
    """Every attribute name, string constant and imported name in the test files."""
    scan = scan_python(tests_root, min_files=1, exclude=DEFAULT_EXCLUDE - {"tests", "test"}, use_git=use_git)
    scan.assert_ok(allow_unparsed=True)
    seen: set[str] = set()
    for parsed in scan:
        for node in ast.walk(parsed.tree):
            if isinstance(node, ast.Attribute):
                seen.add(node.attr)
            elif isinstance(node, ast.Name):
                seen.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
                seen.add(node.value)
            elif isinstance(node, ast.alias):
                seen.add(node.name.split(".")[-1])
    return seen


def find_module_state_test_reset(
    root: Union[str, Path],
    tests_root: Union[str, Path],
    *,
    exclude: Iterable[str] = _NOT_PRODUCTION,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every module-level mutable container a function writes and no file under *tests_root* mentions, sorted by path and line.

    *root* is the production tree; directories named in *exclude* are skipped. Raises ``EmptyScanError`` when fewer than *min_files* production files parsed and
    ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*). The tests directory must hold at least one parsable file.
    """
    scan = scan_python(root, min_files=min_files, exclude=DEFAULT_EXCLUDE | frozenset(exclude), use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    mentioned = _names_tests_mention(tests_root, use_git=use_git)
    out: list[Finding] = []
    for parsed in scan:
        containers = _module_containers(parsed.tree)
        out.extend(
            Finding(
                parsed.rel,
                containers[name],
                RULE,
                f"module-level `{name}` is mutated by a function and no test mentions it, so nothing resets it between tests",
            )
            for name in sorted(_mutated_in_functions(parsed.tree, containers))
            if name not in mentioned
        )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_module_state_test_reset(
    root: Union[str, Path],
    tests_root: Union[str, Path],
    *,
    exclude: Iterable[str] = _NOT_PRODUCTION,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_module_state_test_reset(root, tests_root, exclude=exclude, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "add a test-side reset (a fixture that clears it, or monkeypatch.setattr(module, NAME, ...)), or baseline it with the reason it is safe"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="module_state_test_reset", refresh_command="PY_CI_SHARED_REFRESH=module_state_test_reset")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} module-state-no-test-reset finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
