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

from ._core import DEFAULT_EXCLUDE, ScanResult, parse_file, scan_python

__all__ = ["DEFAULT_STATEMENT_STARTS", "assert_verifier_covers_statements", "sql_constants", "verifier_lists"]

DEFAULT_STATEMENT_STARTS = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")


def _string_value(value: ast.expr | None) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr):
        return "".join(part.value for part in value.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
    return None


_LEADING_COMMENTS = re.compile(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.DOTALL)


def _starts_statement(text: str, starts: tuple[str, ...]) -> bool:
    body = _LEADING_COMMENTS.sub("", text, count=1).lstrip("(").lstrip()
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


def _constants(scan: ScanResult, starts: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    for parsed in scan:
        module = _module_name(parsed.rel)
        for node in parsed.tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            text = _string_value(node.value)
            if text is None or not _starts_statement(text, starts):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found.update(f"{module}.{t.id}" for t in targets if isinstance(t, ast.Name))
    return found


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
