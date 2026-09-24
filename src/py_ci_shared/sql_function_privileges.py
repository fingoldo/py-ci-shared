"""Shared check: a ``SECURITY DEFINER`` SQL function is not left executable by every logged-in user.

PostgreSQL grants ``EXECUTE`` on a new function to ``PUBLIC`` by default. A ``SECURITY DEFINER``
function runs with its owner's rights, so "default privileges" plus "definer" means *any*
authenticated caller can invoke privileged code. On a Supabase project every such function is also
reachable over HTTP at ``/rest/v1/rpc/<name>`` with nothing but an anon key and a session, so the
default is not a theoretical exposure.

Found for real on 2026-09-02 (glossum audit P03-1, the round's only P0): ``sync_plan_grant()``
inserted a row into the admin-plan grant table, was ``SECURITY DEFINER``, and had no ``REVOKE`` --
any signed-in user could grant themselves a permanent admin plan with one HTTP call. The same
round found ``access_token_hook`` (P03-2) and four ``cleanup_*`` functions (P03-10) in the same
state, and five functions with no ``SET search_path`` (P03-9), which lets a caller who can create
objects in a schema on the search path shadow an unqualified name inside the definer body.

Three rules, evaluated on the state the migration directory leaves behind (the replay below matches how a
real ``supabase db push`` applies them):

1. Every ``SECURITY DEFINER`` function must have a ``REVOKE EXECUTE`` from ``PUBLIC`` (or from both
   ``anon`` and ``authenticated``) somewhere in the migrations, or sit in ``allowed`` with a reason.
2. Every ``SECURITY DEFINER`` function must ``SET search_path`` in its header.
3. Advisory: list the definer functions that are deliberately callable, so the reachable RPC
   surface is written down somewhere a reviewer can see it.


Statements are replayed in migration order (files sorted by path, statements in file order), so a later
``CREATE OR REPLACE`` supersedes an earlier header, a ``DROP FUNCTION`` followed by ``CREATE`` starts again from
PostgreSQL's default ``PUBLIC`` grant, and ``ALTER DEFAULT PRIVILEGES ... REVOKE EXECUTE ON FUNCTIONS`` applies
to functions created after it. Functions are keyed by ``(schema, name)``: an unqualified name resolves against
the first schema of the file's last ``SET search_path`` (``public`` otherwise), and a quoted identifier keeps its
case. Comments are stripped and dollar-quoted bodies are opaque, so a commented-out ``REVOKE`` or a ``SECURITY
DEFINER`` inside a body string counts for nothing, while options written after the body (``$$ LANGUAGE plpgsql
SECURITY DEFINER;``) are seen.

A small SQL lexer, no parser and no new dependency, matching this package's other scanners. The same rule fires
on any repo that owns Postgres migrations, whatever the app language.

Usage::

    from py_ci_shared.sql_function_privileges import assert_definer_functions_are_locked_down

    def test_security_definer_functions_are_not_public():
        assert_definer_functions_are_locked_down(
            REPO / "supabase" / "migrations",
            allowed={"authorize": "scopes every branch to auth.uid(); safe to call as any user"},
        )
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ._core import CorpusError, SourceReadError, iter_files, read_source

_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
_QUALIFIED = rf"(?:(?P<schema>{_IDENT})\s*\.\s*)?(?P<name>{_IDENT})"
_CREATE_FUNCTION_RE = re.compile(rf"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+{_QUALIFIED}\s*\(", re.IGNORECASE)
_DROP_FUNCTION_RE = re.compile(r"^DROP\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?(?P<targets>.*?)(?:\s+(?:CASCADE|RESTRICT))?\s*$", re.IGNORECASE | re.DOTALL)
_ALTER_FUNCTION_RE = re.compile(rf"^ALTER\s+FUNCTION\s+{_QUALIFIED}\s*(?:\([^)]*\))?(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_PRIV = r"(?:ALL(?:\s+PRIVILEGES)?|EXECUTE)"
# `REVOKE [GRANT OPTION FOR] EXECUTE ON FUNCTION a(..), b FROM roles` / `GRANT EXECUTE ON FUNCTION a(..) TO roles`
_ON_FUNCTION_RE = re.compile(
    rf"^(?P<verb>REVOKE|GRANT)\s+(?P<grant_option>GRANT\s+OPTION\s+FOR\s+)?{_PRIV}\s+ON\s+(?:FUNCTION|ROUTINE)S?\s+(?P<targets>.*?)\s+(?:FROM|TO)\s+(?P<roles>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_ON_ALL_RE = re.compile(
    rf"^(?P<verb>REVOKE|GRANT)\s+(?P<grant_option>GRANT\s+OPTION\s+FOR\s+)?{_PRIV}\s+ON\s+ALL\s+(?:FUNCTIONS|ROUTINES)\s+IN\s+SCHEMA\s+(?P<schemas>.*?)\s+(?:FROM|TO)\s+(?P<roles>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_DEFAULT_PRIVS_RE = re.compile(
    rf"^ALTER\s+DEFAULT\s+PRIVILEGES\s+(?P<scope>.*?)\b(?P<verb>REVOKE|GRANT)\s+(?P<grant_option>GRANT\s+OPTION\s+FOR\s+)?{_PRIV}\s+ON\s+(?:FUNCTIONS|ROUTINES)\s+(?:FROM|TO)\s+(?P<roles>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_IN_SCHEMA_RE = re.compile(r"\bIN\s+SCHEMA\s+(?P<schemas>.*)$", re.IGNORECASE | re.DOTALL)
_SET_SEARCH_PATH_STMT_RE = re.compile(r"^SET\s+(?:SESSION\s+|LOCAL\s+)?search_path\s*(?:=|TO)\s*(?P<value>.*)$", re.IGNORECASE | re.DOTALL)
_SECURITY_DEFINER_RE = re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE)
_SECURITY_INVOKER_RE = re.compile(r"\bSECURITY\s+INVOKER\b", re.IGNORECASE)
_SEARCH_PATH_RE = re.compile(r"\bSET\s+search_path\b", re.IGNORECASE)
_RESET_SEARCH_PATH_RE = re.compile(r"\bRESET\s+(?:search_path|ALL)\b", re.IGNORECASE)
# Roles that mean "anyone with a session" on a Supabase project.
_CALLER_ROLES = ("public", "anon", "authenticated")


@dataclass(frozen=True)
class _Statement:
    """One SQL statement: ``text`` has comments removed and every string/dollar-quoted literal blanked."""

    path: Path
    line: int
    text: str
    first_line: str


def _statements(path: Path, sql: str) -> Iterator[_Statement]:
    """Split *sql* on top-level ``;``. Comments (``--``, nested ``/* */``) are dropped; the CONTENT of ``'...'``,
    ``E'...'`` and ``$tag$...$tag$`` literals is replaced by spaces so no keyword inside a body or a string is seen,
    while the quotes stay so the statement keeps its shape. Double-quoted identifiers are kept verbatim."""
    out: list[str] = []
    raw_start = 0
    line = 1
    start_line = 1
    i, n = 0, len(sql)
    pending = False

    def flush(end: int) -> Optional[_Statement]:
        text = "".join(out).strip()
        out.clear()
        if not text:
            return None
        first = next((ln.strip() for ln in sql[raw_start:end].splitlines() if ln.strip()), text)
        return _Statement(path, start_line, text, first)

    while i < n:
        ch = sql[i]
        if ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            continue
        if not pending and not ch.isspace() and not sql.startswith("/*", i):
            pending, start_line, raw_start = True, line, i
        if ch == "/" and sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif sql.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            line += sql.count("\n", i, j)
            out.append(" ")
            i = j
            continue
        if ch == "'":
            backslash = i > 0 and sql[i - 1] in "eE" and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            j = i + 1
            while j < n:
                if backslash and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if sql.startswith("''", j):
                        j += 2
                        continue
                    break
                j += 1
            line += sql.count("\n", i, j)
            out.append("''")
            i = j + 1
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if sql.startswith('""', j):
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
            continue
        if ch == "$":
            m = re.match(r"\$(?:[A-Za-z_][\w]*)?\$", sql[i:])
            if m and not (i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                j = n if j < 0 else j + len(tag)
                line += sql.count("\n", i, j)
                out.append("$$ $$")
                i = j
                continue
        if ch == ";":
            stmt = flush(i)
            if stmt is not None:
                yield stmt
            pending = False
            i += 1
            continue
        if ch == "\n":
            line += 1
        out.append(ch)
        i += 1
    stmt = flush(n)
    if stmt is not None:
        yield stmt


def _ident(token: Optional[str]) -> Optional[str]:
    """A PostgreSQL identifier as the catalog stores it: quoted keeps its case, unquoted folds to lower."""
    if token is None:
        return None
    token = token.strip()
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        return token[1:-1].replace('""', '"')
    return token.lower()


def _split_top_level(text: str) -> list[str]:
    """Split on commas outside parentheses: ``a(int, text), b()`` -> ``["a(int, text)", "b()"]``."""
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


_TARGET_RE = re.compile(rf"^{_QUALIFIED}\s*(?:\(.*\))?$", re.DOTALL)


def _targets(text: str, default_schema: str) -> list[tuple[str, str]]:
    out = []
    for part in _split_top_level(text):
        m = _TARGET_RE.match(part)
        if m:
            out.append((_ident(m.group("schema")) or default_schema, _ident(m.group("name")) or ""))
    return out


def _roles(text: str) -> set[str]:
    text = re.split(r"\b(?:GRANTED\s+BY|CASCADE|RESTRICT|WITH\s+GRANT\s+OPTION)\b", text, flags=re.IGNORECASE)[0]
    return {r for r in (_ident(p) for p in _split_top_level(text)) if r in _CALLER_ROLES}


@dataclass
class _Function:
    schema: str
    name: str
    where: str
    definer: bool
    search_path: bool
    revoked: set[str] = field(default_factory=set)
    granted: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return f"{self.schema}.{self.name}"

    def locked(self) -> bool:
        return "public" in self.revoked or {"anon", "authenticated"} <= self.revoked


class _Replay:
    def __init__(self) -> None:
        self.functions: dict[tuple[str, str], _Function] = {}
        self.ever_defined: set[tuple[str, str]] = set()
        # schema (None = every schema) -> caller roles whose EXECUTE is revoked by default on NEW functions
        self.default_revoked: dict[Optional[str], set[str]] = {}

    def _defaults_for(self, schema: str) -> set[str]:
        return set(self.default_revoked.get(None, set())) | set(self.default_revoked.get(schema, set()))

    def apply(self, stmt: _Statement, default_schema: str) -> str:
        """Apply one statement; returns the (possibly changed) default schema for unqualified names."""
        text = stmt.text
        m = _SET_SEARCH_PATH_STMT_RE.match(text)
        if m:
            names = [_ident(s) for s in _split_top_level(m.group("value"))]
            usable = [s for s in names if s and s not in ("''", "$user", "default")]
            return usable[0] if usable else default_schema
        m = _CREATE_FUNCTION_RE.match(text)
        if m:
            self._create(stmt, m, default_schema)
            return default_schema
        m = _DROP_FUNCTION_RE.match(text)
        if m:
            for key in _targets(m.group("targets"), default_schema):
                self.functions.pop(key, None)
            return default_schema
        m = _ALTER_FUNCTION_RE.match(text)
        if m:
            key = (_ident(m.group("schema")) or default_schema, _ident(m.group("name")) or "")
            fn = self.functions.get(key)
            rest = m.group("rest")
            if fn is not None:
                if _SECURITY_DEFINER_RE.search(rest):
                    fn.definer = True
                elif _SECURITY_INVOKER_RE.search(rest):
                    fn.definer = False
                if _SEARCH_PATH_RE.search(rest):
                    fn.search_path = True
                elif _RESET_SEARCH_PATH_RE.search(rest):
                    fn.search_path = False
            return default_schema
        m = _DEFAULT_PRIVS_RE.match(text)
        if m:
            if not m.group("grant_option"):
                scope = _IN_SCHEMA_RE.search(m.group("scope"))
                schemas: list[Optional[str]] = [_ident(s) for s in _split_top_level(scope.group("schemas"))] if scope else [None]
                for schema in schemas:
                    bucket = self.default_revoked.setdefault(schema, set())
                    roles = _roles(m.group("roles"))
                    if m.group("verb").upper() == "REVOKE":
                        bucket |= roles
                    else:
                        bucket -= roles
            return default_schema
        m = _ON_ALL_RE.match(text)
        if m:
            schemas_all = {_ident(s) for s in _split_top_level(m.group("schemas"))}
            keys = [k for k in self.functions if k[0] in schemas_all]
            self._privilege(m, keys)
            return default_schema
        m = _ON_FUNCTION_RE.match(text)
        if m:
            self._privilege(m, _targets(m.group("targets"), default_schema))
        return default_schema

    def _create(self, stmt: _Statement, m: "re.Match[str]", default_schema: str) -> None:
        key = (_ident(m.group("schema")) or default_schema, _ident(m.group("name")) or "")
        definer = bool(_SECURITY_DEFINER_RE.search(stmt.text))
        search_path = bool(_SEARCH_PATH_RE.search(stmt.text))
        where = f"{stmt.path.name}:{stmt.line}: {stmt.first_line}"
        self.ever_defined.add(key)
        existing = self.functions.get(key)
        if existing is not None:  # CREATE OR REPLACE keeps the function's privileges
            existing.definer, existing.search_path, existing.where = definer, search_path, where
            return
        self.functions[key] = _Function(key[0], key[1], where, definer, search_path, revoked=self._defaults_for(key[0]))

    def _privilege(self, m: "re.Match[str]", keys: list[tuple[str, str]]) -> None:
        if m.group("grant_option"):
            return  # REVOKE GRANT OPTION FOR leaves EXECUTE itself in place
        roles = _roles(m.group("roles"))
        revoke = m.group("verb").upper() == "REVOKE"
        for key in keys:
            fn = self.functions.get(key)
            if fn is None:
                continue
            if revoke:
                fn.revoked |= roles
                fn.granted -= roles
            else:
                fn.granted |= roles
                fn.revoked -= roles


def _migration_files(migrations_dir: Path) -> list[Path]:
    return iter_files(migrations_dir, ("*.sql",))


def _allowed_key(name: str, fn: _Function) -> bool:
    return name in (fn.name, fn.label)


def find_unlocked_definer_functions(
    migrations_dir: Path,
    allowed: "Mapping[str, str] | None" = None,
) -> list[str]:
    """Return one problem string per ``SECURITY DEFINER`` function that is callable by any
    signed-in user, or that omits ``SET search_path``.

    ``allowed`` maps a function name (``name`` or ``schema.name``) to the REASON it is deliberately callable (a bare
    name with no reason is rejected -- an allowlist without reasons is how this class of finding survives an
    audit). A function that is explicitly ``GRANT``ed to a caller role after being revoked counts as
    deliberately callable and must therefore also be in ``allowed``. A migration file that cannot be decoded is a
    problem too: its statements were not replayed.
    """
    allowed = dict(allowed or {})
    try:
        files = _migration_files(Path(migrations_dir))
    except CorpusError as exc:
        return [f"{migrations_dir}: {exc} - this check examined nothing, which reads as a pass. Point it at the migrations directory."]
    if not files:
        return [f"{migrations_dir}: no .sql files found - this check examined nothing, which reads as a pass. Point it at the migrations directory."]

    problems: list[str] = []
    replay = _Replay()
    for path in files:
        try:
            sql = read_source(path)
        except SourceReadError as exc:
            problems.append(f"{path.name}: {exc.message} - its statements were not replayed, so no function in it was checked.")
            continue
        schema = "public"
        for stmt in _statements(path, sql):
            schema = replay.apply(stmt, schema)

    definers = sorted((fn for fn in replay.functions.values() if fn.definer), key=lambda f: f.label)
    for fn in definers:
        is_allowed = any(_allowed_key(name, fn) for name in allowed)
        if not fn.locked():
            if is_allowed:
                continue
            problems.append(
                f"{fn.where}\n    SECURITY DEFINER with no `REVOKE EXECUTE ... FROM PUBLIC` (or from "
                f"both anon and authenticated) anywhere in the migrations. PostgreSQL grants "
                f"EXECUTE to PUBLIC by default, so every signed-in user can call this at "
                f"/rest/v1/rpc/{fn.name} with the owner's rights. Revoke it, or add it to `allowed` "
                f"with the reason it is safe to call as any user."
            )
        elif fn.granted and not is_allowed:
            problems.append(
                f"{fn.where}\n    SECURITY DEFINER, revoked and then GRANTed back to "
                f"{sorted(fn.granted)}. That is a deliberate decision, so state it: add "
                f"{fn.name!r} to `allowed` with the reason."
            )
    problems.extend(
        f"{fn.where}\n    {fn.label}(): SECURITY DEFINER with no `SET search_path` in "
        f"its header. An unqualified name inside the body resolves against the "
        f"CALLER's search_path, so a caller who can create objects in any schema "
        f"on it can shadow a table or function this body trusts."
        for fn in definers
        if not fn.search_path
    )
    ever_names = {name for _, name in replay.ever_defined} | {f"{s}.{n}" for s, n in replay.ever_defined}
    problems.extend(
        f"allowed[{name!r}] does not name any function defined under {migrations_dir} - a stale allowlist entry reads as coverage. Remove it."
        for name in sorted(set(allowed) - ever_names)
    )
    return problems


def assert_definer_functions_are_locked_down(
    migrations_dir: Path,
    allowed: "Mapping[str, str] | None" = None,
) -> None:
    """Fail when a ``SECURITY DEFINER`` function is callable by any signed-in user, or omits
    ``SET search_path``. See :func:`find_unlocked_definer_functions`."""
    import pytest

    problems = find_unlocked_definer_functions(migrations_dir, allowed)
    if problems:
        pytest.fail(f"{len(problems)} SECURITY DEFINER privilege problem(s) under {migrations_dir}:\n\n" + "\n\n".join(problems))
