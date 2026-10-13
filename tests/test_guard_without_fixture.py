"""guard_without_fixture: a tool/check* guard that no test file names is found; a named one, or a non-guard, is not."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.guard_without_fixture import assert_every_guard_named_by_a_test, find_guards_without_fixture


def _tree(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return root


def test_a_guard_no_test_names_is_found(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/check_unused.py", "print('ok')\n")
    _tree(tmp_path, "tests/test_other.py", "def test_x():\n    assert True\n")
    assert list(find_guards_without_fixture(tmp_path)) == ["tool/check_unused.py"]


def test_a_guard_named_by_a_test_is_not_found(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/check_docker.sh", "exit 0\n")
    _tree(tmp_path, "tests/test_docker.py", 'subprocess.run(["bash", "tool/check_docker.sh"])\n')
    assert find_guards_without_fixture(tmp_path) == {}


def test_a_longer_name_does_not_count_as_naming_the_guard(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/check_x.py", "print('ok')\n")
    _tree(tmp_path, "tests/test_y.py", 'NAME = "my_check_x.py"\n')
    assert list(find_guards_without_fixture(tmp_path)) == ["tool/check_x.py"]


def test_only_direct_tool_check_files_are_guards(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/meta/check_deep.py", "print('ok')\n")
    _tree(tmp_path, "tool/helper.py", "print('ok')\n")
    assert find_guards_without_fixture(tmp_path) == {}


def test_a_recorded_guard_passes_and_a_new_unnamed_one_fails(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/check_a.py", "print('ok')\n")
    _tree(tmp_path, "tests/test_stub.py", "def test_x():\n    pass\n")
    with pytest.raises(AssertionError):
        assert_every_guard_named_by_a_test(tmp_path)
    _tree(tmp_path, "tool/meta/baselines/guard_without_fixture.json", '{"accepted": {"tool/check_a.py": "fixture is planned"}}')
    assert_every_guard_named_by_a_test(tmp_path)
    _tree(tmp_path, "tool/check_b.sh", "exit 0\n")
    with pytest.raises(AssertionError):
        assert_every_guard_named_by_a_test(tmp_path)


def test_a_guard_named_only_by_a_workflow_is_not_found(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/check_release.sh", "exit 0\n")
    _tree(tmp_path, ".github/workflows/ci.yml", "steps:\n  - run: bash tool/check_release.sh\n")
    assert find_guards_without_fixture(tmp_path) == {}


def test_a_guard_named_only_by_a_git_hook_is_not_found(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/check_lint.py", "print('ok')\n")
    _tree(tmp_path, ".githooks/pre-push", "python tool/check_lint.py\n")
    assert find_guards_without_fixture(tmp_path) == {}


def test_a_guard_named_nowhere_is_found_even_with_workflows_present(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/check_orphan.py", "print('ok')\n")
    _tree(tmp_path, ".github/workflows/ci.yml", "steps:\n  - run: echo ok\n")
    assert list(find_guards_without_fixture(tmp_path)) == ["tool/check_orphan.py"]
