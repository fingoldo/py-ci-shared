"""Shared check: every job in a CI workflow file declares ``timeout-minutes``.

A GitHub Actions job with no ``timeout-minutes`` inherits the platform default of 360 minutes (6
hours) -- a hung step (a flaky network call with no client-side timeout, an interactive prompt
nobody answers, a deadlocked test) burns CI minutes for up to 6 hours before GitHub itself kills
it, instead of failing fast with a clear timeout signal. Confirmed as a real, unaddressed gap in
two high-stakes jobs of a downstream project's own CI (build in ci.yml, publish in release.yml --
the release job being the highest-stakes job in that repo) during the 2026-07-21 audit round.

Deliberately line-based/regex, matching this package's established convention (``sql_lint.py``,
``ci_workflow_gate.py``, ``code_audit``'s scanners) -- not a YAML parser, no new dependency.

Usage (in a consuming repo's test suite)::

    from pathlib import Path
    from py_ci_shared.ci_workflow_timeout_gate import assert_all_jobs_have_timeout

    def test_ci_jobs_have_timeout_minutes():
        assert_all_jobs_have_timeout(
            Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml",
        )

Deliberately dependency-light: ``pytest`` is imported lazily inside the assert function, matching
this package's other modules.
"""

from __future__ import annotations

import re
from pathlib import Path

_JOBS_HEADER_RE = re.compile(r"^jobs:\s*$")
# A job id key at the first indentation level under `jobs:` (e.g. `  build:`), NOT a nested key
# (those sit at a deeper indent). Captures the indent width so job-block boundaries can be
# detected purely from indentation, without needing a real YAML parser.
_JOB_HEADER_RE = re.compile(r"^(?P<indent>[ ]+)(?P<job_id>[\w-]+):\s*(?:#.*)?$")
_TIMEOUT_MINUTES_RE = re.compile(r"^\s*timeout-minutes:\s*\S")


def find_jobs_missing_timeout(workflow_path: Path) -> list[str]:
    """Return the job id of every job under ``jobs:`` in ``workflow_path`` that has no
    ``timeout-minutes:`` key anywhere in its own block (before the next job at the same
    indentation level, or end of file).

    Indentation-driven block detection: the FIRST job header's indent width sets the expected
    indent for every subsequent job header; a line at that same indent (or shallower, i.e. back
    out of the ``jobs:`` section entirely) ends the current job's block.
    """
    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    in_jobs_section = False
    job_indent: "str | None" = None
    jobs: list[tuple[str, int]] = []  # (job_id, header_line_index)
    for i, line in enumerate(lines):
        if _JOBS_HEADER_RE.match(line):
            in_jobs_section = True
            continue
        if not in_jobs_section:
            continue
        if not line.strip():
            continue
        m = _JOB_HEADER_RE.match(line)
        if m:
            indent = m.group("indent")
            if job_indent is None:
                job_indent = indent
            if indent == job_indent:
                jobs.append((m.group("job_id"), i))
                continue
            # A deeper-indented `key:` line inside a job's own body (e.g. `  build:\n    steps:`)
            # -- not a new job, ignore.
            if len(indent) > len(job_indent):
                continue
        # A line back at or above the top level (no leading whitespace, or shallower than the
        # first job's indent) closes the `jobs:` section.
        if not line[:1].isspace():
            in_jobs_section = False

    missing: list[str] = []
    for idx, (job_id, start) in enumerate(jobs):
        end = jobs[idx + 1][1] if idx + 1 < len(jobs) else len(lines)
        block = lines[start:end]
        if not any(_TIMEOUT_MINUTES_RE.match(line) for line in block):
            missing.append(job_id)
    return missing


def assert_all_jobs_have_timeout(workflow_path: Path, exempt_jobs: "frozenset[str] | None" = None) -> None:
    """Fail if any job in ``workflow_path`` has no ``timeout-minutes:``. ``exempt_jobs`` allowlists
    job ids that deliberately rely on the platform default (rare -- prefer setting an explicit,
    generous timeout over exempting a job outright, so the intent is visible in the YAML itself).
    """
    import pytest

    missing = find_jobs_missing_timeout(workflow_path)
    if exempt_jobs:
        missing = [j for j in missing if j not in exempt_jobs]
    if missing:
        pytest.fail(
            f"{len(missing)} job(s) in {workflow_path} have no `timeout-minutes:` -- a hung step "
            f"burns CI minutes for up to GitHub's 360-minute platform default instead of failing "
            f"fast. Add an explicit `timeout-minutes:` (or add the job id to `exempt_jobs` with a "
            f"reason, if the platform default is genuinely intended):\n  " + "\n  ".join(missing)
        )
