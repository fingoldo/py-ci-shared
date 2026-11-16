"""Private plumbing of the text-scanning ratchet gates: which files a gate reads, how a finding is keyed, and how the
findings are enforced against the repo's baseline.

Shared by ``skip_as_pass``, ``wallclock_test_waits``, ``guard_without_fixture``, ``git_child_env`` and
``dup_baseline_percent_key``. Every key is repo-relative and built from the file and the line's normalized text, never
from a line number, so an unrelated edit above an accepted line does not invalidate its baseline entry.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Union

from ._core import DEFAULT_EXCLUDE
from ._core.errors import CorpusError
from ._core.outcome import add_note
from ._gate_report import TextFile, read_text_corpus
from .baseline_ratchet import Baseline

__all__ = [
    "DEFAULT_BASELINE_DIR",
    "TEST_DIR_NAMES",
    "enforce_findings",
    "is_test_path",
    "normalize_line",
    "record_finding",
    "require_text_floor",
    "text_files",
    "text_scan",
]

#: Where a consumer keeps its ratchet files; ``*_dir`` keyword arguments are resolved against the repo root.
DEFAULT_BASELINE_DIR = "tool/meta/baselines"
TEST_DIR_NAMES = frozenset({"test", "tests"})


def normalize_line(text: str) -> str:
    """The line with its whitespace collapsed: indentation and re-wrapping do not change a key."""
    return " ".join(text.split())


def is_test_path(rel: str) -> bool:
    """A file under a ``test``/``tests`` directory, or named like a test module (``test_*.py``, ``*_test.py``, ``*_test.dart``)."""
    parts = rel.split("/")
    name = parts[-1]
    return any(p in TEST_DIR_NAMES for p in parts[:-1]) or name.startswith("test_") or name.endswith(("_test.py", "_test.dart"))


def text_scan(root: Union[str, Path], patterns: Sequence[str], *, under: Iterable[str] = ()) -> "tuple[list[TextFile], dict[str, str]]":
    """``(files, unreadable)``: readable files matching *patterns* under *root* (limited to those whose first path component is in
    *under*; all when empty), and one finding ``{'<rel>: unreadable': reason}`` per matching file that could not be decoded.

    A file the gate cannot read is a finding and never a pass: one latin-1 accent in a comment must not take the whole file out
    of the ratchet. The caller merges *unreadable* into its findings."""
    names = frozenset(under)
    corpus = read_text_corpus(root, patterns=patterns, skip=DEFAULT_EXCLUDE)
    files = [f for f in corpus.files if not names or f.rel.split("/", 1)[0] in names]
    unreadable = {
        f"{p.rel}: unreadable": f"cannot be read as text ({p.message}); the gate cannot judge it"
        for p in corpus.unreadable
        if not names or p.rel.split("/", 1)[0] in names
    }
    return files, unreadable


def text_files(root: Union[str, Path], patterns: Sequence[str], *, under: Iterable[str] = ()) -> list[TextFile]:
    """Readable files only (see :func:`text_scan`, which also reports the unreadable ones as findings)."""
    return text_scan(root, patterns, under=under)[0]


def require_text_floor(label: str, count: int, *, min_files: int, root: Union[str, Path]) -> None:
    """Raise :class:`CorpusError` when fewer than *min_files* files of the gate's corpus were read.

    A wrong root (``repo/src`` for ``repo``, a test file moved away) leaves a text ratchet with nothing to read, and an empty
    corpus has no findings: the gate would pass forever. A repository that really has none of the scanned files sets
    ``min_files=0``.
    """
    if count < min_files:
        raise CorpusError(
            f"{label}: {count} file(s) of its corpus under {root}; expected at least {min_files}. "
            "The gate is not reaching the code (wrong root, or the scanned directories are absent). Pass min_files=0 if the repo has none."
        )


def record_finding(found: "dict[str, str]", key: str, description: str) -> None:
    """Add *key* to *found*; a key already present gets an ordinal (``key #2``, ``key #3``) so N identical lines in one file are N
    findings and a baseline that accepts the first does not hide the copies pasted after it."""
    candidate = key
    n = 1
    while candidate in found:
        n += 1
        candidate = f"{key} #{n}"
    found[candidate] = description


def enforce_findings(
    name: str,
    found: Mapping[str, str],
    *,
    repo_root: Union[str, Path],
    baseline_dir: str,
    label: str,
    guidance: str,
) -> None:
    """Fail (raise ``AssertionError`` carrying the whole report) on each finding the baseline ``<baseline_dir>/<name>.json`` does not
    accept, on a stale or unjustified entry, and on a scan that found nothing. A pass becomes a note on the gate's PASS row (49-T3)."""
    baseline = Baseline(name, directory=os.path.join(str(repo_root), baseline_dir))
    # Messages name the baseline repo-relative with posix separators, never the machine's absolute path (49-T10).
    baseline.shown = Path(baseline_dir, f"{name}.json").as_posix()
    code, text = baseline.check(found, label=label, guidance=guidance)
    if code:
        raise AssertionError(text)
    add_note(text)
