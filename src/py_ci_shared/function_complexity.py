"""Shared ratchet: no NEW over-complex function, and the complex ones already there may not get more complex.

The same rule as :mod:`py_ci_shared.function_length`, for McCabe cyclomatic complexity instead of line count, against a
committed baseline ``{"path::Qual.name": ceiling}``:

* a function whose complexity exceeds *limit* and is not in the baseline fails - split the new branching into helpers;
* a baselined function above its ceiling fails;
* a baselined function that got simpler, or fell to *limit* or below, fails until the baseline is refreshed, so the
  gain is locked in rather than available to be spent again.

Complexity is ruff's C901 number (``ruff check --select C901``), so the gate and the linter a developer runs agree
exactly (ruff also counts a nested function's branches into its enclosing function); ruff reports only functions over
the threshold, which is all the ratchet needs. Each reported ``def`` line is
mapped to its ``path::Qual.name`` (nested functions under ``<locals>``), so moving a function does not reset it.
"""

from __future__ import annotations

import ast
import json
import subprocess  # nosec B404 - runs the ruff module of the current interpreter on repository files
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import atomic_write_text, dump_json, refresh_requested, scan_python

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


def _batches(paths: list, max_chars: int = 24000) -> list:
    """Split ``paths`` into argv-sized groups (Windows caps a command line at 32767 characters)."""
    out: list = [[]]
    size = 0
    for p in paths:
        if out[-1] and size + len(p) + 1 > max_chars:
            out.append([])
            size = 0
        out[-1].append(p)
        size += len(p) + 1
    return [b for b in out if b]


def function_complexities(files: Iterable[Path], root: Path, *, limit: int) -> tuple[dict[str, int], set[str]]:
    """``({"path::Qual.name": complexity for every function over limit}, {every function key measured})``."""
    root = Path(root)
    scan = scan_python([Path(f) for f in files], root=root)
    by_file = {Path(p.path).resolve(): (p.rel, _def_lines(p.tree, p.rel)) for p in scan}
    all_keys = {key for _rel, lines in by_file.values() for key in lines.values()}
    if not by_file:
        return {}, all_keys
    base = [sys.executable, "-m", "ruff", "check", "--no-cache", "--isolated", "--select", "C901", "--config", f"lint.mccabe.max-complexity={int(limit)}",
            "--output-format", "json", "--exit-zero"]
    items: list = []
    for batch in _batches([str(p) for p in by_file]):
        proc = subprocess.run([*base, *batch], capture_output=True, text=True, check=False)  # nosec B603 - fixed argv, no shell
        if proc.returncode != 0:
            raise RuntimeError(f"ruff failed ({proc.returncode}): {proc.stderr.strip()[:2000]}")
        items.extend(json.loads(proc.stdout or "[]"))
    out: dict[str, int] = {}
    for item in items:
        if item.get("code") != "C901":
            continue
        rel, lines = by_file.get(Path(item["filename"]).resolve(), (None, {}))
        key = lines.get(int(item["location"]["row"]))
        if rel is None or key is None:
            continue
        n = int(item["message"].rsplit("(", 1)[1].split(">", 1)[0].strip())
        out[key] = max(out.get(key, 0), n)
    return out, all_keys


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


def write_complexity_baseline(path: Path, complexities: dict[str, int]) -> None:
    """Write every over-limit function with its complexity as its ceiling; atomic, sorted, LF."""
    atomic_write_text(path, dump_json(dict(sorted(complexities.items()))))


def assert_complexity_does_not_grow(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    *,
    limit: int = 25,
    min_functions: int = 50,
    refresh: Optional[bool] = None,
    request: Any = None,
) -> None:
    """Fail on the rules in the module docstring and on a missing baseline (a clean repo commits ``{}``). A refresh
    (*refresh*, ``--refresh-function-complexity-baseline`` or ``PY_CI_SHARED_REFRESH=function-complexity``) rewrites the
    baseline and skips."""
    import pytest

    complexities, all_keys = function_complexities(files, root, limit=limit)
    if len(all_keys) < min_functions:
        pytest.fail(f"only {len(all_keys)} function(s) measured; expected at least {min_functions} -- the file walk broke and this would check nothing")
    if refresh if refresh is not None else refresh_requested(REFRESH_FLAG, request):
        write_complexity_baseline(baseline_path, complexities)
        pytest.skip(f"function-complexity baseline written to {baseline_path}")
    if not baseline_path.is_file():
        pytest.fail(f"function-complexity baseline {baseline_path} does not exist; create it with {REFRESH_FLAG} (a clean repo commits {{}})")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
    problems = complexity_problems(complexities, all_keys, baseline, limit=limit)
    if problems:
        pytest.fail(f"{len(problems)} function-complexity problem(s) ({baseline_path.name}, limit {limit}):\n  " + "\n  ".join(problems))
