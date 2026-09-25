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

import json
import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import BaselineGrowthError, ScanResult, dump_json, refresh_requested, scan_python, write_ratchet

REFRESH_FLAG = "--refresh-function-length-baseline"


def _walk(node: ast.AST, prefix: str, rel: str, out: dict[str, int]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            _walk(child, f"{prefix}{child.name}.", rel, out)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{prefix}{child.name}"
            key = f"{rel}::{qual}"
            out[key] = max(out.get(key, 0), (child.end_lineno or child.lineno) - child.lineno + 1)
            _walk(child, f"{qual}.<locals>.", rel, out)
        else:
            _walk(child, prefix, rel, out)


def _lengths(scan: ScanResult) -> dict[str, int]:
    out: dict[str, int] = {}
    for parsed in scan:
        _walk(parsed.tree, "", parsed.rel, out)
    return out


def function_lengths(files: Iterable[Path], root: Path) -> dict[str, int]:
    """``{"path::Qual.name": lines}``. Unparsable files are not measured here; :func:`assert_functions_do_not_grow`
    fails on them."""
    return _lengths(scan_python([Path(f) for f in files], root=Path(root)))


def length_problems(lengths: dict[str, int], baseline: dict[str, int], *, limit: int) -> list[str]:
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
    problems += [f"{key}: in the baseline but gone -- remove the entry" for key in sorted(set(baseline) - set(lengths))]
    return problems


def write_length_baseline(path: Path, lengths: dict[str, int], *, limit: int, grow: Optional[bool] = None, request: Any = None) -> None:
    """Write every function over *limit* (strictly) with its length as its ceiling; atomic, sorted, LF.

    Shrink-only unless growth is allowed (see ``_core.write_ratchet``): a new long function or a raised ceiling raises
    ``BaselineGrowthError`` after the removals are written."""
    over = {k: v for k, v in sorted(lengths.items()) if v > limit}
    previous = json.loads(Path(path).read_text(encoding="utf-8-sig")) if Path(path).is_file() else None
    write_ratchet(path, over, gate="function-length", previous=previous, render=dump_json, grow=grow, request=request)


def assert_functions_do_not_grow(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    *,
    limit: int = 150,
    min_functions: int = 50,
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
        pytest.fail(f"only {len(lengths)} function(s) measured; expected at least {min_functions} -- the file walk broke and this would check nothing")
    problems: list[str] = [f"{u.rel}:{u.line}: {u.kind}, so its functions were not measured: {u.message}" for u in scan.unparsed]
    if refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request):
        if problems:
            pytest.fail("cannot refresh the function-length baseline while files are unparsable:\n  " + "\n  ".join(problems))
        try:
            write_length_baseline(baseline_path, lengths, limit=limit, grow=grow, request=request)
        except BaselineGrowthError as exc:
            pytest.fail(str(exc), pytrace=False)
        pytest.skip(f"function-length baseline written to {baseline_path}")
    if not baseline_path.is_file():
        pytest.fail(f"function-length baseline {baseline_path} does not exist; create it with {REFRESH_FLAG} (a clean repo commits {{}})")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
    problems += length_problems(lengths, baseline, limit=limit)
    if problems:
        pytest.fail(f"{len(problems)} function-length problem(s) ({baseline_path.name}, limit {limit}):\n  " + "\n  ".join(problems))
