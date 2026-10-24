"""Count the entries of every deferred-debt collection in a meta-test directory.

A meta-test that tolerates known debt keeps it in a named literal (``_USER_DEFERRED_*``, ``_GRANDFATHERED_*``); the
counts this module returns are what :mod:`py_ci_shared.deferred_drift` holds to a shrink-only baseline. The counter
was first written in pyutilz's ``dev.meta_test_utils``, which py-ci-shared then imported back, so the two packages
depended on each other. The directory walk excludes :data:`py_ci_shared._core.corpus.DEFAULT_EXCLUDE`, the one
exclusion set every py-ci-shared scanner uses, so a worktree or a virtualenv under the root is never counted as a
second copy of the tests.
"""

from __future__ import annotations

import ast
import logging
import typing
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from ._core.corpus import DEFAULT_EXCLUDE
from ._core.node_index import walk
from ._core.source import parse_text

logger = logging.getLogger(__name__)

_DEFERRED_PREFIXES = ("_USER_DEFERRED", "_GRANDFATHERED")


def _rglob_test_files(root: Path) -> "typing.Iterator[Path]":
    """``root.rglob("test_*.py")``, skipping the excluded directories, most pointedly ``.claude``.

    ``.claude`` holds Claude Code's agent worktrees, each a COMPLETE checkout of the repo being scanned. Without the
    exclusion, a caller that passes the repo root walks every worktree's own test suite too, and the counts multiply.
    """
    for p in root.rglob("test_*.py"):
        if not any(part in DEFAULT_EXCLUDE for part in p.relative_to(root).parts[:-1]):
            yield p


def count_user_deferred_entries(
    test_meta_dir: Path,
    *,
    extra_prefixes: Iterable[str] = (),
) -> dict[str, int]:
    """Count entries in every ``_USER_DEFERRED_*`` (or other listed
    prefix) set/dict literal across every meta-test file in
    ``test_meta_dir``.

    The drift tracker compares this dict to a stored baseline; growth
    means tech debt is accumulating without anyone noticing.

    Returns
    -------
    dict[str, int]
        ``{f"<test_file_stem>::<var_name>": count}`` — e.g.
        ``{"test_dead_helpers::_USER_DEFERRED_DEAD_HELPERS": 9}``.
    """
    prefixes = tuple(_DEFERRED_PREFIXES) + tuple(extra_prefixes)
    out: dict[str, int] = {}
    if not test_meta_dir.exists():
        return out
    unreadable: list[str] = []
    for py in sorted(_rglob_test_files(test_meta_dir)):
        error = _count_deferred_in_file(py, prefixes, out)
        if error is not None:
            unreadable.append(error)
    if unreadable:
        logger.warning("%d file(s) could not be read and were not scanned: %s", len(unreadable), "; ".join(unreadable))
    return out


def _count_deferred_in_file(py: Path, prefixes: tuple[str, ...], out: dict[str, int]) -> Optional[str]:
    """Add one file's deferred-entry counts to ``out``; return a read-error message when the file is unreadable."""
    try:
        src = py.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("_count_deferred_in_file: file unreadable, reported as an error string: %s: %s", type(exc).__name__, exc)
        return f"{py}: {exc}"
    try:
        tree = parse_text(src, str(py))
    except SyntaxError as exc:
        logger.warning("_count_deferred_in_file: file does not parse, reported as unscanned: %s: %s", type(exc).__name__, exc)
        return f"{py}: does not parse ({type(exc).__name__}: {exc})"
    _count_deferred_in_tree(tree, py.stem, prefixes, out)
    return None


def _count_deferred_in_tree(tree: ast.AST, stem: str, prefixes: tuple[str, ...], out: dict[str, int]) -> None:
    """Record ``{stem::NAME: entries}`` for every prefix-matching assignment in ``tree`` with a countable literal value."""
    for node in walk(tree):
        # Both ``X = {...}`` (Assign) and ``X: set[str] = {...}``
        # (AnnAssign) need to be matched — modern code typically
        # uses the annotated form for whitelists.
        value: Optional[ast.expr]
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target] if node.target else []
            value = node.value
        else:
            continue
        if value is None:
            continue
        for target in targets:
            if not isinstance(target, ast.Name) or not target.id.startswith(prefixes):
                continue
            count = _literal_entry_count(value)
            if count is not None:
                out[f"{stem}::{target.id}"] = count


def _literal_entry_count(value: ast.expr) -> Optional[int]:
    """Number of entries in a set/list/tuple/dict literal, 0 for an empty ``set()``/``dict()`` call, else None."""
    if isinstance(value, (ast.Set, ast.List, ast.Tuple)):
        return len(value.elts)
    if isinstance(value, ast.Dict):
        return len(value.keys)
    if isinstance(value, ast.Call) and not value.args and not value.keywords:
        # An EMPTY ``set()`` / ``dict()`` constructor only. ``set(_LEGACY_ENTRIES)`` is
        # also an ast.Call, and recording it as 0 made 40 tracked entries read as none --
        # the drift tracker reported shrinking debt in exactly the case where it grew.
        return 0
    return None
