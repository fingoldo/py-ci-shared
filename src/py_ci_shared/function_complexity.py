"""Shared ratchet: no NEW over-complex function, and the complex ones already there may not get more complex.

The same rule as :mod:`py_ci_shared.function_length`, for McCabe cyclomatic complexity instead of line count, against a
committed baseline ``{"path::Qual.name": ceiling}``:

* a function whose complexity exceeds *limit* and is not in the baseline fails - split the new branching into helpers;
* a baselined function above its ceiling fails;
* a baselined function that got simpler, or fell to *limit* or below, fails until the baseline is refreshed, so the
  gain is locked in rather than available to be spent again.

Complexity is ruff's C901 number, computed by :mod:`py_ci_shared.complexity_ratchet` from the AST (it matches
``ruff check --select C901`` on every function, so no ruff process is needed). This module keeps its original API and
baseline format for repositories that already call it; new code can use :mod:`py_ci_shared.complexity_ratchet`, which
adds ``fail_on_shrink``. Keys are ``path::Qual.name`` (nested functions under ``<locals>``), so moving a function does
not reset it.

Like :mod:`py_ci_shared.complexity_ratchet`, a file the parser cannot read fails the gate and blocks a refresh, unless
*allow_unparsed*, which tolerates it and leaves its baseline entries alone; an unreadable baseline fails by name.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from . import complexity_ratchet
from ._core import ScanResult, dump_json, refresh_requested, scan_python
from ._core.baseline import write_ratchet
from ._core.errors import BaselineError

REFRESH_FLAG = "--refresh-function-complexity-baseline"


def _def_lines(tree: ast.AST, rel: str) -> dict[int, str]:
    """``{def line: "rel::Qual.name"}`` for every function in ``tree``."""
    out: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                out[child.lineno] = f"{rel}::{qual}"
                walk(child, f"{qual}.<locals>.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def _measured(scan: ScanResult, *, limit: int) -> tuple[dict[str, int], set[str]]:
    all_keys = {key for p in scan for key in _def_lines(p.tree, p.rel).values()}
    values = complexity_ratchet._measure(scan)[0]
    return {k: v for k, v in values.items() if v > limit}, all_keys


def function_complexities(files: Iterable[Path], root: Path, *, limit: int) -> tuple[dict[str, int], set[str]]:
    """``({"path::Qual.name": complexity for every function over limit}, {every function key measured})``. Unparsable
    files are not measured here; :func:`assert_complexity_does_not_grow` fails on them."""
    return _measured(scan_python([Path(f) for f in files], root=Path(root)), limit=limit)


def complexity_problems(complexities: dict[str, int], all_keys: set[str], baseline: dict[str, int], *, limit: int) -> list[str]:
    problems: list[str] = []
    for key, n in sorted(complexities.items()):
        if key in baseline:
            if n > baseline[key]:
                problems.append(f"{key}: complexity {n}, over its ceiling of {baseline[key]} -- put the new branching in a helper")
            elif n < baseline[key]:
                problems.append(f"{key}: complexity {n}, under its ceiling of {baseline[key]} -- refresh the baseline to lock the gain in")
        else:
            problems.append(f"{key}: complexity {n}, over the limit of {limit} -- split it into helpers")
    for key in sorted(set(baseline) - set(complexities)):
        if key in all_keys:
            problems.append(f"{key}: now within the limit of {limit} -- remove the entry (a refresh drops it)")
        else:
            problems.append(f"{key}: in the baseline but gone -- remove the entry")
    return problems


def write_complexity_baseline(
    path: Path, complexities: dict[str, int], *, grow: Optional[bool] = None, request: Any = None, unparsed: Iterable[str] = ()
) -> None:
    """Write every over-limit function with its complexity as its ceiling; atomic, sorted, LF. Shrink-only unless growth
    is allowed (``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``, ``--py-ci-refresh-grow`` or *grow*). Entries in an *unparsed*
    file are kept. Raises :class:`BaselineError` when the existing baseline is unreadable."""
    previous = complexity_ratchet.load_complexity_baseline(path) if Path(path).is_file() else None
    current = dict(complexities)
    if previous is not None:
        current.update(complexity_ratchet.held_by_unparsed(previous, unparsed))
    write_ratchet(path, dict(sorted(current.items())), gate="function-complexity", previous=previous, render=dump_json, grow=grow, request=request)


def assert_complexity_does_not_grow(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    *,
    limit: int = 25,
    min_functions: int = 50,
    refresh: Optional[bool] = None,
    request: Any = None,
    grow: Optional[bool] = None,
    allow_unparsed: bool = False,
) -> None:
    """Fail on the rules in the module docstring, on any unparsable file (unless *allow_unparsed*) and on a missing or
    unreadable baseline (a clean repo commits ``{}``). A refresh (*refresh*, ``--refresh-function-complexity-baseline``
    or ``PY_CI_SHARED_REFRESH=function-complexity``) rewrites the baseline and skips; it is refused while a file is
    unparsable, since it would drop that file's entries as a shrink."""
    import pytest

    scan = scan_python([Path(f) for f in files], root=Path(root))
    complexities, all_keys = _measured(scan, limit=limit)
    if len(all_keys) < min_functions:
        pytest.fail(f"only {len(all_keys)} function(s) measured; expected at least {min_functions} -- the file walk broke and this would check nothing")
    unparsed = [u.rel for u in scan.unparsed]
    problems = [] if allow_unparsed else [f"{u.rel}:{u.line}: {u.kind}, so its functions were not measured: {u.message}" for u in scan.unparsed]
    if refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request):
        if problems:
            pytest.fail("cannot refresh the function-complexity baseline while files are unparsable:\n  " + "\n  ".join(problems))
        try:
            write_complexity_baseline(baseline_path, complexities, grow=grow, request=request, unparsed=unparsed)
        except BaselineError as exc:
            pytest.fail(str(exc), pytrace=False)
        pytest.skip(f"function-complexity baseline written to {baseline_path}")
    baseline_path = Path(baseline_path)
    if not baseline_path.is_file():
        pytest.fail(f"function-complexity baseline {baseline_path} does not exist; create it with {REFRESH_FLAG} (a clean repo commits {{}})")
    try:
        baseline = complexity_ratchet.load_complexity_baseline(baseline_path)
    except BaselineError as exc:
        pytest.fail(str(exc), pytrace=False)
    held = complexity_ratchet.held_by_unparsed(baseline, unparsed)
    problems += complexity_problems(complexities, all_keys, {k: v for k, v in baseline.items() if k not in held}, limit=limit)
    if problems:
        pytest.fail(f"{len(problems)} function-complexity problem(s) ({baseline_path.name}, limit {limit}):\n  " + "\n  ".join(problems))
