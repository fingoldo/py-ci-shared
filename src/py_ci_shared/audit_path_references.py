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


def _path_chains(tree: ast.AST) -> dict[int, list[str]]:
    """``{id(constant): [every string segment of the `a / "b" / "c"` expression it sits in]}``."""
    chains: dict[int, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts: list[ast.AST] = []
            stack: list[ast.AST] = [node]
            while stack:
                cur = stack.pop()
                if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                    stack += [cur.left, cur.right]
                else:
                    parts.append(cur)
            segments = [p.value for p in parts if isinstance(p, ast.Constant) and isinstance(p.value, str)]
            for p in parts:
                if isinstance(p, ast.Constant):
                    chains.setdefault(id(p), []).extend(segments)
    return chains


def find_open_round_literals(
    files: Iterable[Path],
    own_audits: Iterable[Path],
    *,
    other_audits: Iterable[Path] = (),
    root: "Path | None" = None,
    implemented: str = "implemented",
) -> list[str]:
    """Literals pinning an open round.

    A BARE round name (``"2026-09-03"``, a path segment) is judged against the project's OWN open rounds
    only -- another project may well have an open round of the same date while this one's is closed. A
    string holding ``audits/<name>`` is judged against every project's open rounds. A segment that sits in
    the same ``a / b / c`` expression as ``implemented`` is a closed round and never reported.
    """
    own = open_round_names(own_audits, implemented=implemented)
    every = own | open_round_names(other_audits, implemented=implemented)
    if not every:
        return []
    in_path = re.compile(r"audits[/\\](" + "|".join(re.escape(n) for n in sorted(every)) + r")(?:$|[/\\])")
    problems: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        docs = _docstring_nodes(tree)
        chains = _path_chains(tree)
        rel = path.relative_to(root).as_posix() if root else path.as_posix()
        hits = sorted(
            (node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
            and implemented not in chains.get(id(node), [])
            and (node.value in own or in_path.search(node.value))
        )
        problems += [f"{rel}:{line}: {value[:80]!r} pins an OPEN audit round, which moves when it closes -- resolve it instead" for line, value in hits]
    return problems


def assert_no_open_round_paths(
    files: Iterable[Path],
    own_audits: Iterable[Path],
    *,
    other_audits: Iterable[Path] = (),
    root: "Path | None" = None,
    known: Iterable[str] = (),
    min_files: int = 1,
) -> None:
    """Shrink-only against *known*: entries are ``path:<value>`` so a line shift does not break them."""
    import pytest

    files = list(files)
    if len(files) < min_files:
        pytest.fail(f"only {len(files)} file(s) scanned; expected at least {min_files} -- this would check nothing")
    found = find_open_round_literals(files, list(own_audits), other_audits=list(other_audits), root=root)
    keys = {f"{p.split(':', 1)[0]}:{p.split(': ', 1)[1].split(' pins', 1)[0]}": p for p in found}
    new, stale = sorted(set(keys) - set(known)), sorted(set(known) - set(keys))
    if new or stale:
        pytest.fail(
            (f"{len(new)} literal path(s) into an open audit round:\n  " + "\n  ".join(keys[k] for k in new) if new else "")
            + (f"\n{len(stale)} accepted entr(ies) no longer reproduce -- remove them:\n  " + "\n  ".join(stale) if stale else "")
        )
