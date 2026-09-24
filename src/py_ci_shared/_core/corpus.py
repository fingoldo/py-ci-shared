"""One corpus enumerator: what git would commit, or a pruned walk outside git.

Before this module the package enumerated files four ways (``rglob`` in 39 gates, ``os.walk`` in 2, ``git ls-files``
in 4) with seven different skip lists, and at least one matched the skip list against ABSOLUTE path parts, so a
checkout living under a directory named ``build`` or ``venv`` was skipped whole and every gate passed on it
(audit 2026-09-24 ARCH-14, ARCH-15). Local runs also saw venvs, build copies and agent worktrees that CI never had.

Rules here:

* Inside a git work tree the corpus is ``git ls-files -z --cached --others --exclude-standard``: tracked files plus
  untracked-but-not-ignored ones, so a new file is checked before its first commit and ignored output is never read.
  Output is bytes, decoded as UTF-8 with ``surrogateescape`` so an odd filename cannot crash the listing.
* Outside git (or when *root* itself is git-ignored) a walk prunes excluded directories instead of descending.
* :data:`DEFAULT_EXCLUDE` is matched against path components RELATIVE to *root*, in both modes.
* A missing root raises :class:`CorpusError`; it is never an empty, passing scan.
* Output is sorted, so findings and baselines are deterministic.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Optional, Union
from collections.abc import Iterable, Iterator, Sequence

from .errors import CorpusError

PathLike = Union[str, "os.PathLike[str]"]

#: Directory names never part of a source corpus. The union of the seven per-gate lists this replaces
#: (db_transaction_completeness, effect_assertion_parity, llm_call_archive_gate, naive_utcnow, prompt_field_parity,
#: save_failure_markers, value_bearing_asserts) plus repo_hygiene's two cache dirs. value_bearing_asserts' scoping
#: names (``tests``, ``scripts``...) are that gate's policy, not junk, and stay in that gate.
DEFAULT_EXCLUDE: frozenset[str] = frozenset(
    {
        ".benchmarks",
        ".claude",
        ".eggs",
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)


def _git(root: Path, *args: str) -> "Optional[subprocess.CompletedProcess[bytes]]":
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None


def git_listing(root: Path, *, include_untracked: bool = True) -> Optional[list[str]]:
    """Root-relative POSIX paths git would commit under *root*, or ``None`` when *root* is not usable via git.

    ``None`` covers: git missing, not a work tree, "dubious ownership", and *root* itself being ignored (then the
    listing would be empty for a reason that has nothing to do with the files).
    """
    probe = _git(root, "rev-parse", "--is-inside-work-tree")
    if probe is None or probe.returncode != 0 or probe.stdout.strip() != b"true":
        return None
    ignored = _git(root, "check-ignore", "-q", ".")
    if ignored is not None and ignored.returncode == 0:
        return None
    args = ["ls-files", "-z", "--cached"]
    if include_untracked:
        args += ["--others", "--exclude-standard"]
    listing = _git(root, *args)
    if listing is None or listing.returncode != 0:
        return None
    names = listing.stdout.decode("utf-8", "surrogateescape").split("\0")
    # --cached and --others can both list a path in a conflicted state; dedupe.
    return sorted({n for n in names if n})


def _walk(root: Path, exclude: frozenset[str]) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude)
        rel_dir = Path(dirpath).relative_to(root)
        for name in filenames:
            yield (rel_dir / name).as_posix()


def _matches(rel: str, patterns: Sequence[str]) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(rel if "/" in pat else name, pat) for pat in patterns)


def iter_files(
    root: PathLike,
    patterns: Sequence[str] = ("*.py",),
    *,
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
    include_untracked: bool = True,
    use_git: Optional[bool] = None,
) -> list[Path]:
    """Sorted files under *root* matching any of *patterns*, as absolute-ish ``root / rel`` paths.

    *patterns* are ``fnmatch`` patterns against the file NAME, or against the root-relative POSIX path when the
    pattern contains ``/``. *exclude* is a set of directory/file names matched against root-relative components
    only. ``use_git=None`` picks git when *root* is inside a work tree; ``False`` forces the walk; ``True``
    requires git and raises when it is not available.
    """
    base = Path(root)
    if not base.exists():
        raise CorpusError(f"corpus root does not exist: {base}")
    if not base.is_dir():
        raise CorpusError(f"corpus root is not a directory: {base}")
    skip = frozenset(exclude)
    rels: Optional[Iterable[str]] = None
    if use_git is not False:
        rels = git_listing(base, include_untracked=include_untracked)
        if rels is None and use_git:
            raise CorpusError(f"use_git=True but {base} is not a usable git work tree")
    if rels is None:
        rels = _walk(base, skip)
    out: list[Path] = []
    for rel in rels:
        parts = rel.split("/")
        if skip.intersection(parts) or not _matches(rel, patterns):
            continue
        p = base.joinpath(*parts)
        if p.is_file():  # a tracked file deleted from the work tree is not part of the corpus
            out.append(p)
    return sorted(out, key=lambda p: p.relative_to(base).as_posix())


def relative_posix(path: PathLike, root: Optional[PathLike]) -> str:
    """*path* relative to *root* as POSIX, or the absolute POSIX path when it is not under *root* (never raises)."""
    p = Path(path)
    if root is not None:
        try:
            return p.relative_to(Path(root)).as_posix()
        except ValueError:
            try:
                return p.resolve().relative_to(Path(root).resolve()).as_posix()
            except (ValueError, OSError):
                pass
    return p.resolve().as_posix() if not p.is_absolute() else p.as_posix()
