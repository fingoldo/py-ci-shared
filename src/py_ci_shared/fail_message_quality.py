"""Every ``pytest.fail`` message in a meta-test directory tells the reviewer what to do.

A meta-test that fails with "baseline mismatch: 3 entries differ" sends the reviewer into its source to find
out what the fix is. Five repositories carried this check, and four of them accepted any message containing
``:``, ``/``, ``<`` or ``>`` -- which every static message does, so the check passed on all of them without
reading a word. production_scrapers found that (audit 2026-09-05 TEST-8) and tightened it to a fix verb or a
``<placeholder>``; this is that version, once.

Messages built at runtime (an f-string, a variable) are counted but not judged: their text is not in the source.
A directory in which no ``pytest.fail`` call was found fails too -- the collector broke, the suite did not
become perfect.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

__all__ = ["ACTIONABLE_RE", "assert_fail_messages_actionable", "fail_message_problems"]

#: A fix verb, or a ``<placeholder>`` -- the actionable part of a templated message. A bare colon or path is not.
ACTIONABLE_RE = re.compile(
    r"<[^>]+>|\b(Add|Either|Refresh|Whitelist|Fix|Run|Update|Remove|Delete|Document|Check|See|Replace|Carve|Regenerate|Rename|Move|Improve|Drain|Lower|Call|Use|Set|Install|OR)\b",
    re.IGNORECASE,
)


def _fail_calls(tree: ast.AST) -> Iterator[tuple[int, str, bool]]:
    """``(line, static text, has a runtime part)`` for every ``pytest.fail(...)`` call."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "fail"):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest") or not node.args:
            continue
        first = node.args[0]
        dynamic = not isinstance(first, ast.Constant)
        chunks: list[str] = []
        for sub in ast.walk(first):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                chunks.append(sub.value)
            elif isinstance(sub, ast.FormattedValue) or (sub is not first and isinstance(sub, (ast.Name, ast.Attribute, ast.Subscript, ast.Call))):
                dynamic = True
        yield node.lineno, " ".join(chunks), dynamic


def fail_message_problems(files: Iterable[Path], *, pattern: re.Pattern[str] = ACTIONABLE_RE) -> tuple[int, list[str]]:
    """``(calls audited, ["file:line -> text", ...])`` for static messages the pattern does not accept."""
    audited = 0
    bad: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for line, text, dynamic in _fail_calls(tree):
            audited += 1
            if dynamic:
                continue
            if not text:
                bad.append(f"{path.name}:{line} (empty message)")
            elif not pattern.search(text):
                bad.append(f"{path.name}:{line} -> {text[:80]!r}")
    return audited, bad


def assert_fail_messages_actionable(
    meta_dir: Path,
    *,
    pattern: re.Pattern[str] = ACTIONABLE_RE,
    exclude: Iterable[str] = (),
    min_audited: int = 1,
) -> None:
    """Fail on a ``pytest.fail`` message under *meta_dir* with no fix verb and no placeholder."""
    import pytest

    skip = set(exclude)
    files = sorted(p for p in meta_dir.glob("test_*.py") if p.name not in skip)
    audited, bad = fail_message_problems(files, pattern=pattern)
    if audited < min_audited:
        pytest.fail(f"only {audited} pytest.fail call(s) found under {meta_dir}; Check the collector -- it has stopped finding them")
    if bad:
        pytest.fail("pytest.fail message(s) that do not say what to do. Improve each with a fix verb or a <placeholder>:\n  " + "\n  ".join(bad[:30]))
