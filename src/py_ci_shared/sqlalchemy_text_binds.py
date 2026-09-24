"""Shared check: no SQLAlchemy ``text()`` bind parameter is followed directly by a ``::`` cast.

SQLAlchemy's ``text()`` scanner refuses to read ``:name`` as a parameter when the next character is a colon
-- the lookahead exists so ``expr::type`` casts are not mistaken for parameters -- so in ``:name::type`` the
name is not bound at all. It reaches PostgreSQL verbatim, which answers ``syntax error at or near ":"``::

    SELECT :a::text[] AS x        ->  SELECT :a::text[] AS x     (unbound)
    SELECT CAST(:a AS text[]) x   ->  SELECT CAST($1 AS text[])  (bound)

glossum shipped thirty-nine such sites across nine modules, every batched UNNEST insert, and no unit test
noticed because a mocked session accepts any string. ``CAST(...)`` is the fix to prefer over a space
before ``::``: a space is invisible, and a tidy-up reintroduces the bug.

In a ``.py`` file only string literals are scanned (docstrings excluded), so a slice such as ``xs[:n::step]``
and a comment are never mistaken for SQL; in another suffix (``.sql``) the whole text is, minus ``#``/``--``
comments. A quoted type (``:a::"MyEnum"``) counts. A root that does not exist, or a ``.py`` file that cannot be
parsed, fails the check.

Usage::

    from py_ci_shared.sqlalchemy_text_binds import assert_no_colon_cast_binds

    def test_no_bind_parameter_is_followed_directly_by_a_cast():
        assert_no_colon_cast_binds(REPO_ROOT, roots=("pkg", "scripts"), min_files=200)
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from ._core import DEFAULT_EXCLUDE, CorpusError, SourceError, iter_files, parse_source, read_source, relative_posix

#: ``:name::type`` -- a bind parameter immediately followed by a cast. ``a::b::c`` is a chain of casts, not a bind,
#: and ``x:a::int`` (a word before the colon) is not a bind either. The type may be a quoted identifier.
COLON_CAST_RE = re.compile(r"""(?<![:\w]):([A-Za-z_][A-Za-z_0-9]*)::((?:"[^"\n]+"|[A-Za-z_]+)(?:\[\])?)""")
_SQL_COMMENT_RE = re.compile(r"--[^\n]*")


def colon_cast_binds(text: str) -> list[tuple[int, str]]:
    """``(line number, match)`` for every ``:name::type`` in SQL-ish *text*, outside a ``#`` comment line and a
    ``--`` comment."""
    out: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        out.extend((number, m.group(0)) for m in COLON_CAST_RE.finditer(_SQL_COMMENT_RE.sub("", line)))
    return out


def _docstring_nodes(tree: ast.Module) -> set[int]:
    return {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}


def _string_parts(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """``(first line, text)`` for every string literal (f-string literal parts included) that is not a docstring."""
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


def python_colon_cast_binds(source: str, tree: "ast.Module | None" = None) -> list[tuple[int, str]]:
    """``(line number, match)`` for every ``:name::type`` inside a string literal of Python *source*."""
    tree = tree if tree is not None else ast.parse(source)
    out: list[tuple[int, str]] = []
    for lineno, text in _string_parts(tree):
        for offset, match in colon_cast_binds(text):
            out.append((lineno + offset - 1, match))
    return sorted(out)


def find_colon_cast_binds(repo_root: Path, roots: Iterable[str], *, suffixes: Iterable[str] = (".py",)) -> "tuple[list[str], int]":
    """(``path:line: match`` problems, number of files scanned) under the given roots. A missing root and a ``.py``
    file that cannot be parsed are problems too; neither counts as scanned."""
    patterns = tuple(f"*{s}" for s in suffixes)
    problems: list[str] = []
    scanned = 0
    for root in roots:
        base = Path(repo_root) / root
        try:
            files = iter_files(base, patterns, exclude=DEFAULT_EXCLUDE)
        except CorpusError as exc:
            problems.append(f"{root}: {exc} - nothing under it was checked")
            continue
        for path in files:
            rel = relative_posix(path, repo_root)
            try:
                if path.suffix == ".py":
                    source, tree = parse_source(path)
                    found = python_colon_cast_binds(source, tree)
                else:
                    found = colon_cast_binds(read_source(path))
            except SourceError as exc:
                problems.append(f"{rel}:{exc.line or 1}: {exc.kind}: {exc.message} - not checked")
                continue
            scanned += 1
            problems.extend(f"{rel}:{number}: {match}" for number, match in found)
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
