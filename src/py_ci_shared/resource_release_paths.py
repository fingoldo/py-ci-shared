"""Shared check: a resource a module OWNS is released on the failure path too.

2026-09-05 glossum agent wave, findings 05-F9 through 05-F12. Forty-four modules built a
SQLAlchemy engine with ``create_async_engine(...)`` and called ``await eng.dispose()`` as
the last statement of the happy path. Any raise in between -- a bad query, a missing
column, a Ctrl+C -- leaked the engine's whole connection pool, and on Windows its asyncpg
sockets, for the life of the process. Not one of the forty-four had the call in a
``finally``. The audit report named four sites; the sweep found the rest, which is the
argument for a gate: these are short analysis scripts that get copied from each other, so
the shape spreads by construction.

WHAT IT DOES NOT REPORT, deliberately
-------------------------------------
A module that creates a resource and never releases it AT ALL is not a finding here. Some
hand the object to a caller that owns its lifecycle, and reporting those would turn a
precise check into a noisy one. What this catches is the shape that says "I own this" --
by calling the release itself -- and then honours that only when nothing goes wrong.

Nor does it try to prove the release is reachable from every branch: a release of the same
receiver (``eng.dispose(...)``, any arguments) inside an ``async with``/``with`` body or a
``finally`` anywhere in the module is accepted. That is deliberately generous. The failure
this exists for is the total absence of one. Constructors are matched however they were
imported (``create_async_engine as cae``), and a file that cannot be parsed fails the gate.

Usage::

    from py_ci_shared.resource_release_paths import assert_released_on_every_path

    def test_engines_are_disposed_in_a_finally():
        assert_released_on_every_path(
            files=sorted(REPO.rglob("*.py")),
            constructors={"create_async_engine"},
            release="dispose",
            repo_root=REPO,
            min_subjects=10,
        )
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

from ._core import ImportAliases, parse_file, relative_posix, scan_python


def _callee_names(call: ast.Call, aliases: ImportAliases) -> set[str]:
    """The spelled name of *call*'s callee and the last segment of its import-resolved name
    (``from x import create_async_engine as cae; cae()`` -> ``{"cae", "create_async_engine"}``)."""
    func = call.func
    names = {func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")}
    qualified = aliases.qualified_name(call)
    if qualified:
        names.add(qualified.rsplit(".", 1)[-1])
    return names


def _calls(tree: ast.AST) -> Iterable[str]:
    aliases = ImportAliases.from_tree(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield from _callee_names(node, aliases)


def creates_resource(tree: ast.AST, constructors: frozenset[str]) -> bool:
    """True when the module calls one of *constructors*, however it was imported."""
    return any(name in constructors for name in _calls(tree))


def _release_calls(nodes: Iterable[ast.AST], release: str) -> Iterator[ast.Call]:
    """Every ``<receiver>.<release>(...)`` call under *nodes*, whatever its arguments (``dispose(close=False)``)."""
    for top in nodes:
        for node in ast.walk(top):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == release:
                yield node


def _receiver(call: ast.Call) -> str:
    assert isinstance(call.func, ast.Attribute)
    return ast.unparse(call.func.value)


def _protected_calls(tree: ast.AST, release: str) -> list[ast.Call]:
    """Release calls that sit in a ``finally`` block or a ``with``/``async with`` body."""
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        finalbody = getattr(node, "finalbody", None)  # ast.Try, and ast.TryStar on 3.11+
        if finalbody:
            out.extend(_release_calls(finalbody, release))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            out.extend(_release_calls(node.body, release))
    return out


def _release_is_protected(tree: ast.AST, release: str) -> bool:
    """True when some ``finally`` or ``with`` body calls ``.<release>(...)``."""
    return bool(_protected_calls(tree, release))


def _unprotected_lines(tree: ast.AST, release: str) -> list[int]:
    protected = _protected_calls(tree, release)
    protected_ids = {id(c) for c in protected}
    covered_receivers = {_receiver(c) for c in protected}
    out = {call.lineno for call in _release_calls([tree], release) if id(call) not in protected_ids and _receiver(call) not in covered_receivers}
    return sorted(out)


def find_unprotected_releases(path: Path, release: str) -> list[int]:
    """Line numbers calling ``<receiver>.<release>(...)`` outside any ``finally``/``with``, for receivers that
    have no protected release anywhere in the module. Raises ``SourceError`` for an unreadable/unparsable file:
    an empty answer for it would read as "released on every path"."""
    return _unprotected_lines(parse_file(path), release)


def subjects(files: Iterable[Path], constructors: Iterable[str]) -> list[Path]:
    """The files this check applies to: those that construct one of the resources. Unparsable files are left out
    here; :func:`assert_released_on_every_path` reports them."""
    wanted = frozenset(constructors)
    return [f.path for f in scan_python([Path(p) for p in files]) if creates_resource(f.tree, wanted)]


def assert_released_on_every_path(
    *,
    files: Iterable[Path],
    constructors: Iterable[str],
    release: str,
    repo_root: Path,
    min_subjects: int = 1,
    ignore: Iterable[Path] = (),
) -> None:
    """Fail when a module releases its own resource only on the success path, or when a file cannot be parsed.

    ``min_subjects`` guards the gate against becoming the thing it checks for: if the file
    selection stops matching, this fails loudly instead of passing over an empty set.
    """
    import pytest

    wanted = frozenset(constructors)
    ignored = {Path(p).resolve() for p in ignore}
    scan = scan_python([Path(p) for p in files if Path(p).resolve() not in ignored], root=repo_root)
    applicable = [f for f in scan if creates_resource(f.tree, wanted)]

    problems: list[str] = [f"{p.rel}:{p.line}: {p.kind}: {p.message} - whether it releases what it constructs was not checked" for p in scan.unparsed]
    if len(applicable) < min_subjects:
        problems.append(
            f"only {len(applicable)} module(s) construct {sorted(wanted)} -- expected at "
            f"least {min_subjects}. The scan lost its subject; a passing result here would mean "
            f"nothing."
        )

    for parsed in applicable:
        lines = _unprotected_lines(parsed.tree, release)
        if lines:
            problems.append(
                f"{relative_posix(parsed.path, repo_root)} calls .{release}() at line(s) {lines} but from no `finally` or `with`: "
                f"a raise before that point leaks the resource for the lifetime of the process (success path only)"
            )

    if problems:
        pytest.fail(f"{len(problems)} module(s) release a resource only on the success path, or could not be checked:\n  " + "\n  ".join(problems))
