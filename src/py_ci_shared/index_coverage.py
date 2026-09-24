"""Is an expected index already served by a live one under a different name?

Two tools in two packages had the same defect, which is why this lives here rather than in either
of them:

* `realtime_applications/scripts/check_indexes.py` (IDX-1) compared names and told the operator to
  apply a file. The DDL's `CREATE INDEX CONCURRENTLY IF NOT EXISTS` guard could not stop the
  resulting duplicate, because IF NOT EXISTS also matches by NAME.
* `production_scrapers/scripts/check_schema_drift.py` (SQL-10) already fetched the live catalogue
  for its `--unmanaged` list, so a renamed index landed in BOTH of its lists and the report never
  said so. Four of the ten it called missing had their access path already.

AUDIT 2026-09-08 IDX-1. `check_indexes.py` compared index NAMES, and a name-miss printed
`[MISSING] ... -- apply sql/recommended_indexes.sql`. On this database 20 entries missed by name, and
at least eight of them are already present -- five of those ARE the table's primary key:

    idx_job_embeddings_job_uid  (job_uid)  <- job_embeddings_pkey  btree (job_uid)   115 GB
    idx_jobs_details_uid        (uid)      <- jobs_details_pkey    btree (uid)        35 GB
    idx_client_legacy_map_legacy_id        <- client_legacy_map_pkey                 1.7 GB

WHY THE NAME CHECK IS THE DANGEROUS ONE. The DDL files guard every statement with
`CREATE INDEX CONCURRENTLY IF NOT EXISTS` -- and IF NOT EXISTS matches by NAME. A duplicate that
differs only in name is precisely the one that guard cannot catch. So the tool said "apply this", the
file's own safety net could not stop it, and the result would be a second full B-tree over 21M rows
of a 115 GB table, built with two concurrent passes, that the planner will never prefer over the
primary key it duplicates.

DELIBERATELY CONSERVATIVE. Predicate implication is not decidable here, so coverage is claimed only
when redundancy is provable by inspection:

  * same table (and schema, when both statements name one), and same access method, and
  * the live index's leading key columns are exactly the expected index's key columns, with the same
    direction and NULLS ordering (or a whole-index reversal of both, for a btree), and
  * the live index is unconditional, or carries a textually identical predicate, and
  * every INCLUDE column of the expected index is a key or INCLUDE column of the live one, and
  * a UNIQUE expectation is met only by a UNIQUE live index on exactly the same key columns and predicate:
    a wider or non-unique index does not enforce the constraint.

Anything short of that stays `[MISSING]`. A partial live index does NOT cover an unconditional
expectation -- `idx_fj_client_team_uid_nn ... WHERE client_team_uid IS NOT NULL` is left reported
against `idx_freelancers_jobs_client_team_uid (client_team_uid)`, because rows where the column is
NULL are genuinely not in it. Under-claiming costs an operator one manual look; over-claiming hides a
real missing index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CREATE = re.compile(
    r"CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<name>[\w.\"]+)\s+ON\s+(?:ONLY\s+)?(?P<target>[\w.\"]+)\s*"
    r"(?:USING\s+(?P<method>\w+)\s*)?"
    r"\((?P<cols>.*)",
    re.IGNORECASE | re.DOTALL,
)
_TRAILING_DIRECTION = re.compile(
    r"\s+(?:ASC|DESC)(?:\s+NULLS\s+(?:FIRST|LAST))?$|\s+NULLS\s+(?:FIRST|LAST)$",
    re.IGNORECASE,
)
# A cast's TYPE only: `x::int IS NOT NULL` keeps its `IS NOT NULL`. Multi-word type names are listed explicitly.
_CAST = re.compile(
    r"::\s*(?:character\s+varying|double\s+precision|bit\s+varying|"
    r"(?:timestamp|time)(?:\s*\(\d+\))?\s+with(?:out)?\s+time\s+zone|\"[^\"]+\"|[\w.]+)(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?(?:\[\])*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Index:
    """The parts of an index definition that decide whether it can serve a query."""

    name: str
    method: str
    columns: tuple[str, ...]
    predicate: str | None
    descending: tuple[bool, ...] = ()
    schema: str | None = None
    table: str | None = None
    unique: bool = False
    include: tuple[str, ...] = ()
    #: Per key column, whether NULLs sort first (explicit NULLS FIRST/LAST, else Postgres' default: first for DESC).
    nulls_first: tuple[bool, ...] = ()

    def same_target(self, other: "Index") -> bool:
        """Same table, and same schema when both name one (an unqualified name resolves through search_path)."""
        if self.table is not None and other.table is not None and self.table != other.table:
            return False
        return self.schema is None or other.schema is None or self.schema == other.schema

    def covers(self, other: "Index") -> bool:
        """True when `other` would be redundant given this index already exists."""
        if self.method != other.method or not self.same_target(other):
            return False
        width = len(other.columns)
        if self.columns[:width] != other.columns:
            return False
        if not self._orders_agree(other, width):
            return False
        if not set(other.include) <= set(self.columns) | set(self.include):
            return False
        if other.unique and not (self.unique and self.columns == other.columns and self.predicate == other.predicate):
            return False
        return self.predicate is None or self.predicate == other.predicate

    def _orders_agree(self, other: "Index", width: int) -> bool:
        """Sort direction, which cannot simply be discarded.

        A btree is scannable in both directions, so `(a, b)` serves `(a DESC, b DESC)`. That is a
        whole-index reversal. It does NOT serve `(a, b DESC)`: neither the forward nor the backward
        scan produces that order, and treating the two as equal would report a genuinely missing
        index as already covered -- the one error mode this module must not have.
        """
        mine = self._order_keys(width)
        theirs = other._order_keys(width)
        if self.method != "btree" or not self.descending or not other.descending:
            return mine == theirs
        # A backward scan flips both the direction and the NULLS placement of every column at once.
        return mine == theirs or all(a[0] != b[0] and a[1] != b[1] for a, b in zip(mine, theirs))

    def _order_keys(self, width: int) -> tuple[tuple[bool, bool], ...]:
        descending = self.descending[:width]
        nulls = self.nulls_first[:width] if len(self.nulls_first) >= len(descending) else descending
        return tuple(zip(descending, nulls))


def _split_top_level(text: str) -> list[str]:
    """Split a column list on commas that are not inside parentheses or quotes.

    Index columns are frequently expressions -- `((details ->> 'startedOn'::text))`,
    `GREATEST(a, b)` -- so a plain `.split(",")` shreds them into fragments that compare equal to
    nothing and silently make every comparison fail.
    """
    parts, depth, current, quote = [], 0, [], None
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            current.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _balanced_prefix(text: str) -> tuple[str, str]:
    """Return the key-column list (already inside its opening paren) and the rest of the statement."""
    depth = 1
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return "".join(out), text[i + 1 :]
        out.append(ch)
    raise ValueError("unbalanced parentheses in index column list")


def _normalise(column: str) -> tuple[str, bool]:
    """The column expression and whether it sorts DESC, with quoting and whitespace normalised."""
    expression, descending, _nulls_first = _normalise_key(column)
    return expression, descending


def _normalise_key(column: str) -> tuple[str, bool, bool]:
    """``(expression, DESC?, NULLS FIRST?)`` for one key column; NULLS defaults to FIRST for DESC, LAST for ASC."""
    column = " ".join(column.split())
    # Read the direction off the trailing keyword only; a bare `\bDESC\b` search would also fire on
    # the word inside an expression's string literal.
    trailing = _TRAILING_DIRECTION.search(column)
    words = trailing.group(0).lower() if trailing is not None else ""
    descending = "desc" in words
    nulls_first = "nulls first" in words or (descending and "nulls last" not in words)
    column = _TRAILING_DIRECTION.sub("", column).strip()
    while column.startswith("(") and column.endswith(")"):
        inner, rest = _balanced_prefix(column[1:])
        if rest:
            break
        column = inner.strip()
    return column.replace('"', "").lower(), descending, nulls_first


def parse(statement: str) -> Index | None:
    """Parse a `CREATE INDEX` statement -- from a .sql file or from `pg_indexes.indexdef`.

    Both sides go through this one function on purpose: a definition and its echo back from the
    catalogue must normalise identically, or coverage would depend on which spelling was compared.
    """
    statement = statement.strip().rstrip(";")
    match = _CREATE.search(statement)
    if not match:
        return None
    try:
        columns_text, rest = _balanced_prefix(match.group("cols"))
    except ValueError:
        return None
    predicate = None
    where = re.search(r"\bWHERE\b(?P<pred>.*)$", rest, re.IGNORECASE | re.DOTALL)
    if where:
        predicate = _normalise_predicate(where.group("pred"))
    include: tuple[str, ...] = ()
    included = re.match(r"\s*INCLUDE\s*\(", rest, re.IGNORECASE)
    if included:
        try:
            include_text, _ = _balanced_prefix(rest[included.end() :])
        except ValueError:
            return None
        include = tuple(_normalise_key(c)[0] for c in _split_top_level(include_text))
    keys = [_normalise_key(c) for c in _split_top_level(columns_text)]
    # The statement names its own target, so nothing downstream has to guess it. An ad-hoc regex
    # over a whole .sql file for "the table near this index name" matched a query ALIAS and
    # reported `new_upwork.a`; the definition is the only place that answer is reliable.
    target = match.group("target").replace('"', "").split(".")
    return Index(
        name=match.group("name").split(".")[-1].replace('"', ""),
        method=(match.group("method") or "btree").lower(),
        columns=tuple(expression for expression, _, _ in keys),
        predicate=predicate,
        descending=tuple(descending for _, descending, _ in keys),
        schema=target[-2].lower() if len(target) > 1 else None,
        table=target[-1].lower(),
        unique=bool(match.group("unique")),
        include=include,
        nulls_first=tuple(nulls for _, _, nulls in keys),
    )


def _normalise_predicate(predicate: str) -> str:
    """`WHERE (details IS NOT NULL)` and `WHERE details IS NOT NULL` are the same predicate."""
    predicate = " ".join(predicate.split()).strip()
    while predicate.startswith("(") and predicate.endswith(")"):
        inner, rest = _balanced_prefix(predicate[1:])
        if rest:
            break
        predicate = inner.strip()
    return " ".join(_CAST.sub("", predicate).split()).replace('"', "").lower()


def find_cover(expected: Index, live: list[Index]) -> Index | None:
    """The live index that makes `expected` redundant, preferring an exact column-count match."""
    candidates = [i for i in live if i.name != expected.name and i.covers(expected)]
    if not candidates:
        return None
    return min(candidates, key=lambda i: (len(i.columns), i.name))


def statements(sql_text: str):
    """Yield each statement of a .sql file with `--` and `/* */` comments removed.

    Comment stripping is not cosmetic. `sql/audit_wave3_migrations.sql` carries a fully
    commented-out `idx_fj_fluid_clientteam_ts` block whose own header reads "do NOT uncomment" --
    it names a column that does not exist. A scan that ignores comments reads that dead block as
    the live definition and reports the wrong columns for an index defined correctly elsewhere.
    The split is quote-aware: a `;` or `--` inside a string literal, a quoted identifier or a
    dollar-quoted body is text, not syntax.
    """
    current: list[str] = []
    i, n = 0, len(sql_text)
    while i < n:
        ch = sql_text[i]
        if sql_text.startswith("--", i):
            end = sql_text.find("\n", i)
            i = n if end == -1 else end
            continue
        if sql_text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql_text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif sql_text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            current.append(" ")
            i = j
            continue
        if ch in "'\"":
            j = i + 1
            while j < n:
                if sql_text[j] == ch:
                    if j + 1 < n and sql_text[j + 1] == ch:  # doubled quote is an escaped quote
                        j += 2
                        continue
                    break
                j += 1
            current.append(sql_text[i : j + 1])
            i = j + 1
            continue
        dollar = re.match(r"\$(?:[A-Za-z_]\w*)?\$", sql_text[i:]) if ch == "$" else None
        if dollar:
            tag = dollar.group(0)
            end = sql_text.find(tag, i + len(tag))
            stop = n if end == -1 else end + len(tag)
            current.append(sql_text[i:stop])
            i = stop
            continue
        if ch == ";":
            text = "".join(current).strip()
            if text:
                yield text
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    text = "".join(current).strip()
    if text:
        yield text


def find_definition(sql_text: str, index_name: str) -> Index | None:
    """The parsed definition of `index_name` in `sql_text`, or None.

    The match is CONFIRMED by parsing rather than by regex proximity: a statement is accepted only
    when the name it actually declares equals the one asked for. Searching for a name inside a
    statement blob instead attributes a neighbouring statement's columns to it -- which is how an
    earlier pass credited `idx_coj_job_cid` with `client_legacy_map`'s `legacy_id`.
    """
    for statement in statements(sql_text):
        parsed = parse(statement)
        if parsed is not None and parsed.name == index_name:
            return parsed
    return None
