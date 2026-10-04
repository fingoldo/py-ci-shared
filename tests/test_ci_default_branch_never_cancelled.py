"""Tests for py_ci_shared.ci_default_branch_never_cancelled."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import Baseline, EmptyScanError, UnparsedFilesError
from py_ci_shared.ci_default_branch_never_cancelled import assert_ci_default_branch_never_cancelled, find_ci_default_branch_never_cancelled

BOM = b"\xef\xbb\xbf"
PUSH_MASTER = "on:\n  push:\n    branches: [master]\n  pull_request:\n"
JOBS = "jobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"


def _wf(tmp_path: Path, text: str, name: str = "ci.yml", *, raw: bytes = b"") -> Path:
    path = tmp_path / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw or text.encode("utf-8"))
    return tmp_path


def _concurrency(cancel: str, group: str = "ci-${{ github.ref }}", on: str = PUSH_MASTER, comment: str = "") -> str:
    return f"name: ci\n{on}concurrency:\n  group: {group}\n  cancel-in-progress: {cancel}{comment}\n{JOBS}"


def test_reports_the_seeded_violation_with_the_line_of_cancel_in_progress(tmp_path):
    root = _wf(tmp_path, _concurrency("true"))
    found = find_ci_default_branch_never_cancelled(root)
    assert len(found) == 1
    (finding,) = found
    assert (finding.path, finding.line, finding.rule) == (".github/workflows/ci.yml", 8, "ci-default-branch-never-cancelled")
    assert "cancel-in-progress: true" in finding.message and "'master'" in finding.message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    root = _wf(tmp_path, _concurrency("${{ github.event_name == 'pull_request' }}"))
    assert find_ci_default_branch_never_cancelled(root) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = find_ci_default_branch_never_cancelled(_wf(tmp_path / "a", _concurrency("true")))
    bom = find_ci_default_branch_never_cancelled(_wf(tmp_path / "b", "", raw=BOM + _concurrency("true").encode("utf-8")))
    assert [(f.line, f.message) for f in bom] == [(f.line, f.message) for f in plain] and len(plain) == 1


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    _wf(tmp_path, _concurrency("true"))
    _wf(tmp_path, "name: x\non: [push\njobs: {\n", name="broken.yml")
    with pytest.raises(UnparsedFilesError, match=r"broken\.yml"):
        find_ci_default_branch_never_cancelled(tmp_path)
    assert len(find_ci_default_branch_never_cancelled(tmp_path, allow_unparsed=True)) == 1


def test_a_yaml_file_with_no_jobs_mapping_is_unreadable_not_clean(tmp_path):
    root = _wf(tmp_path, "name: just text\n")
    with pytest.raises(UnparsedFilesError, match="no `jobs:` mapping"):
        find_ci_default_branch_never_cancelled(root)


def test_an_empty_corpus_fails_the_floor_and_min_files_counts_readable_workflows(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    with pytest.raises(EmptyScanError, match="only 0 workflow"):
        find_ci_default_branch_never_cancelled(tmp_path)
    root = _wf(tmp_path, _concurrency("false"))
    assert find_ci_default_branch_never_cancelled(root, min_files=1) == []
    with pytest.raises(EmptyScanError, match="only 1 workflow"):
        find_ci_default_branch_never_cancelled(root, min_files=2)


@pytest.mark.parametrize(
    ("cancel", "reported"),
    [
        ("true", True),
        ("'true'", True),
        ("TRUE", True),
        ("false", False),
        ("${{ github.event_name == 'pull_request' }}", False),
        ("${{ github.event_name != 'push' }}", False),
        ("${{ github.ref != 'refs/heads/master' }}", False),
        ("${{ github.ref_name != 'master' }}", False),
        ("${{ github.ref != 'refs/heads/master' && github.event_name != 'push' }}", False),
        ("${{ github.event_name == 'pull_request' || github.event_name == 'push' }}", True),
        ("${{ github.ref == 'refs/heads/master' }}", True),
        ("${{ !startsWith(github.ref, 'refs/heads/release') }}", True),
        ("${{ startsWith(github.ref, 'refs/heads/master') }}", True),
        ("${{ github.head_ref != '' }}", False),
        ("${{ github.event.pull_request.number != null }}", False),
        ("${{ inputs.cancel }}", True),
        ("${{ vars.CANCEL_RUNS == 'true' }}", True),
        ("${{ github.event_name == 'pull_request' && inputs.cancel }}", False),
    ],
)
def test_the_expression_is_evaluated_for_a_push_to_the_default_branch(tmp_path, cancel, reported):
    found = find_ci_default_branch_never_cancelled(_wf(tmp_path, _concurrency(cancel)), default_branches=("master",))
    assert bool(found) is reported, cancel


def test_an_expression_this_check_cannot_know_says_so(tmp_path):
    (finding,) = find_ci_default_branch_never_cancelled(_wf(tmp_path, _concurrency("${{ inputs.cancel }}")))
    assert "cannot be proven false" in finding.message
    (known,) = find_ci_default_branch_never_cancelled(_wf(tmp_path / "k", _concurrency("true")))
    assert "is true" in known.message and "cannot be proven" not in known.message


@pytest.mark.parametrize(
    ("on", "reported"),
    [
        ("on: push\n", True),
        ("on: [push, pull_request]\n", True),
        ("on: [pull_request]\n", False),
        ("on: pull_request\n", False),
        ("on:\n  pull_request:\n  workflow_dispatch:\n", False),
        ("on:\n  push:\n", True),
        ("on:\n  push:\n    branches: [main]\n", True),
        ("on:\n  push:\n    branches: [develop]\n", False),
        ("on:\n  push:\n    branches: ['**']\n", True),
        ("on:\n  push:\n    branches: ['release/*']\n", False),
        ("on:\n  push:\n    branches: ['*', '!main', '!master']\n", False),
        ("on:\n  push:\n    branches-ignore: [master, main]\n", False),
        ("on:\n  push:\n    branches-ignore: [docs/**]\n", True),
        ("on:\n  push:\n    tags: ['v*']\n", False),
        ("on:\n  push:\n    branches: [master]\n    tags: ['v*']\n", True),
        ("on:\n  workflow_run:\n    workflows: [CI]\n    branches: [master]\n", False),
        ("on:\n  schedule:\n    - cron: '0 3 * * *'\n", False),
    ],
)
def test_only_a_push_that_can_reach_the_default_branch_puts_a_workflow_in_scope(tmp_path, on, reported):
    found = find_ci_default_branch_never_cancelled(_wf(tmp_path, _concurrency("true", on=on)))
    assert bool(found) is reported, on


def test_default_branches_are_a_parameter(tmp_path):
    root = _wf(tmp_path, _concurrency("true", on="on:\n  push:\n    branches: [trunk]\n"))
    assert find_ci_default_branch_never_cancelled(root) == []
    (found,) = find_ci_default_branch_never_cancelled(root, default_branches=("trunk",))
    assert "'trunk'" in found.message


def test_an_expression_that_protects_only_the_named_branch_is_judged_against_the_branch_the_workflow_names(tmp_path):
    """No push filter and the guard names master: main is not in play, so the master-only guard is accepted."""
    text = _concurrency("${{ github.ref != 'refs/heads/master' }}", on="on:\n  push:\n")
    assert find_ci_default_branch_never_cancelled(_wf(tmp_path, text)) == []
    unnamed = _concurrency("${{ github.ref != 'refs/heads/develop' }}", on="on:\n  push:\n")
    assert len(find_ci_default_branch_never_cancelled(_wf(tmp_path / "u", unnamed))) == 1


@pytest.mark.parametrize(
    ("group", "reported"),
    [
        ("${{ github.run_id }}", False),
        ("ci-${{ github.sha }}", False),
        ("${{ github.workflow }}-${{ github.head_ref || github.run_id }}", False),
        ("${{ github.workflow }}-${{ github.ref }}", True),
        ("${{ github.workflow }}-${{ github.head_ref || github.ref }}", True),
        ("ci", True),
        ("${{ inputs.group }}", True),
    ],
)
def test_a_group_that_is_unique_per_run_has_nothing_to_cancel(tmp_path, group, reported):
    found = find_ci_default_branch_never_cancelled(_wf(tmp_path, _concurrency("true", group=f"'{group}'")))
    assert bool(found) is reported, group


def test_a_job_level_concurrency_is_checked_and_named(tmp_path):
    text = (
        f"name: ci\n{PUSH_MASTER}jobs:\n  build:\n    runs-on: ubuntu-latest\n    concurrency:\n      group: g\n      cancel-in-progress: true\n"
        "    steps:\n      - run: echo hi\n  other:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
    )
    (finding,) = find_ci_default_branch_never_cancelled(_wf(tmp_path, text))
    assert "job 'build'" in finding.message and finding.line == 11


def test_a_job_that_never_runs_on_a_push_is_not_reported(tmp_path):
    text = (
        f"name: ci\n{PUSH_MASTER}jobs:\n  pr_only:\n    if: github.event_name == 'pull_request'\n    runs-on: ubuntu-latest\n"
        "    concurrency:\n      group: g\n      cancel-in-progress: true\n    steps:\n      - run: echo hi\n"
    )
    assert find_ci_default_branch_never_cancelled(_wf(tmp_path, text)) == []
    always = text.replace("github.event_name == 'pull_request'", "github.event_name == 'push'")
    assert len(find_ci_default_branch_never_cancelled(_wf(tmp_path / "b", always))) == 1


def test_the_suppression_comment_needs_a_reason_and_sits_on_the_line(tmp_path):
    ok = _wf(tmp_path / "a", _concurrency("true", comment="  # cancel-ok: docs preview, superseded runs are noise"))
    assert find_ci_default_branch_never_cancelled(ok) == []
    bare = _wf(tmp_path / "b", _concurrency("true", comment="  # cancel-ok:"))
    assert len(find_ci_default_branch_never_cancelled(bare)) == 1
    elsewhere = _concurrency("true").replace("name: ci\n", "name: ci  # cancel-ok: not on the right line\n")
    assert len(find_ci_default_branch_never_cancelled(_wf(tmp_path / "c", elsewhere))) == 1


def test_exclude_skips_a_workflow_by_file_name_or_fragment(tmp_path):
    _wf(tmp_path, _concurrency("true"), name="docs.yml")
    _wf(tmp_path, _concurrency("true"), name="ci.yml")
    both = find_ci_default_branch_never_cancelled(tmp_path)
    assert [f.path for f in both] == [".github/workflows/ci.yml", ".github/workflows/docs.yml"]
    only_ci = find_ci_default_branch_never_cancelled(tmp_path, exclude=("docs.yml",))
    assert [f.path for f in only_ci] == [".github/workflows/ci.yml"]


def test_yaml_extension_and_two_findings_are_sorted_by_path_and_line(tmp_path):
    _wf(tmp_path, _concurrency("true"), name="b.yaml")
    _wf(tmp_path, _concurrency("true"), name="a.yml")
    assert [f.path for f in find_ci_default_branch_never_cancelled(tmp_path)] == [".github/workflows/a.yml", ".github/workflows/b.yaml"]


def test_the_assert_names_the_workflow_and_a_baseline_accepts_it(tmp_path):
    root = _wf(tmp_path, _concurrency("true"))
    with pytest.raises(AssertionError, match=r"ci\.yml:8"):
        assert_ci_default_branch_never_cancelled(root)
    assert_ci_default_branch_never_cancelled(_wf(tmp_path / "ok", _concurrency("false")))
    baseline = tmp_path / "baseline.json"
    Baseline(baseline, gate="ci_default_branch_never_cancelled").enforce(find_ci_default_branch_never_cancelled(root), refresh=True, grow=True)
    assert_ci_default_branch_never_cancelled(root, baseline_path=baseline)
    _wf(root, _concurrency("true"), name="second.yml")
    with pytest.raises(pytest.fail.Exception, match=r"second\.yml"):
        assert_ci_default_branch_never_cancelled(root, baseline_path=baseline)
