"""The debt-marker scan the ``todo_hygiene`` detector runs: TODO, FIXME, XXX and HACK comment lines, and whether one carries an
attribution (an assignee, a date or an at-mention).

Duplicates ``py_ci_shared.meta_test_utils.scan_todo_markers`` (moved there from pyutilz) so the detector does not import the
meta-test helpers. The two copies must stay equal until one is replaced by an import of the other.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path

from .._core.corpus import iter_files_pruned

logger = logging.getLogger(__name__)

# Path fragments excluded from the production scan by default (the same set meta_test_utils uses).
_DEFAULT_EXCLUDED_FRAGMENTS = ("test", "tests", "__pycache__")

MARKER_LINE_RE = re.compile(
    r"#\s*(?P<kw>TODO|FIXME|XXX|HACK)\b(?P<rest>.*)$",
    re.IGNORECASE,
)

# An attribution is one of:
#   * (assignee) — anything in parens immediately after the keyword
#   * 2026-04-28 — ISO date anywhere in the comment
#   * @assignee  — at-mention anywhere in the comment
ATTRIBUTION_RE = re.compile(r"(\([^)]+\)|\d{4}-\d{2}-\d{2}|@\w+)")


def scan_todo_markers(
    package_dir: Path,
    *,
    extra_excludes: Iterable[str] = (),
) -> list[tuple[Path, int, str, str]]:
    """Every debt-marker comment line under *package_dir*, as ``(path, lineno, marker_kw_uppercase, full_line_stripped)``."""
    excluded_fragments = set(_DEFAULT_EXCLUDED_FRAGMENTS) | set(extra_excludes)
    out: list[tuple[Path, int, str, str]] = []
    unreadable: list[str] = []
    for py in iter_files_pruned(package_dir, (".py",), excluded_fragments | {".claude"}):
        if any(frag in py.parts for frag in excluded_fragments):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append(f"{py}: {exc}")
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = MARKER_LINE_RE.search(line)
            if not m:
                continue
            out.append((py, lineno, m.group("kw").upper(), line.strip()))
    if unreadable:
        logger.warning("%d file(s) could not be read and were not scanned: %s", len(unreadable), "; ".join(unreadable))
    return out
