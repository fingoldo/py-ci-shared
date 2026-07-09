#!/usr/bin/env python
"""Warn-only security-lint check for the pre-commit hook.

Runs ``bandit -ll`` (medium+ severity, matching the CI step) over the staged Python
files under the project's source path and ALWAYS exits 0, so findings surface
locally as WARNINGS at commit time instead of only showing up later in the CI
artifact.

Never blocks the commit and never rewrites anything -- bandit has no auto-fix mode.

Shared across projects via the py-ci-shared package. The source-path filter is set
via the PY_CI_SHARED_SRC_PATH env var (e.g. "src/mlframe" or "src/pyutilz"), since
which files count as "production code" (vs. tests/scripts/docs) is project-specific.
"""
import os
import subprocess
import sys

_src_path = os.environ.get("PY_CI_SHARED_SRC_PATH")
if not _src_path:
    print("[bandit-warn] PY_CI_SHARED_SRC_PATH env var not set -- skipping (nothing to scope the scan to)", file=sys.stderr)
    sys.exit(0)

_files = [a for a in sys.argv[1:] if a.endswith(".py") and _src_path in a.replace("\\", "/")]
if not _files:
    sys.exit(0)

_cmd = [sys.executable, "-m", "bandit", "-ll", *_files]
try:
    _warned = subprocess.run(_cmd).returncode != 0
except Exception as _e:  # bandit missing / any error -> warn, never block
    print(f"[bandit-warn] skipped: {_e}", file=sys.stderr)
    sys.exit(0)

if _warned:
    print(
        "\n[bandit-warn] The findings above are WARNINGS ONLY -- the commit is NOT "
        "blocked. The same scan (bandit -ll) also runs in CI as a non-blocking "
        "step whose full report is uploaded as an artifact.",
        file=sys.stderr,
    )
sys.exit(0)
