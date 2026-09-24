"""Shared check: which worktrees, branches and leftover directories can go, and which still hold work.

A long-running multi-session project accumulates worktrees faster than anyone retires them. Measured on
2026-09-15 across eight repos of this project: about 200 worktrees and 100 local branches were removable,
and six unregistered directories under ``mlframe/.claude/worktrees`` each held a full 100 MB copy of the
tree, invisible to ``git worktree list`` because the registration was already gone. Nothing reports any of
this: ``git worktree list`` shows only what is still registered, ``git branch`` says nothing about whether a
branch's commits reached the default branch, and a directory git has forgotten appears in neither.

The judgement that matters is never "is this dirty" but "does this hold anything not already somewhere
durable", which is three different questions:

* **The file is identical upstream.** Landing a file copies it; the copy stays behind and reads as dirty
  forever. CRLF makes a byte comparison lie on Windows, so both spellings are compared.
* **The content was committed at some point.** An agent copy left on an old commit differs from today's
  default branch in thousands of files while holding nothing new: every blob is reachable from some ref.
  This is what separates "stale copy" from "unsaved work", and it is the check a human skips.
* **Neither.** Actual uncommitted work. It is reported, never deleted, never counted as removable.

``worktree_findings`` answers all three per path and returns one verdict per worktree; the CLI prints them.
What to do about a REVIEW verdict stays a human's call: this reports, it does not remove.

Usage::

    python -m py_ci_shared.worktree_hygiene /path/to/repo            # human-readable report
    python -m py_ci_shared.worktree_hygiene /path/to/repo --json     # machine-readable

    from py_ci_shared.worktree_hygiene import worktree_findings
    findings = worktree_findings(Path("/path/to/repo"))
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Directories whose contents are never authored work: caches, build output, vendored trees.
SKIP_PARTS = frozenset({"__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis", "node_modules", ".venv"})

#: Suffixes that are regenerated rather than written, so holding one is not holding work.
SKIP_SUFFIXES = (".pyc", ".pyo")

REMOVABLE = "REMOVABLE"
REVIEW = "REVIEW"
ORPHAN_DIR = "ORPHAN-DIR"


@dataclass(frozen=True)
class WorktreeFinding:
    """One worktree (or leftover directory) with the reason it can or cannot go."""

    path: Path
    verdict: str
    registered: bool
    head_on_remote: bool
    residual: tuple[str, ...] = field(default=())
    note: str = ""

    def line(self) -> str:
        """One report line, carrying up to three unsaved paths as the evidence for a REVIEW verdict."""
        head = f"{self.verdict:11} {self.path}"
        if self.note:
            head += f"  ({self.note})"
        if not self.residual:
            return head
        shown = ", ".join(self.residual[:3])
        more = f" +{len(self.residual) - 3} more" if len(self.residual) > 3 else ""
        return f"{head}\n    unsaved: {shown}{more}"


def _run(repo: Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run git in *repo*; a missing git binary is reported as a failed run (returncode 127), never raised."""
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=stdin, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(["git", *args], 127, b"", str(exc).encode("utf-8", "replace"))


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    """Run git in *repo* and return stdout, empty on failure: for listings where "nothing" is the safe answer."""
    result = _run(repo, *args, stdin=stdin)
    return result.stdout.decode("utf-8", "replace") if result.returncode == 0 else ""


class GitQueryError(RuntimeError):
    """A git question this report depends on could not be answered, so no verdict may be derived from it."""


def _git_checked(repo: Path, *args: str) -> str:
    result = _run(repo, *args)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise GitQueryError(f"git {' '.join(args)} failed in {repo} (exit {result.returncode}): {detail}")
    return result.stdout.decode("utf-8", "surrogateescape")


def verify_ref(repo: Path, ref: str) -> str:
    """The commit *ref* names in *repo*. Raises :class:`GitQueryError` when it does not resolve to a commit."""
    result = _run(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    commit = result.stdout.decode("utf-8", "replace").strip()
    if result.returncode != 0 or not commit:
        raise GitQueryError(f"ref {ref!r} does not resolve to a commit in {repo}; nothing can be judged against it")
    return commit


def registered_worktrees(repo: Path) -> list[Path]:
    """Every path ``git worktree list`` still knows about, the main checkout first."""
    prefix = "worktree "
    return [Path(line[len(prefix) :].strip()) for line in _git(repo, "worktree", "list", "--porcelain").splitlines() if line.startswith(prefix)]


def orphan_directories(repo: Path, container: str = ".claude/worktrees") -> list[Path]:
    """Directories under *container* that git no longer registers -- the ones nothing else lists.

    Dot-directories are skipped: a shared tool cache lives beside the worktrees on purpose (mlframe's
    ``.mlframe_mypy_cache_shared``, which several repos point at), and deleting one as debris costs the
    rebuild it exists to avoid.
    """
    base = repo / container
    if not base.is_dir():
        return []
    known = {path.resolve() for path in registered_worktrees(repo)}
    return sorted(entry for entry in base.iterdir() if entry.is_dir() and not entry.name.startswith(".") and entry.resolve() not in known)


def _upstream_blobs(repo: Path, ref: str) -> dict[str, str]:
    """``path -> blob id`` for every file in *ref*, empty when the ref does not resolve."""
    blobs: dict[str, str] = {}
    for line in _git(repo, "ls-tree", "-r", ref).splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if path and len(parts) >= 3:
            blobs[path] = parts[2]
    return blobs


def _object_format(repo: Path) -> str:
    fmt = _git(repo, "rev-parse", "--show-object-format").strip()
    return fmt if fmt in ("sha1", "sha256") else "sha1"


def _hash(repo: Path, data: bytes, object_format: str | None = None) -> str:
    """The blob id git would give *data* (``git hash-object --stdin``), computed in-process."""
    digest = hashlib.new(object_format or _object_format(repo))
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def reachable_objects(repo: Path) -> frozenset[str]:
    """Every object id reachable from a branch, tag, remote-tracking ref or the stash.

    Reachability, not existence: ``git add`` writes a blob into the object store without any ref pointing at
    it, and removing the worktree loses that staged content. Worktree HEADs are deliberately not roots: a
    commit made on a detached HEAD disappears with the worktree. Raises :class:`GitQueryError` on failure,
    because an empty answer would make every file look unsaved or, worse, be read as "nothing to lose".
    """
    roots = ["--branches", "--tags", "--remotes"]
    if _run(repo, "rev-parse", "--verify", "--quiet", "refs/stash").returncode == 0:
        roots.append("refs/stash")
    out = _git_checked(repo, "rev-list", "--objects", *roots)
    return frozenset(line.split(" ", 1)[0] for line in out.splitlines() if line)


def _content_ids(repo: Path, data: bytes, object_format: str) -> set[str]:
    return {_hash(repo, data, object_format), _hash(repo, data.replace(b"\r\n", b"\n"), object_format)}


def _skipped(relative: str) -> bool:
    return any(part in SKIP_PARTS for part in Path(relative).parts) or relative.endswith(SKIP_SUFFIXES)


def _status_entries(worktree: Path) -> list[tuple[str, str]]:
    """``(XY, path)`` per changed, untracked or ignored path; rename/copy sources are consumed, not reported."""
    out = _git_checked(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching")
    tokens = out.split("\0")
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if len(token) < 4:
            continue
        code, path = token[:2], token[3:]
        entries.append((code, path))
        if "R" in code or "C" in code:
            i += 1  # -z puts the rename/copy SOURCE path in the next token, with no status prefix
    return entries


def _staged_blobs(worktree: Path) -> dict[str, str]:
    """``path -> blob id`` for every stage-0 index entry of *worktree*."""
    blobs: dict[str, str] = {}
    for record in _git_checked(worktree, "ls-files", "-s", "-z").split("\0"):
        meta, _, path = record.partition("\t")
        parts = meta.split()
        if path and len(parts) == 3 and parts[2] == "0":
            blobs[path] = parts[1]
    return blobs


def _files_under(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*") if path.is_file())


def unsaved_paths(repo: Path, worktree: Path, ref: str = "origin/HEAD", *, reachable: frozenset[str] | None = None) -> list[str]:
    """Changed paths in *worktree* whose content is neither in *ref* nor reachable from any ref of this repo.

    Judged: the working file of every modified, untracked or IGNORED path (an ignored ``.env`` or data file is
    lost with the worktree like any other), and the STAGED blob of every path with an index change, since the
    index dies with the worktree too. A deletion is not unsaved work: what it removes is still in the ref.
    Raises :class:`GitQueryError` when git cannot answer.
    """
    upstream = _upstream_blobs(repo, ref)
    known = reachable if reachable is not None else reachable_objects(repo)
    object_format = _object_format(repo)
    unsaved: list[str] = []
    staged: dict[str, str] | None = None

    def saved(relative: str, ids: set[str]) -> bool:
        return upstream.get(relative) in ids or bool(ids & known)

    for code, relative in _status_entries(worktree):
        if _skipped(relative.rstrip("/")):
            continue
        if code[0] in "AMRCT":
            if staged is None:
                staged = _staged_blobs(worktree)
            blob = staged.get(relative)
            if blob is not None and not saved(relative, {blob}):
                unsaved.append(relative)
                continue
        if "D" in code:
            continue
        candidate = worktree / relative
        if code == "!!" and candidate.is_dir():
            for inner in _files_under(candidate):
                inner_rel = inner.relative_to(worktree).as_posix()
                if not _skipped(inner_rel) and not saved(inner_rel, _content_ids(repo, inner.read_bytes(), object_format)):
                    unsaved.append(inner_rel)
            continue
        if not candidate.is_file():
            continue
        if not saved(relative, _content_ids(repo, candidate.read_bytes(), object_format)):
            unsaved.append(relative)
    return unsaved


def unsaved_files_in_directory(repo: Path, directory: Path, ref: str = "origin/HEAD", *, reachable: frozenset[str] | None = None) -> list[str]:
    """Files under *directory* whose content is neither in *ref* nor reachable from any ref of this repo.

    An unregistered directory cannot be asked through git: ``git -C`` inside one resolves to the enclosing
    repository and answers about THAT, reporting the directory as clean no matter what it holds. So every
    file is walked and judged on content instead, which is the only thing that distinguishes a forgotten
    100 MB copy from a directory somebody never committed.
    """
    upstream = _upstream_blobs(repo, ref)
    known = reachable if reachable is not None else reachable_objects(repo)
    object_format = _object_format(repo)
    unsaved: list[str] = []
    for candidate in _files_under(directory):
        relative = candidate.relative_to(directory)
        if any(part in SKIP_PARTS for part in relative.parts) or candidate.name.endswith(SKIP_SUFFIXES):
            continue
        ids = _content_ids(repo, candidate.read_bytes(), object_format)
        if upstream.get(relative.as_posix()) in ids or ids & known:
            continue
        unsaved.append(relative.as_posix())
    return unsaved


def _head_is_on_a_remote(repo: Path, worktree: Path) -> bool:
    """True when the worktree's HEAD commit is contained in some remote branch."""
    head = _git(worktree, "rev-parse", "HEAD").strip()
    return bool(head) and bool(_git(repo, "branch", "-r", "--contains", head).strip())


def worktree_findings(repo: Path, ref: str = "origin/HEAD", container: str = ".claude/worktrees") -> list[WorktreeFinding]:
    """A verdict per linked worktree and per leftover directory, the main checkout excluded.

    Raises :class:`GitQueryError` when *ref* does not resolve or the reachable-object listing fails: every
    verdict is derived from those two answers.
    """
    verify_ref(repo, ref)
    reachable = reachable_objects(repo)
    findings: list[WorktreeFinding] = []
    for worktree in registered_worktrees(repo)[1:]:
        if not worktree.is_dir():
            findings.append(
                WorktreeFinding(worktree, REMOVABLE, registered=True, head_on_remote=False, note="registered but gone from disk; git worktree prune")
            )
            continue
        try:
            unsaved = unsaved_paths(repo, worktree, ref, reachable=reachable)
        except GitQueryError as exc:
            findings.append(WorktreeFinding(worktree, REVIEW, registered=True, head_on_remote=False, note=f"cannot inspect: {exc}"))
            continue
        on_remote = _head_is_on_a_remote(repo, worktree)
        note = "" if unsaved or on_remote else "HEAD is on no remote branch"
        findings.append(
            WorktreeFinding(
                worktree,
                REMOVABLE if not unsaved and on_remote else REVIEW,
                registered=True,
                head_on_remote=on_remote,
                residual=tuple(unsaved),
                note=note,
            )
        )
    for leftover in orphan_directories(repo, container):
        unsaved = unsaved_files_in_directory(repo, leftover, ref, reachable=reachable)
        findings.append(
            WorktreeFinding(
                leftover,
                ORPHAN_DIR if unsaved else REMOVABLE,
                registered=False,
                head_on_remote=False,
                residual=tuple(unsaved),
                note="git does not register this directory",
            )
        )
    return findings


def branches_without_unique_commits(repo: Path, ref: str = "origin/HEAD") -> list[str]:
    """Local branches whose every commit is already in *ref* by patch id, excluding any that are checked out.

    Raises :class:`GitQueryError` when *ref* does not resolve. A branch whose ``git cherry`` fails is never
    called spare: an unanswered question is not an empty answer.
    """
    verify_ref(repo, ref)
    prefix = "branch refs/heads/"
    checked_out = {line[len(prefix) :].strip() for line in _git(repo, "worktree", "list", "--porcelain").splitlines() if line.startswith(prefix)}
    default = _git(repo, "symbolic-ref", "--short", ref).strip().partition("/")[2]
    spare = []
    for branch in _git_checked(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        if branch in checked_out or branch == default:
            continue
        cherry = _run(repo, "cherry", ref, branch)
        if cherry.returncode != 0:
            continue
        if not any(line.startswith("+") for line in cherry.stdout.decode("utf-8", "replace").splitlines()):
            spare.append(branch)
    return spare


def report(repo: Path, ref: str = "origin/HEAD", container: str = ".claude/worktrees") -> str:
    """The whole hygiene report as text: worktrees, leftover directories, then spare branches."""
    findings = worktree_findings(repo, ref, container)
    lines = [finding.line() for finding in findings]
    spare = branches_without_unique_commits(repo, ref)
    if spare:
        lines.append(f"{len(spare)} branch(es) with no commit of their own: " + ", ".join(spare))
    removable = sum(1 for finding in findings if finding.verdict == REMOVABLE)
    lines.append(f"-- {removable} removable, {len(findings) - removable} needing a look, {len(spare)} spare branch(es)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: print one repository's hygiene report, as text or JSON."""
    parser = argparse.ArgumentParser(description="Report worktrees, leftover directories and branches that hold nothing unsaved.")
    parser.add_argument("repo", type=Path, help="repository to inspect")
    parser.add_argument("--ref", default="origin/HEAD", help="the ref work must be present in to count as saved (default: origin/HEAD)")
    parser.add_argument("--container", default=".claude/worktrees", help="directory holding agent worktrees (default: .claude/worktrees)")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON instead of text")
    args = parser.parse_args(argv)
    try:
        return _emit(args)
    except GitQueryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _emit(args: argparse.Namespace) -> int:
    if args.json:
        payload = [
            {
                "path": str(finding.path),
                "verdict": finding.verdict,
                "registered": finding.registered,
                "head_on_remote": finding.head_on_remote,
                "unsaved": list(finding.residual),
                "note": finding.note,
            }
            for finding in worktree_findings(args.repo, args.ref, args.container)
        ]
        print(json.dumps(payload, indent=2))
    else:
        print(report(args.repo, args.ref, args.container))
    return 0


if __name__ == "__main__":
    sys.exit(main())
