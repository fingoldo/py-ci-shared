"""Database connect failures whose exception text is printed: libpq echoes the connection string, password included.

A malformed DSN is quoted back in full by libpq's parse error, and a failed connect names the host, port and user, so
``except Exception as e: print(e)`` around ``psycopg2.connect(dsn)`` prints the secret it failed to use. Audit probes
against the Upwork dashboard printed its DSN three times exactly that way. The safe form reports the exception's type
and SQLSTATE only (``type(e).__name__``, ``e.pgcode``, ``getattr(e, "pgcode", None)``) and raises a fresh error
outside the handler.

Reported, in an ``except`` handler whose ``try`` body opens a connection or resolves a DSN (any call in *callees*,
through ``import ... as`` and a simple ``connect = psycopg2.connect`` alias):

* the bound exception (or an attribute of it other than ``pgcode``/``sqlstate``/``__class__``) passed to a sink:
  ``print``, ``str``, ``repr``, ``format``, a logger method, ``warn``, ``exit``, ``fail``, ``echo``, ``write``;
* the exception inside an f-string, a ``%`` format or a ``.format(...)`` call;
* ``raise X(e)``: the message travels in the new exception;
* ``logger.exception(...)``, any call with ``exc_info`` set, and ``traceback.print_exc``/``format_exc``: the traceback
  carries the message even when the handler never names the exception.

Not reported: ``type(e)``, ``isinstance(e, ...)``, ``getattr(e, "pgcode")``, the exception passed through a call whose
name says it scrubs (*sanitizers*: ``redact_secrets(str(e))``), a bare ``raise`` and ``raise X from e``.
A handler can be accepted with ``# dsn-echo-ok: <reason>`` on the ``except`` line or the line above.

Relation to its neighbours: ``swallowed_exceptions`` reports a handler that says nothing and ``fail_open_handlers`` one
whose fallback disables a gate; this gate reports a handler that says too much. Fixing a swallow by adding ``print(e)``
turns one finding into the other, so the form that satisfies all three is a WARNING naming the type and SQLSTATE.

Usage::

    from py_ci_shared.connect_error_echo import assert_no_connect_error_echo

    def test_no_connect_error_echo():
        assert_no_connect_error_echo(REPO / "dashboard")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._gate_report import enclosing_functions, report, scan_tree, skip_set

__all__ = ["DEFAULT_CALLEES", "DEFAULT_SANITIZERS", "DEFAULT_SINKS", "RULE", "REFRESH_FLAG", "find_connect_error_echo", "assert_no_connect_error_echo"]

RULE = "connect-error-echo"
REFRESH_FLAG = "--refresh-connect-error-echo-baseline"
MARKER = "dsn-echo-ok"
#: Dotted entries match the import-resolved callee exactly; bare entries match the callee's last name (a project's own
#: ``get_dsn`` helper, wherever it is imported from).
DEFAULT_CALLEES: frozenset[str] = frozenset(
    {
        "psycopg2.connect",
        "psycopg2.pool.SimpleConnectionPool",
        "psycopg2.pool.ThreadedConnectionPool",
        "psycopg.connect",
        "psycopg.Connection.connect",
        "psycopg.AsyncConnection.connect",
        "psycopg_pool.ConnectionPool",
        "psycopg_pool.AsyncConnectionPool",
        "asyncpg.connect",
        "asyncpg.create_pool",
        "sqlalchemy.create_engine",
        "sqlalchemy.ext.asyncio.create_async_engine",
        "create_engine",
        "create_async_engine",
        "dsn_or_none",
        "get_dsn",
        "resolve_dsn",
        "make_url",
    }
)
#: Final names of calls that put their arguments into output.
DEFAULT_SINKS: frozenset[str] = frozenset(
    {
        "print",
        "str",
        "repr",
        "ascii",
        "format",
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "exception",
        "critical",
        "fatal",
        "log",
        "exit",
        "fail",
        "skip",
        "abort",
        "echo",
        "secho",
        "write",
        "writelines",
    }
)
_SAFE_ATTRS = frozenset({"pgcode", "sqlstate", "__class__", "errno"})
_SAFE_WRAPPERS = frozenset({"type", "isinstance", "issubclass", "getattr", "hasattr", "id"})
#: A call whose name contains one of these scrubs what it is given (``redact_secrets(str(e))``), so it is not a sink.
DEFAULT_SANITIZERS: tuple[str, ...] = ("redact", "sanitize", "sanitise", "scrub", "mask")
_TRACEBACK_PRINTERS = frozenset({"print_exc", "format_exc", "print_exception", "format_exception"})
_TRY_TYPES: tuple[type, ...] = (ast.Try,) + ((getattr(ast, "TryStar"),) if hasattr(ast, "TryStar") else ())


def _final_name(func: ast.AST) -> Optional[str]:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _walk_local(nodes: Iterable[ast.AST], prune: Optional[Callable[[ast.AST], bool]] = None) -> Iterator[ast.AST]:
    """Every node under *nodes*, not descending into nested function or class bodies (they run elsewhere), nor below
    a node *prune* accepts."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        if prune is not None and prune(node):
            continue
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)))


class _Callees:
    """Decides whether a call is a connect/DSN call, through import aliases and simple local re-bindings."""

    def __init__(self, tree: ast.Module, callees: frozenset[str]) -> None:
        self.aliases = ImportAliases.from_tree(tree)
        self.dotted = frozenset(c for c in callees if "." in c)
        self.bare = frozenset(c for c in callees if "." not in c)
        self.rebound: set[str] = set()
        # `connect = psycopg2.connect` (anywhere, any scope): the name now opens a connection. Two passes reach a
        # re-binding of a re-binding.
        pairs = [(n.targets[0].id, n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)]
        for _ in range(2):
            self.rebound.update(target for target, value in pairs if isinstance(value, (ast.Name, ast.Attribute)) and self.matches(value))

    def matches(self, func: ast.AST) -> bool:
        if isinstance(func, ast.Name) and func.id in self.rebound:
            return True
        qualified = self.aliases.qualified_name(func)
        if qualified is None:
            return False
        return qualified in self.dotted or qualified.rsplit(".", 1)[-1] in self.bare

    def in_body(self, body: list[ast.stmt]) -> bool:
        return any(isinstance(n, ast.Call) and self.matches(n.func) for n in _walk_local(body))


def _is_exc(node: ast.AST, name: str) -> bool:
    """*node* is the exception, or an attribute chain on it that is not a safe one (``e.args``, ``e.pgerror``)."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Attribute) and node.attr in _SAFE_ATTRS:
            return False
        node = node.value
    return isinstance(node, ast.Name) and node.id == name


def _sanitizes(call: ast.Call, sanitizers: tuple[str, ...]) -> bool:
    final = (_final_name(call.func) or "").lower()
    return final in _SAFE_WRAPPERS or any(s in final for s in sanitizers)


def _mentions(node: ast.AST, name: str, sanitizers: tuple[str, ...]) -> bool:
    """The exception reaches *node*'s value: found outside a ``type(e)``/``getattr(e, ...)``/``redact(...)`` wrapper."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Call) and _sanitizes(current, sanitizers):
            continue
        if _is_exc(current, name):
            return True
        if isinstance(current, ast.Attribute) and current.attr in _SAFE_ATTRS:
            continue
        stack.extend(ast.iter_child_nodes(current))
    return False


def _truthy_keyword(call: ast.Call, keyword: str) -> bool:
    for kw in call.keywords:
        if kw.arg == keyword:
            return not (isinstance(kw.value, ast.Constant) and kw.value.value in (False, None, 0))
    return False


def _call_echo(node: ast.Call, name: Optional[str], sinks: frozenset[str], sanitizers: tuple[str, ...]) -> Optional[str]:
    """What a call in the handler does with the exception's text, or None."""
    final = _final_name(node.func)
    if final == "exception" and isinstance(node.func, ast.Attribute):
        return "logger.exception(...) prints the traceback, message included"
    if _truthy_keyword(node, "exc_info"):
        return f"{final}(..., exc_info=...) prints the traceback, message included"
    if final in _TRACEBACK_PRINTERS:
        return f"traceback.{final}() renders the message"
    if name is not None and final in sinks and any(_mentions(a, name, sanitizers) for a in [*node.args, *(kw.value for kw in node.keywords)]):
        return f"{final}(...) is given the exception"
    return None


def _expr_echo(node: ast.AST, name: str, sanitizers: tuple[str, ...]) -> Optional[str]:
    """What a format or a raise in the handler does with the exception's text, or None."""
    if isinstance(node, ast.JoinedStr):
        if any(isinstance(v, ast.FormattedValue) and _mentions(v.value, name, sanitizers) for v in node.values):
            return "an f-string formats the exception"
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, (ast.Constant, ast.JoinedStr)):
        if _mentions(node.right, name, sanitizers):
            return "a % format is given the exception"
    elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
        if any(_is_exc(a, name) for a in [*node.exc.args, *(kw.value for kw in node.exc.keywords)]):
            return "raise X(e) carries the message into the new exception"
    return None


def _echoes(handler: ast.ExceptHandler, sinks: frozenset[str], sanitizers: tuple[str, ...]) -> Iterator[tuple[ast.AST, str]]:
    """``(node, what)`` for every way *handler* puts the exception's text into output."""
    name = handler.name

    def scrubbed(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and _sanitizes(node, sanitizers) and _final_name(node.func) not in _SAFE_WRAPPERS

    for node in _walk_local(handler.body, scrubbed):
        if scrubbed(node):
            continue
        if isinstance(node, ast.Call):
            what = _call_echo(node, name, sinks, sanitizers)
        else:
            what = _expr_echo(node, name, sanitizers) if name is not None else None
        if what is not None:
            yield node, what


def _marked(lines: list[str], handler: ast.ExceptHandler) -> bool:
    for line in (handler.lineno, handler.lineno - 1):
        if 0 < line <= len(lines) and "#" in lines[line - 1] and MARKER in lines[line - 1].split("#", 1)[1]:
            return True
    return False


def _file_findings(parsed: ParsedFile, callees: frozenset[str], sinks: frozenset[str], sanitizers: tuple[str, ...]) -> list[Finding]:
    tree = parsed.tree
    resolver = _Callees(tree, callees)
    functions = enclosing_functions(tree)
    lines = parsed.source.splitlines()
    out: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, _TRY_TYPES) or not resolver.in_body(node.body):  # type: ignore[attr-defined]
            continue
        for handler in node.handlers:  # type: ignore[attr-defined]
            if _marked(lines, handler):
                continue
            where = functions.get(id(node), "<module>")
            seen: set[int] = set()  # print(f"{e}") is one echo, not an f-string plus a print
            for sink, what in sorted(_echoes(handler, sinks, sanitizers), key=lambda pair: getattr(pair[0], "lineno", 0)):
                line = getattr(sink, "lineno", handler.lineno)
                if line in seen:
                    continue
                seen.add(line)
                out.append(Finding(parsed.rel, line, RULE, f"{where}: {what} after a connect/DSN call; print type(e).__name__ and the SQLSTATE only"))
    return out


def _collect(
    root: Union[str, Path],
    *,
    callees: Iterable[str],
    sinks: Iterable[str],
    sanitizers: Iterable[str],
    skip_dir_names: Iterable[str],
    include_tests: bool,
    use_git: Optional[bool],
) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    wanted, sink_names = frozenset(callees), frozenset(sinks)
    findings = [f for parsed in scan for f in _file_findings(parsed, wanted, sink_names, tuple(x.lower() for x in sanitizers))]
    return sorted(findings, key=lambda f: (f.path, f.line)), scan


def find_connect_error_echo(
    root: Union[str, Path],
    *,
    callees: Iterable[str] = DEFAULT_CALLEES,
    sinks: Iterable[str] = DEFAULT_SINKS,
    sanitizers: Iterable[str] = DEFAULT_SANITIZERS,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every handler under *root* that prints a connect/DSN failure's text, plus one ``unparsed-file`` finding per file
    that could not be parsed."""
    findings, scan = _collect(
        root, callees=callees, sinks=sinks, sanitizers=sanitizers, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git
    )
    return findings + scan.unparsed_findings()


def assert_no_connect_error_echo(
    root: Union[str, Path],
    *,
    callees: Iterable[str] = DEFAULT_CALLEES,
    sinks: Iterable[str] = DEFAULT_SINKS,
    sanitizers: Iterable[str] = DEFAULT_SANITIZERS,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a handler that prints a connect/DSN failure's text (new against *baseline_path* when given), on fewer
    than *min_files* parsed files, and on an unparsable file unless *allow_unparsed*. Refresh with ``REFRESH_FLAG``."""
    findings, scan = _collect(
        root, callees=callees, sinks=sinks, sanitizers=sanitizers, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git
    )
    report(
        findings,
        gate="connect-error-echo",
        flag=REFRESH_FLAG,
        guidance="libpq echoes the connection string in its error text; report type(e).__name__ and getattr(e, 'pgcode', None) only",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        allow_unparsed=allow_unparsed,
    )
