"""Shared check: no SQLAlchemy ``text()`` bind parameter is followed directly by a ``::`` cast.

SQLAlchemy's ``text()`` scanner refuses to read ``:name`` as a parameter when the next character is a colon
-- the lookahead exists so ``expr::type`` casts are not mistaken for parameters -- so in ``:name::type`` the
name is not bound at all. It reaches PostgreSQL verbatim, which answers ``syntax error at or near ":"``::

    SELECT :a::text[] AS x        ->  SELECT :a::text[] AS x     (unbound)
    SELECT CAST(:a AS text[]) x   ->  SELECT CAST($1 AS text[])  (bound)

glossum shipped thirty-nine such sites across nine modules, every batched UNNEST insert, and no unit test
noticed because a mocked session accepts any string. ``CAST(...)`` is the fix to prefer over a space
before ``::``: a space is invisible, and a tidy-up reintroduces the bug.

Usage::

    from py_ci_shared.sqlalchemy_text_binds import assert_no_colon_cast_binds

    def test_no_bind_parameter_is_followed_directly_by_a_cast():
        assert_no_colon_cast_binds(REPO_ROOT, roots=("pkg", "scripts"), min_files=200)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

#: ``:name::type`` -- a bind parameter immediately followed by a cast. ``a::b::c`` is a chain of casts, not a bind.
COLON_CAST_RE = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z_0-9]*)::([A-Za-z_]+(?:\[\])?)")


def colon_cast_binds(text: str) -> list[tuple[int, str]]:
    """``(line number, match)`` for every ``:name::type`` outside a ``#`` comment line."""
    out: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        out.extend((number, m.group(0)) for m in COLON_CAST_RE.finditer(line))
    return out


def find_colon_cast_binds(repo_root: Path, roots: Iterable[str], *, suffixes: Iterable[str] = (".py",)) -> "tuple[list[str], int]":
    """(``path:line: match`` problems, number of files scanned) under the given roots."""
    wanted = set(suffixes)
    problems: list[str] = []
    scanned = 0
    for root in roots:
        base = repo_root / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in wanted or "__pycache__" in path.parts:
                continue
            scanned += 1
            for number, match in colon_cast_binds(path.read_text(encoding="utf-8", errors="replace")):
                problems.append(f"{path.relative_to(repo_root).as_posix()}:{number}: {match}")
    return problems, scanned


def assert_no_colon_cast_binds(repo_root: Path, roots: Iterable[str], *, min_files: int = 1, suffixes: Iterable[str] = (".py",)) -> None:
    """Fail on any ``:name::type``, and when fewer than *min_files* were scanned (the scan lost its subject)."""
    import pytest

    problems, scanned = find_colon_cast_binds(repo_root, roots, suffixes=suffixes)
    if scanned < min_files:
        pytest.fail(f"only {scanned} file(s) scanned under {list(roots)}; expected at least {min_files} -- the scan lost its subject")
    if problems:
        pytest.fail(
            "bind parameters followed directly by `::` are not bound by SQLAlchemy's text() and PostgreSQL rejects the "
            "statement; use CAST(:name AS type):\n  " + "\n  ".join(problems)
        )
