"""The line ranges a diff actually touched, per file.

Written for :mod:`mutation_teeth`, which is only affordable when it is restricted to the lines a
commit changed: mutating a whole module is minutes, mutating twelve lines is seconds. Nothing in
this package family produced changed-line ranges before -- the only git invocations were
``git ls-files`` (:mod:`repo_hygiene`), ``git blame --line-porcelain``
(:mod:`stale_comment_age`) and ``rev-parse``/``merge-base`` -- so this is the shared answer rather
than a fourth private copy.

The traps this handles, each of which produces a silently wrong range if ignored:

* ``--unified=0`` is required. With any context the hunk header covers unchanged lines, so a
  "changed lines" set built from it includes lines the commit never touched, and a mutation hook
  scoped by it does far more work than asked.
* A hunk header is ``@@ -a,b +c,d @@`` where ``,d`` is **omitted when d == 1**. Parsing on the comma
  drops every single-line change -- the most common kind.
* ``d == 0`` marks a pure deletion. There are no new lines to mutate, and the range must be empty
  rather than one line long.
* Paths are quoted and C-escaped when they contain non-ASCII or spaces, unless ``-z`` is used.
  ``-z`` also removes the ambiguity of a filename containing a newline.
* A rename shows as ``a/old -> b/new``; the range belongs to the NEW path.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: ``@@ -old[,n] +new[,n] @@`` -- the ``,n`` groups are genuinely optional; see the module docstring.
_HUNK_RE = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(
    repo_root: Path | str,
    *,
    rev: str | None = None,
    include_untracked: bool = True,
) -> dict[Path, list[range]]:
    """Repo-relative path -> the ranges of NEW-file lines this diff added or modified.

    *rev* selects what to diff against: ``None`` (the default) means the working tree plus the
    index, i.e. "what I have changed and not yet committed", which is what a pre-commit hook wants.
    Passing ``"HEAD~1"`` or a merge base gives the same shape for a range of commits.

    Untracked files are reported whole, because every line of a new file is a changed line. That is
    included by default for the same reason: a mutation hook that skipped new files would skip
    exactly the code most likely to be under-tested.

    Returns ranges, not line numbers, so a caller can test membership cheaply and print something a
    human recognises. An empty result means nothing changed -- distinguishable from "the command
    failed", which raises.
    """
    root = Path(repo_root).resolve()
    args = ["git", "-C", str(root), "diff", "--unified=0", "--no-color", "-z", "--no-ext-diff"]
    if rev:
        args.append(rev)
    else:
        args.append("HEAD")
    completed = subprocess.run(args, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"git diff failed in {root} (exit {completed.returncode}): "
            f"{completed.stderr.decode('utf-8', 'replace')[:400]}"
        )

    out: dict[Path, list[range]] = {}
    current: Path | None = None
    for raw in completed.stdout.split(b"\n"):
        if raw.startswith(b"+++ "):
            # `+++ b/path`, or `+++ /dev/null` for a deletion. With `-z` the path is unquoted.
            target = raw[4:].split(b"\0")[0]
            if target == b"/dev/null":
                current = None
            else:
                text = target.decode("utf-8", "surrogateescape")
                current = Path(text[2:] if text.startswith(("a/", "b/")) else text)
                out.setdefault(current, [])
        elif current is not None and (match := _HUNK_RE.match(raw)):
            start = int(match.group(1))
            count = 1 if match.group(2) is None else int(match.group(2))
            if count:  # count == 0 is a pure deletion: no new lines exist to mutate
                out[current].append(range(start, start + count))

    if include_untracked:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
        )
        for entry in listed.stdout.split(b"\0"):
            if not entry:
                continue
            path = Path(entry.decode("utf-8", "surrogateescape"))
            absolute = root / path
            try:
                line_count = absolute.read_text(encoding="utf-8", errors="replace").count("\n") + 1
            except OSError:
                continue
            out.setdefault(path, []).append(range(1, line_count + 1))

    return {path: ranges for path, ranges in out.items() if ranges}


def lines_for(changed: dict[Path, list[range]], path: Path | str) -> list[range]:
    """The ranges for one file, matched repo-relatively.

    A convenience because the caller usually has one file in hand and the mapping is keyed by
    repo-relative path -- comparing an absolute path against it silently returns nothing.
    """
    wanted = Path(path)
    for key, ranges in changed.items():
        if key == wanted or key.as_posix().endswith(wanted.as_posix()):
            return ranges
    return []
