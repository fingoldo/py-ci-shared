"""Shared check: code never pins the path of an OPEN audit round.

An open round's files move: the convention closes a round by moving it to ``implemented/<date>/``.
A disposition claim in production_scrapers read ``dashboard/audits/2026-09-03/03_performance.md``;
when that round closed on 2026-09-11 the claim raised FileNotFoundError and turned master's tracker
gate red over a file that was fine. Code that needs a round file must find it where it is (the
consumer's own resolver, e.g. ``audit_file()``), and a path under ``implemented/`` is final.

The rule: a string literal in code (docstrings excluded -- prose that NAMES a round is not a path
anybody opens) that is the name of an OPEN round directory, or contains ``audits/<that name>``. A path
built in pieces counts the same: ``os.path.join(ROOT, "audits", name)``/``Path(a, b)`` arguments are path
segments like the parts of ``a / b``, and ``"x/" + name`` / ``f"{R}/2026-09-03/x.md"`` are folded into one
string first. A file that cannot be parsed is reported, never skipped.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple, Optional

from ._core import scan_python
from ._core.node_index import walk as _fast_walk

_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}")


def open_round_names(audits_dirs: Iterable[Path], *, implemented: str = "implemented") -> set[str]:
    names: set[str] = set()
    for audits in audits_dirs:
        if audits.is_dir():
            names |= {d.name for d in audits.iterdir() if d.is_dir() and d.name != implemented and _DATED.match(d.name)}
    return names


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in _fast_walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


_PATH_CALLS = frozenset({"join", "Path", "PurePath", "PosixPath", "WindowsPath", "PurePosixPath", "PureWindowsPath"})


def _call_name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _path_chains(tree: ast.AST) -> dict[int, list[str]]:
    """``{id(constant): [every string segment of the path expression it sits in]}``.

    A path expression is an ``a / "b" / "c"`` chain or the arguments of ``os.path.join(...)``/``Path(...)``.
    """
    chains: dict[int, list[str]] = {}
    for node in _fast_walk(tree):
        parts: list[ast.AST] = []
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            stack: list[ast.AST] = [node]
            while stack:
                cur = stack.pop()
                if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                    stack += [cur.left, cur.right]
                elif isinstance(cur, ast.Call) and _call_name(cur.func) in _PATH_CALLS:
                    stack += list(cur.args)
                else:
                    parts.append(cur)
        elif isinstance(node, ast.Call) and _call_name(node.func) in _PATH_CALLS:
            parts = list(node.args)
        if not parts:
            continue
        segments = [p.value for p in parts if isinstance(p, ast.Constant) and isinstance(p.value, str)]
        for p in parts:
            if isinstance(p, ast.Constant):
                chains.setdefault(id(p), []).extend(segments)
    return chains


def _fold(node: ast.expr) -> Optional[str]:
    """The string an ``a + "b"`` chain or f-string builds, with ``{}`` for every non-literal piece; None if not a string build."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left), _fold(node.right)
        if left is None and right is None:
            return None
        return (left if left is not None else "{}") + (right if right is not None else "{}")
    return None


def _folded_strings(tree: ast.AST) -> list[tuple[ast.expr, str]]:
    """``(node, folded text)`` for every OUTERMOST f-string or string concatenation."""
    inner: set[int] = set()
    out: list[tuple[ast.expr, str]] = []
    for node in _fast_walk(tree):
        if id(node) in inner or not isinstance(node, (ast.JoinedStr, ast.BinOp)):
            continue
        if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Add):
            continue
        text = _fold(node)
        if text is None:
            continue
        out.append((node, text))
        inner.update(id(n) for n in _fast_walk(node))
    return out


_RESOLVER = re.compile(r"audit|round", re.I)
_AUDIT_FILE = re.compile(r"\.md$|^\d\d[a-z]?[-_]", re.I)
_SEGMENT_SPLIT = re.compile(r"[/\\]")


def _used_as_a_path(tree: ast.AST) -> set[int]:
    """``{id(constant)}`` for bare strings a call USES as a path segment.

    A call that resolves a round by name (``audit_file("2026-09-03", "03_performance.md")``,
    ``round_dir(name)``), or one carrying an audit file name in another argument, is pinning a path. A
    date sitting in a tuple of expected values is not.
    """
    ids: set[int] = set()
    for node in _fast_walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args: list[tuple[ast.Constant, str]] = [
            (a, a.value) for a in (*node.args, *(k.value for k in node.keywords)) if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        name = _call_name(node.func)
        if _RESOLVER.search(name) or any(_AUDIT_FILE.search(value) for _, value in args):
            ids |= {id(a) for a, _ in args}
    return ids


def _segments_pin(text: str, own: set[str], implemented: str) -> bool:
    """A built string whose ``/``-segments contain an own open round next to ``audits`` or an audit file name."""
    segments = _SEGMENT_SPLIT.split(text)
    if implemented in segments or not own.intersection(segments):
        return False
    return any(seg == "audits" or _AUDIT_FILE.search(seg) for seg in segments if seg not in own)


class RoundHit(NamedTuple):
    rel: str
    line: int
    value: str
    kind: str = "pin"  # "pin" or "unparsable"

    def render(self) -> str:
        if self.kind == "unparsable":
            return f"{self.rel}:{self.line}: {self.value}"
        return f"{self.rel}:{self.line}: {self.value[:80]!r} pins an OPEN audit round, which moves when it closes -- resolve it instead"

    @property
    def key(self) -> str:
        return f"{self.rel}:{self.value}"


def collect_open_round_literals(
    files: Iterable[Path],
    own_audits: Iterable[Path],
    *,
    other_audits: Iterable[Path] = (),
    root: "Path | None" = None,
    implemented: str = "implemented",
) -> "tuple[list[RoundHit], int]":
    """``(hits, parsed file count)``; unparsable files are hits of kind ``"unparsable"``."""
    own = open_round_names(own_audits, implemented=implemented)
    every = own | open_round_names(other_audits, implemented=implemented)
    scan = scan_python(list(files), root=root, min_files=0)
    hits: list[RoundHit] = [RoundHit(p.rel, p.line, f"{p.kind}: {p.message}", "unparsable") for p in scan.unparsed]
    if not every:
        return hits, scan.parsed_count
    in_path = re.compile(r"audits[/\\](" + "|".join(re.escape(n) for n in sorted(every)) + r")(?:$|[/\\])")
    for f in scan:
        tree = f.tree
        docs = _docstring_nodes(tree)
        chains = _path_chains(tree)
        as_path = _used_as_a_path(tree) | set(chains)
        found: set[tuple[int, str]] = set()
        for node in _fast_walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docs
                and implemented not in chains.get(id(node), [])
                and ((node.value in own and id(node) in as_path) or in_path.search(node.value))
            ):
                found.add((node.lineno, node.value))
        for node, text in _folded_strings(tree):
            if implemented in _SEGMENT_SPLIT.split(text):
                continue
            if in_path.search(text) or _segments_pin(text, own, implemented):
                found.add((node.lineno, text))
        hits += [RoundHit(f.rel, line, value) for line, value in sorted(found)]
    return hits, scan.parsed_count


def find_open_round_literals(
    files: Iterable[Path],
    own_audits: Iterable[Path],
    *,
    other_audits: Iterable[Path] = (),
    root: "Path | None" = None,
    implemented: str = "implemented",
) -> list[str]:
    """Literals pinning an open round, plus ``path:line: unparsable: ...`` for a file that could not be parsed.

    A BARE round name (``"2026-09-03"``, a path segment) is judged against the project's OWN open rounds
    only -- another project may well have an open round of the same date while this one's is closed. A
    string holding ``audits/<name>`` is judged against every project's open rounds. A segment that sits in
    the same path expression as ``implemented`` is a closed round and never reported.

    A bare name counts only where it is USED as a path: a segment of a ``a / b / c`` expression or of
    ``os.path.join``/``Path(...)``, an argument to a call that resolves a round (``audit_file(...)``) or takes
    an audit file beside it, or a segment of a concatenated/f-string path next to ``audits`` or an audit file
    name. A round's folder name is also an ordinary date, and dates are data: four dashboard tests carry one
    as a card's last-activity day, a fixture's observation day, a chart x value and a ``computed_at``, none of
    them a path (2026-09-12).
    """
    hits, _ = collect_open_round_literals(files, own_audits, other_audits=other_audits, root=root, implemented=implemented)
    return [h.render() for h in hits]


def assert_no_open_round_paths(
    files: Iterable[Path],
    own_audits: Iterable[Path],
    *,
    other_audits: Iterable[Path] = (),
    root: "Path | None" = None,
    known: Iterable[str] = (),
    min_files: int = 1,
) -> None:
    """Shrink-only against *known*: entries are ``path:<value>`` so a line shift does not break them.

    The floor counts PARSED files, and a file that cannot be parsed always fails (it is never a known entry).
    """
    import pytest

    hits, parsed = collect_open_round_literals(list(files), list(own_audits), other_audits=list(other_audits), root=root)
    problems: list[str] = []
    if parsed < min_files:
        problems.append(f"only {parsed} file(s) parsed; expected at least {min_files} -- this would check nothing")
    unparsed = [h for h in hits if h.kind == "unparsable"]
    if unparsed:
        problems.append(f"{len(unparsed)} file(s) could not be parsed, so they were not checked:\n  " + "\n  ".join(h.render() for h in unparsed))
    keys: dict[str, RoundHit] = {}
    for h in hits:
        if h.kind == "pin":
            keys.setdefault(h.key, h)
    known_set = set(known)
    new, stale = sorted(set(keys) - known_set), sorted(known_set - set(keys))
    if new:
        problems.append(f"{len(new)} literal path(s) into an open audit round:\n  " + "\n  ".join(keys[k].render() for k in new))
    if stale:
        problems.append(f"{len(stale)} accepted entr(ies) no longer reproduce -- remove them:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))
