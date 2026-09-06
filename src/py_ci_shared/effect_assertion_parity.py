"""Shared check: a side effect the code performs must be one some test actually inspects.

WHERE THIS CAME FROM
--------------------
A mutation sweep of ``realtime_applications`` measured it. Deleting the statement ``cur.execute(...)``
went unnoticed 28 times across 11 files; deleting ``conn.commit()`` went unnoticed 14 times across 6;
deleting ``conn.rollback()`` 7 times across 5. The suite is 3,600 tests and green, and it stayed green
with the database writes removed.

The cause is not laziness. It is that a mock stands in for the database, the code calls it, and the
test then asserts on the RETURN VALUE -- which a mock supplies whether or not the call was made. The
effect is the whole point of those functions, and it is the one thing nothing looks at.

WHAT THIS CHECKS, AND WHAT IT CANNOT
------------------------------------
For every module that performs an effect (a call to ``commit``, ``rollback``, ``execute`` -- the set is
the caller's), at least one of the TESTS THAT IMPORT that module must inspect that effect on a mock:
``.commit.assert_called_once()``, ``.execute.call_args``, ``.rollback.called`` and so on.

It is a proxy, and a cheap one -- an AST walk over both sides, no execution. Mutation testing is what
actually measures whether a test would notice; this answers the far narrower question "does anything
even look?", in seconds rather than hours, which is what makes it usable in a hook.

Two limits, stated rather than papered over:

* **A pass is not proof.** A test can assert ``execute.called`` and check nothing about the SQL it was
  given. That satisfies this check and would still miss a mutated query.
* **A failure is not always a defect.** A module may be exercised end-to-end against a real database
  in an integration test that never touches a mock. Such modules belong in the baseline with a note
  saying so, which is why the ratchet takes descriptions rather than a bare set of names.

The point is the direction: the list can only shrink, and every entry in it names a real effect that
no test currently inspects.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

__all__ = [
    "DEFAULT_EFFECTS",
    "assert_effects_are_asserted",
    "build_import_map",
    "find_unasserted_effects",
]

_SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"})

#: The effects worth pairing by default: a transaction boundary and a statement execution. Each is a
#: call whose entire purpose is what it does elsewhere, so a return-value assertion cannot see it.
DEFAULT_EFFECTS: tuple[str, ...] = ("commit", "rollback", "execute", "executemany", "execute_values")

#: How a test says it looked at a mock's call. ``called``/``call_count``/``call_args`` are reads;
#: ``assert_*`` are the assertion helpers ``unittest.mock`` provides.
_INSPECTIONS = ("called", "call_count", "call_args", "call_args_list", "mock_calls", "await_args")


def _performs(path: Path, effects: Sequence[str]) -> set[str]:
    """Effects this module performs, as method calls: ``conn.commit()``, ``cur.execute(sql)``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in effects:
            found.add(node.func.attr)
        # A bare call, `execute_values(cur, sql, rows)`, is an effect too.
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in effects:
            found.add(node.func.id)
    return found


def _inspects(path: Path, effects: Sequence[str]) -> set[str]:
    """Effects this test inspects on a mock: ``x.commit.assert_called()``, ``x.execute.call_args``.

    Matched on the ATTRIBUTE CHAIN rather than on text, so ``# commit is asserted below`` in a comment
    and a local variable called ``commit`` are both correctly ignored.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not (node.attr.startswith("assert_") or node.attr in _INSPECTIONS):
            continue
        target = node.value
        # `conn.commit.assert_called_once()` -- the mock reached through the object it patches.
        if isinstance(target, ast.Attribute) and target.attr in effects:
            found.add(target.attr)
        # `with patch.object(mod, "execute_values") as execute_values: ... execute_values.assert_called()`
        # binds the mock to a BARE NAME, and that is the commoner idiom for a module-level function.
        # Matching only the attribute chain missed it, and the check then reported an effect as
        # uninspected while a test three lines long was inspecting it.
        elif isinstance(target, ast.Name) and target.id in effects:
            found.add(target.id)
    return found


def _imported_names(path: Path) -> set[str]:
    """Every dotted name the file imports, in both forms.

    `from pipeline import replay` names a MODULE when the submodule exists, so `pipeline.replay` is
    emitted alongside `pipeline` and the caller resolves both against the real file set.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def build_import_map(repo_root: Path, *, package_name: str = "", src_dir: str = "") -> dict[str, list[str]]:
    """``{module path: [test paths importing it]}``, both relative to *repo_root*.

    Import edges rather than measured coverage, and deliberately so. Coverage attributes an executed
    line to the test that was running, but a module's top level runs during COLLECTION, so a test that
    imports a module and reads its constants executes none of its lines and would not appear. For
    "does any test even look at this module", the import edge is the honest relation.

    One level of transitivity through the project's own modules, so a test importing ``pipeline`` is
    credited with ``pipeline/replay.py`` as well. *package_name* covers repositories whose tests
    import themselves as a package (``from dashboard import data``); *src_dir* covers a src layout,
    where the file at ``src/pkg/x.py`` is imported as ``pkg.x``.
    """

    def module_name(path: Path) -> str:
        rel = path.relative_to(repo_root).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    sources = [
        p
        for p in repo_root.rglob("*.py")
        if not _SKIP_DIRS & set(p.parts) and "tests" not in p.relative_to(repo_root).parts
    ]
    tests = [p for p in repo_root.rglob("test_*.py") if not _SKIP_DIRS & set(p.parts)]

    by_module: dict[str, Path] = {}
    for path in sources:
        flat = module_name(path)
        by_module[flat] = path
        if package_name:
            by_module[f"{package_name}.{flat}" if flat else package_name] = path
        if src_dir and flat.startswith(f"{src_dir}."):
            by_module[flat[len(src_dir) + 1 :]] = path

    own_edges = {name: {i for i in _imported_names(path) if i in by_module} for name, path in by_module.items()}

    hits: dict[str, set[str]] = {}
    for test in tests:
        reached = {i for i in _imported_names(test) if i in by_module}
        reached |= {n for m in list(reached) for n in own_edges.get(m, set())}
        for name in reached:
            hits.setdefault(by_module[name].relative_to(repo_root).as_posix(), set()).add(
                test.relative_to(repo_root).as_posix()
            )
    return {source: sorted(found) for source, found in sorted(hits.items())}


def find_unasserted_effects(
    repo_root: Path,
    import_map: Mapping[str, Sequence[str]],
    *,
    effects: Sequence[str] = DEFAULT_EFFECTS,
) -> dict[str, str]:
    """``{"<module>::<effect>": description}`` for effects no importing test inspects.

    *import_map* is ``{module path: [test paths that import it]}``, both relative to *repo_root*. The
    importing tests are the right population rather than all tests: a suite-wide "somebody somewhere
    asserts on commit" would be satisfied by one unrelated test and would gate nothing.
    """
    inspected: dict[str, set[str]] = {}
    problems: dict[str, str] = {}

    for module, tests in sorted(import_map.items()):
        module_path = repo_root / module
        if not module_path.is_file():
            continue
        performed = _performs(module_path, effects)
        if not performed:
            continue
        for effect in sorted(performed):
            checked_by = None
            for test in tests:
                test_path = repo_root / test
                if not test_path.is_file():
                    continue
                if test not in inspected:
                    inspected[test] = _inspects(test_path, effects)
                if effect in inspected[test]:
                    checked_by = test
                    break
            if checked_by is None:
                problems[f"{module}::{effect}"] = (
                    f"{module} calls `{effect}(...)` and none of its {len(tests)} importing test(s) "
                    f"ever inspects that call, so deleting it would not fail a single one"
                )
    return problems


def assert_effects_are_asserted(
    repo_root: Path,
    import_map: Mapping[str, Sequence[str]],
    accepted: Iterable[str] = (),
    *,
    effects: Sequence[str] = DEFAULT_EFFECTS,
) -> None:
    """Fail on an unasserted effect that is not already accepted. Ratchet, not a gate.

    *accepted* is the baseline: what was already true when the check was wired. Existing debt does not
    block a commit, a NEW unasserted effect does, and the set can only shrink.
    """
    import pytest

    found = find_unasserted_effects(repo_root, import_map, effects=effects)
    accepted = set(accepted)
    new = {key: why for key, why in found.items() if key not in accepted}
    if new:
        pytest.fail(
            f"{len(new)} effect(s) performed but inspected by no importing test:\n  "
            + "\n  ".join(f"{key}: {why}" for key, why in sorted(new.items()))
        )
