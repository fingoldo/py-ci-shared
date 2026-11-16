#!/usr/bin/env python
"""One-time per-clone setup: point the generated git hook(s) at ``safe_precommit`` instead of raw
``pre_commit``, so plain ``git commit`` / ``git merge`` transparently survive the concurrent-
session stash-restore race (see ``py_ci_shared.safe_precommit`` for the full explanation) without
anyone needing to remember a wrapper command.

Patches BOTH ``.git/hooks/pre-commit`` and ``.git/hooks/pre-merge-commit`` when present (the
latter only exists once ``pre-commit install --hook-type pre-merge-commit`` has been run at least
once in this clone -- git's ``pre-commit`` hook stage does NOT fire on ``git merge``, only on a
direct ``git commit``, so a repo relying solely on the former has a real gap: a merge commit can
introduce a blocking-gate violation -- e.g. an un-filtered Black reformat from one merge parent --
with zero local hook coverage, surfacing only once pushed to CI. Confirmed 2026-07-16 on mlframe:
a "Merge remote-tracking branch 'origin/master' into HEAD" commit shipped a raw-Black-reformatted
file that the repo's OWN blocking ``black-filtered-blocking`` pre-commit hook would have caught on
a direct commit. Installing/patching the ``pre-merge-commit`` hook closes this: run once per
clone, `python -m pre_commit install --hook-type pre-merge-commit` (creates the hook file) then
this script (points it at the safe wrapper); each consuming repo's ``.pre-commit-config.yaml``
blocking hooks also need ``pre-merge-commit`` added to their ``stages:`` list to actually fire at
that stage (a hook's explicit ``stages: [pre-commit]`` does not implicitly include other stages).

Idempotent -- safe to run repeatedly. Re-run after ``pre-commit install`` (it regenerates the hook
file(s) from its own template and resets this override).

Usage::

    python -m py_ci_shared.install_safe_hook
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ._core.baseline import replace_file

__all__ = ["main"]

_TARGET = "-mpre_commit"
_REPLACEMENT = "-m py_ci_shared.safe_precommit"
_HOOK_NAMES = ("pre-commit", "pre-merge-commit")
_INSTALL_PYTHON = re.compile(r"^INSTALL_PYTHON=(['\"]?)(.+?)\1\s*$", re.M)
_PROBE = "import py_ci_shared.safe_precommit, pre_commit"


def _interpreter_problem(hook_text: str) -> Optional[str]:
    """Why the interpreter the hook bakes in cannot run the safe wrapper (47-T3); None when it can or when there is none.

    A hook whose ``INSTALL_PYTHON`` is not a file falls back to ``pre-commit`` on PATH in pre-commit's own template, so nothing is probed there.
    """
    match = _INSTALL_PYTHON.search(hook_text)
    if match is None or not Path(match.group(2)).is_file():
        return None
    python = match.group(2)
    proc = subprocess.run([python, "-c", _PROBE], capture_output=True, timeout=120, check=False)
    if proc.returncode == 0:
        return None
    lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    detail = lines[-1] if lines else f"exit {proc.returncode}"
    return (
        f"the hook's interpreter {python} cannot run the safe wrapper ({detail}). Install py-ci-shared and pre-commit into it, "
        "or re-run `pre-commit install -f` from the project environment, then re-run install_safe_hook"
    )


def _git_rev_parse(*args: str) -> str:
    """``git rev-parse`` in the current directory, the user's GIT_DIR honoured (this tool patches the repository the
    user is in). Raises ``CalledProcessError`` when git fails, as its callers expect."""
    from ._core.git import GitError, git_text, run_git

    try:
        out = run_git(None, "rev-parse", *args, inherit_location=True)
    except GitError as exc:
        raise subprocess.CalledProcessError(127, ("git", "rev-parse", *args), b"", str(exc).encode("utf-8", "replace")) from exc
    if out.returncode != 0:
        raise subprocess.CalledProcessError(out.returncode, out.args, out.stdout, out.stderr)
    return git_text(out.stdout).strip()


def _hooks_dir() -> Path:
    """The directory git runs hooks from: ``core.hooksPath`` when set, else ``<git-dir>/hooks`` (``--git-path hooks``
    answers both, and a linked worktree's shared hooks too)."""
    path = Path(_git_rev_parse("--git-path", "hooks"))
    return path if path.is_absolute() else Path.cwd() / path


def _check_one(hook_path: Path) -> int:
    """0 when the hook is patched (or pre-merge-commit does not exist yet), 1 when it is not (47-T4)."""
    if not hook_path.exists():
        if hook_path.name == "pre-commit":
            print(f"{hook_path} does not exist: run `pre-commit install` first.")
            return 1
        return 0
    if _REPLACEMENT.encode("utf-8") in hook_path.read_bytes():
        print(f"{hook_path} is patched.")
        return 0
    print(f"{hook_path} is NOT patched: run `python -m py_ci_shared.install_safe_hook` in this clone.")
    return 1


def _patch_one(hook_path: Path) -> int:
    if not hook_path.exists():
        # pre-merge-commit is optional (only created by an explicit `--hook-type` install) -- not
        # an error, just nothing to patch yet.
        if hook_path.name == "pre-commit":
            print(f"{hook_path} does not exist -- run `pre-commit install` first.", file=sys.stderr)
            return 1
        print(f"{hook_path} does not exist (run `pre-commit install --hook-type pre-merge-commit` to add it) -- skipped.")
        return 0

    # Bytes, not text: the hook keeps its own encoding and line endings (a locale codec or newline translation would
    # rewrite a non-ASCII hook, or turn LF into CRLF on Windows and break the shebang line).
    data = hook_path.read_bytes()
    if _REPLACEMENT.encode("utf-8") in data:
        print(f"{hook_path} is already patched.")
        return 0
    if _TARGET.encode("utf-8") not in data:
        print(
            f"Could not find `{_TARGET}` in {hook_path} (unexpected pre-commit hook template); " "leaving it untouched.",
            file=sys.stderr,
        )
        return 1
    problem = _interpreter_problem(data.decode("utf-8", "replace"))
    if problem is not None:
        print(f"Not patching {hook_path}: {problem}.", file=sys.stderr)
        return 1

    if hook_path.is_symlink():  # write_bytes follows the link, so the patch would land on whatever the link points at
        print(f"{hook_path} is a symlink; leaving it untouched.", file=sys.stderr)
        return 1
    staged = hook_path.with_name(hook_path.name + ".py-ci-shared.tmp")
    staged.write_bytes(data.replace(_TARGET.encode("utf-8"), _REPLACEMENT.encode("utf-8")))
    shutil.copymode(hook_path, staged)
    replace_file(staged, hook_path)  # git runs this file on every commit: it is never left half-written
    print(f"Patched {hook_path}: `python {_TARGET}` -> `python {_REPLACEMENT}`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Patch the git hooks of the current repository to run through the safe pre-commit wrapper; returns 1 outside a git repository."""
    check = argv == ["--check"]
    if argv and not check:
        print(f"install_safe_hook takes no arguments but --check; got: {' '.join(argv)}", file=sys.stderr)
        return 2
    try:
        hooks_dir = _hooks_dir()
    except subprocess.CalledProcessError:
        print("Not inside a git repository.", file=sys.stderr)
        return 1

    action = _check_one if check else _patch_one
    results = [action(hooks_dir / name) for name in _HOOK_NAMES]
    return 1 if any(r != 0 for r in results) else 0


if __name__ == "__main__":
    from ._core.console import safe_stdio

    safe_stdio()
    raise SystemExit(main())
