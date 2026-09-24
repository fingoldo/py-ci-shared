#!/usr/bin/env python
"""Warn-only dead-code check for the pre-commit hook.

Runs ``vulture`` (min-confidence 80, matching the CI step) over the staged Python
files under the project's source path and ALWAYS exits 0, so findings surface
locally as WARNINGS at commit time instead of only showing up later in the CI
artifact.

Vulture has real false positives on dynamic-dispatch code (lazy-import proxies,
pydantic fields, pytest fixtures, tuple-unpacking placeholders) -- that's why this
is warn-only rather than a blocking gate, same posture as bandit here. Confirmed
false positives / documented no-ops are tracked once in each project's own
``scripts/vulture_whitelist.py`` instead of being re-reviewed on every run.

Shared across projects via the py-ci-shared package. Two settings scope it to the calling
project, each preferring a CLI flag over its env-var fallback (pre-commit's hook schema has
no ``env:`` key, so an env var would need an extra shell wrapper to set portably; a CLI arg
works directly in ``entry:``):
- ``--src-path`` / PY_CI_SHARED_SRC_PATH: source-path filter (e.g. "src/mlframe").
- ``--whitelist`` / PY_CI_SHARED_VULTURE_WHITELIST: path to the CALLING project's own
  whitelist file (whitelists are inherently project-specific -- each names that project's
  own dynamic-dispatch symbols -- so this is never shipped by py-ci-shared itself).
"""

from __future__ import annotations

import argparse
import os
import posixpath
import subprocess
import sys
from typing import Optional

#: vulture's own exit codes: 0 nothing found, 1 invalid input (unreadable file, syntax error), 2 bad command line,
#: 3 dead code found. Only 3 means "findings"; 1 and 2 mean the scan did not happen.
VULTURE_FOUND_DEAD_CODE = 3

_WARNING = (
    "\n[vulture-warn] The findings above are WARNINGS ONLY -- the commit is NOT "
    "blocked. Some are false positives on dynamic-dispatch code (lazy-import "
    "proxies, pydantic fields, pytest fixtures) -- use judgement before deleting."
)


def _normalise(path: str) -> str:
    cleaned = posixpath.normpath(path.replace("\\", "/"))
    return "" if cleaned == "." else cleaned


def in_scope(path: str, src_path: str) -> bool:
    """Is *path* the source path or under it? Compared as normalised POSIX paths on a component boundary, so
    ``src\\mlframe`` matches ``src/mlframe/x.py`` and ``src/mlframe`` does not match ``src/mlframe_extra/x.py``."""
    src = _normalise(src_path)
    if os.path.isabs(path) and not posixpath.isabs(src):
        try:
            path = os.path.relpath(path)
        except ValueError:  # another drive on Windows
            return False
    candidate = _normalise(path)
    return not src or candidate == src or candidate.startswith(src + "/")


def parse_args(argv: "list[str]") -> "tuple[argparse.Namespace, list[str]]":
    """``(options, remaining arguments)``. ``--src-path X`` and ``--src-path=X`` both work; a flag with no value is a
    usage error (exit 2) naming the flag, not an IndexError."""
    parser = argparse.ArgumentParser(prog="py_ci_shared.vulture_warn", allow_abbrev=False)
    parser.add_argument("--src-path", default=None, help="only files under this path are scanned (env: PY_CI_SHARED_SRC_PATH)")
    parser.add_argument("--whitelist", default=None, help="the calling project's vulture whitelist (env: PY_CI_SHARED_VULTURE_WHITELIST)")
    return parser.parse_known_args(argv)


def main(argv: Optional["list[str]"] = None) -> int:
    """Run vulture over the in-scope files and ALWAYS return 0 (warn-only); a bad command line exits 2."""
    options, rest = parse_args(list(sys.argv[1:] if argv is None else argv))
    src_path = options.src_path or os.environ.get("PY_CI_SHARED_SRC_PATH")
    if not src_path:
        print("[vulture-warn] no --src-path given and PY_CI_SHARED_SRC_PATH env var not set -- skipping (nothing to scope the scan to)", file=sys.stderr)
        return 0
    whitelist = options.whitelist or os.environ.get("PY_CI_SHARED_VULTURE_WHITELIST")

    files = [a for a in rest if a.endswith(".py") and in_scope(a, src_path)]
    if not files:
        return 0

    cmd = [sys.executable, "-m", "vulture", "--min-confidence", "80", *files]
    if whitelist:
        cmd.append(whitelist)
    try:
        returncode = subprocess.run(cmd).returncode
    except Exception as exc:  # vulture missing / any error -> warn, never block
        print(f"[vulture-warn] skipped: {exc}", file=sys.stderr)
        return 0

    if returncode == VULTURE_FOUND_DEAD_CODE:
        print(_WARNING, file=sys.stderr)
    elif returncode != 0:
        print(
            f"[vulture-warn] vulture did not scan (exit {returncode}: not installed, an unreadable file, or a bad argument) -- "
            "these files were NOT checked for dead code; see the output above.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
