"""Unit tests for the CI test-dir reachability check, generalized from
glossum_backend_scripts's tests/test_meta/test_all_test_dirs_reachable_in_ci.py.
Real scratch repo layouts + workflow YAML, same no-mocking convention as
this package's other tests.
"""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.ci_test_dir_reachability import find_unreachable_test_subdirs


def _make_repo(tmp_path: Path, subdirs: list[str], workflow_body: str) -> Path:
    tests_dir = tmp_path / "tests"
    for d in subdirs:
        (tests_dir / d).mkdir(parents=True)
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text(workflow_body, encoding="utf-8")
    return tmp_path


class TestFindUnreachableTestSubdirs:
    def test_directly_invoked_subdir_is_reachable(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_core"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/test_api tests/test_core\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == []

    def test_subdir_never_named_and_not_covered_by_bare_invoke_is_unreachable(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_orphan"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/test_api\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["test_orphan"]

    def test_bare_tests_invoke_covers_every_subdir_not_ignored(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_core"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == []

    def test_ignored_subdir_under_bare_invoke_is_unreachable(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_cli"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/ --ignore=tests/test_cli\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["test_cli"]

    def test_ignoring_one_file_inside_a_subdir_does_not_mark_the_whole_subdir_unreachable(self, tmp_path):
        """Regression: glossum's CI ignores exactly one file inside
        tests/test_smoke (a paid-API live-provider test), not the whole
        directory -- a substring match on the ignore path (e.g.
        `tests/test_smoke` in `tests/test_smoke/test_llm_providers_live.py`)
        wrongly treated that as excluding the entire subdir."""
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_smoke"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/ --ignore=tests/test_smoke/test_llm_providers_live.py\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == []

    def test_ignore_glob_covering_whole_subdir_is_treated_as_unreachable(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_cli"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/ --ignore-glob=tests/test_cli/*\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["test_cli"]

    def test_intentionally_unreached_whitelist_is_respected(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["test_api", "live"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/test_api\n",
        )
        assert (
            find_unreachable_test_subdirs(
                repo,
                repo / ".github" / "workflows",
                intentionally_unreached={"live"},
            )
            == []
        )

    def test_no_workflows_dir_flags_everything_not_whitelisted(self, tmp_path):
        repo = _make_repo(tmp_path, ["test_api"], "jobs: {}\n")
        missing_workflows_dir = repo / ".github" / "does_not_exist"
        assert find_unreachable_test_subdirs(repo, missing_workflows_dir) == ["test_api"]


class TestPathlessPytestInvocation:
    """A CI job that runs `pytest` with no positional path collects from
    testpaths/rootdir, so it reaches every tests/ subdir. Before this was
    recognized, the check reported EVERY subdir as unreachable for the most
    common real-world CI shape (`pytest -m "not gpu" --cov=... --durations=200`),
    which made it unusable as a blocking gate."""

    def test_pathless_invoke_with_value_taking_short_flag_covers_every_subdir(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_meta"],
            'jobs:\n  unit:\n    steps:\n      - run: pytest -m "not gpu" --cov=src/pkg --cov-fail-under=82 --durations=200\n',
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == []

    def test_pathless_invoke_still_honours_an_ignore(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_cli"],
            'jobs:\n  unit:\n    steps:\n      - run: pytest -m "not gpu" --ignore=tests/test_cli\n',
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["test_cli"]

    def test_line_continuation_does_not_fake_a_pathless_invoke(self, tmp_path):
        r"""`pytest -p no:randomly \` + newline + `tests/test_api` names a path; the
        first physical line alone looks pathless and must not be read that way."""
        repo = _make_repo(
            tmp_path,
            ["test_api", "test_orphan"],
            "jobs:\n  unit:\n    steps:\n      - run: |\n          pytest -p no:randomly \\n            tests/test_api\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["test_orphan"]


def test_a_plugin_flag_value_is_not_mistaken_for_a_test_path(tmp_path):
    """`--splits 10 --group 1` is a PATHLESS run; the bare numbers are flag values, not targets.

    Enumerating every plugin's value-taking flags is a losing game, so the positional test is shape-based: a
    pytest target carries a separator, a `.py`, or a `::`. Before this, pytest-split's numeric values read as
    positional paths, which made a pathless invocation look targeted and reported every tests/ subdir in a
    consuming repo as unreached -- 20 of them, all of which CI did in fact collect.
    """
    from py_ci_shared.ci_test_dir_reachability import find_unreachable_test_subdirs

    (tmp_path / "tests" / "alpha").mkdir(parents=True)
    (tmp_path / "tests" / "alpha" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        'jobs:\n  t:\n    steps:\n      - run: |\n          pytest -m "not slow" --splits 10 --group 1 -n auto\n',
        encoding="utf-8",
    )

    assert find_unreachable_test_subdirs(repo_root=tmp_path, workflows_dir=workflows) == []


class TestInvocationParsing:
    def test_pip_install_pytest_is_not_a_pathless_run(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["gpu", "other"],
            "jobs:\n  t:\n    steps:\n      - run: pip install pytest pytest-cov\n      - run: pytest tests/other\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["gpu"]

    def test_a_commented_out_run_is_not_an_invocation(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["gpu", "other"],
            "jobs:\n  t:\n    steps:\n      # - run: pytest -x\n      - run: pytest tests/other  # was: pytest -x\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["gpu"]

    def test_a_sibling_with_a_longer_name_does_not_reach_the_shorter_one(self, tmp_path):
        repo = _make_repo(tmp_path, ["unit", "unit_slow"], "jobs:\n  t:\n    steps:\n      - run: pytest tests/unit_slow\n")
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["unit"]
        repo2 = _make_repo(tmp_path / "b", ["unit", "unit_slow"], "jobs:\n  t:\n    steps:\n      - run: pytest tests/unit/test_a.py::t tests/unit_slow\n")
        assert find_unreachable_test_subdirs(repo2, repo2 / ".github" / "workflows") == []

    def test_the_space_form_of_ignore_is_an_ignore_not_a_target(self, tmp_path):
        repo = _make_repo(tmp_path, ["api", "gpu"], "jobs:\n  t:\n    steps:\n      - run: pytest tests/ --ignore tests/gpu\n")
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["gpu"]

    def test_an_ignore_in_one_job_does_not_hide_a_subdir_another_job_runs(self, tmp_path):
        body = "jobs:\n  a:\n    steps:\n      - run: pytest tests/ --ignore=tests/gpu\n  b:\n    steps:\n      - run: pytest tests/\n"
        repo = _make_repo(tmp_path, ["api", "gpu"], body)
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == []
        body_one_job = "jobs:\n  a:\n    steps:\n      - run: pytest tests/ --ignore=tests/gpu\n      - run: pytest tests/api\n"
        repo2 = _make_repo(tmp_path / "b", ["api", "gpu"], body_one_job)
        assert find_unreachable_test_subdirs(repo2, repo2 / ".github" / "workflows") == ["gpu"]

    def test_the_next_line_is_not_read_as_pytest_arguments(self, tmp_path):
        repo = _make_repo(
            tmp_path,
            ["api", "scripts_check"],
            "jobs:\n  t:\n    steps:\n      - run: |\n          pytest tests/api\n          python tests/scripts_check/x.py\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["scripts_check"]

    def test_wrapped_commands_are_invocations(self):
        from py_ci_shared.ci_test_dir_reachability import pytest_invocations

        text = (
            "      - run: python -m pytest tests/a\n"
            "      - run: CI=1 uv run pytest tests/b && echo done\n"
            "      - run: coverage run --parallel -m pytest tests/c\n"
            "      - name: Run pytest tests/d\n"
        )
        assert [inv.paths for inv in pytest_invocations(text)] == [["tests/a"], ["tests/b"], ["tests/c"]]

    def test_a_bom_workflow_is_read(self, tmp_path):
        repo = _make_repo(tmp_path, ["api", "gpu"], "x")
        (repo / ".github" / "workflows" / "ci.yml").write_bytes(b"\xef\xbb\xbf- run: pytest tests/api\n")
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["gpu"]
