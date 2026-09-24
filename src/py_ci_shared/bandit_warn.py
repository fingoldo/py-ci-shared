#!/usr/bin/env python
"""Warn-only security-lint check for the pre-commit hook.

Runs ``bandit -ll`` (medium+ severity, matching the CI step) over the staged Python
files under the project's source path and ALWAYS exits 0, so findings surface
locally as WARNINGS at commit time instead of only showing up later in the CI
artifact.

Never blocks the commit and never rewrites anything -- bandit has no auto-fix mode.

Shared across projects via the py-ci-shared package. The source-path filter comes from a
``--src-path`` CLI argument (``--src-path src/mlframe`` or ``--src-path=src/mlframe``) or, if absent,
the PY_CI_SHARED_SRC_PATH env var -- since which files count as "production code" (vs.
tests/scripts/docs) is project-specific. CLI flag is preferred over env var because
pre-commit's hook schema has no ``env:`` key, so an env var would need an extra shell
wrapper to set portably across platforms; a CLI arg works directly in ``entry:``.

The source path and the staged paths are compared as ``/``-separated path segments, so ``src\\pkg``
matches ``src/pkg/a.py`` and ``src/pkg`` does not match ``src/pkg_other/a.py``. When .py files were
staged but none falls under the source path, a warning says so instead of silently scanning nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from typing import Optional


def pop_option(argv: Sequence[str], name: str, *, tag: str = "warn") -> "tuple[Optional[str], list[str]]":
    """``(value, remaining argv)`` for ``name VALUE`` or ``name=VALUE``; a trailing ``name`` with no value warns."""
    rest: list[str] = []
    value: Optional[str] = None
    items = list(argv)
    i = 0
    while i < len(items):
        arg = items[i]
        if arg == name:
            if i + 1 < len(items):
                value = items[i + 1]
                i += 2
                continue
            print(f"[{tag}] {name} was given without a value -- ignoring it", file=sys.stderr)
        elif arg.startswith(name + "="):
            value = arg.split("=", 1)[1]
        else:
            rest.append(arg)
        i += 1
    return value, rest


def _norm(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.strip("/") if p not in ("", ".", "/") else ""


def select_src_files(files: Sequence[str], src_path: str) -> list[str]:
    """The ``.py`` entries of *files* under *src_path*, matched on whole path segments (never a substring)."""
    src = _norm(src_path)
    out = []
    for f in files:
        if not f.endswith(".py"):
            continue
        if not src or f"/{_norm(f)}/".find(f"/{src}/") != -1:
            out.append(f)
    return out


def resolve_src_files(argv: Sequence[str], tag: str) -> "Optional[list[str]]":
    """The staged .py files to check, or None when the hook should skip (nothing to scope to / nothing matched)."""
    src_path, rest = pop_option(argv, "--src-path", tag=tag)
    if not src_path:
        src_path = os.environ.get("PY_CI_SHARED_SRC_PATH")
    if not src_path:
        print(f"[{tag}] no --src-path given and PY_CI_SHARED_SRC_PATH env var not set -- skipping (nothing to scope the scan to)", file=sys.stderr)
        return None
    files = select_src_files(rest, src_path)
    staged_py = [a for a in rest if a.endswith(".py")]
    if staged_py and not files:
        print(
            f"[{tag}] {len(staged_py)} staged .py file(s), none under --src-path {src_path!r} -- nothing scanned. "
            "Check the path (it is matched on whole /-separated segments).",
            file=sys.stderr,
        )
    return files or None


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    files = resolve_src_files(sys.argv[1:] if argv is None else argv, "bandit-warn")
    if not files:
        return 0
    cmd = [sys.executable, "-m", "bandit", "-ll", *files]
    try:
        warned = subprocess.run(cmd, check=False).returncode != 0
    except Exception as exc:  # bandit missing / any error -> warn, never block
        print(f"[bandit-warn] skipped: {exc}", file=sys.stderr)
        return 0
    if warned:
        print(
            "\n[bandit-warn] The findings above are WARNINGS ONLY -- the commit is NOT "
            "blocked. The same scan (bandit -ll) also runs in CI as a non-blocking "
            "step whose full report is uploaded as an artifact.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
