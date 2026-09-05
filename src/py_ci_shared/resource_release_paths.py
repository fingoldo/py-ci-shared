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

Nor does it try to prove the release is reachable from every branch: an ``async with`` or
a ``try/finally`` anywhere in the module that mentions the release call is accepted. That
is deliberately generous. The failure this exists for is the total absence of one.

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
from collections.abc import Iterable
from pathlib import Path


def _calls(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            yield func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def creates_resource(tree: ast.AST, constructors: frozenset[str]) -> bool:
    """True when the module calls one of *constructors*."""
    return any(name in constructors for name in _calls(tree))


def _release_is_protected(tree: ast.AST, release: str) -> bool:
    """True when some ``finally`` or ``with`` body mentions the release call."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            if any(f"{release}()" in ast.unparse(stmt) for stmt in node.finalbody):
                return True
        if isinstance(node, (ast.With, ast.AsyncWith)):
            if any(f"{release}()" in ast.unparse(stmt) for stmt in node.body):
                return True
    return False


def find_unprotected_releases(path: Path, release: str) -> list[int]:
    """Line numbers calling ``.<release>()`` when no protected call exists in the module."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    if _release_is_protected(tree, release):
        return []
    out = []
    for node in ast.walk(tree):
        text = ast.unparse(node).strip() if isinstance(node, (ast.Await, ast.Call)) else ""
        if text.endswith(f".{release}()"):
            out.append(node.lineno)
    return sorted(set(out))


def subjects(files: Iterable[Path], constructors: Iterable[str]) -> list[Path]:
    """The files this check applies to: those that construct one of the resources."""
    wanted = frozenset(constructors)
    out = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        if creates_resource(tree, wanted):
            out.append(path)
    return out


def assert_released_on_every_path(
    *,
    files: Iterable[Path],
    constructors: Iterable[str],
    release: str,
    repo_root: Path,
    min_subjects: int = 1,
    ignore: Iterable[Path] = (),
) -> None:
    """Fail when a module releases its own resource only on the success path.

    ``min_subjects`` guards the gate against becoming the thing it checks for: if the file
    selection stops matching, this fails loudly instead of passing over an empty set.
    """
    import pytest

    ignored = {Path(p).resolve() for p in ignore}
    applicable = [p for p in subjects(files, constructors) if p.resolve() not in ignored]

    if len(applicable) < min_subjects:
        pytest.fail(
            f"only {len(applicable)} module(s) construct {sorted(constructors)} -- expected at "
            f"least {min_subjects}. The scan lost its subject; a passing result here would mean "
            f"nothing."
        )

    problems: list[str] = []
    for path in applicable:
        lines = find_unprotected_releases(path, release)
        if lines:
            rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
            problems.append(
                f"{rel} calls .{release}() at line(s) {lines} but from no `finally` or `with`: "
                f"a raise before that point leaks the resource for the lifetime of the process"
            )

    if problems:
        pytest.fail(f"{len(problems)} module(s) release a resource only on the success path:\n  " + "\n  ".join(problems))
