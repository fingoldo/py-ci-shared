#!/usr/bin/env python
"""Warn-only advisory-taste check for the pre-commit hook.

Runs mccabe complexity (``ruff check --select C90``, matching the CI step) over the staged
Python files under the project's source path, plus ``pip-audit`` (dependency CVE feed) once per
invocation that has staged source files. ALWAYS exits 0 -- mirrors lint-advisory.yml's own posture
exactly: complexity is a design-taste signal rather than a correctness bug, and a CVE feed's fix
isn't always on your own timeline, so neither ever blocks a commit in CI either.

Never blocks the commit and never rewrites anything.

Shared across projects via the py-ci-shared package. The source-path filter comes from a
``--src-path`` CLI argument (``--src-path src/mlframe`` or ``--src-path=src/mlframe``) or, if absent,
the PY_CI_SHARED_SRC_PATH env var -- same convention and same segment-wise matching as bandit_warn.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from typing import Optional

from .bandit_warn import resolve_src_files


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    files = resolve_src_files(sys.argv[1:] if argv is None else argv, "advisory-warn")
    # A commit that stages no source file (README only, tests only) runs nothing: pip-audit goes to the network
    # and its result does not depend on the commit, so there is no reason to pay for it here.
    if not files:
        return 0

    # Windows' default console codepage (cp1251/cp437/...) can't encode characters some tools print
    # (pip-audit's own report formatter includes non-ASCII glyphs) -- forcing PYTHONIOENCODING=utf-8
    # on the child process avoids a crash that would otherwise look like this hook itself is broken.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    warned = False
    # Same select as lint-blocking's `--ignore C901`-excluded complexity plus lint-advisory's own
    # unrestricted "Run ruff (full - advisory)" step: full configured select/ignore from
    # pyproject.toml with NOTHING ignored, so anything C901 (blocking) hides also surfaces here.
    for cmd in (
        [sys.executable, "-m", "ruff", "check", *files],
        [sys.executable, "-m", "ruff", "check", "--select", "C90", "--statistics", *files],
    ):
        try:
            if subprocess.run(cmd, env=env, check=False).returncode != 0:
                warned = True
        except Exception as e:  # noqa: PERF203 -- one bad command must not skip checking the rest; ruff missing / any error -> warn, never block
            print(f"[advisory-warn] skipped {' '.join(cmd[2:4])}: {e}", file=sys.stderr)

    # pip-audit scans the whole installed environment, not per-file, so it runs once regardless of
    # which files changed (unlike the complexity check above) -- same as lint-advisory.yml's own
    # single per-run step.
    try:
        if subprocess.run([sys.executable, "-m", "pip_audit", "--desc"], env=env, check=False).returncode != 0:
            warned = True
    except Exception as e:  # pip-audit missing / any error -> warn, never block
        print(f"[advisory-warn] skipped pip-audit: {e}", file=sys.stderr)

    if warned:
        print(
            "\n[advisory-warn] The findings above are WARNINGS ONLY -- the commit is NOT blocked. "
            "Mirrors lint-advisory.yml's own continue-on-error posture in CI.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
