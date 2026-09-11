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
on their own under a ``<locals>`` qualname.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable
from pathlib import Path


def _walk(node: ast.AST, prefix: str, rel: str, out: dict[str, int]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            _walk(child, f"{prefix}{child.name}.", rel, out)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{prefix}{child.name}"
            out[f"{rel}::{qual}"] = (child.end_lineno or child.lineno) - child.lineno + 1
            _walk(child, f"{qual}.<locals>.", rel, out)
        else:
            _walk(child, prefix, rel, out)


def function_lengths(files: Iterable[Path], root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        _walk(tree, "", path.relative_to(root).as_posix(), out)
    return out


def length_problems(lengths: dict[str, int], baseline: dict[str, int], *, limit: int) -> list[str]:
    problems: list[str] = []
    for key, n in sorted(lengths.items()):
        if key in baseline:
            if n > baseline[key]:
                problems.append(f"{key}: {n} lines, over its ceiling of {baseline[key]} -- put the new branch in a helper")
            elif n < baseline[key]:
                problems.append(f"{key}: {n} lines, under its ceiling of {baseline[key]} -- refresh the baseline to lock the gain in")
        elif n > limit:
            problems.append(f"{key}: {n} lines, over the {limit}-line limit -- split it, or it becomes the next one nobody can read")
    problems += [f"{key}: in the baseline but gone -- remove the entry" for key in sorted(set(baseline) - set(lengths))]
    return problems


def write_length_baseline(path: Path, lengths: dict[str, int], *, limit: int) -> None:
    over = {k: v for k, v in sorted(lengths.items()) if v > limit}
    path.write_text(json.dumps(over, indent=2) + "\n", encoding="utf-8")


def assert_functions_do_not_grow(files: Iterable[Path], root: Path, baseline_path: Path, *, limit: int = 150, min_functions: int = 50) -> None:
    import pytest

    lengths = function_lengths(files, root)
    if len(lengths) < min_functions:
        pytest.fail(f"only {len(lengths)} function(s) measured; expected at least {min_functions} -- the file walk broke and this would check nothing")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    problems = length_problems(lengths, baseline, limit=limit)
    if problems:
        pytest.fail(f"{len(problems)} function-length problem(s) ({baseline_path.name}, limit {limit}):\n  " + "\n  ".join(problems))
