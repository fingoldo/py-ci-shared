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

from ._core import DEFAULT_EXCLUDE, CorpusError, SourceReadError, iter_files, read_source, relative_posix

_CATCH_BLOCK_RE = re.compile(r"\bcatch\s*\([^)]*\)\s*\{")
_STATUS_RE = re.compile(r"status:\s*(\d{3})")
# `Response.json(body)` answers 200 unless it is given a status, exactly like `new Response(body)`.
_NEW_RESPONSE_RE = re.compile(r"new\s+Response\s*\(|\bResponse\.json\s*\(")
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
_LOG_CALL_RE = re.compile(r"console\.(?:log|info|warn|error|debug)\s*\(")
_IP_WORD_RE = re.compile(r"\bip\b", re.IGNORECASE)
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


# Comparing a secret with an empty value is a presence check, not a secret comparison: there is no prefix to time.
_EMPTY_RIGHT_RE = re.compile(r"""\s*\(*\s*(?:""|''|``|null|undefined)(?![\w$])""")
_EMPTY_LEFT_RE = re.compile(r"""(?<![\w$])(?:""|''|``|null|undefined)\s*\)*\s*$""")
_EQUALITY_OP_RE = re.compile(r"===|!==|==|!=")


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _skip_quoted(src: str, i: int) -> int:
    """Index just past the '...' or "..." literal opening at *i*."""
    quote = src[i]
    i += 1
    while i < len(src):
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == quote or src[i] == "\n":
            return i + 1
        i += 1
    return i


def _scan_template(src: str, i: int) -> tuple[int, list[str]]:
    """*i* is just past an opening backtick: ``(index past the closing backtick, code of every ${...})``."""
    units: list[str] = []
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1, units
        if src.startswith("${", i):
            i, inner = _scan_code(src, i + 2, "}")
            units.extend(inner)
            continue
        i += 1
    return i, units


def _scan_code(src: str, i: int, closer: str) -> tuple[int, list[str]]:
    """Code from *i* up to the unmatched *closer*, split at top-level commas, with string literal TEXT removed.

    Template interpolations become units of their own, so ``${redactIp(ip)}`` and a bare ``ip`` argument are judged
    separately, and a ``)`` inside a string or a nested call no longer ends the argument list early.
    """
    units: list[str] = []
    cur: list[str] = []
    depth = 0
    while i < len(src):
        c = src[i]
        if c in "'\"":
            i = _skip_quoted(src, i)
            cur.append(" ")
            continue
        if c == "`":
            i, inner = _scan_template(src, i + 1)
            units.extend(inner)
            cur.append(" ")
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0 and c == closer:
                units.append("".join(cur))
                return i + 1, units
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            units.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    units.append("".join(cur))
    return i, units


def _logs_raw_ip(source: str, open_paren: int) -> bool:
    _, units = _scan_code(source, open_paren + 1, ")")
    return any(_IP_WORD_RE.search(u) and not _REDACTION_RE.search(u) for u in units)


def _is_presence_check(comparison: str) -> bool:
    parts = _EQUALITY_OP_RE.split(comparison, maxsplit=1)
    if len(parts) != 2:
        return False
    left, right = parts
    return _EMPTY_RIGHT_RE.match(right) is not None or _EMPTY_LEFT_RE.search(left) is not None


def _function_name(path: Path, functions_dir: Path) -> str:
    """The deployed function a file belongs to: the FIRST directory under *functions_dir*, however deep the file."""
    parts = Path(relative_posix(path, functions_dir)).parts
    return parts[0] if len(parts) > 1 else path.stem


def find_edge_function_problems(
    functions_dir: Path,
    *,
    public_functions: Iterable[str] = (),
) -> list[str]:
    """Return one problem string per edge-function hygiene violation.

    ``public_functions`` names the directories deployed with JWT verification off; those get the
    stricter array-cap rule, because anyone on the internet can call them. The body-cap rules are judged per
    deployed function (every file under its directory), so a cap in a helper module counts and a helper in a
    nested directory is still part of its function. A missing *functions_dir* is a problem, not a clean pass.
    """
    functions_dir = Path(functions_dir)
    public = set(public_functions)
    problems: list[str] = []
    try:
        files = [p for p in iter_files(functions_dir, ("*.ts",), exclude=DEFAULT_EXCLUDE) if not p.name.endswith(".test.ts")]
    except CorpusError as exc:
        return [f"{functions_dir}: {exc} - this check examined nothing."]
    if not files:
        return [f"{functions_dir}: no function sources found - this check examined nothing."]

    groups: dict[str, list[tuple[str, str]]] = {}
    for path in files:
        rel = relative_posix(path, functions_dir)
        try:
            source = read_source(path)
        except SourceReadError as exc:
            problems.append(f"{rel}: unreadable, so it was not checked: {exc.message}")
            continue
        groups.setdefault(_function_name(path, functions_dir), []).append((rel, source))

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

        for m in _COMPARISON_RE.finditer(source):
            if not _SECRET_TOKEN_RE.search(m.group(0)) or _is_presence_check(m.group(0)):
                continue
            window = source[max(0, m.start() - 200) : m.end() + 200]
            if not _DIGEST_RE.search(window):
                problems.append(
                    f"{rel}:{_line_of(source, m.start())}: a secret compared with string equality "
                    f"- comparison time varies with the shared prefix. Compare digests."
                )

        problems.extend(
            f"{rel}:{_line_of(source, m.start())}: logs a raw IP address - the platform's log retention becomes undeclared personal-data retention."
            for m in _LOG_CALL_RE.finditer(source)
            if _logs_raw_ip(source, m.end() - 1)
        )

        problems.extend(
            f"{rel}:{_line_of(source, m.start())}: takes the FIRST x-forwarded-for hop, which "
            f"is the value the client sent and anyone can forge. Take the last."
            for m in _XFF_FIRST_HOP_RE.finditer(source)
        )

    for name, members in groups.items():
        combined = "\n".join(src for _, src in members)
        if not (_REQ_JSON_RE.search(combined) and _INSERT_RE.search(combined)):
            continue
        rel = next((r for r, src in members if _REQ_JSON_RE.search(src)), members[0][0])
        if not _CAP_RE.search(combined):
            problems.append(f"{rel}: reads the request body and inserts it with no size cap - whatever a " f"caller sends lands in the column.")
        inserts_an_array = _ARRAY_INSERT_RE.search(combined) is not None
        if name in public and inserts_an_array and not _ARRAY_CAP_RE.search(combined):
            problems.append(
                f"{rel}: deployed without JWT verification and has no array-length cap - one "
                f"request from anyone on the internet can carry an unbounded batch."
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
