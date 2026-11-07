"""Tests for py_ci_shared.tool_config_env_vars_set_in_every_tool_job."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.tool_config_env_vars_set_in_every_tool_job import (
    RULE_JOB,
    RULE_TEST_JOB,
    assert_tool_config_env_vars_set_in_every_tool_job,
    find_tool_config_env_vars_set_in_every_tool_job,
)

BOM = b"\xef\xbb\xbf"

PYPROJECT = '[project]\nname = "x"\nversion = "0"\n\n[tool.ruff]\nextend = "$PY_CI_SHARED_DIR/configs/ruff-base.toml"\n'

LLM_BENCH = """name: ci
on: [push]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - name: Resolve PY_CI_SHARED_DIR
        run: echo "PY_CI_SHARED_DIR=$d" >> "$GITHUB_ENV"
      - run: ruff check src
  meta:
    runs-on: ubuntu-latest
    steps:
      - run: pytest tests/test_meta/ --no-cov
"""

SHELL_OUT = 'import subprocess\n\ndef test_tz():\n    subprocess.run(["python", "-m", "ruff", "check", "."])\n'


def _find(root, **kw):
    return find_tool_config_env_vars_set_in_every_tool_job(root, use_git=False, **kw)


def _repo(tmp_path: Path, workflow: str = LLM_BENCH, pyproject: str = PYPROJECT, tests: "dict[str, str] | None" = None) -> Path:
    (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    for rel, text in (tests if tests is not None else {"tests/test_meta/test_tz.py": SHELL_OUT}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _hits(tmp_path: Path, **kw):
    return [(f.rule, f.message.split("`")[1]) for f in _find(_repo(tmp_path, **kw))]


def test_llm_bench_meta_job_that_shells_out_to_ruff_without_the_variable(tmp_path):
    """llm_bench d5990de: the lint job resolved PY_CI_SHARED_DIR, the meta-tests job (timezone_honest runs ruff) did not."""
    assert _hits(tmp_path) == [(RULE_TEST_JOB, "meta")]
    finding = _find(tmp_path)[0]
    assert "PY_CI_SHARED_DIR" in finding.message and finding.line == 10


def test_negative_control_the_job_that_resolves_the_variable_is_clean(tmp_path):
    fixed = LLM_BENCH.replace(
        "      - run: pytest tests/test_meta/", '      - run: echo "PY_CI_SHARED_DIR=$d" >> "$GITHUB_ENV"\n      - run: pytest tests/test_meta/'
    )
    assert _hits(tmp_path, workflow=fixed) == []


def test_direct_invocation_without_the_variable(tmp_path):
    wf = LLM_BENCH.replace('        run: echo "PY_CI_SHARED_DIR=$d" >> "$GITHUB_ENV"\n', "        run: echo hi\n")
    assert (RULE_JOB, "lint") in _hits(tmp_path, workflow=wf)


def test_job_env_workflow_env_step_env_and_inline_assignment_all_define_it(tmp_path):
    for variant, wf in {
        "job": LLM_BENCH.replace("  meta:\n    runs-on: ubuntu-latest\n", "  meta:\n    runs-on: ubuntu-latest\n    env:\n      PY_CI_SHARED_DIR: /x\n"),
        "workflow": LLM_BENCH.replace("jobs:", "env:\n  PY_CI_SHARED_DIR: /x\njobs:", 1),
        "step": LLM_BENCH.replace(
            "      - run: pytest tests/test_meta/ --no-cov", "      - run: pytest tests/test_meta/ --no-cov\n        env:\n          PY_CI_SHARED_DIR: /x"
        ),
        "inline": LLM_BENCH.replace("pytest tests/test_meta/ --no-cov", "PY_CI_SHARED_DIR=/x pytest tests/test_meta/ --no-cov"),
    }.items():
        assert _hits(tmp_path / variant, workflow=wf) == [], variant


def test_a_variable_written_after_the_tool_step_does_not_count(tmp_path):
    wf = LLM_BENCH.replace(
        "      - run: pytest tests/test_meta/ --no-cov",
        '      - run: pytest tests/test_meta/ --no-cov\n      - run: echo "PY_CI_SHARED_DIR=/x" >> "$GITHUB_ENV"',
    )
    assert _hits(tmp_path, workflow=wf) == [(RULE_TEST_JOB, "meta")]


def test_a_pytest_job_whose_paths_miss_the_shell_out_test_is_clean(tmp_path):
    wf = LLM_BENCH.replace("pytest tests/test_meta/", "pytest tests/unit/")
    assert _hits(tmp_path, workflow=wf, tests={"tests/test_meta/test_tz.py": SHELL_OUT, "tests/unit/test_a.py": "x = 1\n"}) == []


def test_pip_install_of_the_tool_and_echo_lines_are_not_invocations(tmp_path):
    wf = "name: ci\non: [push]\njobs:\n  setup:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pip install ruff==0.1\n      - run: echo run ruff later\n"
    assert _hits(tmp_path, workflow=wf, tests={}) == []


def test_runner_variables_and_pyproject_without_references_are_ignored(tmp_path):
    assert _hits(tmp_path, pyproject=PYPROJECT.replace("$PY_CI_SHARED_DIR", "$GITHUB_WORKSPACE")) == []
    assert _hits(tmp_path / "plain", pyproject="[tool.ruff]\nline-length = 100\n") == []


def test_allow_jobs_skips_a_named_job(tmp_path):
    root = _repo(tmp_path)
    assert _find(root, allow_jobs=["ci.yml::meta"]) == []


def test_a_bom_prefixed_workflow_is_read_like_the_plain_one(tmp_path):
    root = _repo(tmp_path)
    wf = root / ".github" / "workflows" / "ci.yml"
    wf.write_bytes(BOM + wf.read_bytes())
    assert [(f.rule) for f in _find(root)] == [RULE_TEST_JOB]


def test_an_unparsable_workflow_fails_by_name_unless_allowed(tmp_path):
    root = _repo(tmp_path, workflow="jobs: [\n  - run: ruff\n")
    with pytest.raises(UnparsedFilesError, match=r"ci\.yml"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_no_workflow_fails_the_floor(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)


def test_assert_names_the_fix_and_passes_a_clean_repo(tmp_path):
    with pytest.raises(AssertionError, match=r"GITHUB_ENV"):
        assert_tool_config_env_vars_set_in_every_tool_job(_repo(tmp_path / "bad"), use_git=False)
    assert_tool_config_env_vars_set_in_every_tool_job(_repo(tmp_path / "good", pyproject="[tool.ruff]\nline-length = 100\n"), use_git=False)


def test_a_baseline_accepts_known_findings_and_fails_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _repo(tmp_path / "r")
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_tool_config_env_vars_set_in_every_tool_job(root, baseline_path=baseline, refresh=True, use_git=False)
    assert_tool_config_env_vars_set_in_every_tool_job(root, baseline_path=baseline, use_git=False)
    other = root / ".github" / "workflows" / "other.yml"
    other.write_text(LLM_BENCH.replace('        run: echo "PY_CI_SHARED_DIR=$d" >> "$GITHUB_ENV"\n', "        run: echo hi\n"), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_tool_config_env_vars_set_in_every_tool_job(root, baseline_path=baseline, use_git=False)
