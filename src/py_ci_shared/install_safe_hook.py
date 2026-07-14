#!/usr/bin/env python
"""One-time per-clone setup: point the generated git hook at ``safe_precommit`` instead of raw
``pre_commit``, so plain ``git commit`` transparently survives the concurrent-session stash-restore
race (see ``py_ci_shared.safe_precommit`` for the full explanation) without anyone needing to
remember a wrapper command.

Idempotent -- safe to run repeatedly. Re-run after ``pre-commit install`` (it regenerates
``.git/hooks/pre-commit`` from its own template and resets this override).

Usage::

    python -m py_ci_shared.install_safe_hook
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_TARGET = "-mpre_commit"
_REPLACEMENT = "-m py_ci_shared.safe_precommit"


def _git_dir() -> Path:
    out = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    try:
        hook_path = _git_dir() / "hooks" / "pre-commit"
    except subprocess.CalledProcessError:
        print("Not inside a git repository.", file=sys.stderr)
        return 1
    if not hook_path.exists():
        print(f"{hook_path} does not exist -- run `pre-commit install` first.", file=sys.stderr)
        return 1

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


if __name__ == "__main__":
    raise SystemExit(main())
