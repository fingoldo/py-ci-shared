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
import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional

from ._core import ImportAliases, scan_python
from ._core.node_index import walk as _fast_walk

__all__ = ["ACTIONABLE_RE", "assert_fail_messages_actionable", "fail_message_problems"]

_VERBS = (
    "Add|Either|Refresh|Whitelist|Fix|Run|Update|Remove|Delete|Document|Check|See|Replace|Carve|Regenerate|Rename|Move|Improve|Drain|Lower|Call|Use|Set|Install"
)

#: A fix verb, or a ``<placeholder>`` -- the actionable part of a templated message. A bare colon or path is not.
#: Case matters: a capitalised verb reads as an instruction anywhere, a lower-case one only where a sentence or
#: clause starts (``...; run X``, ``-- update the baseline``). Otherwise nouns (``the set of``, ``see also``, ``use``)
#: and ``or`` in ``wrong or missing`` counted as instructions.
ACTIONABLE_RE = re.compile(r"<[^>]+>|\b(?:" + _VERBS + r")\b|(?:^|[.;:!?]\s+|\s--?\s+|\n\s*)(?:" + _VERBS.lower() + r")\b")

_FAIL_TARGETS = frozenset({"pytest.fail", "_pytest.outcomes.fail"})
_MESSAGE_KWARGS = ("reason", "msg")


def _message_node(node: ast.Call) -> Optional[ast.expr]:
    if node.args:
        return node.args[0]
    for kw in node.keywords:
        if kw.arg in _MESSAGE_KWARGS:
            return kw.value
    return None


def _fail_calls(tree: ast.AST) -> Iterator[tuple[int, str, bool]]:
    """``(line, static text, has a runtime part)`` for every ``pytest.fail(...)`` call, however ``fail`` was imported
    (``from pytest import fail``, ``import pytest as pt``) and whether the message is positional or ``reason=``."""
    aliases = ImportAliases.from_tree(tree)
    for node in _fast_walk(tree):
        if not (isinstance(node, ast.Call) and aliases.qualified_name(node) in _FAIL_TARGETS):
            continue
        first = _message_node(node)
        if first is None:
            yield node.lineno, "", False
            continue
        dynamic = not isinstance(first, ast.Constant)
        chunks: list[str] = []
        for sub in _fast_walk(first):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                chunks.append(sub.value)
            elif isinstance(sub, ast.FormattedValue) or (sub is not first and isinstance(sub, (ast.Name, ast.Attribute, ast.Subscript, ast.Call))):
                dynamic = True
        yield node.lineno, " ".join(chunks), dynamic


def _common_root(files: list[Path]) -> Optional[Path]:
    if not files:
        return None
    try:
        return Path(os.path.commonpath([str(p.resolve().parent) for p in files]))
    except ValueError:  # different drives
        return None


def fail_message_problems(files: Iterable[Path], *, pattern: re.Pattern[str] = ACTIONABLE_RE, root: Optional[Path] = None) -> tuple[int, list[str]]:
    """``(calls audited, ["path:line -> text", ...])`` for static messages the pattern does not accept.

    Paths are relative to *root* (default: the files' common directory). A file that cannot be parsed is itself a
    problem, never skipped.
    """
    paths = [Path(p) for p in files]
    base = root if root is not None else _common_root(paths)
    scan = scan_python([p.resolve() for p in paths], root=base.resolve() if base is not None else None)
    audited = 0
    bad: list[str] = [f"{u.rel}:{u.line} ({u.kind}: {u.message})" for u in scan.unparsed]
    for parsed in scan:
        for line, text, dynamic in _fail_calls(parsed.tree):
            audited += 1
            if dynamic:
                continue
            if not text:
                bad.append(f"{parsed.rel}:{line} (empty message)")
            elif not pattern.search(text):
                bad.append(f"{parsed.rel}:{line} -> {text[:80]!r}")
    return audited, bad


def assert_fail_messages_actionable(
    meta_dir: Path,
    *,
    pattern: re.Pattern[str] = ACTIONABLE_RE,
    exclude: Iterable[str] = (),
    min_audited: int = 1,
) -> None:
    """Fail on a ``pytest.fail`` message under *meta_dir* with no fix verb and no placeholder, or a file that cannot
    be parsed."""
    import pytest

    skip = set(exclude)
    files = sorted(p for p in meta_dir.glob("test_*.py") if p.name not in skip)
    audited, bad = fail_message_problems(files, pattern=pattern, root=meta_dir)
    if audited < min_audited:
        pytest.fail(f"only {audited} pytest.fail call(s) found under {meta_dir}; Check the collector -- it has stopped finding them")
    if bad:
        pytest.fail("pytest.fail message(s) that do not say what to do. Improve each with a fix verb or a <placeholder>:\n  " + "\n  ".join(bad[:30]))
