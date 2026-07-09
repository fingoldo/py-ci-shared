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
import os
import subprocess
import sys

_argv = sys.argv[1:]
_src_path = None
if "--src-path" in _argv:
    _idx = _argv.index("--src-path")
    _src_path = _argv[_idx + 1]
    _argv = _argv[:_idx] + _argv[_idx + 2 :]
if not _src_path:
    _src_path = os.environ.get("PY_CI_SHARED_SRC_PATH")
if not _src_path:
    print("[vulture-warn] no --src-path given and PY_CI_SHARED_SRC_PATH env var not set -- skipping (nothing to scope the scan to)", file=sys.stderr)
    sys.exit(0)

_whitelist = None
if "--whitelist" in _argv:
    _idx = _argv.index("--whitelist")
    _whitelist = _argv[_idx + 1]
    _argv = _argv[:_idx] + _argv[_idx + 2 :]
if not _whitelist:
    _whitelist = os.environ.get("PY_CI_SHARED_VULTURE_WHITELIST")

_files = [a for a in _argv if a.endswith(".py") and _src_path in a.replace("\\", "/")]
if not _files:
    sys.exit(0)

_cmd = [sys.executable, "-m", "vulture", "--min-confidence", "80", *_files]
if _whitelist:
    _cmd.append(_whitelist)
try:
    _warned = subprocess.run(_cmd).returncode != 0
except Exception as _e:  # vulture missing / any error -> warn, never block
    print(f"[vulture-warn] skipped: {_e}", file=sys.stderr)
    sys.exit(0)

if _warned:
    print(
        "\n[vulture-warn] The findings above are WARNINGS ONLY -- the commit is NOT "
        "blocked. Some are false positives on dynamic-dispatch code (lazy-import "
        "proxies, pydantic fields, pytest fixtures) -- use judgement before deleting.",
        file=sys.stderr,
    )
sys.exit(0)
