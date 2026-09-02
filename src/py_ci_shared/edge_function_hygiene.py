"""Shared check: a serverless edge function is not a hole in the product's own back end.

An edge function is the least-reviewed code in most products: it lives outside the app's test suite,
outside its type checker's project, and it is deployed by a separate command. Every rule here is
one real finding from the 2026-09-02 glossum round.

1. **A catch that answers 200.** The outer handler swallowed a failure and returned success, so the
   client believed a login had been recorded and the row was never written (P01-13).
2. **An unbounded body.** A function that reads ``await req.json()`` and inserts it must cap what it
   accepts, and one deployed with ``verify_jwt = false`` must also cap array length: the CSP-report
   endpoint accepted an unbounded report array from anyone on the internet (P03-5), and log-login
   accepted a multi-megabyte user agent straight into a TEXT column (P03-8).
3. **A secret compared with ``===``.** String equality on a shared secret is timing-variable; use a
   digest comparison (P03-11).
4. **An IP in a log line.** ``console.log`` of a raw address turns the platform's log retention into
   undeclared personal-data retention (P03-12).
5. **``x-forwarded-for[0]``.** The FIRST hop is the value the client sent, which anyone can forge;
   the LAST hop is the one the proxy appended (P03-13).

Deliberately regex over TypeScript source: these are shapes, not semantics, and a real TS parser
would be a dependency for five rules. Reusable by any project with a ``supabase/functions`` or
``netlify/functions`` directory.

Usage::

    from py_ci_shared.edge_function_hygiene import assert_edge_functions_are_sound

    def test_edge_functions():
        assert_edge_functions_are_sound(REPO / "supabase" / "functions")
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_CATCH_BLOCK_RE = re.compile(r"\bcatch\s*\([^)]*\)\s*\{")
_STATUS_RE = re.compile(r"status:\s*(\d{3})")
_NEW_RESPONSE_RE = re.compile(r"new\s+Response\s*\(")
_REQ_JSON_RE = re.compile(r"await\s+req\.json\s*\(\)")
_INSERT_RE = re.compile(r"\.insert\s*\(|\.upsert\s*\(")
_CAP_RE = re.compile(r"content-length|MAX_[A-Z_]+|\.slice\s*\(|\.length\s*[<>]", re.IGNORECASE)
_ARRAY_CAP_RE = re.compile(r"\.length\s*>\s*\d+|\.slice\s*\(\s*0\s*,\s*\d+")
# A function that inserts ONE row from a parsed body has no batch to cap; the rule is about a
# request that can carry many. `insert(rows)` where rows came from an array is the shape.
_ARRAY_INSERT_RE = re.compile(r"Array\.isArray|\.map\s*\(|\.insert\s*\(\s*\w*[Rr]ows")
# The secret can sit on either side of the operator, so match the comparison and inspect both
# operands: `key === env(SECRET)` and `env(SECRET) === key` are the same bug.
_COMPARISON_RE = re.compile(r"[^=!<>\n]{0,120}(?:===|!==|==|!=)[^=\n]{0,120}")
_SECRET_TOKEN_RE = re.compile(r"SERVICE_ROLE_KEY|_SECRET\b|_TOKEN\b|\bsecret\b|\bapiKey\b|signed_request", re.IGNORECASE)
_DIGEST_RE = re.compile(r"digest|timingSafe|createHash|subtle\.", re.IGNORECASE)
# An interpolation that runs the address through a redaction helper is the FIX for this
# finding, so matching it again would make the rule impossible to satisfy.
_REDACTION_RE = re.compile(r"redact|mask|truncate|anonymi[sz]e|hash", re.IGNORECASE)
_LOG_IP_RE = re.compile(r"console\.(?:log|info|warn|error)\s*\([^)]*?(\$\{[^}]*\bip\b[^}]*\})", re.IGNORECASE)
_XFF_FIRST_HOP_RE = re.compile(r"""x-forwarded-for["']?\s*\)[^;\n]*?\.split\([^)]*\)\s*\[\s*0\s*\]""", re.IGNORECASE)


def _balanced(source: str, open_index: int, opener: str = "{", closer: str = "}") -> str:
    depth = 0
    i = open_index
    while i < len(source):
        if source[i] == opener:
            depth += 1
        elif source[i] == closer:
            depth -= 1
            if depth == 0:
                return source[open_index + 1 : i]
        i += 1
    return source[open_index + 1 :]


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def find_edge_function_problems(
    functions_dir: Path,
    *,
    public_functions: Iterable[str] = (),
) -> list[str]:
    """Return one problem string per edge-function hygiene violation.

    ``public_functions`` names the directories deployed with JWT verification off; those get the
    stricter array-cap rule, because anyone on the internet can call them.
    """
    if not functions_dir.is_dir():
        return []
    public = set(public_functions)
    problems: list[str] = []
    files = sorted(p for p in functions_dir.rglob("*.ts") if not p.name.endswith(".test.ts"))
    if not files:
        return [f"{functions_dir}: no function sources found - this check examined nothing."]

    for path in files:
        source = path.read_text(encoding="utf-8", errors="replace")
        name = path.parent.name
        rel = f"{name}/{path.name}"

        for m in _CATCH_BLOCK_RE.finditer(source):
            body = _balanced(source, source.find("{", m.end() - 1))
            if not _NEW_RESPONSE_RE.search(body):
                continue
            statuses = _STATUS_RE.findall(body)
            if not statuses or any(s.startswith("2") for s in statuses):
                problems.append(
                    f"{rel}:{_line_of(source, m.start())}: a catch block answers "
                    f"{statuses or ['(no status, so 200)']} - the client is told the write "
                    f"succeeded when it failed."
                )

        if _REQ_JSON_RE.search(source) and _INSERT_RE.search(source):
            if not _CAP_RE.search(source):
                problems.append(f"{rel}: reads the request body and inserts it with no size cap - whatever a " f"caller sends lands in the column.")
            inserts_an_array = _ARRAY_INSERT_RE.search(source) is not None
            if name in public and inserts_an_array and not _ARRAY_CAP_RE.search(source):
                problems.append(
                    f"{rel}: deployed without JWT verification and has no array-length cap - one "
                    f"request from anyone on the internet can carry an unbounded batch."
                )

        for m in _COMPARISON_RE.finditer(source):
            if not _SECRET_TOKEN_RE.search(m.group(0)):
                continue
            window = source[max(0, m.start() - 200) : m.end() + 200]
            if not _DIGEST_RE.search(window):
                problems.append(
                    f"{rel}:{_line_of(source, m.start())}: a secret compared with string equality "
                    f"- comparison time varies with the shared prefix. Compare digests."
                )

        for m in _LOG_IP_RE.finditer(source):
            if _REDACTION_RE.search(m.group(1)):
                continue
            problems.append(
                f"{rel}:{_line_of(source, m.start())}: logs a raw IP address - the platform's log " f"retention becomes undeclared personal-data retention."
            )

        for m in _XFF_FIRST_HOP_RE.finditer(source):
            problems.append(
                f"{rel}:{_line_of(source, m.start())}: takes the FIRST x-forwarded-for hop, which "
                f"is the value the client sent and anyone can forge. Take the last."
            )
    return problems


def assert_edge_functions_are_sound(
    functions_dir: Path,
    *,
    public_functions: Iterable[str] = (),
) -> None:
    """Fail on any edge-function hygiene violation."""
    import pytest

    problems = find_edge_function_problems(functions_dir, public_functions=public_functions)
    if problems:
        pytest.fail(f"{len(problems)} edge-function problem(s):\n  " + "\n  ".join(problems))
