"""Shared ratchet: no NEW long function, and the long ones already there may not grow.

``loc_budget`` bounds files. A file can stay under its budget while one function in it keeps
absorbing every fix: production_scrapers found on 2026-09-11 that every function its July audit had
left long on purpose (the split was judged a behaviour risk) had GROWN since -- ``scan_job_pub_batch2``
from 512 lines to 597 -- because each later fix added its branch inline. A won't-fix that keeps growing
is drift, not a decision.

The rule, against a committed baseline ``{"path::Qual.name": ceiling}``:

* a function longer than *limit* lines that is not in the baseline fails -- write the new branch as a
  helper instead;
* a baselined function longer than its ceiling fails;
* a baselined function that got SHORTER, or fell under *limit*, fails until the baseline is refreshed,
  so the gain is locked in rather than available to be spent again.

Length is ``end_lineno - lineno + 1`` of the ``def`` (decorators excluded), nested functions measured
on their own under a ``<locals>`` qualname. Two definitions under one qualname (a property getter and
its setter, ``typing.overload`` stubs) report the LONGEST, so a short setter cannot hide a long getter.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Union, Any, Optional

from ._core.outcome import refresh_hint, refreshed_skip
from ._core import BaselineGrowthError, ScanResult, dump_json, load_json, refresh_requested, scan_python, write_ratchet
from ._core.scopes import iter_scopes

__all__ = ["REFRESH_FLAG", "assert_functions_do_not_grow", "function_lengths", "length_problems", "write_length_baseline"]

REFRESH_FLAG = "--refresh-function-length-baseline"


def _measure_tree(tree: ast.Module, rel: str, out: dict[str, int]) -> None:
    """Every function's qualified name and length, from the shared scope index, in source order (the order the walk this replaces used)."""
    qual_of: dict[int, str] = {}
    found: list[tuple[int, int, str, Union[ast.FunctionDef, ast.AsyncFunctionDef]]] = []
    for scope in iter_scopes(tree):
        if scope.node is None:
            continue
        parent = scope.parent
        if parent is None or parent.node is None:
            prefix = ""
        elif isinstance(parent.node, ast.ClassDef):
            prefix = f"{qual_of[id(parent)]}."
        else:
            prefix = f"{qual_of[id(parent)]}.<locals>."
        qual_of[id(scope)] = f"{prefix}{scope.node.name}"
        if isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((scope.node.lineno, scope.node.col_offset, qual_of[id(scope)], scope.node))
    for _, _, qual, node in sorted(found, key=lambda item: (item[0], item[1])):
        key = f"{rel}::{qual}"
        out[key] = max(out.get(key, 0), (node.end_lineno or node.lineno) - node.lineno + 1)


def _lengths(scan: ScanResult) -> dict[str, int]:
    out: dict[str, int] = {}
    for parsed in scan:
        _measure_tree(parsed.tree, parsed.rel, out)
    return out


def function_lengths(files: Iterable[Path], root: Path) -> dict[str, int]:
    """``{"path::Qual.name": lines}``. Unparsable files are not measured here; :func:`assert_functions_do_not_grow`
    fails on them."""
    return _lengths(scan_python([Path(f) for f in files], root=Path(root)))


def length_problems(lengths: dict[str, int], baseline: dict[str, int], *, limit: int) -> list[str]:
    """Messages for functions whose line count rose over their baseline ceiling, now fit the limit, or are over ``limit`` with no baseline entry."""
    problems: list[str] = []
    for key, n in sorted(lengths.items()):
        if key in baseline:
            if n > baseline[key]:
                problems.append(f"{key}: {n} lines, over its ceiling of {baseline[key]} -- put the new branch in a helper")
            elif n < baseline[key] and n <= limit:
                problems.append(f"{key}: {n} lines, now within the {limit}-line limit -- remove the entry (a refresh drops it)")
            elif n < baseline[key]:
                problems.append(f"{key}: {n} lines, under its ceiling of {baseline[key]} -- refresh the baseline to lock the gain in")
        elif n > limit:
            problems.append(f"{key}: {n} lines, over the {limit}-line limit -- split it, or it becomes the next one nobody can read")
    problems += [
        f"{key}: in the baseline but gone -- remove the entry, or run " + refresh_hint(REFRESH_FLAG, "function_length") + " to drop it"
        for key in sorted(set(baseline) - set(lengths))
    ]
    return problems


def write_length_baseline(path: Path, lengths: dict[str, int], *, limit: int, grow: Optional[bool] = None, request: Any = None) -> None:
    """Write every function over *limit* (strictly) with its length as its ceiling; atomic, sorted, LF.

    Shrink-only unless growth is allowed (see ``_core.write_ratchet``): a new long function or a raised ceiling raises
    ``BaselineGrowthError`` after the removals are written."""
    over = {k: v for k, v in sorted(lengths.items()) if v > limit}
    previous = load_json(path) if Path(path).is_file() else None
    write_ratchet(path, over, gate="function-length", previous=previous, render=dump_json, grow=grow, request=request)


def assert_functions_do_not_grow(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    *,
    limit: int = 150,
    min_functions: int = 50,
    allow_unparsed: bool = False,
    refresh: Optional[bool] = None,
    request: Any = None,
    grow: Optional[bool] = None,
) -> None:
    """Fail on the rules in the module docstring, on any unparsable file, and on a missing baseline (a clean repo
    commits ``{}``). A refresh (*refresh*, ``--refresh-function-length-baseline`` or ``PY_CI_SHARED_REFRESH=
    function-length``) rewrites the baseline and skips; it only drops and lowers entries unless *grow* is allowed."""
    import pytest

    scan = scan_python([Path(f) for f in files], root=Path(root))
    lengths = _lengths(scan)
    if len(lengths) < min_functions:
        pytest.fail(
            f"only {len(lengths)} function(s) measured; expected at least {min_functions} -- the file walk broke and this would check nothing (lower min_functions in [tool.py_ci_shared.gates.function_length] if the repo is genuinely this small)"
        )
    problems: list[str] = [f"{u.rel}:{u.line}: {u.kind}, so its functions were not measured: {u.message}" for u in ([] if allow_unparsed else scan.unparsed)]
    if refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request):
        if problems:
            pytest.fail("cannot refresh the function-length baseline while files are unparsable:\n  " + "\n  ".join(problems))
        try:
            write_length_baseline(baseline_path, lengths, limit=limit, grow=grow, request=request)
        except BaselineGrowthError as exc:
            pytest.fail(str(exc), pytrace=False)
        refreshed_skip(f"function-length baseline written to {baseline_path.name}")
    if not baseline_path.is_file():
        pytest.fail(
            f"function-length baseline {baseline_path.name} does not exist; create it with {refresh_hint(REFRESH_FLAG, 'function_length')} (a clean repo commits {{}})"
        )
    baseline = load_json(baseline_path)
    if not isinstance(baseline, dict) or not all(isinstance(k, str) and isinstance(v, int) for k, v in baseline.items()):
        pytest.fail(
            f'{baseline_path.name} is not a {{"path::function": lines}} object (got {type(baseline).__name__}); fix the file by hand, a refresh would drop the malformed entries'
        )
    problems += length_problems(lengths, baseline, limit=limit)
    if problems:
        pytest.fail(
            f"{len(problems)} function-length problem(s) ({baseline_path.name}, limit {limit} lines per function, set by `limit` in [tool.py_ci_shared.gates.function_length]):\n  "
            + "\n  ".join(problems)
        )
