"""Exception handlers that swallow a broad or I/O failure and carry on with the state the failure left behind.

A handler whose whole body is ``pass``/``continue``/``...`` turns a failure into a silent wrong state (a DEBUG/INFO log
line counts as silent too with ``quiet_log_is_silent=True``). The instance that paid for this rule: a cache writer
staged to a temp name, ``np.save`` appended ``.npy`` to it, the ``os.replace`` raised, and ``except OSError: pass``
swallowed it, so the cache never landed and every process re-encoded for two days while 21.9 GB of orphans accumulated.

Scope: handlers catching everything (bare ``except:``, ``BaseException``, ``Exception``) or an I/O failure (``OSError``
and its aliases ``IOError``/``EnvironmentError``, ``PermissionError``, ``shutil.Error``), alone or in a tuple.
``FileNotFoundError`` alone is out of scope (idempotent delete is its normal use), and so are narrow lookups
(``KeyError``, ``ValueError``, ...), which are control flow far more often than failures.

Not reported:

* a handler that raises, returns a value, assigns, or does anything else: that is a decision, not a swallow;
* an optional-dependency probe: the ``try`` body only imports;
* a fallback: the ``try`` body returns a value on success, so a failure falls through to the code after it (read a
  cache, else rebuild);
* a metadata probe or touch of a listed file (``stat``/``getmtime``/``scandir``/``utime``...): it can vanish in
  between, and skipping it is the answer;
* best-effort cleanup: every call in the ``try`` body is a release-type method or function (``close``,
  ``unlink``, ``remove``, ``rmtree``, ``kill``, ``terminate``, ``shutdown``, ...), or the handler sits in a ``finally``
  block or in ``__del__``/``__exit__``/``__aexit__``/``close``/``emit`` (a logging handler must not log its own failure);
* a handler marked ``# swallow-ok: <reason>`` (or bandit's ``# nosec B110``/``B112``, which acknowledges exactly this
  shape) on the ``except`` line, the line above it, or its first body line.

Existing sites are ratcheted by the baseline. Usage::

    from py_ci_shared.swallowed_exceptions import assert_no_swallowed_exceptions

    def test_no_swallowed_exceptions(request):
        assert_no_swallowed_exceptions(REPO / "src", baseline_path=HERE / "_swallowed_exceptions_baseline.json", request=request)
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ImportAliases, ParsedFile, ScanResult, scan_python
from ._core.node_index import walk as _fast_walk
from ._gate_run import enforce_findings

__all__ = ["REFRESH_FLAG", "assert_no_swallowed_exceptions", "find_swallowed_exceptions", "is_silent_handler"]

REFRESH_FLAG = "--refresh-swallowed-exceptions-baseline"
GATE = "swallowed-exceptions"
RULE = "swallowed-exception"
_DEFAULT_EXCLUDE = frozenset({"tests", "test", "testing", "benchmarks", "bench", "_benchmarks", "examples", "scripts", "probes"})
_BROAD = frozenset({"BaseException", "Exception", "builtins.BaseException", "builtins.Exception"})
_IO = frozenset({"OSError", "IOError", "EnvironmentError", "PermissionError", "shutil.Error", "builtins.OSError", "builtins.PermissionError"})
_QUIET_LOG_LEVELS = frozenset({"debug", "info", "trace"})
_CLEANUP = frozenset(
    {"close", "aclose", "unlink", "remove", "rmdir", "rmtree", "removedirs", "kill", "terminate", "cancel", "shutdown", "disconnect"}
    | {
        "dispose",
        "release",
        "join",
        "flush",
        "stop",
        "cleanup",
        "detach",
        "unregister",
        "delete",
        "free",
        "wait",
        "__exit__",
        "rollback",
        "fsync",
        "killpg",
        "pop",
        "discard",
    }
)
_METADATA = frozenset({"stat", "lstat", "getmtime", "getsize", "getatime", "getctime", "utime", "touch", "scandir", "listdir", "iterdir"})
#: Calls that neither do the work nor fail it: predicates and conversions around the real call.
_NEUTRAL = frozenset(
    {"startswith", "endswith", "exists", "is_file", "is_dir", "isinstance", "hasattr", "getattr", "len", "str", "int", "fileno", "getpgid", "Path"}
)
_KILL_COMMANDS = frozenset({"taskkill", "kill", "pkill"})
_CLEANUP_SCOPES = frozenset({"__del__", "__exit__", "__aexit__", "close", "aclose", "emit"})
_MARKER = re.compile(r"#\s*swallow-ok:\s*\S|#\s*nosec\b.*\bB11[02]\b")


def _caught(handler: ast.ExceptHandler, aliases: ImportAliases) -> Optional[str]:
    """The in-scope type the handler catches (``"bare"`` for ``except:``), or None when it is out of scope."""
    if handler.type is None:
        return "bare"
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for t in types:
        name = aliases.qualified_name(t) or ""
        if name in _BROAD or name in _IO:
            return name.replace("builtins.", "")
    return None


def _is_quiet_log(stmt: ast.stmt) -> bool:
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Attribute)):
        return False
    return stmt.value.func.attr in _QUIET_LOG_LEVELS


def is_silent_handler(handler: ast.ExceptHandler, *, quiet_log_is_silent: bool = False) -> bool:
    """True when the body only passes, continues, breaks or holds a constant (and, with *quiet_log_is_silent*, when it
    also only logs at DEBUG/INFO)."""
    for stmt in handler.body:
        if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        if quiet_log_is_silent and _is_quiet_log(stmt):
            continue
        return False
    return True


def _call_name(node: ast.expr) -> str:
    func = node.func if isinstance(node, ast.Call) else node
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _is_import_probe(body: list[ast.stmt]) -> bool:
    """An optional-dependency probe: the ``try`` body only imports (and binds what it imported)."""
    return (
        bool(body)
        and any(isinstance(s, (ast.Import, ast.ImportFrom)) for s in body)
        and all(isinstance(s, (ast.Import, ast.ImportFrom, ast.Assign)) for s in body)
    )


def _is_kill_command(call: ast.Call) -> bool:
    """``subprocess.run(["taskkill", ...])``: a process kill spelled as a command line."""
    first = call.args[0] if call.args else None
    head = first.elts[0] if isinstance(first, (ast.List, ast.Tuple)) and first.elts else None
    return isinstance(head, ast.Constant) and head.value in _KILL_COMMANDS


def _outer_calls(expr: Optional[ast.AST]) -> list[str]:
    """The calls an expression MAKES: ``os.remove(self._path(k))`` is ``remove`` (its arguments only build the target),
    while a method chain ``entry.stat().st_size`` is ``stat``. Neutral predicates are dropped."""
    out: list[str] = []
    stack = [expr] if expr is not None else []
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            name = "kill" if _is_kill_command(node) else _call_name(node)
            if name not in _NEUTRAL and not name.endswith("_path"):  # a path builder names the target, it does no I/O
                out.append(name)
            if isinstance(node.func, ast.Attribute):
                stack.append(node.func.value)  # the receiver of a chained call, never the arguments
        elif isinstance(node, (ast.Lambda, ast.comprehension)):
            continue
        else:
            stack.extend(ast.iter_child_nodes(node))
    return out


def _calls(body: list[ast.stmt]) -> list[str]:
    """The effect calls of a statement list; ``if`` tests and loop targets do not count, their bodies do."""
    out: list[str] = []
    for stmt in body:
        if isinstance(stmt, (ast.If, ast.While)):
            out += _calls(stmt.body) + _calls(stmt.orelse)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            out += _outer_calls(stmt.iter) + _calls(stmt.body) + _calls(stmt.orelse)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            out += [c for item in stmt.items for c in _outer_calls(item.context_expr)] + _calls(stmt.body)
        elif isinstance(stmt, ast.Try):
            out += _calls(stmt.body) + _calls(stmt.orelse) + _calls(stmt.finalbody) + [c for h in stmt.handlers for c in _calls(h.body)]
        else:
            out += _outer_calls(stmt)
    return out


def _is_best_effort(body: list[ast.stmt]) -> bool:
    """Every effect call in the ``try`` body releases something or reads/touches file metadata (``os.remove(p);
    count += 1``, ``total += entry.stat().st_size``, a scandir-and-remove sweep)."""
    calls = _calls(body)
    allowed = _CLEANUP | _METADATA | ({"open"} if "fsync" in calls else set())  # os.open(dir) + os.fsync: best-effort durability
    return bool(calls) and all(c in allowed for c in calls)


def _is_fallback(body: list[ast.stmt]) -> bool:
    """The ``try`` body returns on success, so a failure falls through to the code after it (a cache read, then a rebuild)."""
    return any(isinstance(n, ast.Return) and n.value is not None for stmt in body for n in _fast_walk(stmt))


def _marked(lines: list[str], handler: ast.ExceptHandler) -> bool:
    first_body = handler.body[0].lineno if handler.body else handler.lineno
    for ln in {handler.lineno - 1, handler.lineno, first_body}:
        if 1 <= ln <= len(lines) and _MARKER.search(lines[ln - 1]):
            return True
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(self, f: ParsedFile, quiet_log_is_silent: bool) -> None:
        self.f = f
        self.quiet_log_is_silent = quiet_log_is_silent
        self.aliases = ImportAliases.from_tree(f.tree)
        self.lines = f.source.splitlines()
        self.scope: list[str] = []
        self.in_finally = 0
        self.findings: list[Finding] = []

    def _function(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        self.scope.append(node.name)
        saved, self.in_finally = self.in_finally, 0
        self.generic_visit(node)
        self.in_finally = saved
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Try(self, node: ast.Try) -> None:
        for handler in node.handlers:
            self._judge(node, handler)
        for stmt in node.body + node.orelse:
            self.visit(stmt)
        for handler in node.handlers:
            self.visit(handler)
        self.in_finally += 1
        for stmt in node.finalbody:
            self.visit(stmt)
        self.in_finally -= 1

    def visit_TryStar(self, node: ast.AST) -> None:
        self.visit_Try(node)  # type: ignore[arg-type]

    @staticmethod
    def _benign(body: list[ast.stmt]) -> bool:
        return _is_best_effort(body) or _is_import_probe(body) or _is_fallback(body)

    def _judge(self, node: ast.Try, handler: ast.ExceptHandler) -> None:
        caught = _caught(handler, self.aliases)
        if caught is None or not is_silent_handler(handler, quiet_log_is_silent=self.quiet_log_is_silent):
            return
        if self.in_finally or (self.scope and self.scope[-1] in _CLEANUP_SCOPES) or self._benign(node.body):
            return
        if _marked(self.lines, handler):
            return
        where = ".".join(self.scope) or "<module>"
        first = ast.unparse(node.body[0]).splitlines()[0] if node.body else ""
        message = f"`except {caught}` in {where} swallows a failure of `{first[:100]}`"
        self.findings.append(Finding(self.f.rel, handler.lineno, RULE, message))


def find_swallowed_exceptions(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    quiet_log_is_silent: bool = False,
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)`` over the production files under *root* (test, bench and script directories excluded).

    *quiet_log_is_silent* also reports a handler whose only action is a DEBUG/INFO log call. It is off by default:
    repositories that convert every swallow into a debug line have made that line their record of it.
    """
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    findings: list[Finding] = []
    for f in scan:
        visitor = _Visitor(f, quiet_log_is_silent)
        visitor.visit(f.tree)
        findings.extend(visitor.findings)
    findings.sort(key=lambda x: (x.path, x.line))
    return findings, scan


def assert_no_swallowed_exceptions(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    quiet_log_is_silent: bool = False,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a silent broad/I-O handler not accepted by *baseline_path* (every one, without a baseline).

    A missing baseline fails; refresh with ``REFRESH_FLAG`` or ``PY_CI_SHARED_REFRESH=swallowed-exceptions``.
    """
    findings, scan = find_swallowed_exceptions(root, exclude_parts=exclude_parts, quiet_log_is_silent=quiet_log_is_silent, use_git=use_git)
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="log at WARNING or above, re-raise, or return an explicit sentinel; mark deliberate ones `# swallow-ok: <reason>`",
    )
