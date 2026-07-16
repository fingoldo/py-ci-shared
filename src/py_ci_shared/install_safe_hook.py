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

import subprocess
import sys
from pathlib import Path

_TARGET = "-mpre_commit"
_REPLACEMENT = "-m py_ci_shared.safe_precommit"
_HOOK_NAMES = ("pre-commit", "pre-merge-commit")


def _git_dir() -> Path:
    out = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def _patch_one(hook_path: Path) -> int:
    if not hook_path.exists():
        # pre-merge-commit is optional (only created by an explicit `--hook-type` install) -- not
        # an error, just nothing to patch yet.
        if hook_path.name == "pre-commit":
            print(f"{hook_path} does not exist -- run `pre-commit install` first.", file=sys.stderr)
            return 1
        print(f"{hook_path} does not exist (run `pre-commit install --hook-type pre-merge-commit` to add it) -- skipped.")
        return 0

    text = hook_path.read_text()
    if _REPLACEMENT in text:
        print(f"{hook_path} is already patched.")
        return 0
    if _TARGET not in text:
        print(
            f"Could not find `{_TARGET}` in {hook_path} (unexpected pre-commit hook template); " "leaving it untouched.",
            file=sys.stderr,
        )
        return 1

    patched = text.replace(_TARGET, _REPLACEMENT)
    hook_path.write_text(patched)
    print(f"Patched {hook_path}: `python {_TARGET}` -> `python {_REPLACEMENT}`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        hooks_dir = _git_dir() / "hooks"
    except subprocess.CalledProcessError:
        print("Not inside a git repository.", file=sys.stderr)
        return 1

    results = [_patch_one(hooks_dir / name) for name in _HOOK_NAMES]
    return 1 if any(r != 0 for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
