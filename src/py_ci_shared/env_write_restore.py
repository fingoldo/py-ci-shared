"""A test restores every ``os.environ`` write it makes.

THE DEFECT. A test function that assigns ``os.environ["X"] = "1"`` (or ``update``/``setdefault``/``pop``, ``del``, ``os.putenv``) and never
puts the variable back leaks the setting into every test that runs after it in the same process. Which tests those are depends on the
ordering and on pytest-xdist's distribution, so the victim is a different innocent test each run and it passes when run alone -- the most
expensive kind of failure to chase. mlframe: a test set ``MLFRAME_FE_GPU_STRICT=1``, ``MLFRAME_FE_GPU_STRICT_RESIDENT=1`` and
``MLFRAME_CMI_GPU=1`` directly; the next module's "host" arm then ran in strict-GPU mode and a bit-identity test failed 4 runs in 5.
:mod:`py_ci_shared.import_side_effects` covers the import-time half (a module-level write); this gate covers the write inside a function.

WHAT IS REPORTED. A function whose own body writes the environment, where the write is not restored by one of the shapes below. One
finding per write statement, keyed ``rule::path::function: statement`` (no line number, so an edit above does not re-report it).

WHAT IS NOT REPORTED (the write is restored or scoped):

* inside ``with mock.patch.dict(os.environ, ...)`` / ``with monkeypatch.context()``;
* in a function with a ``try`` whose ``finally`` writes the environment again (the write may sit just before the ``try``);
* in a generator that writes the environment again after its first ``yield`` (a fixture or ``@contextmanager`` that sets, yields and restores);
* in a ``setUp``/``setup_method``/``setup_class`` whose class also has a ``tearDown``/``teardown_method``/``teardown_class`` that writes it back;
* in a function that registers ``request.addfinalizer`` / ``addCleanup``;
* in a function whose NAME says it restores or resets (``_restore_env``, ``reset_env``, ``teardown``).

``monkeypatch.setenv`` and ``monkeypatch.delenv`` are not writes to ``os.environ`` as this gate sees them: pytest undoes them.

Usage in a consumer's meta test::

    from py_ci_shared.env_write_restore import assert_env_write_restore

    def test_tests_restore_the_environment(request):
        assert_env_write_restore("tests", baseline_path=BASELINE, min_files=100)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from collections.abc import Iterator
from typing import Any, Optional, Union

from ._core import Baseline, Finding, ImportAliases, refresh_requested, scan_python
from .import_side_effects import _ENV_WRITERS, _MUTATING_METHODS, _is_environ

__all__ = ["DEFAULT_NOTE", "REFRESH_FLAG", "RULE", "assert_env_write_restore", "find_env_write_restore"]

RULE = "env-write-restore"
DEFAULT_NOTE = "pre-existing when the gate was adopted; not yet triaged"
REFRESH_FLAG = "--refresh-env-write-restore-baseline"

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_SETUP_NAMES = frozenset({"setUp", "setUpClass", "setup_method", "setup_class", "setup_function", "setup_module", "setup", "asyncSetUp"})
_TEARDOWN_NAMES = frozenset(
    {"tearDown", "tearDownClass", "teardown_method", "teardown_class", "teardown_function", "teardown_module", "teardown", "asyncTearDown"}
)
# A function whose job is to put the environment back: its own writes ARE the restoration.
_RESTORER_NAME = re.compile(r"(restor|reset|clean_?up|tear_?down|finali[sz]|revert|unset)", re.IGNORECASE)
_SCOPING_CALLS = ("patch.dict", "monkeypatch.context")
_FINALIZER_ATTRS = frozenset({"addfinalizer", "addCleanup", "enterContext", "callback"})
_MAX_TEXT = 120


def _own_nodes(fn: ast.AST) -> Iterator[ast.AST]:
    """Every node of *fn*'s body that belongs to *fn* itself: nested functions, classes and lambdas are their own scopes."""
    stack: list[ast.AST] = list(getattr(fn, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPE_NODES):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _env_write_text(node: ast.AST, aliases: ImportAliases) -> Optional[str]:
    """The text of the statement when *node* writes ``os.environ`` (assignment, ``del``, a mutating method, ``putenv``/``unsetenv``)."""
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(t, ast.Subscript) and _is_environ(t.value, aliases) for t in targets):
            return ast.unparse(node)[:_MAX_TEXT]
    elif isinstance(node, ast.Delete):
        if any(isinstance(t, ast.Subscript) and _is_environ(t.value, aliases) for t in node.targets):
            return ast.unparse(node)[:_MAX_TEXT]
    elif isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in _MUTATING_METHODS and _is_environ(f.value, aliases):
            return ast.unparse(node)[:_MAX_TEXT]
        if aliases.qualified_name(f) in _ENV_WRITERS or (isinstance(f, ast.Attribute) and f.attr in ("putenv", "unsetenv")):
            return ast.unparse(node)[:_MAX_TEXT]
    return None


def _writes(fn: ast.AST, aliases: ImportAliases) -> list[tuple[int, str]]:
    return sorted((getattr(n, "lineno", 0), t) for n in _own_nodes(fn) if (t := _env_write_text(n, aliases)) is not None)


def _end(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None)
    return int(end) if end is not None else int(getattr(node, "lineno", 0))


def _is_scoping_call(expr: ast.AST) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    try:
        text = ast.unparse(expr.func)
    except Exception:  # pragma: no cover - unparse of an odd node
        return False
    return any(text == name or text.endswith("." + name) for name in _SCOPING_CALLS)


def _protected_ranges(fn: ast.AST, aliases: ImportAliases, write_lines: list[int]) -> list[tuple[int, int]]:
    """Line ranges inside which a write is scoped or restored: a ``patch.dict`` / ``monkeypatch.context`` block, or the body of a ``try``
    whose ``finally`` writes the environment, and the whole of a generator that writes the environment again AFTER its first ``yield`` (a fixture or
    ``@contextmanager`` that sets, yields and puts back); a generator that only sets before the yield is not protected."""
    ranges: list[tuple[int, int]] = []
    first_yield: Optional[int] = None
    for node in _own_nodes(fn):
        if isinstance(node, (ast.With, ast.AsyncWith)) and any(_is_scoping_call(item.context_expr) for item in node.items):
            ranges.append((node.lineno, _end(node)))
        elif isinstance(node, ast.Try) and node.finalbody and any(_env_write_text(n, aliases) for s in node.finalbody for n in ast.walk(s)):
            # From the top of the function: the usual shape sets the variable just BEFORE the try whose finally puts it back.
            ranges.append((getattr(fn, "lineno", node.lineno), _end(node)))
        elif isinstance(node, (ast.Yield, ast.YieldFrom)):
            line = getattr(node, "lineno", 0)
            first_yield = line if first_yield is None else min(first_yield, line)
    if first_yield is not None and any(line > first_yield for line in write_lines):
        ranges.append((0, 10**9))
    return ranges


def _registers_finalizer(fn: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _FINALIZER_ATTRS for n in _own_nodes(fn))


def _functions(tree: ast.AST) -> Iterator[tuple[str, ast.AST, Optional[ast.ClassDef]]]:
    """``(qualified name, function node, the class that directly owns it or None)`` for every function in the module."""

    def walk(node: ast.AST, prefix: str, owner: Optional[ast.ClassDef]) -> Iterator[tuple[str, ast.AST, Optional[ast.ClassDef]]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                yield qual, child, owner
                yield from walk(child, qual + ".", None)
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.", child)
            else:
                yield from walk(child, prefix, owner)

    yield from walk(tree, "", None)


def _class_restores(cls: Optional[ast.ClassDef], aliases: ImportAliases) -> bool:
    """Whether *cls* has a teardown method that writes the environment back."""
    if cls is None:
        return False
    return any(isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in _TEARDOWN_NAMES and _writes(m, aliases) for m in cls.body)


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases) -> list[Finding]:
    """The findings in one parsed file: each unrestored ``os.environ`` write inside a function."""
    out: list[Finding] = []
    for qual, fn, owner in _functions(tree):
        name = getattr(fn, "name", "")
        writes = _writes(fn, aliases)
        if not writes or _RESTORER_NAME.search(name) or name in _TEARDOWN_NAMES or _registers_finalizer(fn):
            continue
        if name in _SETUP_NAMES and _class_restores(owner, aliases):
            continue
        ranges = _protected_ranges(fn, aliases, [line for line, _ in writes])
        for line, text in writes:
            if any(lo <= line <= hi for lo, hi in ranges):
                continue
            out.append(
                Finding(
                    rel,
                    line,
                    RULE,
                    f"{qual}: {text}",
                )
            )
    return out


def find_env_write_restore(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every unrestored ``os.environ`` write inside a function under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree)))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_env_write_restore(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    grow: Optional[bool] = None,
    request: Any = None,
    new_note: str = DEFAULT_NOTE,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any unrestored write, or with *baseline_path* on any the baseline does not accept (and on a stale baseline entry).

    A refresh (*refresh*, ``--refresh-env-write-restore-baseline`` via *request* or argv, or ``PY_CI_SHARED_REFRESH=env-write-restore``) is shrink-only unless *grow* (or ``--py-ci-refresh-grow`` / ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``): seed a first baseline with it.
    *new_note* is written as the justification of each newly recorded entry."""
    found = find_env_write_restore(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = (
        "an os.environ write inside a function is never undone, so it leaks into every later test in the process (a different victim each run, "
        "passing when run alone). Use monkeypatch.setenv / monkeypatch.delenv (pytest restores them), `with mock.patch.dict(os.environ, {...})`, "
        "a yield-fixture that restores after the yield, or try/finally"
    )
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="env_write_restore", refresh_command="PY_CI_SHARED_REFRESH=env-write-restore", new_note=new_note)
        do_refresh = refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request)
        baseline.enforce(found, refresh=do_refresh, guidance=guidance, grow=grow, request=request).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} env-write-restore finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
