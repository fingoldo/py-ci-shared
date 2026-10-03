"""Declared tables of external facts (vendor prices, context windows, rate limits) cite a source and a dated check
that expires.

pyutilz 423fdc4 found by live verification that ``claude-sonnet-5-5`` was missing (billed at double), xAI's live
search cost was off by 40% and three Gemini models had wrong or missing prices; tests had pinned the wrong values. A
table copied from a vendor page is right on the day it is copied, and nothing says when it was. This gate makes the
copy carry its provenance and turns it red when the check is older than the repo accepts.

Declaring a table (the convention). The repo lists its fact tables in ONE place, so a table that is renamed or deleted
is a finding instead of silently leaving the check; nothing is added to the library modules themselves except the
citation comment most of them already carry::

    [tool.py_ci_shared.gates.external_fact_tables]
    tables = {"src/pyutilz/llm/openai_provider.py:_PRICING" = 45, "src/pyutilz/llm/xai_provider.py:_PRICING" = 30}

(or the same mapping passed to :func:`assert_external_fact_tables_current` from a meta test; a value is the maximum
age in days, or ``{max_age_days = N}``). Each declared name must be bound at module level in that file, and carry a
citation: the comment block directly above the assignment (contiguous ``#`` lines), or comments inside the literal
before its first entry, must hold a source (an ``http(s)://`` URL or a ``domain.tld/path``) and an ISO date
``YYYY-MM-DD`` of the last check against it::

    # Pricing per 1M tokens (USD): (input, output).
    # Source: https://developers.openai.com/api/docs/pricing (verified 2026-09-26).
    _PRICING = {...}

The newest date in the citation is the check date. Findings: a declared table that no longer exists, no source, no
date, a date in the future, and a check older than its ``max_age_days``. Tables within ``warn_days`` of expiry are
listed by :func:`find_expiring_fact_tables` and printed by the assert, never failed: a ``warnings.warn`` would turn red
under ``-W error``.

:func:`find_undeclared_fact_tables` is an advisory for adoption: module-level dict literals with at least three numeric
values whose name looks like a vendor fact (``PRIC``, ``COST``, ``CONTEXT_WINDOW``, ``MAX_OUTPUT``, ``LIMITS``) and that
are not declared. Names alone cannot tell a vendor price from an internal cost model (autopsia's ``DEFAULT_MISS_COST``),
which is why the gate itself works from the declaration.
"""

from __future__ import annotations

import ast
import datetime as _dt
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from ._core import CoreError, Finding, SourceError, parse_source, scan_python

__all__ = [
    "RULE",
    "FactTable",
    "assert_external_fact_tables_current",
    "citation",
    "find_expiring_fact_tables",
    "find_external_fact_table_problems",
    "find_undeclared_fact_tables",
    "parse_declarations",
]

RULE = "external-fact-table"
UNDECLARED_RULE = "undeclared-fact-table"
_URL = re.compile(r"https?://[^\s)\]>,]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/[^\s)\]>,]*", re.IGNORECASE)
_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_FACT_NAME = re.compile(r"PRIC|COST|RATE_CARD|CONTEXT_WINDOW|MAX_OUTPUT|_LIMITS?$|RETIRED|DEPRECATED_MODELS", re.IGNORECASE)
_MAX_BLOCK = 12
_UNREADABLE = "declared fact table file cannot be read"

PathLike = Union[str, Path]


@dataclass(frozen=True)
class FactTable:
    """One declared table as found: where, its citation, and its check date (None when undated)."""

    path: str
    name: str
    line: int
    max_age_days: int
    source: Optional[str]
    checked: Optional[_dt.date]


def parse_declarations(tables: Mapping[str, Any]) -> dict[tuple[str, str], int]:
    """``{(path, NAME): max_age_days}`` from ``{"path.py:NAME": days | {"max_age_days": days}}``; CoreError when malformed."""
    out: dict[tuple[str, str], int] = {}
    for key, value in tables.items():
        path, sep, name = str(key).rpartition(":")
        if not sep or not path or not name.isidentifier():
            raise CoreError(f"external_fact_tables: declaration {key!r} must be 'path/to/module.py:NAME'")
        days = value.get("max_age_days") if isinstance(value, Mapping) else value
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise CoreError(f"external_fact_tables: {key!r} needs a positive integer max_age_days, got {value!r}")
        out[(path.replace("\\", "/"), name)] = days
    return out


def _module_bindings(tree: ast.Module) -> Iterator[tuple[str, ast.stmt]]:
    """``(name, statement)`` for assignments at module level, including under module-level ``if``/``try``."""
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop(0)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            yield node.target.id, node
        elif isinstance(node, (ast.If, ast.Try)):
            stack.extend(node.body + node.orelse + getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)


def _comment(line: str) -> Optional[str]:
    stripped = line.strip()
    return stripped[1:].strip() if stripped.startswith("#") else None


def citation(lines: list[str], node: ast.stmt) -> str:
    """The citation text of the table assigned by *node*: the contiguous comment block above it, plus comment lines inside
    the literal before its first entry."""
    above: list[str] = []
    i = node.lineno - 2
    while i >= 0 and len(above) < _MAX_BLOCK and _comment(lines[i]) is not None:
        above.insert(0, _comment(lines[i]) or "")
        i -= 1
    value = getattr(node, "value", None)
    first = None
    if isinstance(value, ast.Dict) and value.keys:
        first = min((k or v).lineno for k, v in zip(value.keys, value.values))
    elif isinstance(value, (ast.List, ast.Tuple, ast.Set)) and value.elts:
        first = value.elts[0].lineno
    inside = [_comment(lines[j]) or "" for j in range(node.lineno, (first or node.lineno) - 1) if _comment(lines[j]) is not None]
    return "\n".join(above + inside)


def _valid_date(y: str, m: str, d: str) -> Optional[_dt.date]:
    try:
        return _dt.date(int(y), int(m), int(d))
    except ValueError:  # 2026-02-30 reads as a date to the regex only
        return None


def _date(text: str) -> Optional[_dt.date]:
    dates = [day for day in (_valid_date(*ymd) for ymd in _DATE.findall(text)) if day is not None]
    return max(dates) if dates else None


def _resolve(root: Path, declared: dict[tuple[str, str], int]) -> tuple[list[FactTable], list[Finding]]:
    found: list[FactTable] = []
    problems: list[Finding] = []
    trees: dict[str, Optional[tuple[list[str], ast.Module]]] = {}
    for (rel, name), days in sorted(declared.items()):
        if rel not in trees:
            try:
                source, tree = parse_source(root / rel)
                trees[rel] = (source.splitlines(), tree)
            except SourceError as exc:
                trees[rel] = None
                problems.append(Finding(rel, exc.line or 1, RULE, f"{_UNREADABLE}: {exc.kind}: {exc.message}"))
        entry = trees[rel]
        if entry is None:
            continue
        lines, tree = entry
        node = next((n for bound, n in _module_bindings(tree) if bound == name), None)
        if node is None:
            problems.append(Finding(rel, 1, RULE, f"declared fact table {name} no longer exists at module level; update the declaration"))
            continue
        text = citation(lines, node)
        url = _URL.search(text)
        found.append(FactTable(rel, name, node.lineno, days, url.group(0) if url else None, _date(text)))
    return found, problems


def _judge(table: FactTable, today: _dt.date) -> Optional[str]:
    if table.source is None and table.checked is None:
        return "cites no source and no check date: add `# Source: <url> (verified YYYY-MM-DD)` above it"
    if table.source is None:
        return "cites no source URL next to its check date"
    if table.checked is None:
        return f"cites {table.source} with no check date: add (verified YYYY-MM-DD)"
    if table.checked > today:
        return f"check date {table.checked} is in the future"
    age = (today - table.checked).days
    if age > table.max_age_days:
        return f"last checked {table.checked} ({age} days ago, limit {table.max_age_days}): re-verify against {table.source} and update the date"
    return None


def _declared(tables: Mapping[str, Any], min_tables: int) -> dict[tuple[str, str], int]:
    declared = parse_declarations(tables)
    if len(declared) < min_tables:
        raise CoreError(f"external_fact_tables: {len(declared)} table(s) declared; expected at least {min_tables}, so this would check nothing")
    return declared


def find_external_fact_table_problems(
    root: PathLike, tables: Mapping[str, Any], *, today: Optional[_dt.date] = None, min_tables: int = 1, allow_unparsed: bool = False
) -> list[Finding]:
    """Every declared table that is missing, uncited, undated, dated in the future or older than its limit. A declared
    file that cannot be read or parsed is a finding too, unless *allow_unparsed*."""
    found, problems = _resolve(Path(root), _declared(tables, min_tables))
    if allow_unparsed:
        problems = [p for p in problems if _UNREADABLE not in p.message]
    day = today or _dt.datetime.now(_dt.timezone.utc).date()
    for table in found:
        why = _judge(table, day)
        if why:
            problems.append(Finding(table.path, table.line, RULE, f"{table.name}: {why}"))
    return sorted(problems, key=lambda f: (f.path, f.line))


def find_expiring_fact_tables(
    root: PathLike, tables: Mapping[str, Any], *, warn_days: int = 7, today: Optional[_dt.date] = None, min_tables: int = 1
) -> list[str]:
    """``path:line: NAME ...`` for each dated table that is still valid and expires within *warn_days* days."""
    found, _ = _resolve(Path(root), _declared(tables, min_tables))
    day = today or _dt.datetime.now(_dt.timezone.utc).date()
    out = []
    for t in found:
        if t.checked is None or t.source is None or t.checked > day:
            continue
        age = (day - t.checked).days
        if t.max_age_days - warn_days < age <= t.max_age_days:
            expires = t.checked + _dt.timedelta(days=t.max_age_days)
            out.append(f"{t.path}:{t.line}: {t.name} expires on {expires} (checked {t.checked}, limit {t.max_age_days} days); re-verify against {t.source}")
    return out


def find_undeclared_fact_tables(root: PathLike, tables: Optional[Mapping[str, Any]] = None, *, use_git: Optional[bool] = None) -> list[Finding]:
    """Advisory: module-level numeric dict literals named like vendor facts that are not declared. Never a gate."""
    declared = set(parse_declarations(tables or {}))
    scan = scan_python(root, min_files=0, use_git=use_git)
    out: list[Finding] = []
    for parsed in scan:
        if any(part in ("tests", "test") for part in parsed.rel.split("/")[:-1]):
            continue
        for name, node in _module_bindings(parsed.tree):
            value = getattr(node, "value", None)
            if not _FACT_NAME.search(name) or not isinstance(value, ast.Dict) or len(value.keys) < 3 or (parsed.rel, name) in declared:
                continue
            numbers = sum(isinstance(x, ast.Constant) and isinstance(x.value, (int, float)) and not isinstance(x.value, bool) for x in ast.walk(value))
            if numbers >= 3:
                out.append(
                    Finding(parsed.rel, node.lineno, UNDECLARED_RULE, f"{name} looks like a table of external facts; declare it (or ignore if internal)")
                )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_external_fact_tables_current(
    root: PathLike,
    tables: Mapping[str, Any],
    *,
    warn_days: int = 7,
    today: Optional[_dt.date] = None,
    min_tables: int = 1,
    min_files: Optional[int] = None,
    allow_unparsed: bool = False,
) -> None:
    """Fail on any problem :func:`find_external_fact_table_problems` reports (and on fewer than *min_tables* declared);
    print the tables expiring within *warn_days* days without failing. *min_files* is an alias of *min_tables* (the gate
    contract's floor name); *allow_unparsed* skips declared files that cannot be parsed."""
    if min_files is not None:
        min_tables = min_files
    problems = find_external_fact_table_problems(root, tables, today=today, min_tables=min_tables, allow_unparsed=allow_unparsed)
    for line in find_expiring_fact_tables(root, tables, warn_days=warn_days, today=today, min_tables=min_tables) if warn_days > 0 else []:
        sys.stdout.write(f"external-fact-tables early warning: {line}" + "\n")
    if problems:
        raise AssertionError(
            f"{len(problems)} external fact table(s) without a current, cited check; re-verify each against its source and "
            "update the date in its citation:\n  " + "\n  ".join(f.render() for f in problems)
        )
