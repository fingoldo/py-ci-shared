"""Shared check: every save-failure marker a writer emits is one the success decision recognises.

WHERE THIS CAME FROM
--------------------
glossum's savers append ``"<name>_failed: ..."`` markers to a result's error list, and a separate list of
fatal prefixes decides whether the run is stamped successful. The two lived in different files with nothing
tying them together, and they drifted twice: markers the savers appended matched no fatal prefix, so a whole
category that failed to save still produced a green status row. When the first check was written, eleven
markers were unrecognised.

WHAT THIS CHECKS
----------------
:func:`find_emitted_markers` scans source files for markers with caller-supplied patterns (by default:
``<something>.append(f"<name>_failed: ...")`` and ``marker="<name>_failed"`` handed to a shared loop).
:func:`assert_markers_are_fatal` fails for a marker the ``is_fatal`` predicate does not accept unless it is in
``non_fatal`` with a reason, for a ``non_fatal`` entry that is actually fatal, and for a ``non_fatal`` entry
no source emits any more.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable
from re import Pattern
from collections.abc import Iterable, Mapping, Sequence

__all__ = ["DEFAULT_MARKER_PATTERNS", "assert_markers_are_fatal", "find_emitted_markers"]

DEFAULT_MARKER_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"""\.append\(\s*f?["']([a-z0-9_]+_failed):"""),
    re.compile(r"""marker\s*=\s*["']([a-z0-9_]+_failed)["']"""),
)
_SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv", "node_modules"})


def find_emitted_markers(root: Path, patterns: Sequence[Pattern[str]] = DEFAULT_MARKER_PATTERNS, *, glob: str = "*.py") -> dict[str, list[str]]:
    """``{marker: ["relative/path.py:line", ...]}`` for every marker the patterns find under ``root``."""
    out: dict[str, list[str]] = {}
    for path in sorted(root.rglob(glob)):
        if _SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        src = path.read_text(encoding="utf-8")
        for pattern in patterns:
            for m in pattern.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                out.setdefault(m.group(1), []).append(f"{path.relative_to(root).as_posix()}:{line}")
    return out


def assert_markers_are_fatal(
    root: Path,
    is_fatal: Callable[[str], bool],
    *,
    non_fatal: Mapping[str, str] = {},
    patterns: Sequence[Pattern[str]] = DEFAULT_MARKER_PATTERNS,
    probe: Callable[[str], str] = lambda name: f"{name}: probe",
    extra_markers: Iterable[str] = (),
) -> None:
    """Fail unless every emitted marker is fatal per ``is_fatal`` or listed in ``non_fatal`` with a reason.

    ``probe`` turns a marker name into the error string the predicate is asked about, so a predicate keyed
    on a prefix, a separator or a whole-line format can be tested the way the writer really emits it.
    """
    markers = find_emitted_markers(root, patterns)
    for name in extra_markers:
        markers.setdefault(name, ["<extra>"])
    blank = [n for n, reason in non_fatal.items() if not reason.strip()]
    unrecognised = {n: sites for n, sites in markers.items() if n not in non_fatal and not is_fatal(probe(n))}
    wrongly_listed = [n for n in non_fatal if is_fatal(probe(n))]
    stale = sorted(set(non_fatal) - set(markers))
    messages = []
    if blank:
        messages.append(f"every non-fatal marker needs a reason: {blank}")
    if unrecognised:
        messages.append(
            "these save-failure markers are emitted but the success decision does not treat them as fatal, so a "
            f"failed save still reads as success: {unrecognised}. Make them fatal, or list them as non-fatal with the reason."
        )
    if wrongly_listed:
        messages.append(f"these are listed as non-fatal but the predicate treats them as fatal; drop them from the list: {wrongly_listed}")
    if stale:
        messages.append(f"these non-fatal entries match no emitted marker; a stale exemption is a blind spot waiting for the next marker of that name: {stale}")
    if messages:
        raise AssertionError("\n".join(messages))
