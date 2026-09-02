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

Three rules, all last-definition-wins across the migration directory (a later ``CREATE OR REPLACE``
supersedes an earlier one, matching how a real ``supabase db push`` applies them):

1. Every ``SECURITY DEFINER`` function must have a ``REVOKE EXECUTE`` from ``PUBLIC`` (or from both
   ``anon`` and ``authenticated``) somewhere in the migrations, or sit in ``allowed`` with a reason.
2. Every ``SECURITY DEFINER`` function must ``SET search_path`` in its header.
3. Advisory: list the definer functions that are deliberately callable, so the reachable RPC
   surface is written down somewhere a reviewer can see it.

Deliberately regex/line-based, no SQL parser and no new dependency, matching this package's other
scanners. The same rule fires on any repo that owns Postgres migrations, whatever the app language.

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
from collections.abc import Mapping
from pathlib import Path

# `CREATE [OR REPLACE] FUNCTION [schema.]name(` -- the schema qualifier is optional because
# migrations write both `public.f(` and a bare `f(` under a `SET search_path` at file scope.
_CREATE_FUNCTION_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:(?P<schema>\w+)\.)?(?P<name>\w+)\s*\(",
    re.IGNORECASE,
)
_SECURITY_DEFINER_RE = re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE)
_SEARCH_PATH_RE = re.compile(r"\bSET\s+search_path\b", re.IGNORECASE)
# `REVOKE [ALL|EXECUTE] ON FUNCTION [schema.]name(...) FROM <roles>` -- the arg list is ignored, so
# one REVOKE covers an overload set the way a reviewer reads it.
_REVOKE_RE = re.compile(
    r"\bREVOKE\s+(?:ALL(?:\s+PRIVILEGES)?|EXECUTE)\s+ON\s+FUNCTION\s+(?:\w+\.)?(?P<name>\w+)\s*\((?P<args>[^)]*)\)\s*(?P<rest>.*?);",
    re.IGNORECASE | re.DOTALL,
)
_GRANT_RE = re.compile(
    r"\bGRANT\s+(?:ALL(?:\s+PRIVILEGES)?|EXECUTE)\s+ON\s+FUNCTION\s+(?:\w+\.)?(?P<name>\w+)\s*\([^)]*\)\s*TO\s+(?P<roles>.*?);",
    re.IGNORECASE | re.DOTALL,
)
# Roles that mean "anyone with a session" on a Supabase project.
_CALLER_ROLES = ("public", "anon", "authenticated")


def _function_bodies(sql: str) -> list[tuple[str, str]]:
    """Return ``(name, header_text)`` for every ``CREATE FUNCTION`` in ``sql``.

    ``header_text`` runs from the ``CREATE`` line to the body delimiter (``AS $$`` / ``AS $func$``)
    or, when the definition has no dollar-quoted body, to the end of the statement. Everything this
    check cares about (``SECURITY DEFINER``, ``SET search_path``) is by PostgreSQL's own grammar in
    that header, so the body -- which may legitimately contain the words in a comment or a string --
    is never scanned.
    """
    out: list[tuple[str, str]] = []
    lines = sql.splitlines()
    for i, line in enumerate(lines):
        m = _CREATE_FUNCTION_RE.match(line)
        if not m:
            continue
        header: list[str] = []
        for follow in lines[i:]:
            header.append(follow)
            if re.search(r"\bAS\s+\$[\w]*\$", follow, re.IGNORECASE):
                break
            if follow.rstrip().endswith(";") and len(header) > 1:
                break
        out.append((m.group("name"), "\n".join(header)))
    return out


def _revoked_from_callers(sql: str) -> set[str]:
    """Function names whose EXECUTE is revoked from PUBLIC, or from anon *and* authenticated."""
    revoked: dict[str, set[str]] = {}
    for m in _REVOKE_RE.finditer(sql):
        roles = m.group("rest").lower()
        got = {r for r in _CALLER_ROLES if re.search(rf"\b{r}\b", roles)}
        revoked.setdefault(m.group("name"), set()).update(got)
    return {name for name, roles in revoked.items() if "public" in roles or {"anon", "authenticated"} <= roles}


def _granted_to_callers(sql: str) -> dict[str, set[str]]:
    granted: dict[str, set[str]] = {}
    for m in _GRANT_RE.finditer(sql):
        roles = m.group("roles").lower()
        got = {r for r in _CALLER_ROLES if re.search(rf"\b{r}\b", roles)}
        if got:
            granted.setdefault(m.group("name"), set()).update(got)
    return granted


def find_unlocked_definer_functions(
    migrations_dir: Path,
    allowed: "Mapping[str, str] | None" = None,
) -> list[str]:
    """Return one problem string per ``SECURITY DEFINER`` function that is callable by any
    signed-in user, or that omits ``SET search_path``.

    ``allowed`` maps a function name to the REASON it is deliberately callable (a bare name with no
    reason is rejected -- an allowlist without reasons is how this class of finding survives an
    audit). A function that is explicitly ``GRANT``ed to a caller role after being revoked counts as
    deliberately callable and must therefore also be in ``allowed``.
    """
    allowed = dict(allowed or {})
    files = sorted(p for p in migrations_dir.rglob("*.sql") if p.is_file())
    if not files:
        return [f"{migrations_dir}: no .sql files found - this check examined nothing, which reads as " f"a pass. Point it at the migrations directory."]
    combined = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)

    # Last definition wins: a later CREATE OR REPLACE is what the database ends up running.
    definers: dict[str, str] = {}
    all_defined: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, header in _function_bodies(text):
            all_defined.add(name)
            if _SECURITY_DEFINER_RE.search(header):
                definers[name] = f"{path.name}: {header.splitlines()[0].strip()}"
            else:
                definers.pop(name, None)

    revoked = _revoked_from_callers(combined)
    granted = _granted_to_callers(combined)

    problems: list[str] = []
    for name in sorted(definers):
        where = definers[name]
        if name not in revoked:
            if name in allowed:
                continue
            problems.append(
                f"{where}\n    SECURITY DEFINER with no `REVOKE EXECUTE ... FROM PUBLIC` (or from "
                f"both anon and authenticated) anywhere in the migrations. PostgreSQL grants "
                f"EXECUTE to PUBLIC by default, so every signed-in user can call this at "
                f"/rest/v1/rpc/{name} with the owner's rights. Revoke it, or add it to `allowed` "
                f"with the reason it is safe to call as any user."
            )
        elif name in granted and name not in allowed:
            problems.append(
                f"{where}\n    SECURITY DEFINER, revoked and then GRANTed back to "
                f"{sorted(granted[name])}. That is a deliberate decision, so state it: add "
                f"{name!r} to `allowed` with the reason."
            )

    for name in sorted(definers):
        header = definers[name]
        # Re-read the header text for the search_path rule (definers stores a one-line summary).
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            headers = {n: h for n, h in _function_bodies(text)}
            if name in headers and _SECURITY_DEFINER_RE.search(headers[name]):
                if not _SEARCH_PATH_RE.search(headers[name]):
                    problems.append(
                        f"{path.name}: {name}()\n    SECURITY DEFINER with no `SET search_path` in "
                        f"its header. An unqualified name inside the body resolves against the "
                        f"CALLER's search_path, so a caller who can create objects in any schema "
                        f"on it can shadow a table or function this body trusts."
                    )
    for name in sorted(set(allowed) - all_defined):
        problems.append(
            f"allowed[{name!r}] does not name any function defined under {migrations_dir} - a " f"stale allowlist entry reads as coverage. Remove it."
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
