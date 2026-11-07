"""Fail-open exception handlers in gate code: a failure inside a gate must not disable the gate.

A selection gate that keeps a candidate whenever evaluating it raises is switched off for exactly the candidates that
fail, and a substitution logged below WARNING hides that it happened. Six shapes, each seen shipping a candidate the gate
existed to stop:

* ``admit_on_error``: an ``except`` handler inside a loop appends the loop's own element to a list, i.e. the element is
  kept because evaluating it failed (``except Exception: survivors.append(spec)``).
* ``gate_returns_true``: an ``except`` handler returns ``True`` inside a function whose name says it decides
  (``*gate*``, ``*filter*``, ``*check*``, ``*_ok``), so an error reads as a pass.
* ``quiet_substitution``: an ``except`` handler assigns a replacement value and logs only at DEBUG/INFO, so the fallback
  is taken silently. A ``# best-effort:`` comment on the ``except`` line (or the line above) marks a handler whose
  fallback is genuinely inconsequential, and is honoured.
* ``finite_guarded_reject``: a reject guarded by ``isfinite(x) and x >= thr`` (or ``>``, ``<``, ``<=``): NaN and inf skip
  the rejection, so the candidate whose score could not be computed is the one that passes.
* ``silent_default_return``: an ``except`` handler whose whole body is one ``return`` (a literal, a name such as ``default``,
  a call such as ``failed()``, or a bare ``return``) and that logs nothing, so the failure reaches the caller as an ordinary
  value. A ``# best-effort:`` marker on the ``except`` line (or the line above) honours a fallback that cannot change a result.
  A handler another rule already reports (``except: return True`` in a deciding function) is not reported a second time.

* ``gate_swallows_error``: in a gate-named function an ``except`` for ``Exception``, ``OSError``, ``SyntaxError``, ``ValueError`` (or a bare one) whose whole
  body is ``pass``, ``continue``, ``sys.exit(0)``, ``pytest.skip(...)`` or ``print(...)``: the failure ends as a pass. Logging at WARNING or above, raising, or a
  ``# best-effort:`` marker honours the handler.

Usage from a repository's meta tests::

    from py_ci_shared.fail_open_handlers import assert_no_new_fail_open_handlers

    def test_no_new_fail_open_handlers():
        assert_no_new_fail_open_handlers(files=..., repo_root=REPO_ROOT, baseline_path=BASELINE)

The baseline is a ratchet keyed ``path::function::rule`` and counted per key, so moving a handler within its function does
not turn it into a new finding; the list can only shrink.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Optional, Union

from ._core.outcome import refresh_hint, refreshed_skip
from ._core import BaselineGrowthError, ScanResult, child_nodes, dump_json, load_json, refresh_requested, scan_python, write_ratchet
from ._core.node_index import walk as _fast_walk

__all__ = ["FailOpenHandler", "find_fail_open_handlers", "assert_no_new_fail_open_handlers", "DEFAULT_GATE_NAME_RE"]

DEFAULT_GATE_NAME_RE = r"gate|filter|check|_ok$"
_QUIET_LEVELS = frozenset({"debug", "info"})
_LOUD_LEVELS = frozenset({"warning", "warn", "error", "exception", "critical", "fatal"})
_BEST_EFFORT = "# best-effort:"
_QUIET_LEVEL_NAMES = frozenset({"DEBUG", "INFO", "NOTSET"})
_LOUD_LEVEL_NAMES = frozenset({"WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"})
REFRESH_FLAG = "--refresh-fail-open-baseline"
_Scope = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.Module]


class FailOpenHandler:
    """One finding: where, which rule, and a one-line reason. ``function`` is the qualified name (``Cls.check``,
    ``outer.<locals>.inner``), or ``<module>`` for module-level code."""

    __slots__ = ("detail", "function", "lineno", "path", "rule")

    def __init__(self, path: str, function: str, lineno: int, rule: str, detail: str) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.rule = rule
        self.detail = detail

    @property
    def scope(self) -> str:
        """``path::function::rule``: what the ratchet counts against."""
        return f"{self.path}::{self.function}::{self.rule}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"FailOpenHandler({self.scope} line {self.lineno})"


def _call_attr(node: ast.AST) -> str | None:
    """The attribute or name a call invokes (``logger.debug(...)`` -> ``debug``)."""
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute):
            return f.attr
        if isinstance(f, ast.Name):
            return f.id
    return None


def _level_name(node: ast.AST) -> Optional[str]:
    """``logging.DEBUG`` / ``DEBUG`` -> ``"DEBUG"``; anything else -> None."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _log_call_level(node: ast.AST) -> Optional[str]:
    """For ``<x>.log(LEVEL, ...)``, the level's name."""
    if isinstance(node, ast.Call) and _call_attr(node) == "log" and node.args:
        return _level_name(node.args[0])
    return None


def _log_throttle_level(node: ast.AST) -> Optional[str]:
    """For ``log_throttle(logger, key, LEVEL, ...)``, the level's name (the level is the third argument)."""
    if isinstance(node, ast.Call) and _call_attr(node) == "log_throttle" and len(node.args) >= 3:
        return _level_name(node.args[2])
    return None


def _logs_loudly(handler: ast.ExceptHandler) -> bool:
    """True when the handler raises, or logs at WARNING or above (a ``logging.WARNING`` argument counts)."""
    for n in _fast_walk(handler):
        if isinstance(n, ast.Raise):
            return True
        if _call_attr(n) in _LOUD_LEVELS:
            return True
        if (isinstance(n, ast.Attribute) and n.attr in _LOUD_LEVEL_NAMES) or _log_call_level(n) in _LOUD_LEVEL_NAMES:
            return True
        # A helper whose name says it rejects (``gate_error_reject``) records the failure: not a quiet fallback.
        if isinstance(n, ast.Call) and "reject" in (_call_attr(n) or "").lower():
            return True
    return False


def _logs_quietly(handler: ast.ExceptHandler) -> bool:
    """True when the handler logs at DEBUG or INFO (``log.debug(..)``, ``log.log(logging.DEBUG, ..)``, or
    ``log_throttle(logger, key, logging.DEBUG, ..)``)."""
    return any(
        _call_attr(n) in _QUIET_LEVELS or _log_call_level(n) in _QUIET_LEVEL_NAMES or _log_throttle_level(n) in _QUIET_LEVEL_NAMES for n in _fast_walk(handler)
    )


def _assigns_a_value(handler: ast.ExceptHandler) -> bool:
    """True when the handler assigns something (a fallback value or a fallback call)."""
    return any(isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) for n in _fast_walk(handler))


def _marked_best_effort(lines: list[str], handler: ast.ExceptHandler) -> bool:
    """True when the ``except`` line or the line above carries the ``# best-effort:`` marker."""
    i = handler.lineno - 1
    return any(_BEST_EFFORT in lines[j] for j in (i - 1, i) if 0 <= j < len(lines))


_FAILURE_LIST_RE = re.compile(r"fail|reject|drop|skip|error|bad|invalid|broken|unparsed|unreadable|problem|violation|missing|absent|unresolved", re.IGNORECASE)


def _is_message(node: ast.AST) -> bool:
    """An f-string, ``"..." % x`` or ``"...".format(...)``: text describing something, not the thing itself."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    )


def _reports_the_error(value: ast.expr, exc_name: Optional[str]) -> bool:
    """True when the appended *value* is a message string, or a record constructed from the caught exception.

    A bare tuple such as ``(spec, exc)`` is still an admission: the element itself goes into the list."""
    if _is_message(value):
        return True
    if exc_name is None or not isinstance(value, ast.Call):
        return False
    return any(isinstance(a, ast.Name) and a.id == exc_name for a in _fast_walk(value))


_ADMIT_METHODS = frozenset({"append", "add", "extend", "insert", "appendleft", "extendleft"})


def _mentions(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(a, ast.Name) and a.id in names for a in _fast_walk(node))


def _target_name(target: ast.AST) -> str:
    if isinstance(target, ast.Subscript):
        target = target.value
    return target.id if isinstance(target, ast.Name) else (target.attr if isinstance(target, ast.Attribute) else "")


def _appends_name(handler: ast.ExceptHandler, names: set[str], loud: bool = False) -> bool:
    """True when the handler admits the loop's element: ``<list>.append/add/extend/insert(...)``, ``out += [x]``,
    ``out[x] = ...`` or a ``yield`` of a value built from one of ``names`` (``spec``, ``(spec, 0)``, ``spec.name``), into a
    collection not named for failures.

    ``failed.append(k)`` records the failure; ``survivors.append(spec)`` admits the element that failed. A value that
    carries the caught exception (``problems.append(f"{path}: {exc}")``) or is a formatted message string reports the
    failure, whatever the collection is called, and is not an admission.
    """
    for n in _fast_walk(handler):
        value: Optional[ast.expr] = None
        list_name = ""
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _ADMIT_METHODS and n.args:
            value = n.args[-1] if n.func.attr == "insert" else n.args[0]
            list_name = _target_name(n.func.value)
        elif isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add):
            value, list_name = n.value, _target_name(n.target)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Subscript) for t in n.targets):
            sub = next(t for t in n.targets if isinstance(t, ast.Subscript))
            if loud and isinstance(n.value, ast.Constant):
                continue  # a loud handler that stores a constant under the loop key (``out[i] = 0.0``) records the failure
            # `counts[x] = count` stores a number (not the element) under the element as a key: a counter, not an admission.
            stores_a_count = not isinstance(n.value, ast.Constant) and (not _mentions(n.value, names) or _mentions(n.value, {_target_name(sub)}))
            if stores_a_count:
                continue
            value, list_name = sub.slice, _target_name(sub)
        elif isinstance(n, (ast.Yield, ast.YieldFrom)) and n.value is not None:
            value = n.value
        if value is None or not _mentions(value, names) or _reports_the_error(value, handler.name):
            continue
        if not _FAILURE_LIST_RE.search(list_name):
            return True
    return False


def _binds_true_then_returns(handler: ast.ExceptHandler, scope: Optional[ast.AST]) -> bool:
    """``ok = True`` in the handler and ``return ok`` in the deciding function: ``return True`` spelled in two steps."""
    if scope is None:
        return False
    flags = {
        t.id
        for n in _fast_walk(handler)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value is True
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    return bool(flags) and any(isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id in flags for n in _fast_walk(scope))


def _caught_names(handler: ast.ExceptHandler) -> set[str]:
    """The exception class names an ``except`` clause catches (``except (A, B)`` -> ``{"A", "B"}``); empty for a bare ``except``."""
    if handler.type is None:
        return set()
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {t.id if isinstance(t, ast.Name) else t.attr for t in types if isinstance(t, (ast.Name, ast.Attribute))}


#: A handler that only catches a missing optional dependency: the caller is built to run without it.
_OPTIONAL_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError"})
#: A handler that only catches a missing file: absence is a state the caller sees, not a hidden failure.
_ABSENT_ERRORS = frozenset({"FileNotFoundError"})
#: A function whose name says it reads a cache, stamp, token, digest or signature: a failure there is a miss the caller recomputes.
_CACHE_MISS_NAME_RE = re.compile(r"cache|stamp|token|digest|signature", re.IGNORECASE)
#: A function whose name says it builds a key, an encoding or a frozen snapshot: a fallback there swaps one deterministic
#: representation for another (``json.dumps`` for ``deepcopy``). Accepted only while the handler swallows no I/O error.
_KEY_BUILDER_NAME_RE = re.compile(r"(?:^|_)(?:key|encode|freeze)(?:_|$)", re.IGNORECASE)
#: Exceptions that are ordinary control flow of a single lookup, ``next()`` or kill probe: the exception IS the answer "nothing there", so
#: ``try: return d[k] except KeyError: return None`` is ``d.get(k)``. Accepted only when the try body is one statement; I/O errors, timeouts and
#: ``Exception`` are never control flow here.
_CONTROL_FLOW_ERRORS = frozenset(
    {"StopIteration", "StopAsyncIteration", "KeyError", "IndexError", "Empty", "ProcessLookupError", "ZeroDivisionError", "CancelledError"}
)
#: Exception names whose swallowing can hide a failed read (a file, a subprocess, a database); a bare ``except`` is one too.
_IO_ERROR_NAMES = frozenset(
    {
        "OSError",
        "IOError",
        "EnvironmentError",
        "FileNotFoundError",
        "PermissionError",
        "IsADirectoryError",
        "SubprocessError",
        "CalledProcessError",
        "TimeoutExpired",
        "Error",
        "OperationalError",
        "Exception",
        "BaseException",
    }
)


#: A local SQLite read: the only failure that a missing table or file raises through ``execute``.
_SQLITE_READ_CALLS = frozenset({"execute", "fetchone", "fetchall"})


def _call_names(nodes: list[ast.stmt]) -> set[str]:
    """Every name a call in *nodes* invokes (``int``, ``split``, ``execute``)."""
    return {name for stmt in nodes for n in _fast_walk(stmt) if (name := _call_attr(n)) is not None}


def _local_db_fallback(handler: ast.ExceptHandler, try_node: Optional[ast.Try]) -> bool:
    """True for ``try: <read a local SQLite table> except sqlite3.OperationalError: return <default>``: a missing table or
    install answers "not installed". A broader ``sqlite3.Error``, or a try that makes no SQLite read, is not accepted."""
    if try_node is None or _caught_names(handler) != {"OperationalError"}:
        return False
    return bool(_call_names(try_node.body) & _SQLITE_READ_CALLS)


def _reasoned_fallback(handler: ast.ExceptHandler, fn_name: str, try_node: Optional[ast.Try] = None) -> bool:
    """True for the fallbacks that are correct by construction: an optional import, a missing file, a cache miss, or a
    local SQLite table that is not installed. A parse that falls back to a default is NOT one of them: the existing
    silent_default_return spec flags ``try: return int(text) except ValueError: return default``."""
    caught = _caught_names(handler)
    if caught and (caught <= _OPTIONAL_IMPORT_ERRORS or caught <= _ABSENT_ERRORS):
        return True
    if _local_db_fallback(handler, try_node):
        return True
    if caught and caught <= _CONTROL_FLOW_ERRORS and try_node is not None and len(try_node.body) == 1:
        return True
    if _KEY_BUILDER_NAME_RE.search(fn_name) and not _swallows_io_error(handler):
        return True
    return bool(_CACHE_MISS_NAME_RE.search(fn_name))


def _swallows_io_error(handler: ast.ExceptHandler) -> bool:
    """True when the handler catches a read failure, or everything (a bare ``except`` or ``except Exception``)."""
    caught = _caught_names(handler)
    return not caught or bool(caught & _IO_ERROR_NAMES)


def _carries_reason(returned: ast.expr) -> bool:
    """True when the returned value holds a human-readable message (a string with a space or a colon): the failure is reported in-band."""
    for n in _fast_walk(returned):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and (" " in n.value or ":" in n.value):
            return True
    return False


def _hands_off_exception(handler: ast.ExceptHandler) -> bool:
    """True when the handler stores the caught exception itself (``error = e``) for the caller to raise or report."""
    if not handler.name:
        return False
    return any(isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id == handler.name for n in _fast_walk(handler))


def _silent_default_return(handler: ast.ExceptHandler, lines: list[str], fn_name: str = "", try_node: Optional[ast.Try] = None) -> bool:
    """True when the handler's whole body is one ``return`` and nothing in the handler logs, raises or is marked best-effort."""
    if len(handler.body) != 1 or not isinstance(handler.body[0], ast.Return):
        return False
    returned = handler.body[0].value
    if handler.name and returned is not None and _mentions(returned, {handler.name}):
        return False  # the return hands back the failure's own data (``return exc.sqlstate``), not a default
    if returned is not None and _carries_reason(returned):
        return False  # the returned value says what failed (``DatasetSummary(..., "not installed in this checkout")``)
    if _reasoned_fallback(handler, fn_name, try_node):
        return False
    return not (_logs_loudly(handler) or _logs_quietly(handler) or _marked_best_effort(lines, handler))


#: What a gate-named function may catch and dispose of without a trace: anything broad, plus the I/O and parse failures a gate meets on a consumer's files.
_SWALLOWABLE = frozenset({"Exception", "BaseException", "OSError", "IOError", "SyntaxError", "UnicodeDecodeError", "ValueError", "JSONDecodeError"})


def _is_clean_exit_or_skip(stmt: ast.stmt) -> bool:
    """``sys.exit(0)`` / ``exit(0)`` / ``pytest.skip(...)`` / ``print(...)`` as a whole statement: the failure ends as a clean exit, a skip or a printed line."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    name = _call_attr(call)
    if name in {"exit", "_exit"}:
        return not call.args or (isinstance(call.args[0], ast.Constant) and call.args[0].value in (0, None))
    return name in {"skip", "print"}


def _gate_swallows_error(handler: ast.ExceptHandler, lines: list[str]) -> bool:
    """True for a handler in a gate-named function that disposes of a broad/I-O/parse failure with ``pass``, ``continue``, a clean exit, a skip or a print.

    ``return`` handlers are ``silent_default_return``'s; anything that raises, logs at WARNING or above, appends, assigns or is marked best-effort is not this.
    """
    caught = _caught_names(handler)
    if caught and not caught & _SWALLOWABLE:
        return False
    if _logs_loudly(handler) or _marked_best_effort(lines, handler):
        return False
    return all(isinstance(st, (ast.Pass, ast.Continue)) or _is_clean_exit_or_skip(st) for st in handler.body)


def _returns_true(handler: ast.ExceptHandler) -> bool:
    """True when the handler contains ``return True``."""
    return any(isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and n.value.value is True for n in _fast_walk(handler))


def _is_finite_test(node: ast.AST) -> bool:
    """``isfinite(x)``, ``not isnan(x)``, ``not isinf(x)``: true only for a value NaN/inf would fail."""
    if _call_attr(node) == "isfinite":
        return True
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not) and _call_attr(node.operand) in {"isnan", "isinf"}


def _is_finite_guarded_reject(node: ast.If) -> bool:
    """``if isfinite(x) and x <cmp> thr:`` whose body rejects (``continue``, or a call naming ``reject``)."""
    test = node.test
    if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)):
        return False
    has_finite = any(_is_finite_test(v) for v in test.values)
    has_cmp = any(isinstance(v, ast.Compare) and any(isinstance(o, (ast.Gt, ast.GtE, ast.Lt, ast.LtE)) for o in v.ops) for v in test.values)
    if not (has_finite and has_cmp):
        return False
    for n in _fast_walk(ast.Module(body=node.body, type_ignores=[])):
        if isinstance(n, ast.Continue):
            return True
        if isinstance(n, ast.Call) and "reject" in (_call_attr(n) or "").lower():
            return True
    return False


def _handler_findings(
    handler: ast.ExceptHandler,
    rel: str,
    name: str,
    lines: list[str],
    is_gate: bool,
    loop_vars: frozenset[str],
    scope: Optional[ast.AST] = None,
    try_node: Optional[ast.Try] = None,
) -> list[FailOpenHandler]:
    """The rules one ``except`` handler breaks (admit, gate-returns-true, quiet substitution, silent default)."""
    found: list[FailOpenHandler] = []
    # A handler that logs at WARNING or above (logger or log_throttle) and stores a constant under the loop key reports
    # the failure and records a constant, not the element; a loud handler that keeps the element is still an admission.
    if loop_vars and _appends_name(handler, set(loop_vars), loud=_logs_loudly(handler)):
        found.append(FailOpenHandler(rel, name, handler.lineno, "admit_on_error", "the handler keeps the loop's element because evaluating it failed"))
    if is_gate and (_returns_true(handler) or _binds_true_then_returns(handler, scope)):
        found.append(FailOpenHandler(rel, name, handler.lineno, "gate_returns_true", "an error in a deciding function returns True"))
    if (
        _assigns_a_value(handler)
        and _logs_quietly(handler)
        and not _logs_loudly(handler)
        and not _marked_best_effort(lines, handler)
        and not _hands_off_exception(handler)
    ):
        found.append(FailOpenHandler(rel, name, handler.lineno, "quiet_substitution", "a fallback value is taken with only a DEBUG/INFO log"))
    if is_gate and not found and _gate_swallows_error(handler, lines):
        found.append(
            FailOpenHandler(
                rel, name, handler.lineno, "gate_swallows_error", "a gate-named function disposes of a failure with pass, continue, a clean exit or a skip"
            )
        )
    if not found and _silent_default_return(handler, lines, name.rsplit(".", 1)[-1], try_node):
        found.append(FailOpenHandler(rel, name, handler.lineno, "silent_default_return", "the handler returns a default and logs nothing"))
    return found


def _scan_function(fn: _Scope, rel: str, lines: list[str], gate_re: re.Pattern, qualname: Optional[str] = None) -> list[FailOpenHandler]:
    """Findings inside one function or module body (nested functions and classes are scanned on their own)."""
    found: list[FailOpenHandler] = []
    name: str = qualname or str(getattr(fn, "name", "<module>"))
    try_of: dict[int, ast.Try] = {id(h): n for n in _fast_walk(fn) if isinstance(n, ast.Try) for h in n.handlers}
    is_gate = not isinstance(fn, ast.Module) and bool(gate_re.search(fn.name))

    def visit(node: ast.AST, loop_vars: frozenset[str]) -> None:
        for child in child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            vars_here = loop_vars
            if isinstance(child, (ast.For, ast.AsyncFor)):
                vars_here = loop_vars | {n.id for n in _fast_walk(child.target) if isinstance(n, ast.Name)}
            if isinstance(child, ast.ExceptHandler):
                found.extend(_handler_findings(child, rel, name, lines, is_gate, loop_vars, fn, try_of.get(id(child))))
            if isinstance(child, ast.If) and _is_finite_guarded_reject(child):
                found.append(FailOpenHandler(rel, name, child.lineno, "finite_guarded_reject", "NaN/inf skip this rejection"))
            visit(child, vars_here)

    visit(fn, frozenset())
    return found


def _scopes(tree: ast.Module) -> Iterator[tuple[str, _Scope]]:
    """The module body, then every function with its qualified name."""
    yield "<module>", tree

    def walk(node: ast.AST, prefix: str) -> Iterator[tuple[str, _Scope]]:
        for child in child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                yield qual, child
                yield from walk(child, f"{qual}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def _find_in_scan(scan: ScanResult, gate_re: re.Pattern) -> list[FailOpenHandler]:
    out: list[FailOpenHandler] = []
    for parsed in scan:
        lines = parsed.source.splitlines()
        for qual, node in _scopes(parsed.tree):
            out.extend(_scan_function(node, parsed.rel, lines, gate_re, qual))
    return out


def find_fail_open_handlers(files: Iterable[Path], repo_root: Path, gate_name_re: str = DEFAULT_GATE_NAME_RE) -> list[FailOpenHandler]:
    """Every fail-open handler in ``files``, keyed by repo-relative path. Unparsable files are not in this list;
    :func:`assert_no_new_fail_open_handlers` fails on them."""
    return _find_in_scan(scan_python([Path(p) for p in files], root=Path(repo_root)), re.compile(gate_name_re))


def assert_no_new_fail_open_handlers(
    files: Iterable[Path],
    repo_root: Path,
    baseline_path: Path,
    gate_name_re: str = DEFAULT_GATE_NAME_RE,
    *,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Any = None,
    grow: Optional[bool] = None,
    allow_unparsed: bool = False,
) -> None:
    """Fail on a fail-open handler beyond what *baseline_path* accepts for its ``path::function::rule``, and on stale entries.

    The baseline maps each accepted scope to a note; a scope listed ``n`` times (``scope``, ``scope#2``, ...) accepts ``n``
    findings, so a function keeps its accepted count however its handlers move. Also fails when fewer than *min_files*
    files parsed, when a file could not be parsed, and when the baseline is missing; a refresh (*refresh*, ``--refresh-
    fail-open-baseline`` or ``PY_CI_SHARED_REFRESH=fail-open``) rewrites it, keeping existing notes.
    """
    scan = scan_python([Path(p) for p in files], root=Path(repo_root), min_files=min_files)
    scan.check_floor()
    found = _find_in_scan(scan, re.compile(gate_name_re))
    by_scope: dict[str, list[FailOpenHandler]] = {}
    for h in found:
        by_scope.setdefault(h.scope, []).append(h)
    exists = Path(baseline_path).is_file()
    accepted: dict[str, str] = load_json(baseline_path) if exists else {}
    if refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request):
        _refresh_baseline(baseline_path, by_scope, accepted, exists, grow, request)
    allowed: dict[str, int] = {}
    for key in accepted:
        scope = key.split("#", 1)[0]
        allowed[scope] = allowed.get(scope, 0) + 1
    problems = _baseline_problems(scan, by_scope, allowed, exists, baseline_path, allow_unparsed=allow_unparsed)
    if problems:
        raise AssertionError("\n".join(problems))


def _refresh_baseline(
    baseline_path: Path, by_scope: dict[str, list[FailOpenHandler]], accepted: dict[str, str], exists: bool, grow: Optional[bool], request: Any
) -> None:
    """Rewrite the baseline from the current findings (keeping notes), then skip the test; fail when it would grow."""
    import pytest

    before: Optional[dict[str, int]] = None
    if exists:
        before = {}
        for key in accepted:
            before[key.split("#", 1)[0]] = before.get(key.split("#", 1)[0], 0) + 1

    def render(kept: dict[str, int]) -> str:
        entries: dict[str, str] = {}
        for scope, n in sorted(kept.items()):
            for i in range(n):
                key = scope if i == 0 else f"{scope}#{i + 1}"
                entries[key] = accepted.get(key) or "NEEDS-JUSTIFICATION: why this fallback cannot disable the gate"
        return dump_json(entries)

    try:
        kept = write_ratchet(
            baseline_path, {s: len(hs) for s, hs in by_scope.items()}, gate="fail-open", previous=before, render=render, grow=grow, request=request
        )
    except BaselineGrowthError as exc:
        pytest.fail(str(exc), pytrace=False)
    refreshed_skip(f"fail-open baseline written: {sum(kept.values())} entr(ies) in {baseline_path}")


def _baseline_problems(
    scan: ScanResult,
    by_scope: dict[str, list[FailOpenHandler]],
    allowed: dict[str, int],
    exists: bool,
    baseline_path: Path,
    *,
    allow_unparsed: bool = False,
) -> list[str]:
    """The failure messages for unparsed files (unless *allow_unparsed*), a missing baseline, new handlers and stale baseline scopes."""
    problems: list[str] = []
    if scan.unparsed and not allow_unparsed:
        problems.append(f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked:\n  " + "\n  ".join(u.render() for u in scan.unparsed))
    if not exists:
        problems.append(
            f"baseline {baseline_path} does not exist, so nothing is accepted; create it with {refresh_hint(REFRESH_FLAG, 'fail_open_handlers')} (an empty {{}} for a clean repo)"
        )
    new = [h for scope, hs in sorted(by_scope.items()) for h in hs[allowed.get(scope, 0) :]]
    if new:
        lines = "\n  ".join(f"{h.path}:{h.lineno} [{h.rule}] in {h.function}: {h.detail}" for h in new)
        problems.append(
            f"{len(new)} fail-open handler(s) in gate code -- a failure inside a gate must not disable the gate:\n  {lines}\n"
            "Reject (and record why) instead of keeping the candidate, log a substitution at WARNING, reject non-finite scores "
            "explicitly, or, when the fallback genuinely cannot matter, mark the except line with '# best-effort: <reason>'."
        )
    stale = sorted(scope for scope, n in allowed.items() if n > len(by_scope.get(scope, ())))
    if stale:
        problems.append(f"these baseline scopes accept more fail-open handlers than remain -- lower or remove them: {stale}")
    return problems
