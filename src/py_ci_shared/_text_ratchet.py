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
from ._gate_report import TextFile, read_text_corpus
from .baseline_ratchet import Baseline

__all__ = ["DEFAULT_BASELINE_DIR", "TEST_DIR_NAMES", "enforce_findings", "is_test_path", "normalize_line", "text_files"]

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


def text_files(root: Union[str, Path], patterns: Sequence[str], *, under: Iterable[str] = ()) -> list[TextFile]:
    """Readable files matching *patterns* under *root*, limited to those whose first path component is in *under*
    (all files when *under* is empty). Undecodable files are left out: they are not text, so no line of them is a finding."""
    names = frozenset(under)
    files = read_text_corpus(root, patterns=patterns, skip=DEFAULT_EXCLUDE).files
    return [f for f in files if not names or f.rel.split("/", 1)[0] in names]


def enforce_findings(
    name: str,
    found: Mapping[str, str],
    *,
    repo_root: Union[str, Path],
    baseline_dir: str,
    label: str,
    guidance: str,
) -> None:
    """Fail (raise ``AssertionError``) on each finding the baseline ``<baseline_dir>/<name>.json`` does not accept."""
    baseline = Baseline(name, directory=os.path.join(str(repo_root), baseline_dir))
    accepted = baseline.load()
    if baseline.enforce(found, label=label, guidance=guidance):
        new = [f"{key} ({found[key]})" for key in sorted(found) if key not in accepted]
        listed = "; ".join(new) if new else "the baseline needs a note or a refresh, see the report above"
        raise AssertionError(f"{label}: {len(new)} new violation(s): {listed}. {baseline.path} accepts only the entries it holds")
