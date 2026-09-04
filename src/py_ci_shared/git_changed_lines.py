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
* Paths are quoted and C-escaped when they contain non-ASCII, spaces or control characters.
  ``-z`` does NOT help here: git honours it for ``--raw``/``--numstat``/``--name-only``/
  ``--name-status`` and ignores it in patch mode, so the quoting is decoded explicitly below. An
  earlier version of this docstring claimed the opposite and the parser was written to match it.
* ``+++ `` at the start of a line is only a file header in the right POSITION. An added source line
  whose text begins ``++ `` renders as ``+++ ...`` and is otherwise indistinguishable, so the
  headers are recognised by a small state machine rather than by prefix alone.
* A rename shows as ``a/old -> b/new``; the range belongs to the NEW path.
"""

from __future__ import annotations

import re
import subprocess
import warnings
from pathlib import Path

#: ``@@ -old[,n] +new[,n] @@`` -- the ``,n`` groups are genuinely optional; see the module docstring.
_HUNK_RE = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


_C_ESCAPES = {ord("a"): 7, ord("b"): 8, ord("f"): 12, ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("v"): 11}


def _unquote(raw: bytes) -> bytes:
    """Decode git's C-quoting of a path. Returns *raw* unchanged when it is not quoted.

    git quotes a path whose bytes include a control character, a quote, a backslash, or (unless
    ``core.quotepath=false``) any byte above 0x7f, and escapes the offending bytes as ``\\ooo``
    octal or as one of the single-character escapes. Decoding has to happen on BYTES: the octal
    escapes are the individual bytes of a UTF-8 sequence, so decoding to text first would turn one
    character into several.
    """
    if not (raw.startswith(b'"') and raw.endswith(b'"')):
        return raw
    body = raw[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        char = body[i]
        if char != 0x5C:  # backslash
            out.append(char)
            i += 1
            continue
        i += 1
        if i >= len(body):
            break
        nxt = body[i]
        if 0x30 <= nxt <= 0x37:  # \ooo, always exactly three octal digits
            out.append(int(body[i : i + 3], 8) & 0xFF)
            i += 3
        elif nxt in _C_ESCAPES:
            out.append(_C_ESCAPES[nxt])
            i += 1
        else:  # \" and \\ and anything else: the byte stands for itself
            out.append(nxt)
            i += 1
    return bytes(out)


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
    args = ["git", "-C", str(root), "diff", "--unified=0", "--no-color", "--no-ext-diff"]
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
    # A `+++ ` line is a header only after a `--- ` line, which is itself only a header after a
    # `diff --git` line. Without this, an added source line beginning `++ ` is read as a header and
    # every hunk after it is filed under a path that does not exist.
    saw_diff = False
    saw_minus = False
    for raw in completed.stdout.split(b"\n"):
        if raw.startswith(b"diff --git "):
            saw_diff, saw_minus = True, False
            continue
        if saw_diff and raw.startswith(b"--- "):
            saw_minus = True
            continue
        if saw_minus and raw.startswith(b"+++ "):
            saw_diff = saw_minus = False
            # git appends a TAB after the path when it quotes one. A filename that genuinely ends
            # in a tab is quoted too, and its tab is escaped INSIDE the quotes as `\t`, so a raw
            # trailing tab is always the separator and never part of the name.
            target = _unquote(raw[4:].rstrip(b"\r").rstrip(b"\t"))
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
            except OSError as exc:
                # Skipping in silence made an unreadable new file look like an unchanged one, and a
                # sweep scoped by this result then covers everything except the file nobody could
                # read. Not fatal -- a transient lock should not abort an unrelated run -- but never
                # silent either.
                warnings.warn(f"changed_lines: cannot read untracked {path}: {exc}", stacklevel=2)
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
        # Segment-wise, not a raw suffix. `endswith` made `m.py` match `sub/m.py`, so a sweep could
        # be scoped by ANOTHER file's changed lines and report clean on lines it never looked at --
        # and `helpers.py` would match `test_helpers.py`.
        if key == wanted or key.parts[-len(wanted.parts):] == wanted.parts:
            return ranges
    return []
