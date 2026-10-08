"""SQL statements in code name only columns the project's DDL files define.

The incident (Upwork ``production_scrapers``, audit 2026-10-03 CORR-26, on 2026-10-04): ``INSERT_JOB_AUTH_SQL`` and
``SELECT_STALE_ACTIVE_SQL`` were changed to name ``new_upwork.job_auth_details.checked_at`` and shipped BEFORE the additive
migration was applied to the production database. The only protection was a pre-push PREPARE against the live server, which
a machine without the connection string silently skips. This gate is the offline half: the code and the DDL files live in
the same repository, so the two can be compared with no server at all.

It builds a catalogue of tables and columns from the DDL files (``CREATE TABLE [IF NOT EXISTS] s.t (col type ..., ...)``,
``ALTER TABLE ... ADD COLUMN [IF NOT EXISTS] c``, ``DROP COLUMN``, ``RENAME COLUMN``, ``DROP TABLE``; constraints, indexes
and generated-column expressions are read past), then checks every SQL statement that is a module-level constant (found
the way ``sql_verifier_coverage`` finds them: literals, f-strings' literal parts, ``+`` chains and names bound to strings):

* ``INSERT INTO t (c1, c2, ...)``: every listed column;
* ``ON CONFLICT (c1, ...)`` and ``ON CONFLICT ... DO UPDATE SET c = ...``: the key and the assigned columns, and
  ``EXCLUDED.c`` / ``t.c`` inside it, all against the INSERT's table;
* ``UPDATE t SET c = ...``: every assigned column;
* ``alias.c`` and ``t.c`` wherever the alias binds, through the FROM/JOIN of its own or an enclosing SELECT/UPDATE/DELETE,
  to a table the catalogue holds.

WHAT IT DOES NOT CHECK, and says so. The statements are parsed with sqlglot (the ``sql`` extra, imported inside the call).
A statement is NOT CHECKED, and counted as such in the report (never as clean), when it is not fully literal (an f-string
field, a ``{name}`` format field, ``.format()``, ``%``, a call or an unbound name in the chain), when sqlglot cannot parse it, or when it touches no
table the DDL defines. Tables the DDL does not define (views, temp tables, tables of another database, tables created inside a
``DO $$`` block), and tables whose columns the DDL cannot list (``CREATE TABLE ... (LIKE x)``, ``AS SELECT``, ``INHERITS``,
``PARTITION OF``), are UNKNOWN: ignored, and listed in the report. Unqualified columns are checked only with *check_unqualified*
(and then only where one catalogue table is the whole FROM, the name is no output alias and no outer query could own it), and a qualifier bound to a CTE, a derived table or
``VALUES`` is opaque. When in doubt the statement is skipped and counted.

DDL ORDER. Files are applied in sorted path order and statements in file order; an ALTER of a table that is not created yet
waits for its CREATE (``add_x.sql`` sorts before ``create_x.sql``). Repeated ``CREATE TABLE IF NOT EXISTS`` of one table add up
their columns. A project can legitimately be ahead of its DDL files for a window, so the findings are baseline-able
(shrink-only); a finding carries the statement's constant name, the table and the column, not a line.

A DDL file that cannot be read or tokenised (an unterminated string or comment) fails the check like an unparsable Python
file, unless *allow_unparsed*.

Usage in a consumer's meta test::

    from py_ci_shared.statement_columns_exist_in_ddl import assert_statement_columns_exist_in_ddl

    def test_statement_columns_exist_in_ddl():
        assert_statement_columns_exist_in_ddl("src", "tests/statement_columns_baseline.json", ddl_dirs=["sql"], exclude_top_dirs=["tests", "scripts"])
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._core import (
    DEFAULT_EXCLUDE,
    Baseline,
    CorpusError,
    Finding,
    ScanResult,
    SourceError,
    SourceProblem,
    iter_files,
    read_source,
    relative_posix,
    scan_python,
)
from .sql_verifier_coverage import DEFAULT_STATEMENT_STARTS, StatementConstant, statement_constants

__all__ = [
    "RULE",
    "StatementColumnReport",
    "assert_statement_columns_exist_in_ddl",
    "ddl_catalogue",
    "find_unknown_statement_columns",
    "statement_column_report",
]

RULE = "statement-columns-exist-in-ddl"

#: Why a statement was not checked. Keys of ``StatementColumnReport.not_checked``.
NOT_LITERAL = "not fully literal (f-string field, format field, call or unbound name)"
UNPARSABLE = "sqlglot cannot parse it"
NO_DDL_TABLE = "touches no table the DDL defines"
NO_RESOLVABLE_COLUMN = "names no column it can resolve (unqualified columns only)"

_FORMAT_FIELD = re.compile(r"\{[A-Za-z_]\w*[^{}]*\}")
_PYFORMAT_PARAM = re.compile(r"%\((\w+)\)s|%s")
_VALUES_PARAM = re.compile(r"(\bVALUES\s+)(?:%\(\w+\)s|%s)", re.IGNORECASE)
_CONSTRAINT_WORDS = frozenset({"constraint", "primary", "unique", "check", "foreign", "exclude"})


# ---------------------------------------------------------------------------------------------------------------------
# DDL: tokens, statements, the catalogue
# ---------------------------------------------------------------------------------------------------------------------


class _DdlSyntaxError(Exception):
    def __init__(self, message: str, line: int) -> None:
        super().__init__(message)
        self.message = message
        self.line = line


@dataclass(frozen=True)
class _Tok:
    kind: str  # "w" word or number (lower-cased), "q" quoted identifier (unquoted, lower-cased), "s" opaque literal, "p" punctuation
    text: str
    line: int


_DOLLAR = re.compile(r"\$([A-Za-z_]\w*)?\$")


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c == "_"


#: A scanner returns ``(end index, token or None)`` when it recognises what starts at *i*, else ``None``.
_Scan = Optional[tuple[int, Optional[_Tok]]]


def _scan_space(text: str, i: int, line: int) -> _Scan:
    return (i + 1, None) if text[i].isspace() else None


def _scan_line_comment(text: str, i: int, line: int) -> _Scan:
    if not text.startswith("--", i):
        return None
    j = text.find("\n", i)
    return (len(text) if j < 0 else j, None)


def _scan_block_comment(text: str, i: int, line: int) -> _Scan:
    if not text.startswith("/*", i):
        return None
    depth, j = 1, i + 2
    while j < len(text) and depth:
        if text.startswith("/*", j):
            depth, j = depth + 1, j + 2
        elif text.startswith("*/", j):
            depth, j = depth - 1, j + 2
        else:
            j += 1
    if depth:
        raise _DdlSyntaxError("unterminated /* comment", line)
    return j, None


def _scan_string(text: str, i: int, line: int) -> _Scan:
    if text[i] != "'":
        return None
    escapes = i > 0 and text[i - 1] in "eE" and (i < 2 or not _is_word_char(text[i - 2]))
    j = i + 1
    while True:
        if j >= len(text):
            raise _DdlSyntaxError("unterminated string literal", line)
        if escapes and text[j] == "\\":
            j += 2
        elif text.startswith("''", j):
            j += 2
        elif text[j] == "'":
            return j + 1, _Tok("s", "", line)
        else:
            j += 1


def _scan_quoted_identifier(text: str, i: int, line: int) -> _Scan:
    if text[i] != '"':
        return None
    j = i + 1
    while True:
        if j >= len(text):
            raise _DdlSyntaxError("unterminated quoted identifier", line)
        if text.startswith('""', j):
            j += 2
        elif text[j] == '"':
            return j + 1, _Tok("q", text[i + 1 : j].replace('""', '"').lower(), line)
        else:
            j += 1


def _scan_dollar_quote(text: str, i: int, line: int) -> _Scan:
    opener = _DOLLAR.match(text, i) if text[i] == "$" else None
    if opener is None or (i > 0 and _is_word_char(text[i - 1])):
        return None
    end = text.find(opener.group(0), opener.end())
    if end < 0:
        raise _DdlSyntaxError(f"unterminated {opener.group(0)} quoted body", line)
    return end + len(opener.group(0)), _Tok("s", "", line)


def _scan_word(text: str, i: int, line: int) -> _Scan:
    if not _is_word_char(text[i]):
        return None
    j = i + 1
    while j < len(text) and (_is_word_char(text[j]) or text[j] == "$"):
        j += 1
    return j, _Tok("w", text[i:j].lower(), line)


def _scan_punctuation(text: str, i: int, line: int) -> _Scan:
    return i + 1, _Tok("p", text[i], line)


_SCANNERS = (
    _scan_space,
    _scan_line_comment,
    _scan_block_comment,
    _scan_string,
    _scan_quoted_identifier,
    _scan_dollar_quote,
    _scan_word,
    _scan_punctuation,
)


def _tokenize(text: str) -> list[_Tok]:
    """Tokens of a DDL file with comments dropped and string/dollar-quoted bodies kept opaque. Raises on an unterminated one."""
    out: list[_Tok] = []
    i, line = 0, 1
    while i < len(text):
        hit: _Scan = None
        for scan in _SCANNERS:
            hit = scan(text, i, line)
            if hit is not None:
                break
        end, token = hit if hit is not None else (i + 1, None)  # unreachable: the last scanner takes any character
        if token is not None:
            out.append(token)
        line += text.count("\n", i, end)
        i = end
    return out


def _statements(tokens: list[_Tok]) -> list[list[_Tok]]:
    out: list[list[_Tok]] = []
    current: list[_Tok] = []
    for tok in tokens:
        if tok.kind == "p" and tok.text == ";":
            if current:
                out.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        out.append(current)
    return out


def _is(tok: Optional[_Tok], *words: str) -> bool:
    return tok is not None and tok.kind == "w" and tok.text in words


def _punct(tok: Optional[_Tok], char: str) -> bool:
    return tok is not None and tok.kind == "p" and tok.text == char


def _ident(tok: Optional[_Tok]) -> bool:
    return tok is not None and tok.kind in ("w", "q")


def _name_at(toks: list[_Tok], i: int) -> Optional[tuple[tuple[Optional[str], str], int]]:
    """``((schema, table), next index)`` for a possibly schema-qualified name at *i*."""
    if i >= len(toks) or not _ident(toks[i]):
        return None
    parts = [toks[i].text]
    i += 1
    while i + 1 < len(toks) and _punct(toks[i], ".") and _ident(toks[i + 1]):
        parts.append(toks[i + 1].text)
        i += 2
    return (parts[-2] if len(parts) > 1 else None, parts[-1]), i


def _split_top(toks: list[_Tok]) -> list[list[_Tok]]:
    """*toks* split at the commas outside parentheses."""
    items: list[list[_Tok]] = [[]]
    depth = 0
    for tok in toks:
        if tok.kind == "p":
            if tok.text == "(":
                depth += 1
            elif tok.text == ")":
                depth -= 1
            elif tok.text == "," and depth == 0:
                items.append([])
                continue
        items[-1].append(tok)
    return [item for item in items if item]


def _matching_close(toks: list[_Tok], open_at: int) -> Optional[int]:
    depth = 0
    for k in range(open_at, len(toks)):
        if _punct(toks[k], "("):
            depth += 1
        elif _punct(toks[k], ")"):
            depth -= 1
            if depth == 0:
                return k
    return None


Key = tuple[Optional[str], str]


@dataclass
class _Table:
    schema: Optional[str]
    name: str
    columns: dict[str, int] = field(default_factory=dict)  # column -> line that introduced it
    dropped: dict[str, str] = field(default_factory=dict)  # column -> file whose DDL dropped it last
    created_in: list[str] = field(default_factory=list)
    open: bool = False  # columns the DDL cannot list: LIKE, AS SELECT, INHERITS, PARTITION OF

    @property
    def display(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


#: ``(kind, key, payload, file, line)``
_Event = tuple[str, Key, Any, str, int]


@dataclass
class _Catalogue:
    tables: dict[Key, _Table] = field(default_factory=dict)
    pending: list[_Event] = field(default_factory=list)  # ALTERs of a table whose CREATE has not been seen yet
    files: int = 0

    def find(self, key: Key) -> Optional[_Table]:
        """The one table *key* names, or ``None``. An unqualified name matches any schema; two matches are not resolved."""
        exact = self.tables.get(key)
        if exact is not None:
            return exact
        schema, name = key
        hits = [t for k, t in self.tables.items() if k[1] == name and (schema is None or k[0] is None)]
        return hits[0] if len(hits) == 1 else None

    def ambiguous(self, key: Key) -> bool:
        if key in self.tables:
            return False
        schema, name = key
        return sum(1 for k in self.tables if k[1] == name and (schema is None or k[0] is None)) > 1

    def apply(self, event: _Event) -> None:
        kind, key = event[0], event[1]
        if kind == "create":
            self._create(event)
            return
        table = self.find(key)
        if table is None:
            if kind != "drop_table":
                self.pending.append(event)
            return
        getattr(self, f"_{kind}")(table, event)

    def _create(self, event: _Event) -> None:
        _, key, payload, file, _ = event
        created = self.tables.setdefault(key, _Table(key[0], key[1]))
        created.created_in.append(file)
        created.open = created.open or payload["open"]
        for column, column_line in payload["columns"]:
            created.columns.setdefault(column, column_line)
            created.dropped.pop(column, None)
        replay = [e for e in self.pending if self.find(e[1]) is created]
        self.pending = [e for e in self.pending if e not in replay]
        for pending_event in replay:
            self.apply(pending_event)

    def _add(self, table: _Table, event: _Event) -> None:
        table.columns.setdefault(event[2], event[4])
        table.dropped.pop(event[2], None)

    def _drop(self, table: _Table, event: _Event) -> None:
        if table.columns.pop(event[2], None) is not None:
            table.dropped[event[2]] = event[3]

    def _rename(self, table: _Table, event: _Event) -> None:
        old, new = event[2]
        if table.columns.pop(old, None) is not None:
            table.columns[new] = event[4]
            table.dropped[old] = event[3]
            table.dropped.pop(new, None)

    def _rename_table(self, table: _Table, event: _Event) -> None:
        self.tables.pop((table.schema, table.name), None)
        table.name = event[2]
        self.tables[(table.schema, table.name)] = table

    def _drop_table(self, table: _Table, event: _Event) -> None:
        self.tables.pop((table.schema, table.name), None)


def _token_at(toks: list[_Tok], i: int) -> Optional[_Tok]:
    return toks[i] if i < len(toks) else None


def _create_target(toks: list[_Tok]) -> Optional[tuple[Key, int]]:
    """``(key, index after the name)`` of a persistent ``CREATE [UNLOGGED] TABLE [IF NOT EXISTS] name``; ``None`` for anything else
    (``CREATE TEMP TABLE`` is a session table, not part of the schema, and fails the ``TABLE`` test)."""
    i = 1
    if _is(_token_at(toks, i), "unlogged"):
        i += 1
    if not _is(_token_at(toks, i), "table"):
        return None
    i += 1
    if _is(_token_at(toks, i), "if") and _is(_token_at(toks, i + 1), "not") and _is(_token_at(toks, i + 2), "exists"):
        i += 3
    return _name_at(toks, i)


def _create_columns(items: list[list[_Tok]]) -> tuple[list[tuple[str, int]], bool]:
    """``(columns, open)`` from the comma-separated items of a CREATE TABLE body; ``LIKE`` makes the column list unknowable."""
    columns: list[tuple[str, int]] = []
    open_table = False
    for item in items:
        head = item[0]
        if head.kind == "w" and head.text in _CONSTRAINT_WORDS:
            continue
        if _is(head, "like"):
            open_table = True
        elif _ident(head) and len(item) > 1:
            columns.append((head.text, head.line))
    return columns, open_table


def _create_event(toks: list[_Tok], file: str) -> Optional[_Event]:
    target = _create_target(toks)
    if target is None:
        return None
    key, i = target
    line = toks[0].line
    close = _matching_close(toks, i) if _punct(_token_at(toks, i), "(") else None
    if close is None:  # PARTITION OF, AS SELECT, OF type
        return ("create", key, {"columns": [], "open": True}, file, line)
    columns, open_table = _create_columns(_split_top(toks[i + 1 : close]))
    open_table = open_table or any(_is(t, "inherits") for t in toks[close + 1 :])
    return ("create", key, {"columns": columns, "open": open_table}, file, line)


def _skip_if(rest: list[_Tok], *words: str) -> list[_Tok]:
    """*rest* without a leading ``IF <words>`` (``IF NOT EXISTS``, ``IF EXISTS``) when it starts with exactly that."""
    if _is(_token_at(rest, 0), "if") and all(_is(_token_at(rest, k + 1), w) for k, w in enumerate(words)):
        return rest[1 + len(words) :]
    return rest


def _add_action(key: Key, rest: list[_Tok], file: str, line: int) -> list[_Event]:
    if rest and _is(rest[0], "column"):
        rest = rest[1:]
    rest = _skip_if(rest, "not", "exists")
    if rest and _ident(rest[0]) and not (rest[0].kind == "w" and rest[0].text in _CONSTRAINT_WORDS):
        return [("add", key, rest[0].text, file, line)]
    return []


def _drop_action(key: Key, rest: list[_Tok], file: str, line: int) -> list[_Event]:
    if rest and _is(rest[0], "constraint"):
        return []
    if rest and _is(rest[0], "column"):
        rest = rest[1:]
    rest = _skip_if(rest, "exists")
    return [("drop", key, rest[0].text, file, line)] if rest and _ident(rest[0]) else []


def _rename_action(key: Key, rest: list[_Tok], file: str, line: int) -> list[_Event]:
    if rest and _is(rest[0], "to") and len(rest) > 1 and _ident(rest[1]):
        return [("rename_table", key, rest[1].text, file, line)]
    if rest and _is(rest[0], "column"):
        rest = rest[1:]
    if len(rest) >= 3 and _ident(rest[0]) and not _is(rest[0], "constraint") and _is(rest[1], "to") and _ident(rest[2]):
        return [("rename", key, (rest[0].text, rest[2].text), file, line)]
    return []


def _alter_events(toks: list[_Tok], file: str) -> list[_Event]:
    rest = _skip_if(toks[2:], "exists")
    if rest and _is(rest[0], "only"):
        rest = rest[1:]
    named = _name_at(rest, 0)
    if named is None:
        return []
    key, i = named
    if _punct(_token_at(rest, i), "*"):
        i += 1
    events: list[_Event] = []
    for action in _split_top(rest[i:]):
        head, args = action[0], action[1:]
        if _is(head, "add"):
            events.extend(_add_action(key, args, file, head.line))
        elif _is(head, "drop"):
            events.extend(_drop_action(key, args, file, head.line))
        elif _is(head, "rename"):
            events.extend(_rename_action(key, args, file, head.line))
        elif _is(head, "set") and args and _is(args[0], "schema"):
            events.append(("drop_table", key, None, file, head.line))  # moved to a schema this catalogue cannot follow
    return events


def _drop_table_events(toks: list[_Tok], file: str) -> list[_Event]:
    i = 2
    if i + 1 < len(toks) and _is(toks[i], "if") and _is(toks[i + 1], "exists"):
        i += 2
    events: list[_Event] = []
    for part in _split_top(toks[i:]):
        named = _name_at(part, 0)
        if named is not None:
            events.append(("drop_table", named[0], None, file, part[0].line))
    return events


def _file_events(tokens: list[_Tok], file: str) -> list[_Event]:
    events: list[_Event] = []
    for toks in _statements(tokens):
        if _is(toks[0], "create") and len(toks) > 2:
            created = _create_event(toks, file)
            if created is not None:
                events.append(created)
        elif _is(toks[0], "alter") and len(toks) > 3 and _is(toks[1], "table"):
            events.extend(_alter_events(toks, file))
        elif _is(toks[0], "drop") and len(toks) > 2 and _is(toks[1], "table"):
            events.extend(_drop_table_events(toks, file))
    return events


def _build_catalogue(root: Path, ddl_dirs: Iterable[Union[str, Path]], use_git: Optional[bool]) -> tuple[_Catalogue, list[SourceProblem]]:
    problems: list[SourceProblem] = []
    files: list[Path] = []
    for entry in ddl_dirs:
        base = Path(entry) if Path(entry).is_absolute() else Path(root) / entry
        if not base.is_dir():
            raise CorpusError(f"DDL directory does not exist: {base}")
        files.extend(iter_files(base, ("*.sql",), exclude=DEFAULT_EXCLUDE, use_git=use_git))
    catalogue = _Catalogue()
    seen: set[Path] = set()
    ordered = sorted(set(files), key=lambda p: relative_posix(p, Path(root)))
    for path in ordered:
        if path.resolve() in seen:
            continue
        seen.add(path.resolve())
        rel = relative_posix(path, Path(root))
        try:
            tokens = _tokenize(read_source(path))
        except SourceError as exc:
            problems.append(SourceProblem(path, rel, exc.line or 1, exc.kind, exc.message))
            continue
        except _DdlSyntaxError as exc:
            problems.append(SourceProblem(path, rel, exc.line, "unparsable", exc.message))
            continue
        catalogue.files += 1
        for event in _file_events(tokens, rel):
            catalogue.apply(event)
    return catalogue, problems


def ddl_catalogue(root: Union[str, Path], *, ddl_dirs: Iterable[Union[str, Path]] = ("sql",), use_git: Optional[bool] = None) -> dict[str, list[str]]:
    """``{"schema.table": [columns...]}`` for every table with a column list the DDL files under *ddl_dirs* define."""
    catalogue, _ = _build_catalogue(Path(root), ddl_dirs, use_git)
    return {t.display: sorted(t.columns) for t in catalogue.tables.values() if not t.open}


# ---------------------------------------------------------------------------------------------------------------------
# statements
# ---------------------------------------------------------------------------------------------------------------------


@dataclass
class StatementColumnReport:
    """What the gate saw: the findings, and how much of the code it could actually compare against the DDL."""

    findings: list[Finding] = field(default_factory=list)
    statements: int = 0
    checked: int = 0
    #: reason -> constant names; a statement here was NOT compared with the DDL, which is not the same as clean.
    not_checked: dict[str, list[str]] = field(default_factory=dict)
    #: ``schema.table`` -> statements naming a table the DDL does not define (or cannot list the columns of).
    unknown_tables: dict[str, int] = field(default_factory=dict)
    tables: int = 0
    ddl_files: int = 0

    @property
    def unchecked(self) -> int:
        return sum(len(names) for names in self.not_checked.values())

    def summary(self) -> str:
        reasons = "; ".join(f"{len(names)} {reason}" for reason, names in sorted(self.not_checked.items())) or "none"
        unknown = ", ".join(f"{name} ({count})" for name, count in sorted(self.unknown_tables.items(), key=lambda kv: (-kv[1], kv[0]))[:10])
        return (
            f"{self.statements} SQL constant(s): {self.checked} checked against {self.tables} DDL table(s) in {self.ddl_files} file(s); "
            f"{self.unchecked} NOT checked ({reasons}); tables the DDL does not define: {len(self.unknown_tables)}"
            + (f" ({unknown}{', ...' if len(self.unknown_tables) > 10 else ''})" if unknown else "")
        )


def _sqlglot() -> Any:
    try:
        import sqlglot
        import sqlglot.errors
        from sqlglot import exp
    except ImportError as exc:
        raise ImportError("statement_columns_exist_in_ddl needs sqlglot: pip install 'py-ci-shared[sql]'") from exc
    return sqlglot, exp


def _parse(sqlglot: Any, exp: Any, text: str) -> list[Any]:
    """The statement's trees, or ``[]`` when sqlglot cannot parse it (a ``Command`` fallback understands nothing, so it is no parse)."""
    logger = logging.getLogger("sqlglot")
    level = logger.level
    logger.setLevel(logging.ERROR)  # its "falling back to parsing as a Command" warning is what this function turns into a count
    try:
        trees = [t for t in sqlglot.parse(_parse_ready(text), read="postgres") if t is not None]
    except (sqlglot.errors.SqlglotError, RecursionError):
        return []
    finally:
        logger.setLevel(level)
    return [] if any(isinstance(t, exp.Command) for t in trees) else trees


def _parse_ready(text: str) -> str:
    """The statement with each driver placeholder replaced by ``NULL`` (``VALUES %s`` by one row of it)."""
    text = _VALUES_PARAM.sub(r"\1(NULL)", text)
    return _PYFORMAT_PARAM.sub("NULL", text).replace("%%", "%")


class _Statement:
    """One parsed statement walked against the catalogue: the missing columns, the tables it touches."""

    def __init__(self, tree: Any, exp: Any, catalogue: _Catalogue, unqualified: bool) -> None:
        self.exp = exp
        self.unqualified = unqualified
        self.catalogue = catalogue
        self.ctes = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
        self.missing: dict[tuple[str, str], tuple[_Table, str]] = {}  # (table, column) -> (table, column)
        self.touched = False
        self.known = False
        self.unknown: set[str] = set()
        self._tables: dict[int, Optional[_Table]] = {}
        self._bindings: dict[int, dict[str, Any]] = {}
        self._walk(tree)

    # -- tables -------------------------------------------------------------------------------------------------------

    def table_of(self, node: Any) -> Optional[_Table]:
        """The catalogue table a ``Table`` node names; ``None`` for a CTE, a function, an unknown, open or ambiguous table."""
        cached = self._tables.get(id(node), self)
        if cached is not self:
            return cached  # type: ignore[return-value]
        found: Optional[_Table] = None
        if isinstance(node, self.exp.Table) and isinstance(node.this, self.exp.Identifier):
            schema = node.db.lower() if node.db else None
            name = node.name.lower()
            if not (schema is None and name in self.ctes):
                table = self.catalogue.find((schema, name))
                if table is None or table.open:
                    if not self.catalogue.ambiguous((schema, name)):
                        self.unknown.add(f"{schema}.{name}" if schema else name)
                else:
                    found = table
                    self.known = True
        self._tables[id(node)] = found
        return found

    def _sources(self, node: Any) -> list[Any]:
        exp = self.exp
        found: list[Any] = []

        def add(source: Any) -> None:
            if source is None:
                return
            found.append(source)
            for join in source.args.get("joins") or []:
                add(join.this)

        if isinstance(node, exp.Select):
            origin = node.args.get("from_") or node.args.get("from")
            add(origin.this if origin is not None else None)
            for join in node.args.get("joins") or []:
                add(join.this)
        elif isinstance(node, exp.Update):
            add(node.this)
            origin = node.args.get("from_") or node.args.get("from")
            add(origin.this if origin is not None else None)
            for join in node.args.get("joins") or []:
                add(join.this)
        elif isinstance(node, exp.Delete):
            add(node.this)
            for using in node.args.get("using") or []:
                add(using)
        return found

    def bindings(self, node: Any) -> dict[str, Any]:
        """Alias or table name -> the ``Table`` node it binds, or ``None`` for a subquery, VALUES, LATERAL or function."""
        cached = self._bindings.get(id(node))
        if cached is None:
            cached = {}
            for source in self._sources(node):
                name = (source.alias_or_name or "").lower()
                if name:
                    cached[name] = source if isinstance(source, self.exp.Table) else None
            self._bindings[id(node)] = cached
        return cached

    # -- checks -------------------------------------------------------------------------------------------------------

    def check(self, table: Optional[_Table], column: str) -> None:
        if table is None:
            return
        self.touched = True
        column = column.lower()
        if column and column not in table.columns:
            self.missing.setdefault((table.display, column), (table, column))

    def _qualified(self, column: Any) -> None:
        exp = self.exp
        qualifier = column.table.lower()
        if column.args.get("db"):
            return
        node, in_conflict = column.parent, False
        while node is not None:
            if isinstance(node, exp.OnConflict):
                in_conflict = True
            if isinstance(node, (exp.Select, exp.Update, exp.Delete)):
                bound = self.bindings(node)
                if qualifier in bound:
                    self.check(self.table_of(bound[qualifier]) if bound[qualifier] is not None else None, column.name)
                    return
            elif isinstance(node, exp.Insert) and in_conflict:
                target = node.this.this if isinstance(node.this, exp.Schema) else node.this
                if qualifier == "excluded" or qualifier == (target.alias_or_name or "").lower():
                    self.check(self.table_of(target), column.name)
                    return
            node = node.parent

    def _has_outer_scope(self, scope: Any) -> bool:
        """True when a column that *scope* cannot resolve might belong to an enclosing SELECT/UPDATE/DELETE (a correlated subquery)."""
        exp = self.exp
        derived = False
        node = scope.parent
        while node is not None:
            if isinstance(node, exp.CTE):
                return False
            if isinstance(node, exp.Lateral):
                return True
            if isinstance(node, exp.Subquery) and isinstance(node.parent, (exp.From, exp.Join)):
                derived = True
            if isinstance(node, (exp.Select, exp.Update, exp.Delete)):
                return not derived
            node = node.parent
        return False

    def _bare(self, column: Any) -> None:
        """An unqualified column, checked only where it can only mean one thing: the single source of its own scope is a catalogue
        table, the name is not an output alias or the whole-row reference to that source, and no enclosing scope could own it."""
        exp = self.exp
        scope = column.parent
        while scope is not None and not isinstance(scope, (exp.Select, exp.Update, exp.Delete, exp.Insert)):
            scope = scope.parent
        if scope is None or isinstance(scope, exp.Insert):
            return
        sources = self._sources(scope)
        if len(sources) != 1 or not isinstance(sources[0], exp.Table):
            return
        table = self.table_of(sources[0])
        name = column.name.lower()
        if table is None or name in table.columns:
            self.check(table, name)
            return
        aliases = {(e.alias or "").lower() for e in scope.expressions} if isinstance(scope, exp.Select) else set()
        if name in aliases or name == (sources[0].alias_or_name or "").lower() or self._has_outer_scope(scope):
            return
        self.check(table, name)

    def _walk_insert(self, node: Any) -> None:
        exp = self.exp
        target = node.this
        names = []
        if isinstance(target, exp.Schema):
            names = [c.name for c in target.expressions if isinstance(c, (exp.Identifier, exp.Column))]
            target = target.this
        table = self.table_of(target)
        for name in names:
            self.check(table, name)
        conflict = node.args.get("conflict")
        if conflict is None:
            return
        for key in conflict.args.get("conflict_keys") or []:
            key = key.this if isinstance(key, exp.Ordered) else key
            if isinstance(key, (exp.Identifier, exp.Column)):
                self.check(table, key.name)
        self._check_assignments(table, conflict.expressions)

    def _check_assignments(self, table: Optional[_Table], assignments: Any) -> None:
        for assign in assignments or []:
            if isinstance(assign, self.exp.EQ) and isinstance(assign.this, self.exp.Column):
                self.check(table, assign.this.name)

    def _walk(self, tree: Any) -> None:
        exp = self.exp
        for node in tree.find_all(exp.Insert):
            self._walk_insert(node)
        for node in tree.find_all(exp.Update):
            self._check_assignments(self.table_of(node.this), node.expressions)
        for column in tree.find_all(exp.Column):
            if column.table:
                self._qualified(column)
            elif self.unqualified and isinstance(column.this, exp.Identifier):
                self._bare(column)
        for table_node in tree.find_all(exp.Table):
            self.table_of(table_node)


def _line_of(constant: StatementConstant, column: str) -> int:
    if constant.single:
        hit = re.search(rf"(?<![\w]){re.escape(column)}(?![\w])", constant.text, re.IGNORECASE)
        if hit:
            return constant.line + constant.text.count("\n", 0, hit.start())
    return constant.line


def statement_column_report(
    root: Union[str, Path],
    *,
    ddl_dirs: Iterable[Union[str, Path]] = ("sql",),
    exclude_top_dirs: Iterable[str] = (),
    statement_starts: Iterable[str] = DEFAULT_STATEMENT_STARTS,
    min_files: int = 1,
    min_tables: int = 1,
    check_unqualified: bool = False,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> StatementColumnReport:
    """Compare every SQL constant under *root* with the DDL files in *ddl_dirs* (relative to *root*); see the module docstring.

    Raises ``EmptyScanError`` below *min_files* parsed Python files, ``UnparsedFilesError`` for a Python or DDL file that cannot be
    read or parsed (unless *allow_unparsed*), and ``AssertionError`` when the DDL defines fewer than *min_tables* tables.
    """
    root_path = Path(root)
    sqlglot, exp = _sqlglot()
    skip = set(exclude_top_dirs)
    scan: ScanResult = scan_python(root_path, min_files=min_files, use_git=use_git)
    scan.files = [f for f in scan.files if f.rel.split("/")[0] not in skip]
    scan.unparsed = [p for p in scan.unparsed if p.rel.split("/")[0] not in skip]
    catalogue, problems = _build_catalogue(root_path, ddl_dirs, use_git)
    scan.unparsed.extend(problems)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    known = [t for t in catalogue.tables.values() if not t.open]
    if len(known) < min_tables:
        raise AssertionError(
            f"only {len(known)} table(s) with a column list found in the DDL under {list(ddl_dirs)} ({catalogue.files} file(s)); expected at least "
            f"{min_tables}. Check ddl_dirs: the gate has lost its subject"
        )
    report = StatementColumnReport(tables=len(known), ddl_files=catalogue.files)
    starts = tuple(s.upper() for s in statement_starts)
    for constant in statement_constants(scan, starts, include_dynamic=True):
        report.statements += 1
        if not constant.exact or _FORMAT_FIELD.search(constant.text):
            report.not_checked.setdefault(NOT_LITERAL, []).append(constant.name)
            continue
        trees = _parse(sqlglot, exp, constant.text)
        if not trees:
            report.not_checked.setdefault(UNPARSABLE, []).append(constant.name)
            continue
        touched = has_known = False
        unknown: set[str] = set()
        missing: dict[tuple[str, str], tuple[_Table, str]] = {}
        for tree in trees:
            statement = _Statement(tree, exp, catalogue, check_unqualified)
            touched = touched or statement.touched
            has_known = has_known or statement.known
            unknown |= statement.unknown
            for key, value in statement.missing.items():
                missing.setdefault(key, value)
        for name in unknown:
            report.unknown_tables[name] = report.unknown_tables.get(name, 0) + 1
        if not touched:
            report.not_checked.setdefault(NO_RESOLVABLE_COLUMN if has_known else NO_DDL_TABLE, []).append(constant.name)
            continue
        report.checked += 1
        for table, column in missing.values():
            dropped = table.dropped.get(column)
            why = (
                f"which the DDL drops in {dropped}"
                if dropped
                else f"which the DDL ({', '.join(sorted(set(table.created_in))[:2]) or 'its files'}) does not define"
            )
            report.findings.append(
                Finding(
                    constant.rel,
                    _line_of(constant, column),
                    RULE,
                    f"{constant.name} names column `{column}` of {table.display}, {why}; Add the ALTER TABLE ... ADD COLUMN migration to a DDL file, or correct the name",
                )
            )
    report.findings.sort(key=lambda f: (f.path, f.line, f.message))
    return report


def find_unknown_statement_columns(
    root: Union[str, Path],
    *,
    ddl_dirs: Iterable[Union[str, Path]] = ("sql",),
    exclude_top_dirs: Iterable[str] = (),
    statement_starts: Iterable[str] = DEFAULT_STATEMENT_STARTS,
    min_files: int = 1,
    min_tables: int = 1,
    check_unqualified: bool = False,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every column a SQL constant names that the DDL files do not define, sorted by path and line."""
    return statement_column_report(
        root,
        ddl_dirs=ddl_dirs,
        exclude_top_dirs=exclude_top_dirs,
        statement_starts=statement_starts,
        min_files=min_files,
        min_tables=min_tables,
        check_unqualified=check_unqualified,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    ).findings


def assert_statement_columns_exist_in_ddl(
    root: Union[str, Path],
    baseline_path: Optional[Union[str, Path]] = None,
    *,
    ddl_dirs: Iterable[Union[str, Path]] = ("sql",),
    exclude_top_dirs: Iterable[str] = (),
    statement_starts: Iterable[str] = DEFAULT_STATEMENT_STARTS,
    refresh: bool = False,
    min_files: int = 1,
    min_tables: int = 1,
    min_checked: int = 1,
    check_unqualified: bool = False,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a column a statement names that no DDL file defines, or with *baseline_path* on any the baseline does not accept.

    Also fails when fewer than *min_checked* statements could be compared with the DDL at all: a gate that skipped everything
    has checked nothing.
    """
    report = statement_column_report(
        root,
        ddl_dirs=ddl_dirs,
        exclude_top_dirs=exclude_top_dirs,
        statement_starts=statement_starts,
        min_files=min_files,
        min_tables=min_tables,
        check_unqualified=check_unqualified,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    if report.checked < min_checked:
        raise AssertionError(
            f"only {report.checked} SQL statement(s) could be compared with the DDL; expected at least {min_checked}. "
            f"Check ddl_dirs and exclude_top_dirs; {report.summary()}"
        )
    guidance = "Add the additive ALTER TABLE ... ADD COLUMN migration to the DDL directory before the code ships, or correct the column name"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="statement_columns_exist_in_ddl", refresh_command="PY_CI_SHARED_REFRESH=statement_columns_exist_in_ddl")
        baseline.enforce(report.findings, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if report.findings:
        raise AssertionError(
            f"{len(report.findings)} statement-columns-exist-in-ddl finding(s); {guidance}:\n  "
            + "\n  ".join(f.render() for f in report.findings)
            + f"\n  ({report.summary()})"
        )
