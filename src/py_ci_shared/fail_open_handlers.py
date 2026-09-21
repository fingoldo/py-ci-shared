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
from collections.abc import Iterable
from pathlib import Path

__all__ = ["FailOpenHandler", "find_fail_open_handlers", "assert_no_new_fail_open_handlers", "DEFAULT_GATE_NAME_RE"]

DEFAULT_GATE_NAME_RE = r"gate|filter|check|_ok$"
_QUIET_LEVELS = frozenset({"debug", "info"})
_LOUD_LEVELS = frozenset({"warning", "warn", "error", "exception", "critical", "fatal"})
_BEST_EFFORT = "# best-effort:"


class FailOpenHandler:
    """One finding: where, which rule, and a one-line reason."""

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


def _logs_loudly(handler: ast.ExceptHandler) -> bool:
    """True when the handler raises, or logs at WARNING or above (a ``logging.WARNING`` argument counts)."""
    for n in ast.walk(handler):
        if isinstance(n, ast.Raise):
            return True
        if _call_attr(n) in _LOUD_LEVELS:
            return True
        if isinstance(n, ast.Attribute) and n.attr in {"WARNING", "ERROR", "CRITICAL"}:
            return True
        # A helper whose name says it rejects (``gate_error_reject``) records the failure: not a quiet fallback.
        if isinstance(n, ast.Call) and "reject" in (_call_attr(n) or "").lower():
            return True
    return False


def _logs_quietly(handler: ast.ExceptHandler) -> bool:
    """True when the handler logs at DEBUG or INFO."""
    return any(_call_attr(n) in _QUIET_LEVELS for n in ast.walk(handler))


def _assigns_a_value(handler: ast.ExceptHandler) -> bool:
    """True when the handler assigns something (a fallback value or a fallback call)."""
    return any(isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) for n in ast.walk(handler))


def _marked_best_effort(lines: list[str], handler: ast.ExceptHandler) -> bool:
    """True when the ``except`` line or the line above carries the ``# best-effort:`` marker."""
    i = handler.lineno - 1
    return any(_BEST_EFFORT in lines[j] for j in (i - 1, i) if 0 <= j < len(lines))


_FAILURE_LIST_RE = re.compile(r"fail|reject|drop|skip|error|bad|invalid|broken", re.IGNORECASE)


def _appends_name(handler: ast.ExceptHandler, names: set[str]) -> bool:
    """True when the handler calls ``<list>.append(<name>)`` with one of ``names``, into a list not named for failures.

    ``failed.append(k)`` records the failure; ``survivors.append(spec)`` admits the element that failed.
    """
    for n in ast.walk(handler):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "append"
            and len(n.args) == 1
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id in names
        ):
            target = n.func.value
            list_name = target.id if isinstance(target, ast.Name) else (target.attr if isinstance(target, ast.Attribute) else "")
            if not _FAILURE_LIST_RE.search(list_name):
                return True
    return False


def _returns_true(handler: ast.ExceptHandler) -> bool:
    """True when the handler contains ``return True``."""
    return any(isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and n.value.value is True for n in ast.walk(handler))


def _is_finite_guarded_reject(node: ast.If) -> bool:
    """``if isfinite(x) and x <cmp> thr:`` whose body rejects (``continue``, or a call naming ``reject``)."""
    test = node.test
    if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)):
        return False
    has_finite = any(_call_attr(v) == "isfinite" for v in test.values)
    has_cmp = any(isinstance(v, ast.Compare) and any(isinstance(o, (ast.Gt, ast.GtE, ast.Lt, ast.LtE)) for o in v.ops) for v in test.values)
    if not (has_finite and has_cmp):
        return False
    for n in ast.walk(ast.Module(body=node.body, type_ignores=[])):
        if isinstance(n, ast.Continue):
            return True
        if isinstance(n, ast.Call) and "reject" in (_call_attr(n) or "").lower():
            return True
    return False


def _scan_function(fn: ast.FunctionDef | ast.AsyncFunctionDef, rel: str, lines: list[str], gate_re: re.Pattern) -> list[FailOpenHandler]:
    """Findings inside one function (nested functions are scanned on their own)."""
    found: list[FailOpenHandler] = []
    is_gate = bool(gate_re.search(fn.name))

    def visit(node: ast.AST, loop_vars: frozenset[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            vars_here = loop_vars
            if isinstance(child, (ast.For, ast.AsyncFor)):
                vars_here = loop_vars | {n.id for n in ast.walk(child.target) if isinstance(n, ast.Name)}
            if isinstance(child, ast.ExceptHandler):
                if loop_vars and _appends_name(child, set(loop_vars)):
                    found.append(FailOpenHandler(rel, fn.name, child.lineno, "admit_on_error", "the handler keeps the loop's element because evaluating it failed"))
                if is_gate and _returns_true(child):
                    found.append(FailOpenHandler(rel, fn.name, child.lineno, "gate_returns_true", "an error in a deciding function returns True"))
                if _assigns_a_value(child) and _logs_quietly(child) and not _logs_loudly(child) and not _marked_best_effort(lines, child):
                    found.append(FailOpenHandler(rel, fn.name, child.lineno, "quiet_substitution", "a fallback value is taken with only a DEBUG/INFO log"))
            if isinstance(child, ast.If) and _is_finite_guarded_reject(child):
                found.append(FailOpenHandler(rel, fn.name, child.lineno, "finite_guarded_reject", "NaN/inf skip this rejection"))
            visit(child, vars_here)

    visit(fn, frozenset())
    return found


def find_fail_open_handlers(files: Iterable[Path], repo_root: Path, gate_name_re: str = DEFAULT_GATE_NAME_RE) -> list[FailOpenHandler]:
    """Every fail-open handler in ``files``, keyed by repo-relative path; unparseable files are skipped."""
    gate_re = re.compile(gate_name_re)
    out: list[FailOpenHandler] = []
    for path in files:
        try:
            text = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        rel = Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.extend(_scan_function(node, rel, lines, gate_re))
    return out


def assert_no_new_fail_open_handlers(
    files: Iterable[Path],
    repo_root: Path,
    baseline_path: Path,
    gate_name_re: str = DEFAULT_GATE_NAME_RE,
) -> None:
    """Fail on a fail-open handler beyond what *baseline_path* accepts for its ``path::function::rule``, and on stale entries.

    The baseline maps each accepted scope to a note; a scope listed ``n`` times (``scope``, ``scope#2``, ...) accepts ``n``
    findings, so a function keeps its accepted count however its handlers move.
    """
    accepted: dict[str, str] = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
    allowed: dict[str, int] = {}
    for key in accepted:
        scope = key.split("#", 1)[0]
        allowed[scope] = allowed.get(scope, 0) + 1
    by_scope: dict[str, list[FailOpenHandler]] = {}
    for h in find_fail_open_handlers(files, repo_root, gate_name_re):
        by_scope.setdefault(h.scope, []).append(h)
    new = [h for scope, hs in sorted(by_scope.items()) for h in hs[allowed.get(scope, 0):]]
    if new:
        lines = "\n  ".join(f"{h.path}:{h.lineno} [{h.rule}] in {h.function}: {h.detail}" for h in new)
        raise AssertionError(
            f"{len(new)} fail-open handler(s) in gate code -- a failure inside a gate must not disable the gate:\n  {lines}\n"
            "Reject (and record why) instead of keeping the candidate, log a substitution at WARNING, reject non-finite scores "
            "explicitly, or, when the fallback genuinely cannot matter, mark the except line with '# best-effort: <reason>'."
        )
    stale = sorted(scope for scope, n in allowed.items() if n > len(by_scope.get(scope, ())))
    if stale:
        raise AssertionError(f"these baseline scopes accept more fail-open handlers than remain -- lower or remove them: {stale}")
