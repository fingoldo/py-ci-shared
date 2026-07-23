"""Unit tests for the CI test-dir reachability check, generalized from
glossum_backend_scripts's tests/test_meta/test_all_test_dirs_reachable_in_ci.py.
Real scratch repo layouts + workflow YAML, same no-mocking convention as
this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
            tmp_path, ["test_api", "test_core"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/test_api tests/test_core\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == []

    def test_subdir_never_named_and_not_covered_by_bare_invoke_is_unreachable(self, tmp_path):
        repo = _make_repo(
            tmp_path, ["test_api", "test_orphan"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/test_api\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["test_orphan"]

    def test_bare_tests_invoke_covers_every_subdir_not_ignored(self, tmp_path):
        repo = _make_repo(
            tmp_path, ["test_api", "test_core"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == []

    def test_ignored_subdir_under_bare_invoke_is_unreachable(self, tmp_path):
        repo = _make_repo(
            tmp_path, ["test_api", "test_cli"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/ --ignore=tests/test_cli\n",
        )
        assert find_unreachable_test_subdirs(repo, repo / ".github" / "workflows") == ["test_cli"]

    def test_intentionally_unreached_whitelist_is_respected(self, tmp_path):
        repo = _make_repo(
            tmp_path, ["test_api", "live"],
            "jobs:\n  unit:\n    steps:\n      - run: pytest tests/test_api\n",
        )
        assert find_unreachable_test_subdirs(
            repo, repo / ".github" / "workflows", intentionally_unreached={"live"},
        ) == []

    def test_no_workflows_dir_flags_everything_not_whitelisted(self, tmp_path):
        repo = _make_repo(tmp_path, ["test_api"], "jobs: {}\n")
        missing_workflows_dir = repo / ".github" / "does_not_exist"
        assert find_unreachable_test_subdirs(repo, missing_workflows_dir) == ["test_api"]
