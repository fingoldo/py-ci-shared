"""Shared ratchet: no NEW function over the cyclomatic-complexity limit, and the ones already over it may not grow.

Ruff's ``C901`` (mccabe) reports every function over ``max-complexity``, but a repo with a backlog can only ignore the
rule wholesale, and then nothing stops the next function from joining it. This gate turns the rule into a ratchet
against a committed baseline ``{"path::Qual.name": complexity}``:

* a function over *limit* that is not in the baseline fails: split the new branch into a helper;
* a baselined function more complex than its recorded value fails;
* a baselined function that got simpler, or fell to *limit* or under, fails until the baseline is refreshed (a
  refresh lowers or drops the entry), so the gain is locked in rather than available to be spent again;
* a baselined function that no longer exists fails the same way.

Complexity is ruff's mccabe number, computed here from the AST so the gate needs no ruff at run time: 1, plus one for
each ``if``/``elif``, ``for``, ``while``, ``except`` handler, ``try ... else``, ``match`` case (an irrefutable last
case excepted) and nested ``def``, recursing into ``with``, class and nested bodies; boolean operators and
comprehensions add nothing, as in ruff. Nested functions are measured on their own under a ``<locals>`` qualname too.
Two definitions under one qualname (a property getter and setter, overload stubs) report the HIGHER value.

A refresh (*refresh*, ``--refresh-complexity-baseline`` or ``PY_CI_SHARED_REFRESH=complexity``) is shrink-only unless
growth is opted into (*grow*, ``--py-ci-refresh-grow``, ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``).
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import BaselineGrowthError, ScanResult, dump_json, refresh_requested, scan_python, write_ratchet

REFRESH_FLAG = "--refresh-complexity-baseline"
DEFAULT_LIMIT = 10  # ruff's default max-complexity

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _irrefutable(pattern: ast.AST) -> bool:
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None
    if isinstance(pattern, ast.MatchOr):
        return any(_irrefutable(p) for p in pattern.patterns)
    return False


_TRIES: tuple[type[ast.stmt], ...] = (ast.Try, getattr(ast, "TryStar", ast.Try))


def _try_complexity(stmt: Any) -> int:
    """``try`` (or ``try*``): its bodies, one per handler, one for an ``else``."""
    total: int = _body_complexity(stmt.body) + _body_complexity(stmt.orelse) + _body_complexity(stmt.finalbody)
    total += 1 if stmt.orelse else 0
    return total + sum(1 + _body_complexity(h.body) for h in stmt.handlers)


def _body_complexity(stmts: Iterable[ast.stmt]) -> int:
    total = 0
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            total += 1 + _body_complexity(stmt.body) + _body_complexity(stmt.orelse)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            total += 1 + _body_complexity(stmt.body) + _body_complexity(stmt.orelse)
        elif isinstance(stmt, (ast.With, ast.AsyncWith, ast.ClassDef)):
            total += _body_complexity(stmt.body)
        elif isinstance(stmt, _FUNCS):
            total += 1 + _body_complexity(stmt.body)
        elif isinstance(stmt, getattr(ast, "Match", ())):
            cases = stmt.cases  # type: ignore[attr-defined]
            total += sum(1 + _body_complexity(case.body) for case in cases)
            if cases and cases[-1].guard is None and _irrefutable(cases[-1].pattern):
                total -= 1
        elif isinstance(stmt, _TRIES):
            total += _try_complexity(stmt)
    return total


def function_complexity(node: ast.AST) -> int:
    """Ruff's C901 number for one ``def``."""
    return 1 + _body_complexity(getattr(node, "body", []))


def _walk(node: ast.AST, prefix: str, rel: str, out: dict[str, int], lines: dict[tuple[str, int], int]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            _walk(child, f"{prefix}{child.name}.", rel, out, lines)
        elif isinstance(child, _FUNCS):
            qual = f"{prefix}{child.name}"
            key = f"{rel}::{qual}"
            value = function_complexity(child)
            out[key] = max(out.get(key, 0), value)
            lines[(rel, child.lineno)] = value
            _walk(child, f"{qual}.<locals>.", rel, out, lines)
        else:
            _walk(child, prefix, rel, out, lines)


def _measure(scan: ScanResult) -> tuple[dict[str, int], dict[tuple[str, int], int]]:
    out: dict[str, int] = {}
    lines: dict[tuple[str, int], int] = {}
    for parsed in scan:
        _walk(parsed.tree, "", parsed.rel, out, lines)
    return out, lines


def function_complexities(files: Iterable[Path], root: Path) -> dict[str, int]:
    """``{"path::Qual.name": complexity}`` for every function. Unparsable files are not measured here;
    :func:`assert_complexity_does_not_grow` fails on them."""
    return _measure(scan_python([Path(f) for f in files], root=Path(root)))[0]


def complexities_by_line(files: Iterable[Path], root: Path) -> dict[tuple[str, int], int]:
    """``{(path, def line): complexity}``, the shape ruff reports in, for comparing the two."""
    return _measure(scan_python([Path(f) for f in files], root=Path(root)))[1]


def complexity_problems(values: dict[str, int], baseline: dict[str, int], *, limit: int = DEFAULT_LIMIT, fail_on_shrink: bool = True) -> list[str]:
    """One line per way *values* break the ratchet against *baseline*; shrink lines only when *fail_on_shrink*."""
    problems: list[str] = []
    for key, n in sorted(values.items()):
        if key in baseline:
            if n > baseline[key]:
                problems.append(f"{key}: complexity {n}, over its recorded {baseline[key]} -- put the new branch in a helper")
            elif fail_on_shrink and n < baseline[key] and n <= limit:
                problems.append(f"{key}: complexity {n}, now within the limit of {limit} -- remove the entry (a refresh drops it)")
            elif fail_on_shrink and n < baseline[key]:
                problems.append(f"{key}: complexity {n}, under its recorded {baseline[key]} -- refresh the baseline to lock the gain in")
        elif n > limit:
            problems.append(f"{key}: complexity {n}, over the limit of {limit} -- split it into helpers before it joins the backlog")
    if fail_on_shrink:
        problems += [f"{key}: in the baseline but gone -- remove the entry (a refresh drops it)" for key in sorted(set(baseline) - set(values))]
    return problems


def write_complexity_baseline(path: Path, values: dict[str, int], *, limit: int = DEFAULT_LIMIT, grow: Optional[bool] = None, request: Any = None) -> None:
    """Record every function over *limit* with its complexity; shrink-only unless growth is allowed (then a new or
    raised entry raises ``BaselineGrowthError`` after the removals are written)."""
    over = {k: v for k, v in sorted(values.items()) if v > limit}
    previous = json.loads(Path(path).read_text(encoding="utf-8-sig")) if Path(path).is_file() else None
    write_ratchet(path, over, gate="complexity", previous=previous, render=dump_json, grow=grow, request=request)


def assert_complexity_does_not_grow(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    *,
    limit: int = DEFAULT_LIMIT,
    min_functions: int = 50,
    fail_on_shrink: bool = True,
    refresh: Optional[bool] = None,
    request: Any = None,
    grow: Optional[bool] = None,
) -> None:
    """Fail on the rules in the module docstring, on any unparsable file, on fewer than *min_functions* measured, and
    on a missing baseline (a clean repo commits ``{}``). A refresh rewrites the baseline and skips."""
    import pytest

    scan = scan_python([Path(f) for f in files], root=Path(root))
    values = _measure(scan)[0]
    if len(values) < min_functions:
        pytest.fail(f"only {len(values)} function(s) measured; expected at least {min_functions} -- the file walk broke and this would check nothing")
    problems: list[str] = [f"{u.rel}:{u.line}: {u.kind}, so its functions were not measured: {u.message}" for u in scan.unparsed]
    if refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request):
        if problems:
            pytest.fail("cannot refresh the complexity baseline while files are unparsable:\n  " + "\n  ".join(problems))
        try:
            write_complexity_baseline(baseline_path, values, limit=limit, grow=grow, request=request)
        except BaselineGrowthError as exc:
            pytest.fail(str(exc), pytrace=False)
        pytest.skip(f"complexity baseline written to {baseline_path}")
    if not Path(baseline_path).is_file():
        pytest.fail(f"complexity baseline {baseline_path} does not exist; create it with {REFRESH_FLAG} (a clean repo commits {{}})")
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8-sig"))
    problems += complexity_problems(values, baseline, limit=limit, fail_on_shrink=fail_on_shrink)
    if problems:
        pytest.fail(f"{len(problems)} complexity problem(s) ({Path(baseline_path).name}, limit {limit}):\n  " + "\n  ".join(problems))
