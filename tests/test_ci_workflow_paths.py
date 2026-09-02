"""Unit tests for the CI workflow path/permissions/pin check. Real scratch files, same no-mocking
convention as this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.ci_workflow_paths import assert_workflow_paths_exist, find_missing_workflow_paths


def _repo(tmp_path: Path, workflow: str, *existing: str) -> tuple[Path, Path]:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(workflow, encoding="utf-8")
    for rel in existing:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    return wf_dir, tmp_path


class TestPaths:
    def test_missing_working_directory_is_flagged(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - working-directory: packages/gone\n")
        problems = find_missing_workflow_paths(wf, root)
        assert len(problems) == 1
        assert "packages/gone" in problems[0]

    def test_existing_working_directory_passes(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - working-directory: tool\n", "tool/x.sh")
        assert find_missing_workflow_paths(wf, root) == []

    def test_missing_run_script_is_flagged(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - run: python tool/check-gone.py\n")
        problems = find_missing_workflow_paths(wf, root)
        assert len(problems) == 1
        assert "check-gone.py" in problems[0]

    def test_existing_run_script_passes(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - run: python tool/ok.py\n", "tool/ok.py")
        assert find_missing_workflow_paths(wf, root) == []

    def test_script_resolved_against_a_cd_in_the_same_command(self, tmp_path):
        wf, root = _repo(
            tmp_path,
            "jobs:\n  a:\n    steps:\n      - run: (cd e2e && node probe.js http://x 3)\n",
            "e2e/probe.js",
        )
        assert find_missing_workflow_paths(wf, root) == []

    def test_script_resolved_against_the_step_working_directory(self, tmp_path):
        wf, root = _repo(
            tmp_path,
            "jobs:\n  a:\n    steps:\n      - working-directory: e2e\n        run: node probe.js\n",
            "e2e/probe.js",
        )
        assert find_missing_workflow_paths(wf, root) == []

    def test_expression_valued_path_is_not_guessed_at(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - working-directory: ${{ matrix.dir }}\n")
        assert find_missing_workflow_paths(wf, root) == []

    def test_commented_line_is_ignored(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      # - run: python tool/gone.py\n")
        assert find_missing_workflow_paths(wf, root) == []

    def test_empty_workflow_dir_reports_examining_nothing(self, tmp_path):
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        problems = find_missing_workflow_paths(wf, tmp_path)
        assert len(problems) == 1
        assert "examined nothing" in problems[0]


class TestPermissionsAndPins:
    def test_missing_permissions_flagged_only_when_required(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - run: echo hi\n")
        assert find_missing_workflow_paths(wf, root) == []
        problems = find_missing_workflow_paths(wf, root, require_permissions=True)
        assert len(problems) == 1
        assert "permissions" in problems[0]

    def test_declared_permissions_pass(self, tmp_path):
        wf, root = _repo(tmp_path, "permissions:\n  contents: read\njobs:\n  a:\n    steps:\n      - run: echo hi\n")
        assert find_missing_workflow_paths(wf, root, require_permissions=True) == []

    def test_tag_pinned_third_party_action_flagged(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n")
        problems = find_missing_workflow_paths(wf, root, require_sha_pins=True)
        assert len(problems) == 1
        assert "actions/checkout@v4" in problems[0]

    def test_sha_pinned_action_passes(self, tmp_path):
        sha = "a" * 40
        wf, root = _repo(tmp_path, f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{sha}\n")
        assert find_missing_workflow_paths(wf, root, require_sha_pins=True) == []

    def test_first_party_owner_is_exempt(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - uses: fingoldo/py-ci-shared/.github/workflows/x.yml@master\n")
        assert find_missing_workflow_paths(wf, root, require_sha_pins=True, first_party_owners=["fingoldo"]) == []

    def test_local_action_path_is_exempt(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - uses: ./.github/actions/setup\n")
        assert find_missing_workflow_paths(wf, root, require_sha_pins=True) == []


class TestAssert:
    def test_assert_passes(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - run: echo hi\n")
        assert_workflow_paths_exist(wf, root)

    def test_assert_fails_naming_the_path(self, tmp_path):
        wf, root = _repo(tmp_path, "jobs:\n  a:\n    steps:\n      - run: sh tool/nope.sh\n")
        with pytest.raises(pytest.fail.Exception, match="nope.sh"):
            assert_workflow_paths_exist(wf, root)
