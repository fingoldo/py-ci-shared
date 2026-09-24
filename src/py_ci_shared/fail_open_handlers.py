"""Fail-open exception handlers in gate code: a failure inside a gate must not disable the gate.

A selection gate that keeps a candidate whenever evaluating it raises is switched off for exactly the candidates that
fail, and a substitution logged below WARNING hides that it happened. Four shapes, each seen shipping a candidate the gate
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

Usage from a repository's meta tests::

    from py_ci_shared.fail_open_handlers import assert_no_new_fail_open_handlers

    def test_no_new_fail_open_handlers():
        assert_no_new_fail_open_handlers(files=..., repo_root=REPO_ROOT, baseline_path=BASELINE)

The baseline is a ratchet keyed ``path::function::rule`` and counted per key, so moving a handler within its function does
not turn it into a new finding; the list can only shrink.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Optional, Union

from ._core import ScanResult, atomic_write_text, dump_json, refresh_requested, scan_python

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


def _logs_loudly(handler: ast.ExceptHandler) -> bool:
    """True when the handler raises, or logs at WARNING or above (a ``logging.WARNING`` argument counts)."""
    for n in ast.walk(handler):
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
    """True when the handler logs at DEBUG or INFO (``log.debug(..)``, or ``log.log(logging.DEBUG, ..)``)."""
    return any(_call_attr(n) in _QUIET_LEVELS or _log_call_level(n) in _QUIET_LEVEL_NAMES for n in ast.walk(handler))


def _assigns_a_value(handler: ast.ExceptHandler) -> bool:
    """True when the handler assigns something (a fallback value or a fallback call)."""
    return any(isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) for n in ast.walk(handler))


def _marked_best_effort(lines: list[str], handler: ast.ExceptHandler) -> bool:
    """True when the ``except`` line or the line above carries the ``# best-effort:`` marker."""
    i = handler.lineno - 1
    return any(_BEST_EFFORT in lines[j] for j in (i - 1, i) if 0 <= j < len(lines))


_FAILURE_LIST_RE = re.compile(r"fail|reject|drop|skip|error|bad|invalid|broken|unparsed|unreadable|problem|violation", re.IGNORECASE)


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
    return any(isinstance(a, ast.Name) and a.id == exc_name for a in ast.walk(value))


def _appends_name(handler: ast.ExceptHandler, names: set[str]) -> bool:
    """True when the handler calls ``<list>.append(...)`` with an argument built from one of ``names`` (``spec``,
    ``(spec, 0)``, ``spec.name``), into a list not named for failures.

    ``failed.append(k)`` records the failure; ``survivors.append(spec)`` admits the element that failed. An appended
    value that carries the caught exception (``problems.append(f"{path}: {exc}")``) or is a formatted message string
    reports the failure, whatever the list is called, and is not an admission.
    """
    for n in ast.walk(handler):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "append"
            and len(n.args) == 1
            and any(isinstance(a, ast.Name) and a.id in names for a in ast.walk(n.args[0]))
            and not _reports_the_error(n.args[0], handler.name)
        ):
            target = n.func.value
            list_name = target.id if isinstance(target, ast.Name) else (target.attr if isinstance(target, ast.Attribute) else "")
            if not _FAILURE_LIST_RE.search(list_name):
                return True
    return False


def _returns_true(handler: ast.ExceptHandler) -> bool:
    """True when the handler contains ``return True``."""
    return any(isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and n.value.value is True for n in ast.walk(handler))


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
    for n in ast.walk(ast.Module(body=node.body, type_ignores=[])):
        if isinstance(n, ast.Continue):
            return True
        if isinstance(n, ast.Call) and "reject" in (_call_attr(n) or "").lower():
            return True
    return False


def _scan_function(fn: _Scope, rel: str, lines: list[str], gate_re: re.Pattern, qualname: Optional[str] = None) -> list[FailOpenHandler]:
    """Findings inside one function or module body (nested functions and classes are scanned on their own)."""
    found: list[FailOpenHandler] = []
    name: str = qualname or str(getattr(fn, "name", "<module>"))
    is_gate = not isinstance(fn, ast.Module) and bool(gate_re.search(fn.name))

    def visit(node: ast.AST, loop_vars: frozenset[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            vars_here = loop_vars
            if isinstance(child, (ast.For, ast.AsyncFor)):
                vars_here = loop_vars | {n.id for n in ast.walk(child.target) if isinstance(n, ast.Name)}
            if isinstance(child, ast.ExceptHandler):
                if loop_vars and _appends_name(child, set(loop_vars)):
                    found.append(
                        FailOpenHandler(rel, name, child.lineno, "admit_on_error", "the handler keeps the loop's element because evaluating it failed")
                    )
                if is_gate and _returns_true(child):
                    found.append(FailOpenHandler(rel, name, child.lineno, "gate_returns_true", "an error in a deciding function returns True"))
                if _assigns_a_value(child) and _logs_quietly(child) and not _logs_loudly(child) and not _marked_best_effort(lines, child):
                    found.append(FailOpenHandler(rel, name, child.lineno, "quiet_substitution", "a fallback value is taken with only a DEBUG/INFO log"))
            if isinstance(child, ast.If) and _is_finite_guarded_reject(child):
                found.append(FailOpenHandler(rel, name, child.lineno, "finite_guarded_reject", "NaN/inf skip this rejection"))
            visit(child, vars_here)

    visit(fn, frozenset())
    return found


def _scopes(tree: ast.Module) -> Iterator[tuple[str, _Scope]]:
    """The module body, then every function with its qualified name."""
    yield "<module>", tree

    def walk(node: ast.AST, prefix: str) -> Iterator[tuple[str, _Scope]]:
        for child in ast.iter_child_nodes(node):
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
) -> None:
    """Fail on a fail-open handler beyond what *baseline_path* accepts for its ``path::function::rule``, and on stale entries.

    The baseline maps each accepted scope to a note; a scope listed ``n`` times (``scope``, ``scope#2``, ...) accepts ``n``
    findings, so a function keeps its accepted count however its handlers move. Also fails when fewer than *min_files*
    files parsed, when a file could not be parsed, and when the baseline is missing; a refresh (*refresh*, ``--refresh-
    fail-open-baseline`` or ``PY_CI_SHARED_REFRESH=fail-open``) rewrites it, keeping existing notes.
    """
    import pytest

    scan = scan_python([Path(p) for p in files], root=Path(repo_root), min_files=min_files)
    scan.check_floor()
    found = _find_in_scan(scan, re.compile(gate_name_re))
    by_scope: dict[str, list[FailOpenHandler]] = {}
    for h in found:
        by_scope.setdefault(h.scope, []).append(h)
    exists = Path(baseline_path).is_file()
    accepted: dict[str, str] = json.loads(Path(baseline_path).read_text(encoding="utf-8-sig")) if exists else {}
    if refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request):
        entries: dict[str, str] = {}
        for scope, hs in sorted(by_scope.items()):
            for i in range(len(hs)):
                key = scope if i == 0 else f"{scope}#{i + 1}"
                entries[key] = accepted.get(key) or "NEEDS-JUSTIFICATION: why this fallback cannot disable the gate"
        atomic_write_text(baseline_path, dump_json(entries))
        pytest.skip(f"fail-open baseline written: {len(entries)} entr(ies) in {baseline_path}")
    allowed: dict[str, int] = {}
    for key in accepted:
        scope = key.split("#", 1)[0]
        allowed[scope] = allowed.get(scope, 0) + 1
    problems: list[str] = []
    if scan.unparsed:
        problems.append(f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked:\n  " + "\n  ".join(u.render() for u in scan.unparsed))
    if not exists:
        problems.append(f"baseline {baseline_path} does not exist, so nothing is accepted; create it with {REFRESH_FLAG} (an empty {{}} for a clean repo)")
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
    if problems:
        raise AssertionError("\n".join(problems))
