"""Shared check: every job in a CI workflow file declares ``timeout-minutes``.

A GitHub Actions job with no ``timeout-minutes`` inherits the platform default of 360 minutes (6
hours) -- a hung step (a flaky network call with no client-side timeout, an interactive prompt
nobody answers, a deadlocked test) burns CI minutes for up to 6 hours before GitHub itself kills
it, instead of failing fast with a clear timeout signal. Confirmed as a real, unaddressed gap in
two high-stakes jobs of a downstream project's own CI (build in ci.yml, publish in release.yml --
the release job being the highest-stakes job in that repo) during the 2026-07-21 audit round.

A ``uses:``-based reusable-workflow-call job is EXEMPT: GitHub Actions' schema only allows
``name``/``uses``/``with``/``secrets``/``needs``/``if``/``permissions`` on that job shape --
``timeout-minutes`` is a real YAML syntax error there (confirmed via ``actionlint``, which caught
an earlier, incorrect version of this scanner that flagged 9 such jobs and would have broken CI
had the "fix" landed). Whatever timeout that job effectively runs under is bounded by the CALLED
workflow's own job(s), not settable from the caller side.

Only a ``timeout-minutes`` (and a ``uses``) at the JOB's own key level counts: a step-level
``timeout-minutes`` bounds that one step, not the job, and a step's ``uses: actions/checkout@v4`` does
not make the job a reusable-workflow call. A workflow in which no job is found fails the assert (a
``jobs:`` the scanner cannot read is not a workflow with every timeout set).

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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._core import read_source

_JOBS_HEADER_RE = re.compile(r"^jobs:\s*(?:#.*)?$")
# A job id key at the first indentation level under `jobs:` (e.g. `  build:`), NOT a nested key
# (those sit at a deeper indent). Captures the indent width so job-block boundaries can be
# detected purely from indentation, without needing a real YAML parser.
_JOB_HEADER_RE = re.compile(r"^(?P<indent>[ ]+)(?P<job_id>[\w-]+|\"[^\"]+\"|'[^']+'):\s*(?:#.*)?$")
_TIMEOUT_MINUTES_RE = re.compile(r"^\s*timeout-minutes:\s*\S")
# A `uses:` key directly inside the job's own block (one level deeper than the job header) marks
# it as a reusable-workflow-call job -- GitHub's schema forbids `timeout-minutes` there entirely.
_USES_KEY_RE = re.compile(r"^\s*uses:\s*\S")
_JOB_KEY_RE = re.compile(r"^(?P<indent>[ ]*)(?P<key>[\w-]+)\s*:(?:\s|$)")


@dataclass(frozen=True)
class JobTimeout:
    """One job under ``jobs:``: whether it sets a job-level ``timeout-minutes`` or is a reusable-workflow call."""

    job_id: str
    line: int
    has_timeout: bool
    reusable: bool


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def collect_jobs(workflow_path: Path) -> list[JobTimeout]:
    """Every job under ``jobs:`` with its job-level ``timeout-minutes``/``uses`` status.

    Indentation-driven: the FIRST job header's indent sets the job level; the first key line inside a job
    sets that job's key level; only keys at the key level are the job's own. A job's block ends at the next
    line at or above the job level, so a top-level section after ``jobs:`` is never part of the last job.
    """
    lines = read_source(workflow_path).splitlines()
    jobs: list[JobTimeout] = []
    in_jobs = False
    job_indent: Optional[int] = None
    current: Optional[dict] = None

    def close() -> None:
        nonlocal current
        if current is not None:
            jobs.append(JobTimeout(current["id"], current["line"], current["timeout"], current["uses"]))
        current = None

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent(line)
        if indent == 0:
            close()
            in_jobs = bool(_JOBS_HEADER_RE.match(line))
            job_indent = None
            continue
        if not in_jobs:
            continue
        if job_indent is None:
            job_indent = indent
        if indent <= job_indent:
            close()
            m = _JOB_HEADER_RE.match(line)
            if m and indent == job_indent:
                current = {"id": m.group("job_id").strip("\"'"), "line": lineno, "timeout": False, "uses": False, "key_indent": None}
            continue
        if current is None:
            continue
        if current["key_indent"] is None:
            current["key_indent"] = indent
        if indent != current["key_indent"] or stripped.startswith("-"):
            continue
        if _TIMEOUT_MINUTES_RE.match(line):
            current["timeout"] = True
        elif _USES_KEY_RE.match(line):
            current["uses"] = True
    close()
    return jobs


def find_jobs_missing_timeout(workflow_path: Path) -> list[str]:
    """Return the job id of every NON-reusable-workflow-call job under ``jobs:`` in
    ``workflow_path`` that has no job-level ``timeout-minutes:`` key. A job with a job-level
    ``uses:`` key (a reusable-workflow-call job) is skipped entirely -- ``timeout-minutes`` is not
    a valid key there per GitHub's own schema. A step-level ``timeout-minutes`` or ``uses`` is a
    property of that step and counts for neither.
    """
    return [job.job_id for job in collect_jobs(workflow_path) if not job.reusable and not job.has_timeout]


def assert_all_jobs_have_timeout(workflow_path: Path, exempt_jobs: "frozenset[str] | None" = None) -> None:
    """Fail if any job in ``workflow_path`` has no ``timeout-minutes:``. ``exempt_jobs`` allowlists
    job ids that deliberately rely on the platform default (rare -- prefer setting an explicit,
    generous timeout over exempting a job outright, so the intent is visible in the YAML itself).
    """
    import pytest

    jobs = collect_jobs(workflow_path)
    if not jobs:
        pytest.fail(
            f"no job found under a top-level `jobs:` in {workflow_path} -- either the file is not a workflow or its "
            "layout is one this scanner cannot read; either way nothing was checked"
        )
    missing = [job.job_id for job in jobs if not job.reusable and not job.has_timeout]
    if exempt_jobs:
        missing = [j for j in missing if j not in exempt_jobs]
    if missing:
        pytest.fail(
            f"{len(missing)} job(s) in {workflow_path} have no `timeout-minutes:` -- a hung step "
            f"burns CI minutes for up to GitHub's 360-minute platform default instead of failing "
            f"fast. Add an explicit `timeout-minutes:` (or add the job id to `exempt_jobs` with a "
            f"reason, if the platform default is genuinely intended):\n  " + "\n  ".join(missing)
        )
