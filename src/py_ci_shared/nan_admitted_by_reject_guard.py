"""A reject guard written as an ordered comparison (``if loss >= worst: continue``) admits NaN, because NaN fails every comparison.

mlframe 584c9d880: the CPU ``_insert_top`` kept a NaN loss in the last slot of a top-N because ``loss >= worst`` is False for NaN, so the candidate was not rejected; the later non-finite filter dropped
it and a cardinality came back one candidate short. The fix was the negated accepting form, ``if not loss < worst: continue`` (True for NaN, so NaN is rejected). The same shape guards thresholds,
early stopping and best-so-far updates.

Matched: an ``if`` with no ``else`` whose test is one ordered comparison (``>``, ``>=``, ``<``, ``<=``) between a score-like name (``loss``, ``score``, ``cost``, ``err``, ``metric``, ``value`` ... as an
underscore-separated token) and a bound-like name (``worst``, ``best``, ``cutoff``, ``thresh``, ``threshold``, ``bound``, ``limit``), and whose body is only ``continue``/``break`` or only ``return``, in a function
that shows float evidence (``nan``/``inf``/``float(``/``isnan`` ... anywhere in it, or a ``float`` annotation on the name). Rules, by what the rejection does:

* ``nan-admitted-by-loop-reject``: the body is ``continue``/``break``, so a NaN stays in the loop and is admitted downstream.
* ``nan-admitted-by-return-reject``: the body is a ``return`` (an early exit that a NaN does not take).

Not matched: ``if not loss < worst`` (the NaN-safe form), a guard preceded in the function by ``isnan``/``isfinite`` of the same name (or ``x != x`` / ``x == x``), a function with no float evidence (an
integer counter compared with a limit), and an ``if`` with an ``else`` (the other arm handles the NaN).

Suppression: ``# nan-ok: <reason>`` on the ``if`` line (the value is finite by construction, or a NaN is handled below).

Usage in a consumer's meta test::

    from py_ci_shared.nan_admitted_by_reject_guard import assert_nan_admitted_by_reject_guard

    def test_reject_guards_reject_nan():
        assert_nan_admitted_by_reject_guard("src", baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, nodes_of, scan_python

__all__ = [
    "MARKER",
    "RULE_LOOP",
    "RULE_RETURN",
    "assert_nan_admitted_by_reject_guard",
    "find_nan_admitted_by_reject_guard",
]

RULE_LOOP = "nan-admitted-by-loop-reject"
RULE_RETURN = "nan-admitted-by-return-reject"
MARKER = "nan-ok:"

_SCORE_TOKENS = frozenset(
    {"loss", "score", "cost", "err", "error", "metric", "value", "val", "mse", "rmse", "mae", "dist", "distance", "fitness", "objective", "gain"}
)
_BOUND_TOKENS = frozenset({"worst", "best", "cutoff", "thresh", "threshold", "bound", "limit"})
_ORDERED = (ast.Gt, ast.GtE, ast.Lt, ast.LtE)
_NAN_TESTS = frozenset({"isnan", "isfinite", "isinf", "isna", "isnull"})
_FLOAT_NAMES = frozenset({"nan", "inf", "NAN", "INF", "float", "float32", "float64", "isnan", "isfinite"})


def _identifier(node: ast.AST) -> str:
    """The last identifier of a name, attribute or subscript (``self.worst[k]`` is ``worst``)."""
    while isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _tokens(identifier: str) -> "set[str]":
    return {t for t in identifier.lower().split("_") if t}


def _compared_names(test: ast.expr) -> "Optional[tuple[str, str]]":
    """``(score name, bound name)`` of a one-comparison score-versus-bound test, else ``None`` (either side may be the score)."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], _ORDERED)):
        return None
    sides = [_identifier(test.left), _identifier(test.comparators[0])]
    for score, bound in (sides, sides[::-1]):
        if _tokens(score) & _SCORE_TOKENS and _tokens(bound) & _BOUND_TOKENS and not _tokens(score) & _BOUND_TOKENS:
            return score, bound
    return None


def _reject_rule(body: "list[ast.stmt]") -> Optional[str]:
    if len(body) == 1 and isinstance(body[0], (ast.Continue, ast.Break)):
        return RULE_LOOP
    if len(body) == 1 and isinstance(body[0], ast.Return):
        return RULE_RETURN
    return None


def _has_float_evidence(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in _FLOAT_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _FLOAT_NAMES:
            return True
        if isinstance(node, ast.arg) and isinstance(node.annotation, ast.Name) and node.annotation.id == "float":
            return True
    return False


def _checks_nan_before(fn: ast.AST, name: str, line: int) -> bool:
    """An ``isnan``/``isfinite`` call on *name*, or ``name != name`` / ``name == name``, earlier in the function."""
    for node in ast.walk(fn):
        if getattr(node, "lineno", line) >= line:
            continue
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in _NAN_TESTS:
            if any(_identifier(a) == name for a in node.args):
                return True
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == name:
            if len(node.comparators) == 1 and isinstance(node.comparators[0], ast.Name) and node.comparators[0].id == name:
                return True
    return False


def _marked(lines: "list[str]", number: int) -> bool:
    if not 0 < number <= len(lines):
        return False
    line = lines[number - 1]
    return "#" in line and MARKER in line[line.index("#") :]


def _function_findings(fn: "ast.FunctionDef | ast.AsyncFunctionDef", rel: str, lines: "list[str]") -> "list[Finding]":
    if not _has_float_evidence(fn):
        return []
    out: "list[Finding]" = []
    for node in nodes_of(fn, ast.If):
        names = _compared_names(node.test)
        rule = _reject_rule(node.body) if not node.orelse else None
        if names is None or rule is None or _marked(lines, node.lineno) or _checks_nan_before(fn, names[0], node.lineno):
            continue
        out.append(
            Finding(
                rel,
                node.lineno,
                rule,
                f"`if {names[0]} <cmp> {names[1]}: ...` rejects only when the comparison is True, and NaN fails every comparison, so a NaN `{names[0]}` is admitted: write the accepting form "
                f"`if not {names[0]} <inverse cmp> {names[1]}:`, test `isnan`/`isfinite` first, or mark `# nan-ok: <reason>`",
            )
        )
    return out


def find_nan_admitted_by_reject_guard(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every ordered-comparison reject guard under *root* that lets a NaN through, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code
    must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        lines = parsed.source.splitlines()
        for fn in nodes_of(parsed.tree, ast.FunctionDef, ast.AsyncFunctionDef):
            out.extend(_function_findings(fn, parsed.rel, lines))
    return sorted(set(out), key=lambda f: (f.path, f.line, f.rule))


def assert_nan_admitted_by_reject_guard(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any NaN-admitting reject guard, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_nan_admitted_by_reject_guard(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "write the NaN-safe accepting form (`if not loss < worst:`), test isnan/isfinite first, or mark the `if` `# nan-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="nan_admitted_by_reject_guard", refresh_command="PY_CI_SHARED_REFRESH=nan_admitted_by_reject_guard")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} reject guard(s) that admit NaN; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
