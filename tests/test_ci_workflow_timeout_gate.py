"""Unit tests for the CI timeout-minutes gate check. Real scratch workflow YAML files, same
no-mocking convention as this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.ci_workflow_timeout_gate import assert_all_jobs_have_timeout, find_jobs_missing_timeout


def _write_workflow(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "ci.yml"
    p.write_text(body, encoding="utf-8")
    return p


_ONE_JOB_WITH_TIMEOUT = """\
jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Run tests
        run: pytest
"""

_ONE_JOB_NO_TIMEOUT = """\
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Run tests
        run: pytest
"""

_TWO_JOBS_ONE_MISSING = """\
jobs:
  lint:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Run ruff
        run: ruff check .

  publish:
    runs-on: ubuntu-latest
    steps:
      - name: Publish
        run: twine upload dist/*
"""

_TIMEOUT_IN_A_STEP_NOT_JOB_LEVEL_STILL_COUNTS = """\
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Run tests
        run: pytest
        timeout-minutes: 15
"""

_REUSABLE_WORKFLOW_CALL_JOB_NO_TIMEOUT = """\
jobs:
  lint:
    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@abc123
    with:
      ignore: "C901"
"""

_MIX_REUSABLE_AND_REGULAR_ONLY_REGULAR_FLAGGED = """\
jobs:
  lint:
    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@abc123
    with:
      ignore: "C901"

  build:
    runs-on: ubuntu-latest
    steps:
      - name: Build
        run: python -m build
"""


class TestFindJobsMissingTimeout:
    def test_job_with_timeout_not_flagged(self, tmp_path):
        p = _write_workflow(tmp_path, _ONE_JOB_WITH_TIMEOUT)
        assert find_jobs_missing_timeout(p) == []

    def test_job_without_timeout_flagged(self, tmp_path):
        p = _write_workflow(tmp_path, _ONE_JOB_NO_TIMEOUT)
        assert find_jobs_missing_timeout(p) == ["build"]

    def test_two_jobs_only_missing_one_flagged(self, tmp_path):
        p = _write_workflow(tmp_path, _TWO_JOBS_ONE_MISSING)
        assert find_jobs_missing_timeout(p) == ["publish"]

    def test_step_level_timeout_still_satisfies_the_check(self, tmp_path):
        """The scanner doesn't require job-level placement -- any timeout-minutes: line anywhere
        in the job's block (including a per-step one) counts, matching a real human's reading."""
        p = _write_workflow(tmp_path, _TIMEOUT_IN_A_STEP_NOT_JOB_LEVEL_STILL_COUNTS)
        assert find_jobs_missing_timeout(p) == []

    def test_no_jobs_section_returns_empty(self, tmp_path):
        p = _write_workflow(tmp_path, "name: CI\non: [push]\n")
        assert find_jobs_missing_timeout(p) == []

    def test_reusable_workflow_call_job_exempt_even_without_timeout(self, tmp_path):
        """GitHub's schema forbids timeout-minutes on a uses:-based job entirely (confirmed via
        actionlint) -- must never be flagged, regardless of whether it has a timeout."""
        p = _write_workflow(tmp_path, _REUSABLE_WORKFLOW_CALL_JOB_NO_TIMEOUT)
        assert find_jobs_missing_timeout(p) == []

    def test_mix_of_reusable_and_regular_jobs_only_flags_the_regular_one(self, tmp_path):
        p = _write_workflow(tmp_path, _MIX_REUSABLE_AND_REGULAR_ONLY_REGULAR_FLAGGED)
        assert find_jobs_missing_timeout(p) == ["build"]


class TestAssertAllJobsHaveTimeout:
    def test_passes_when_all_jobs_have_timeout(self, tmp_path):
        p = _write_workflow(tmp_path, _ONE_JOB_WITH_TIMEOUT)
        assert_all_jobs_have_timeout(p)  # no raise

    def test_fails_when_a_job_is_missing_timeout(self, tmp_path):
        p = _write_workflow(tmp_path, _TWO_JOBS_ONE_MISSING)
        with pytest.raises(pytest.fail.Exception, match="publish"):
            assert_all_jobs_have_timeout(p)

    def test_exempt_jobs_suppresses_the_failure(self, tmp_path):
        p = _write_workflow(tmp_path, _TWO_JOBS_ONE_MISSING)
        assert_all_jobs_have_timeout(p, exempt_jobs=frozenset({"publish"}))  # no raise
