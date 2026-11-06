"""``sys.exit()`` / ``raise SystemExit`` inside a function that is not a script entry: a library helper that a background thread can reach.

``SystemExit`` is not an ``Exception``, so ``except Exception`` lets it through and it ends only the thread that raised it: a dashboard helper
written for a CLI (``_get_dsn`` calling ``sys.exit(1)``) was later imported and called from background threads, and a missing DSN silently killed
the cache scheduler and the health alerter while the service looked alive.

Reported, for a ``sys.exit``/``exit``/``quit``/``os._exit`` call or a ``raise SystemExit`` inside a function, when EITHER the module has no
``if __name__ == "__main__":`` guard (it is a library) OR the function's name is imported by name from another scanned module (a CLI helper
that became shared).

Test files (``tests/`` directories, ``test_*.py``, ``conftest.py``) are skipped unless *include_tests*.

Not reported: functions named like an entry (*entry_names* regex, default anchored: exactly ``main``/``cli``/``run_cli``/``parse_args``/``entry`` (leading underscores allowed) or a ``main_``/``cli_`` prefix, so ``get_domain_dsn`` and ``client_config`` are not exempt); a function with a
parameter whose name matches *opt_in_param_regex* (default ``exit``: an opt-in ``exit_on_fail`` flag); a line carrying
``# exit-ok: <reason>`` (on the line or the line above); an exit at module level inside the guard.

Usage::

    from py_ci_shared.systemexit_in_library import assert_no_systemexit_in_library

    def test_no_systemexit_in_library():
        assert_no_systemexit_in_library(REPO / "src")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, child_nodes, scan_python, walk

__all__ = ["RULE", "assert_no_systemexit_in_library", "find_systemexit_in_library"]

RULE = "systemexit-in-library"
MARKER = "exit-ok"
DEFAULT_ENTRY_NAMES = r"^_*(?:main|cli|run_cli|parse_args|entry|entrypoint)$|^_*(?:main|cli)_"
DEFAULT_OPT_IN_PARAM = r"exit"
_EXIT_CALLS = frozenset({"exit", "quit", "_exit"})


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            names = [node.test.left, *node.test.comparators]
            if any(isinstance(n, ast.Name) and n.id == "__name__" for n in names) and any(isinstance(n, ast.Constant) and n.value == "__main__" for n in names):
                return True
    return False


def _imported_names(trees: dict) -> dict:
    """``{name: {rel paths that import it by name}}`` over every scanned module."""
    out: dict = {}
    for rel, tree in trees.items():
        for node in walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    out.setdefault(alias.name, set()).add(rel)
    return out


def _exit_kind(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _EXIT_CALLS and isinstance(func.value, ast.Name) and func.value.id in {"sys", "os", "builtins"}:
            return f"{func.value.id}.{func.attr}()"
        if isinstance(func, ast.Name) and func.id in _EXIT_CALLS:
            return f"{func.id}()"
    if isinstance(node, ast.Raise) and node.exc is not None:
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(target, ast.Name) and target.id == "SystemExit":
            return "raise SystemExit"
    return None


def _function_exits(fn: ast.AST) -> list:
    out: list = []
    stack = list(child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        kind = _exit_kind(node)
        if kind is not None:
            out.append((node, kind))
        stack.extend(child_nodes(node))
    return out


def _marked(lines: list, line: int) -> bool:
    return any(0 < n <= len(lines) and "#" in lines[n - 1] and MARKER in lines[n - 1].split("#", 1)[1] for n in (line, line - 1))


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    return "tests" in parts[:-1] or parts[-1].startswith("test_") or parts[-1] == "conftest.py"


def _params(fn: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> list:
    a = fn.args
    return [p.arg for p in [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []), *([a.kwarg] if a.kwarg else [])]]


def find_systemexit_in_library(
    root: Union[str, Path],
    *,
    entry_names: str = DEFAULT_ENTRY_NAMES,
    opt_in_param_regex: str = DEFAULT_OPT_IN_PARAM,
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every exit inside a library function under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    parsed_files = list(scan)
    imported = _imported_names({p.rel: p.tree for p in parsed_files})
    entry, opt_in = re.compile(entry_names, re.IGNORECASE), re.compile(opt_in_param_regex, re.IGNORECASE)
    out: list[Finding] = []
    for parsed in parsed_files:
        if not include_tests and _is_test_path(parsed.rel):
            continue
        guarded = _has_main_guard(parsed.tree)
        lines = parsed.source.splitlines()
        for fn in walk(parsed.tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or entry.search(fn.name):
                continue
            if any(opt_in.search(p) for p in _params(fn)):
                continue
            shared = bool(imported.get(fn.name, set()) - {parsed.rel})
            if guarded and not shared:
                continue
            why = "imported by another module" if guarded else "the module has no __main__ guard"
            for node, kind in _function_exits(fn):
                if _marked(lines, node.lineno):
                    continue
                out.append(
                    Finding(
                        parsed.rel,
                        node.lineno,
                        RULE,
                        f"{fn.name}: {kind} in a library function ({why}); SystemExit passes `except Exception` and ends only the calling thread. "
                        "Raise a normal exception and exit in main()",
                    )
                )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_no_systemexit_in_library(
    root: Union[str, Path],
    *,
    entry_names: str = DEFAULT_ENTRY_NAMES,
    opt_in_param_regex: str = DEFAULT_OPT_IN_PARAM,
    include_tests: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any exit inside a library function, or with *baseline_path* on any the baseline does not accept."""
    found = find_systemexit_in_library(
        root,
        entry_names=entry_names,
        opt_in_param_regex=opt_in_param_regex,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "raise a normal exception from the helper and exit only in main() (or mark `# exit-ok: reason`)"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="systemexit_in_library", refresh_command="PY_CI_SHARED_REFRESH=systemexit_in_library")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} systemexit-in-library finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
