"""A top-level ``def``/``class`` that follows the script's ``if __name__ == "__main__":`` entry guard.

Run as a script, Python executes the module top to bottom; the guard body runs the moment it is reached, so a name defined BELOW it does not exist
yet and the first use raises ``NameError`` (a scraper rescan loop died at its first cycle on ``_record_cycle_run`` this way, 18 startup rows in
three hours). Under pytest the module is imported, every ``def`` runs first, and no test can see it.

Reported: a module-level ``if __name__ == "__main__":`` (either operand order, ``==``) whose body CALLS a bare name that is not a builtin
(``main()``, ``sys.exit(main())``, ``asyncio.run(main())``), followed at module level by a ``def``, ``async def`` or ``class``. One finding per
late definition.

Not reported: a guard that only manipulates ``sys.modules``/``sys.path`` or calls attributes (``sys.path.insert(...)``): it is not an entry;
a guard that is the last statement; definitions nested in the guard body (they precede their own use).

Usage::

    from py_ci_shared.definition_after_entry_guard import assert_definition_after_entry_guard

    def test_no_definition_after_the_script_entry():
        assert_definition_after_entry_guard(REPO / "scripts")
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python, walk

__all__ = ["RULE", "assert_definition_after_entry_guard", "find_definitions_after_entry_guard"]

RULE = "definition-after-entry-guard"
_BUILTINS = frozenset(dir(builtins))


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    sides = [test.left, test.comparators[0]]
    names = any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
    literal = any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)
    return names and literal


def _calls_a_module_name(guard: ast.If) -> bool:
    for stmt in guard.body:
        for node in walk(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id not in _BUILTINS:
                return True
    return False


def _findings_in(tree: ast.Module, rel: str) -> list:
    guards = [i for i, node in enumerate(tree.body) if _is_main_guard(node) and _calls_a_module_name(node)]  # type: ignore[arg-type]
    if not guards:
        return []
    first = guards[0]
    guard = tree.body[first]
    late = [n for n in tree.body[first + 1 :] if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return [
        Finding(
            rel,
            node.lineno,
            RULE,
            f"{node.name} is defined after the `if __name__ == '__main__'` entry guard (line {guard.lineno}); "
            "run as a script the guard body reaches it before it exists (NameError). Move the guard to the end of the module",
        )
        for node in late
    ]


def find_definitions_after_entry_guard(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every definition below an entry guard under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_definition_after_entry_guard(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any definition below an entry guard, or with *baseline_path* on any the baseline does not accept."""
    found = find_definitions_after_entry_guard(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "move the `if __name__ == '__main__':` guard to the end of the module"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="definition_after_entry_guard", refresh_command="PY_CI_SHARED_REFRESH=definition_after_entry_guard")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} definition-after-entry-guard finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
