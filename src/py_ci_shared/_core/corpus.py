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
* Submodule content is never part of the corpus: git lists a submodule as one gitlink, and the walk prunes every
  directory that holds a ``.git`` FILE.
* Inside a work tree a failed or timed-out ``git ls-files`` raises :class:`CorpusError`; it never silently becomes
  the walk (``PY_CI_SHARED_GIT_TIMEOUT_S`` raises the timeout, see :mod:`py_ci_shared._core.git`).
* :data:`DEFAULT_EXCLUDE` is matched against path components RELATIVE to *root*, in both modes.
* A missing root raises :class:`CorpusError`; it is never an empty, passing scan.
* Output is sorted, so findings and baselines are deterministic.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import subprocess
import threading
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Optional, Union

from .errors import CorpusError
from .git import GitError, git_text, run_git

PathLike = Union[str, "os.PathLike[str]"]

logger = logging.getLogger(__name__)

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


# Roots that git has said ARE a usable work tree and are not themselves ignored. One listing used to cost three git processes and a run
# lists the same few roots 60 or more times; the two probes depend on the repository's shape, not on which files exist, so a positive answer is
# kept. The listing itself (ls-files) is never kept: a gate may write a file and list again. Only a positive answer is stored, so a directory
# that becomes a repository later is still noticed.
_USABLE_ROOTS: set[str] = set()
_USABLE_LOCK = threading.Lock()


def clear_listing_cache() -> None:
    """Forget which roots were found to be usable work trees (the next listing probes again)."""
    with _USABLE_LOCK:
        _USABLE_ROOTS.clear()


def _git(root: Path, *args: str) -> "Optional[subprocess.CompletedProcess[bytes]]":
    """A git call, or None when git itself is missing. A TIMEOUT is not "no git": it raises :class:`CorpusError`."""
    try:
        return run_git(root, *args)
    except GitError as exc:
        if exc.timed_out:
            raise CorpusError(f"cannot list the corpus under {root}: {exc}") from exc
        return None


_LS_FILES_ATTEMPTS = 3


def _ls_files(root: Path, args: list[str]) -> bytes:
    """stdout of ``git ls-files`` in *root*. A timeout, or a non-zero exit with no stderr, is retried up to two more times
    (each retry logged at WARNING with the exit code); anything else, or the last failure, raises :class:`CorpusError`
    naming the exit code, git's stderr (or ``no stderr``) and the working directory."""
    detail = ""
    for attempt in range(1, _LS_FILES_ATTEMPTS + 1):
        try:
            proc = run_git(root, *args)
        except GitError as exc:
            if not exc.timed_out:
                raise CorpusError(f"git ls-files could not run in the work tree {root}: {exc}; refusing to fall back to a walk") from exc
            detail = f"timed out ({exc}); exit code none"
            transient = True
        else:
            if proc.returncode == 0:
                return proc.stdout
            stderr = git_text(proc.stderr).strip()[:300]
            detail = f"exit code {proc.returncode}, stderr: {stderr or 'no stderr'}"
            transient = not stderr
        if not transient or attempt == _LS_FILES_ATTEMPTS:
            break
        logger.warning("git ls-files in %s failed (attempt %d of %d; %s); retrying", root, attempt, _LS_FILES_ATTEMPTS, detail)
    raise CorpusError(
        f"git ls-files failed in the work tree {root} (cwd {os.getcwd()}): {detail}; refusing to fall back to a walk that would read ignored files"
    )


def git_listing(root: Path, *, include_untracked: bool = True) -> Optional[list[str]]:
    """Root-relative POSIX paths git would commit under *root*, or ``None`` when *root* is not usable via git.

    ``None`` covers: git missing, not a work tree, "dubious ownership", and *root* itself being ignored (then the
    listing would be empty for a reason that has nothing to do with the files). Once git has said *root* IS a work
    tree, a failed or timed-out ``ls-files`` raises :class:`CorpusError`: falling back to the walk would silently
    widen the corpus to ignored build output and submodules (audit 2026-10-03 K-4). Pass ``use_git=False`` to
    :func:`iter_files` to walk on purpose.
    """
    key = str(root.resolve())
    with _USABLE_LOCK:
        known_usable = key in _USABLE_ROOTS
    if not known_usable:
        probe = _git(root, "rev-parse", "--is-inside-work-tree")
        if probe is None or probe.returncode != 0 or probe.stdout.strip() != b"true":
            return None
        ignored = _git(root, "check-ignore", "-q", ".")
        if ignored is not None and ignored.returncode == 0:
            return None
        with _USABLE_LOCK:
            _USABLE_ROOTS.add(key)
    args = ["ls-files", "-z", "--cached"]
    if include_untracked:
        args += ["--others", "--exclude-standard"]
    stdout = _ls_files(root, args)
    names = stdout.decode("utf-8", "surrogateescape").split("\0")
    # --cached and --others can both list a path in a conflicted state; dedupe.
    return sorted({n for n in names if n})


def _walk(root: Path, exclude: frozenset[str]) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # A directory holding a `.git` FILE is a submodule (or linked worktree) checkout: git mode never lists its
        # content, so the walk does not either.
        dirnames[:] = sorted(d for d in dirnames if d not in exclude and not os.path.isfile(os.path.join(dirpath, d, ".git")))
        rel_dir = Path(dirpath).relative_to(root)
        for name in filenames:
            yield (rel_dir / name).as_posix()


def iter_files_pruned(root: PathLike, suffixes: Sequence[str] = (), exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE) -> list[Path]:
    """Sorted files under *root* whose NAME ends with one of *suffixes* (any file when empty), never entering *exclude_dirs*.

    The replacement for ``root.rglob(...)`` and an unpruned ``os.walk(root)``: those descend into ``.claude``, where Claude Code keeps
    its agent worktrees, each a COMPLETE copy of the repository, and a gate handed the repo root then reads every copy (one audit test
    took 19 to 30 minutes with about 20 of them). Directories are pruned before they are entered and only components BELOW *root* are
    judged, so where *root* itself lives never matters. A missing *root* yields an empty list. Symlinked files are listed, as ``rglob`` did.
    """
    base = Path(root)
    skip = frozenset(exclude_dirs)
    ends = tuple(suffixes)
    return [base.joinpath(*rel.split("/")) for rel in sorted(_walk(base, skip)) if not ends or rel.endswith(ends)]


def _matches(rel: str, patterns: Sequence[str]) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(rel if "/" in pat else name, pat) for pat in patterns)


#: Excluded names that are junk only when untracked. A tracked ``src/pkg/build/m.py`` is a real package, and git says so; dot-directories
#: and ``__pycache__`` stay excluded whatever git tracks.
_SOFT_EXCLUDE = frozenset({"build", "dist", "node_modules", "site-packages", "venv"})


class _TrackedNames:
    """In git mode, which excluded-looking paths are tracked (one extra ``git ls-files --cached``, made only when some path needs it)."""

    def __init__(self, base: Path, git_mode: bool) -> None:
        self._base = base
        self._git_mode = git_mode
        self._tracked: Optional[frozenset[str]] = None

    def exempts(self, rel: str, hit: "frozenset[str]") -> bool:
        if not self._git_mode or not hit <= _SOFT_EXCLUDE:
            return False
        if self._tracked is None:
            self._tracked = frozenset(git_listing(self._base, include_untracked=False) or ())
        return rel in self._tracked


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
    only; in git mode a TRACKED file under a directory named ``build``, ``dist``, ``venv``, ``node_modules`` or ``site-packages`` is kept
    (it is a real package), dot-directories and ``__pycache__`` are skipped whatever git tracks. ``use_git=None`` picks git when *root* is inside a work tree; ``False`` forces the walk; ``True``
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
    tracked = _TrackedNames(base, rels is not None and use_git is not False)
    for rel in rels:
        parts = rel.split("/")
        if not _matches(rel, patterns):
            continue
        hit = skip.intersection(parts)
        if hit and not tracked.exempts(rel, hit):
            continue
        p = base.joinpath(*parts)
        # A tracked file deleted from the work tree is not part of the corpus. A symlink is not either: git lists it, is_file() follows it,
        # and a link to a file outside the checkout would put that file's text into a gate's findings and make the run machine-dependent.
        if p.is_file() and not p.is_symlink():
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
