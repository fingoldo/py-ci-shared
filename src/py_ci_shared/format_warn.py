#!/usr/bin/env python
"""Warn-only formatting / lint check for the pre-commit hook.

Runs ``ruff format --check --diff`` and ``ruff check`` (lint) over the staged
Python files in CHECK mode. It NEVER rewrites files and ALWAYS exits 0, so it
surfaces formatting / lint differences as WARNINGS without auto-reformatting and
without blocking the commit.

Auto-reformat is intentionally disabled (project policy): the formatters churn
large diffs that collide with concurrent work. To actually apply changes, run
``ruff format`` / ``ruff check --fix`` manually.

Shared across projects via the py-ci-shared package.
"""
import subprocess
import sys

# ``--select UP`` re-enables py<3.10-parity UP codes on the CLI regardless of what ruff-base.toml
# ignores (--select REPLACES the effective rule set, same caveat as the blocking gate's own
# --ignore-only rule -- see ruff-blocking.yml). This list must stay a SUBSET of ruff-base.toml's
# own `ignore` list -- tests/test_format_warn_ignore_subset.py pins that relationship so drift
# between the two fails a test instead of silently nagging about deliberately-kept idioms.
_UP_IGNORE = ["UP006", "UP007", "UP037", "UP045", "UP031"]

def main() -> None:
    files = [a for a in sys.argv[1:] if a.endswith(".py")]
    if not files:
        return

    warned = False
    for cmd in (
        # taste only: formatting + pycodestyle/naming/pyupgrade/import-order. Real
        # problems (pyflakes F + bugbear B) are a SEPARATE blocking hook, not here.
        [sys.executable, "-m", "ruff", "format", "--check", "--diff", *files],
        [sys.executable, "-m", "ruff", "check", "--select", "E,W,N,UP,I", "--ignore", ",".join(_UP_IGNORE), *files],
    ):
        try:
            if subprocess.run(cmd).returncode != 0:
                warned = True
        except Exception as e:  # noqa: PERF203 -- one bad command must not skip checking the rest; ruff missing / any error -> warn, never block
            print(f"[format-warn] skipped {' '.join(cmd[2:4])}: {e}", file=sys.stderr)

    if warned:
        print(
            "\n[format-warn] The diffs / lint issues above are WARNINGS ONLY -- nothing "
            "was rewritten and the commit is NOT blocked. Run 'ruff format' / "
            "'ruff check --fix' manually to apply.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
    sys.exit(0)
