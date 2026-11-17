"""Move the py-ci-shared pin lines of ONE consumer checkout to a ref. Dry run by default (audit 47-T8).

A consumer pins py-ci-shared in several files: a workflow ``uses:`` line, a reusable workflow's ``py-ci-shared-ref:``
input, a pre-commit ``rev:`` and a requirements or pyproject URL, each with a trailing ``# vX.Y.Z`` comment. A bump that
moves only some of them fails later (``pins-disagree``). This tool moves all of them, and only them.

Usage::

    py-ci-shared tool bump_pin v1.23.11 /path/to/consumer           # dry run: prints the diff
    py-ci-shared tool bump_pin v1.23.11 /path/to/consumer --write   # rewrites the files

It never writes outside the checkout it is given: files are found under that directory, symlinks are not followed, and
only workflow, pre-commit, pyproject and requirements files are candidates.
"""

from __future__ import annotations

import difflib
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Optional

from ._core.corpus import iter_files_pruned

__all__ = ["main", "rewrite"]


def _say(message: str, *, err: bool = False) -> None:
    (sys.stderr if err else sys.stdout).write(message + "\n")

#: ``fingoldo/py-ci-shared[/path][.git]@REF``, in a ``uses:`` line, a ``rev:`` or a git URL.
_PIN = re.compile(r"(fingoldo/py-ci-shared(?:\.git)?(?:/[\w.\-/]*)?@)(?P<ref>[^\s\"'#]+)")
#: ``py-ci-shared-ref: REF`` on a reusable workflow's input.
_INPUT = re.compile(r"(py-ci-shared-ref:[ \t]*)(?P<ref>[^\s\"'#]+)")
#: The trailing ``# vX.Y.Z`` comment that names the pinned release.
_VERSION_COMMENT = re.compile(r"#[ \t]*v\d+\.\d+\.\d+\b")
_REF = re.compile(r"^[A-Za-z0-9._/\-]+$")
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__"})


def rewrite(text: str, ref: str) -> str:
    """*text* with every py-ci-shared pin moved to *ref*. Lines that carry no pin are returned byte for byte."""
    out = []
    for line in text.splitlines(keepends=True):
        new = _INPUT.sub(lambda m: m.group(1) + ref, _PIN.sub(lambda m: m.group(1) + ref, line))
        if new != line and _VERSION_COMMENT.search(new):
            new = _VERSION_COMMENT.sub(f"# {ref}", new, count=1)
        out.append(new)
    return "".join(out)


def _is_pin_file(rel: Path) -> bool:
    name = rel.name
    if rel.parts and rel.parts[0] == ".github" and rel.suffix in (".yml", ".yaml"):
        return True
    return name in (".pre-commit-config.yaml", "pyproject.toml") or (name.startswith("requirements") and name.endswith(".txt"))


def _candidates(root: Path) -> Iterator[Path]:
    """Pin-bearing files under *root*. Symlinks and anything under a skipped directory are not candidates."""
    for path in sorted(iter_files_pruned(root)):
        rel = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or set(rel.parts[:-1]) & _SKIP_DIRS:
            continue
        if _is_pin_file(rel):
            yield path


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``bump_pin REF CHECKOUT [--write]``: 0 when it ran (dry run or written), 2 for a usage error."""
    args = list(sys.argv[1:] if argv is None else argv)
    write = "--write" in args
    args = [a for a in args if a != "--write"]
    if len(args) != 2:
        _say("usage: bump_pin REF CHECKOUT [--write]", err=True)
        return 2
    ref, checkout = args
    if not _REF.match(ref):
        _say(f"bump_pin: refusing ref {ref!r}: only letters, digits, '.', '_', '/' and '-' are allowed", err=True)
        return 2
    root = Path(checkout)
    if not root.is_dir():
        _say(f"bump_pin: {checkout} is not a directory", err=True)
        return 2

    changed = 0
    for path in _candidates(root):
        rel = path.relative_to(root).as_posix()
        try:
            old = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            _say(f"bump_pin: {rel} is not UTF-8, skipped", err=True)
            continue
        new = rewrite(old, ref)
        if new == old:
            continue
        changed += 1
        sys.stdout.writelines(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"))
        if write:
            path.write_bytes(new.encode("utf-8"))

    verb = "rewrote" if write else "would rewrite (dry run, nothing written)"
    _say(f"bump_pin: {verb} {changed} file(s) to {ref}" if changed else f"bump_pin: no py-ci-shared pin differs from {ref}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    from ._core.console import safe_stdio

    safe_stdio()
    raise SystemExit(main())
