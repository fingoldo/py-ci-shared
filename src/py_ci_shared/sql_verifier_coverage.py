"""Every SQL statement a package defines is on its SQL verifier's list, and the list names nothing that has gone.

A verifier that PREPAREs statements against a real database cannot block a commit, so its report is only as good as
its list -- and the list drifts the quiet way: a query is added, the list is not, and the next run says "all
accepted" about a set that no longer includes it. production_scrapers and realtime_applications each carried this
check, line for line; this is it once.

It runs with no database. The verifier is PARSED, never imported: importing it would run a module body that sets a
credential placeholder and reaches for a server. Both directions fail -- a listed constant that no longer exists is
as invisible in a passing run as a constant nobody lists -- and every exclusion must carry a real reason.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

__all__ = ["DEFAULT_STATEMENT_STARTS", "assert_verifier_covers_statements", "sql_constants", "verifier_lists"]

DEFAULT_STATEMENT_STARTS = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")


def _string_value(value: ast.expr | None) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr):
        return "".join(part.value for part in value.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
    return None


def sql_constants(root: Path, *, exclude_top_dirs: Iterable[str], statement_starts: Iterable[str] = DEFAULT_STATEMENT_STARTS) -> set[str]:
    """``{"module.NAME", ...}`` for every module-level constant under *root* whose value begins like a statement."""
    skip = set(exclude_top_dirs)
    starts = tuple(s.upper() for s in statement_starts)
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] in skip or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = relative[:-3].replace("/", ".")
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            text = _string_value(node.value)
            if text is None or not text.lstrip().upper().startswith(starts):
                continue
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name):
                found.add(f"{module}.{target.id}")
    return found


def verifier_lists(verifier: Path, *, listed_name: str = "STATEMENTS", excluded_name: str = "EXCLUDED") -> tuple[set[str], dict[str, str]]:
    """``(listed, {excluded: reason})`` parsed from *verifier*.

    ``STATEMENTS`` is a sequence of ``(module, (NAME, ...))`` pairs; ``EXCLUDED`` a ``{"module.NAME": reason}`` dict.
    """
    tree = ast.parse(verifier.read_text(encoding="utf-8"))
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

    found = sql_constants(root, exclude_top_dirs=exclude_top_dirs, statement_starts=statement_starts)
    if len(found) < min_constants:
        pytest.fail(f"only {len(found)} SQL constant(s) found under {root}; expected at least {min_constants} -- Check the scan, it has lost its subject")
    listed, excluded = verifier_lists(verifier)
    problems: list[str] = []
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
