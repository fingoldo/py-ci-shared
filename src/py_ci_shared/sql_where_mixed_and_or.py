"""A WHERE clause must not mix AND and OR at one parenthesis level.

``WHERE lang = :l AND text = :t AND sense IN (1, 2) OR sense IN (3, 4)`` binds as ``(lang AND text AND sense IN ..) OR
sense IN ..``: the second arm stands alone, so the language and text filters never reach it. The statement reads as
intended and passes every mock-based test. Bracket one side.

The check reads each string constant (and each f-string, its placeholders as ``X``) that holds ``UPDATE``, ``DELETE``
or ``SELECT`` together with ``WHERE``. Docstrings are skipped; ``--`` and ``/* */`` comments, quoted literals and
``$$`` bodies (plpgsql) are blanked; statements are split on ``;``. For each ``WHERE`` the walk tracks parenthesis
depth and stops at the next clause keyword (``ORDER BY``, ``GROUP BY``, ``LIMIT`` ...) or an unmatched ``)``. ``BETWEEN
x AND y`` and ``CASE ... END`` do not count. A level holding both AND and OR is a finding.

Accepted deliberately with ``# sql-precedence-ok: <reason>`` on the string's first line or the line above. A marker with
no reason is itself a finding.

Usage::

    from py_ci_shared.sql_where_mixed_and_or import assert_sql_where_mixed_and_or

    def test_sql_where_levels_are_not_mixed():
        assert_sql_where_mixed_and_or("mypackage", min_files=20)
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, walk
from ._core.gate_contract import check_scan
from ._gate_report import line_has_marker, scan_tree, skip_set

__all__ = ["MARKER", "RULE", "assert_sql_where_mixed_and_or", "find_sql_where_mixed_and_or"]

RULE = "sql-where-mixed-and-or"
MARKER = "sql-precedence-ok"

_HINT = re.compile(r"\b(?:UPDATE|DELETE|SELECT)\b", re.IGNORECASE)
_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[()]|;|\S")
_STOP_WORDS = frozenset({"LIMIT", "OFFSET", "RETURNING", "HAVING", "UNION", "INTERSECT", "EXCEPT", "WINDOW", "FETCH", "FOR"})
_STOP_PAIRS = frozenset({("ORDER", "BY"), ("GROUP", "BY"), ("ON", "CONFLICT")})


@dataclass(frozen=True)
class _Tok:
    start: int
    end: int
    text: str

    @property
    def upper(self) -> str:
        return self.text.upper()


def _clean(sql: str) -> str:
    """*sql* with ``$$`` bodies, block and line comments, and quoted literals blanked; positions are not preserved."""
    sql = re.sub(r"\$\$.*?\$\$", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return re.sub(r'"[^"]*"', "x", sql)


def _statements(text: str) -> list[list[_Tok]]:
    out: list[list[_Tok]] = [[]]
    for m in _TOKEN.finditer(text):
        tok = _Tok(m.start(), m.end(), m.group(0))
        if tok.text == ";":
            out.append([])
        else:
            out[-1].append(tok)
    return [s for s in out if s]


def _is_stop(toks: list[_Tok], j: int) -> bool:
    """True when token *j* (at the WHERE's own level) opens the next clause."""
    nxt = toks[j + 1].upper if j + 1 < len(toks) else ""
    return (toks[j].upper, nxt) in _STOP_PAIRS or toks[j].upper in _STOP_WORDS


def _level_words(toks: list[_Tok], start: int) -> list[str]:
    """The AND/OR words at the level of the WHERE before token *start*; BETWEEN's AND and CASE bodies do not count."""
    depth = 0
    words: list[str] = []
    between = 0
    case = 0
    for j in range(start + 1, len(toks)):
        t = toks[j]
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0:
            if _is_stop(toks, j):
                break
            between, case, is_and_or = _track(t.upper, between, case)
            if is_and_or:
                words.append(t.upper)
    return words


def _track(word: str, between: int, case: int) -> tuple[int, int, bool]:
    """Advance the BETWEEN and CASE counters past *word*; True when *word* is a live AND/OR (not a BETWEEN bound or inside CASE)."""
    if word == "CASE":
        return between, case + 1, False
    if word == "END":
        return between, max(case - 1, 0), False
    if word == "BETWEEN":
        return between + 1, case, False
    if word == "AND" and between:
        return between - 1, case, False
    return between, case, word in ("AND", "OR") and not case


def _where_levels(toks: list[_Tok]) -> list[tuple[int, list[str]]]:
    """For each WHERE: ``(index of WHERE, AND/OR words at its own level)``."""
    return [(i, _level_words(toks, i)) for i, tok in enumerate(toks) if tok.upper == "WHERE"]


def _clause(text: str, toks: list[_Tok], start: int) -> str:
    """The WHERE clause text from token *start* to the last token before the stop, whitespace collapsed."""
    end = start
    depth = 0
    for j in range(start + 1, len(toks)):
        t = toks[j]
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and _is_stop(toks, j):
            break
        end = j
    return " ".join(text[toks[start].start : toks[end].end].split())[:200]


def _mixed_clauses(sql: str) -> list[str]:
    text = _clean(sql)
    clauses: list[str] = []
    for statement in _statements(text):
        for index, words in _where_levels(statement):
            if "AND" in words and "OR" in words:
                clauses.append(_clause(text, statement, index))
    return clauses


def _sql_text(node: ast.AST) -> Optional[str]:
    """The SQL a string constant or f-string holds, with each placeholder as ``X``; None for other nodes."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "X" for v in node.values)
    return None


def _docstring_ids(tree: ast.Module) -> set[int]:
    """Ids of docstring constants, and of the literal parts of f-strings (the f-string itself is the unit checked)."""
    ids: set[int] = set()
    for node in walk(tree):
        if isinstance(node, ast.JoinedStr):
            ids.update(id(v) for v in node.values if isinstance(v, ast.Constant))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _marker(source: str, line: int) -> Optional[str]:
    """The reason after ``# sql-precedence-ok:`` on *line* or the one above; "" for no reason; None without a marker."""
    lines = source.splitlines()
    for number in (line, line - 1):
        if line_has_marker(lines, number, MARKER):
            comment = lines[number - 1].split("#", 1)[1]
            return comment.split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def find_sql_where_mixed_and_or(
    root: Union[str, Path],
    *,
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every SQL string under *root* whose WHERE level mixes AND and OR without a reasoned marker, sorted by path and line."""
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        skip = _docstring_ids(parsed.tree)
        for node in walk(parsed.tree):
            if not isinstance(node, (ast.Constant, ast.JoinedStr)) or id(node) in skip:
                continue
            text = _sql_text(node)
            if text is None or not (_HINT.search(text) and _WHERE.search(text)):
                continue
            clauses = _mixed_clauses(text)
            if not clauses:
                continue
            reason = _marker(parsed.source, node.lineno)
            if reason:
                continue
            suffix = "; the # sql-precedence-ok marker needs a reason after it" if reason == "" else ""
            out.extend(
                Finding(
                    parsed.rel,
                    node.lineno,
                    RULE,
                    f"WHERE mixes AND and OR at one level: `{clause}`; bracket one side so the arms bind as written{suffix}",
                )
                for clause in clauses
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_sql_where_mixed_and_or(
    root: Union[str, Path],
    *,
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any WHERE level that mixes AND and OR."""
    found = find_sql_where_mixed_and_or(
        root,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    if found:
        raise AssertionError(f"{len(found)} {RULE} finding(s):\n  " + "\n  ".join(f.render() for f in found))
