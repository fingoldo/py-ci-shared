"""Shared check: code never pins the path of an OPEN audit round.

An open round's files move: the convention closes a round by moving it to ``implemented/<date>/``.
A disposition claim in production_scrapers read ``dashboard/audits/2026-09-03/03_performance.md``;
when that round closed on 2026-09-11 the claim raised FileNotFoundError and turned master's tracker
gate red over a file that was fine. Code that needs a round file must find it where it is (the
consumer's own resolver, e.g. ``audit_file()``), and a path under ``implemented/`` is final.

The rule: a string literal in code (docstrings excluded -- prose that NAMES a round is not a path
anybody opens) that is the name of an OPEN round directory, or contains ``audits/<that name>``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}")


def open_round_names(audits_dirs: Iterable[Path], *, implemented: str = "implemented") -> set[str]:
    names: set[str] = set()
    for audits in audits_dirs:
        if audits.is_dir():
            names |= {d.name for d in audits.iterdir() if d.is_dir() and d.name != implemented and _DATED.match(d.name)}
    return names


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def find_open_round_literals(files: Iterable[Path], audits_dirs: Iterable[Path], *, root: "Path | None" = None) -> list[str]:
    names = open_round_names(audits_dirs)
    if not names:
        return []
    pattern = re.compile(r"(?:^|[/\\])(" + "|".join(re.escape(n) for n in sorted(names)) + r")(?:$|[/\\])")
    problems: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        docs = _docstring_nodes(tree)
        rel = path.relative_to(root).as_posix() if root else path.as_posix()
        hits = sorted(
            (node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
            and (node.value in names or ("audits" in node.value and pattern.search(node.value)))
        )
        problems += [f"{rel}:{line}: {value[:80]!r} pins an OPEN audit round, which moves when it closes -- resolve it instead" for line, value in hits]
    return problems


def assert_no_open_round_paths(files: Iterable[Path], audits_dirs: Iterable[Path], *, root: "Path | None" = None, min_files: int = 1) -> None:
    import pytest

    files = list(files)
    if len(files) < min_files:
        pytest.fail(f"only {len(files)} file(s) scanned; expected at least {min_files} -- this would check nothing")
    problems = find_open_round_literals(files, list(audits_dirs), root=root)
    if problems:
        pytest.fail(f"{len(problems)} literal path(s) into an open audit round:\n  " + "\n  ".join(problems))
