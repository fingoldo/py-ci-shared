#!/usr/bin/env python
"""Warn-only advisory-taste check for the pre-commit hook.

Runs mccabe complexity (``ruff check --select C90``, matching the CI step) over the staged
Python files under the project's source path, plus ``pip-audit`` (dependency CVE feed) once per
invocation. ALWAYS exits 0 -- mirrors lint-advisory.yml's own posture exactly: complexity is a
design-taste signal rather than a correctness bug, and a CVE feed's fix isn't always on your own
timeline, so neither ever blocks a commit in CI either.

Never blocks the commit and never rewrites anything.

Shared across projects via the py-ci-shared package. The source-path filter comes from a
``--src-path`` CLI argument (e.g. ``--src-path src/mlframe``) or, if absent, the
PY_CI_SHARED_SRC_PATH env var -- same convention as bandit_warn.py / vulture_warn.py.
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
    print("[advisory-warn] no --src-path given and PY_CI_SHARED_SRC_PATH env var not set -- skipping (nothing to scope the scan to)", file=sys.stderr)
    sys.exit(0)

_files = [a for a in _argv if a.endswith(".py") and _src_path in a.replace("\\", "/")]

# Windows' default console codepage (cp1251/cp437/...) can't encode characters some tools print
# (pip-audit's own report formatter includes non-ASCII glyphs) -- forcing PYTHONIOENCODING=utf-8
# on the child process avoids a crash that would otherwise look like this hook itself is broken.
_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

_warned = False
if _files:
    # Same select as lint-blocking's `--ignore C901`-excluded complexity plus lint-advisory's own
    # unrestricted "Run ruff (full - advisory)" step: full configured select/ignore from
    # pyproject.toml with NOTHING ignored, so anything C901 (blocking) hides also surfaces here.
    for _cmd in (
        [sys.executable, "-m", "ruff", "check", *_files],
        [sys.executable, "-m", "ruff", "check", "--select", "C90", "--statistics", *_files],
    ):
        try:
            if subprocess.run(_cmd, env=_env).returncode != 0:
                _warned = True
        except Exception as e:  # noqa: PERF203 -- one bad command must not skip checking the rest; ruff missing / any error -> warn, never block
            print(f"[advisory-warn] skipped {' '.join(_cmd[2:4])}: {e}", file=sys.stderr)

# pip-audit scans the whole installed environment, not per-file, so it runs once regardless of
# which files changed (unlike the complexity check above) -- same as lint-advisory.yml's own
# single per-run step.
try:
    if subprocess.run([sys.executable, "-m", "pip_audit", "--desc"], env=_env).returncode != 0:
        _warned = True
except Exception as e:  # pip-audit missing / any error -> warn, never block
    print(f"[advisory-warn] skipped pip-audit: {e}", file=sys.stderr)

if _warned:
    print(
        "\n[advisory-warn] The findings above are WARNINGS ONLY -- the commit is NOT blocked. "
        "Mirrors lint-advisory.yml's own continue-on-error posture in CI.",
        file=sys.stderr,
    )
sys.exit(0)
