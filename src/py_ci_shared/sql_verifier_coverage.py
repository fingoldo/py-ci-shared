"""Every SQL statement a package defines is on its SQL verifier's list, and the list names nothing that has gone.

A verifier that PREPAREs statements against a real database cannot block a commit, so its report is only as good as
its list -- and the list drifts the quiet way: a query is added, the list is not, and the next run says "all
accepted" about a set that no longer includes it. production_scrapers and realtime_applications each carried this
check, line for line; this is it once.

It runs with no database. The verifier is PARSED, never imported: importing it would run a module body that sets a
credential placeholder and reaches for a server. Both directions fail -- a listed constant that no longer exists is
as invisible in a passing run as a constant nobody lists -- and every exclusion must carry a real reason.

A constant is SQL when, after any leading ``--``/``/* */`` comments, it starts with a statement keyword as a whole
word (``"Withdrawal failed"`` is prose, not ``WITH``). ``A = B = "SELECT ..."`` defines both names, and a constant
in ``pkg/__init__.py`` is ``pkg.NAME``. A file that cannot be read or parsed fails the check.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from ._core import DEFAULT_EXCLUDE, ScanResult, parse_file, scan_python

__all__ = [
    "DEFAULT_STATEMENT_STARTS",
    "StatementConstant",
    "assert_verifier_covers_statements",
    "sql_constants",
    "statement_constants",
    "verifier_lists",
]

DEFAULT_STATEMENT_STARTS = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "REFRESH")


def _string_value(value: ast.expr | None, names: dict[str, str] | None = None) -> str | None:
    """The text of a string constant: a literal, an f-string's literal parts, or a ``+`` chain of those and of module-level names bound to strings.

    A name the module does not bind to a string (an import, a call) contributes nothing, so a statement built from one is still SEEN by its leading
    literal and must be listed or excluded: reading it as "not a constant" let `_CTE + "SELECT ..."` escape every verifier list.
    """
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr):
        return "".join(part.value for part in value.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        left, right = _string_value(value.left, names), _string_value(value.right, names)
        return None if left is None and right is None else (left or "") + (right or "")
    if isinstance(value, ast.Name) and names is not None:
        return names.get(value.id)
    return None


_LEADING_COMMENTS = re.compile(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.DOTALL)


#: A session setting in front of the statement (`SET LOCAL TimeZone = 'UTC'; INSERT ...`): not what is being sent.
_SESSION_PREFIX = re.compile(r"^SET\s+(?:LOCAL\s+|SESSION\s+)?[^;]*;\s*", re.IGNORECASE)


def _starts_statement(text: str, starts: tuple[str, ...]) -> bool:
    body = _LEADING_COMMENTS.sub("", text, count=1).lstrip("(").lstrip()
    while _SESSION_PREFIX.match(body):
        body = _LEADING_COMMENTS.sub("", _SESSION_PREFIX.sub("", body, count=1), count=1).lstrip()
    head = re.match(r"[A-Za-z_]+", body)
    return head is not None and head.group(0).upper() in starts


def _module_name(relative: str) -> str:
    parts = relative[: -len(".py")].split("/") if relative.endswith(".py") else relative.split("/")
    if len(parts) > 1 and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _scan(root: Path, skip: set[str]) -> ScanResult:
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE)
    scan.files = [f for f in scan.files if f.rel.split("/")[0] not in skip]
    scan.unparsed = [p for p in scan.unparsed if p.rel.split("/")[0] not in skip]
    return scan


class StatementConstant(NamedTuple):
    """A module-level constant whose value begins like a SQL statement, with its text as far as it can be known."""

    name: str  # ``module.NAME``
    rel: str  # path of the defining file, relative to the scan root
    line: int  # line of the assignment (of the opening quote for a single literal)
    text: str  # the literal text; a part that is not a literal contributes nothing
    exact: bool  # False when the text is incomplete: an f-string field, a call, a name the module does not bind to a literal
    single: bool  # True when the value is one plain string literal, so a line inside it maps to a line of the file


def _literal(value: ast.expr | None, exact: dict[str, str]) -> str | None:
    """The whole text of *value* when it is knowable without running code, else ``None``.

    Plain literals, ``+`` chains, names in *exact* (module-level names bound to such text) and an f-string whose replacement fields are
    bare names in *exact*. A call, an attribute, a conversion or a format spec makes the text unknowable: `_string_value` would drop it
    silently, which is right for finding the constant and wrong for reading what it says.
    """
    if isinstance(value, ast.Constant):
        return value.value if isinstance(value.value, str) else None
    if isinstance(value, ast.JoinedStr):
        parts: list[str] = []
        for part in value.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            elif isinstance(part, ast.FormattedValue) and part.conversion == -1 and part.format_spec is None and isinstance(part.value, ast.Name):
                if part.value.id not in exact:
                    return None
                parts.append(exact[part.value.id])
            else:
                return None
        return "".join(parts)
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        left, right = _literal(value.left, exact), _literal(value.right, exact)
        return None if left is None or right is None else left + right
    if isinstance(value, ast.Name):
        return exact.get(value.id)
    return None


def _dynamic_head(value: ast.expr | None) -> str | None:
    """The literal a ``"...".format(...)`` call or ``"..." % args`` is made from; the finished statement is not knowable."""
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "format":
        value = value.func.value
    elif isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mod):
        value = value.left
    else:
        return None
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def statement_constants(scan: ScanResult, starts: tuple[str, ...], *, include_dynamic: bool = False) -> list[StatementConstant]:
    """Every module-level constant of the scanned files whose value begins like a statement, in file and source order.

    With *include_dynamic*, a ``"...".format(...)`` or ``"..." % args`` value is listed too, as an inexact constant of its literal head.
    """
    found: list[StatementConstant] = []
    for parsed in scan:
        module = _module_name(parsed.rel)
        bound: dict[str, str] = {}  # module-level names bound to strings so far, in source order: what a `+` chain may name
        known: dict[str, str] = {}  # the subset whose text is complete
        for node in parsed.tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            text = _string_value(node.value, bound)
            if text is None and include_dynamic:
                text = _dynamic_head(node.value)
            if text is None:
                continue
            whole = _literal(node.value, known)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            bound.update({n: text for n in names})
            for n in names:
                known.pop(n, None)
                if whole is not None:
                    known[n] = whole
            if _starts_statement(text, starts):
                single = isinstance(node.value, ast.Constant)
                shown = whole if whole is not None else text
                found.extend(StatementConstant(f"{module}.{n}", parsed.rel, node.lineno, shown, whole is not None, single) for n in names)
    return found


def _constants(scan: ScanResult, starts: tuple[str, ...]) -> set[str]:
    return {c.name for c in statement_constants(scan, starts)}


def sql_constants(
    root: Path,
    *,
    exclude_top_dirs: Iterable[str],
    statement_starts: Iterable[str] = DEFAULT_STATEMENT_STARTS,
    allow_unparsed: bool = False,
) -> set[str]:
    """``{"module.NAME", ...}`` for every module-level constant under *root* whose value begins like a statement.

    A file that cannot be read or parsed raises ``UnparsedFilesError`` (an ``AssertionError``) unless *allow_unparsed*.
    """
    scan = _scan(Path(root), set(exclude_top_dirs))
    if not allow_unparsed:
        scan.check_unparsed()
    return _constants(scan, tuple(s.upper() for s in statement_starts))


def verifier_lists(verifier: Path, *, listed_name: str = "STATEMENTS", excluded_name: str = "EXCLUDED") -> tuple[set[str], dict[str, str]]:
    """``(listed, {excluded: reason})`` parsed from *verifier*.

    ``STATEMENTS`` is a sequence of ``(module, (NAME, ...))`` pairs; ``EXCLUDED`` a ``{"module.NAME": reason}`` dict.
    """
    tree = parse_file(verifier)
    listed: set[str] = set()
    excluded: dict[str, str] = {}
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        name = getattr(target, "id", None)
        value = getattr(node, "value", None)
        if name == listed_name and isinstance(value, (ast.List, ast.Tuple)):
            for entry in value.elts:
                if isinstance(entry, (ast.Tuple, ast.List)) and len(entry.elts) == 2:
                    module = _string_value(entry.elts[0])
                    names = entry.elts[1].elts if isinstance(entry.elts[1], (ast.Tuple, ast.List)) else []
                    listed.update(f"{module}.{_string_value(n)}" for n in names if _string_value(n))
        elif name == excluded_name and isinstance(value, ast.Dict):
            for key, reason in zip(value.keys, value.values):
                if key is not None and _string_value(key):
                    excluded[_string_value(key) or ""] = _string_value(reason) or ast.unparse(reason)
    return listed, excluded


def assert_verifier_covers_statements(
    root: Path,
    verifier: Path,
    *,
    exclude_top_dirs: Iterable[str],
    min_constants: int = 1,
    min_reason_len: int = 40,
    statement_starts: Iterable[str] = DEFAULT_STATEMENT_STARTS,
) -> None:
    """Fail when a SQL constant is neither listed nor excluded, a listed or excluded name is gone, or a reason is empty."""
    import pytest

    scan = _scan(Path(root), set(exclude_top_dirs))
    found = _constants(scan, tuple(s.upper() for s in statement_starts))
    if len(found) < min_constants:
        pytest.fail(f"only {len(found)} SQL constant(s) found under {root}; expected at least {min_constants} -- Check the scan, it has lost its subject")
    listed, excluded = verifier_lists(verifier)
    problems: list[str] = []
    if scan.unparsed:
        problems.append("files that could not be read or parsed, so their SQL constants are unknown:\n    " + "\n    ".join(p.render() for p in scan.unparsed))
    missing = sorted(found - listed - set(excluded))
    if missing:
        problems.append(f"not checked by {verifier.name}; Add each to STATEMENTS, or to EXCLUDED with a reason:\n    " + "\n    ".join(missing))
    stale = sorted((listed | set(excluded)) - found)
    if stale:
        problems.append(f"{verifier.name} names constants the package no longer defines; Remove or rename them:\n    " + "\n    ".join(stale))
    thin = sorted(name for name, reason in excluded.items() if len(reason.strip()) < min_reason_len)
    if thin:
        problems.append(f"EXCLUDED entries without a real reason (under {min_reason_len} characters); Document why:\n    " + "\n    ".join(thin))
    if problems:
        pytest.fail("\n".join(problems))
