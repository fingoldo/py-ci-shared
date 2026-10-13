"""git_child_env: a test that spawns git without scrubbing GIT_* is found; a scrubbed env, or no git spawn, is not."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.git_child_env import assert_no_new_unscrubbed_git_spawns, find_unscrubbed_git_spawns


def _tree(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return root


BAD_PY = 'import subprocess\n\ndef run(repo):\n    subprocess.run(["git", "commit", "-m", "x"], cwd=repo, check=True)\n'
BAD_PY_ENV = 'import os, subprocess\n\ndef run(repo):\n    subprocess.run(["git", "status"], cwd=repo, env={**os.environ, "LANG": "C"})\n'
GOOD_PY = 'import subprocess\nfrom py_ci_shared._core.git import git_env\n\ndef run(repo):\n    subprocess.run(["git", "status"], cwd=repo, env=git_env())\n'
GOOD_PY_LITERAL = (
    "import os, subprocess\n\ndef run(repo):\n"
    '    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}\n'
    '    subprocess.run(["git", "status"], cwd=repo, env=env)\n'
)
BAD_DART = "Future<void> run() async {\n  await Process.run('git', ['status'], workingDirectory: dir);\n}\n"
GOOD_DART = "Future<void> run() async {\n  await Process.run('git', ['status'], environment: _cleanEnv(), includeParentEnvironment: false);\n}\n"
# Scrubbed, but Dart merges it into the parent's environment, which still holds the hook's GIT_DIR.
MERGED_DART = "Future<void> run() async {\n  await Process.run('git', ['status'], environment: _cleanEnv());\n}\n"
# A map literal with no nested call: the body used to end at the closing quote of 'git', so this read as no environment.
LITERAL_DART = (
    "void run() {\n  Process.runSync(\n    'git',\n    ['status'],\n"
    "    environment: {for (final e in env.entries) if (!e.key.startsWith('GIT_')) e.key: e.value},\n"
    "    includeParentEnvironment: false,\n  );\n}\n"
)


@pytest.mark.parametrize("body", [BAD_PY, BAD_PY_ENV], ids=["no-env", "env-without-GIT"])
def test_python_git_spawn_without_scrubbed_env_is_found(tmp_path: Path, body: str) -> None:
    found = find_unscrubbed_git_spawns(_tree(tmp_path, "tests/test_repo.py", body))
    assert len(found) == 1
    assert next(iter(found)).startswith("tests/test_repo.py: subprocess.run(")


@pytest.mark.parametrize("body", [GOOD_PY, GOOD_PY_LITERAL], ids=["helper", "literal"])
def test_python_git_spawn_with_scrubbed_env_is_not_found(tmp_path: Path, body: str) -> None:
    assert find_unscrubbed_git_spawns(_tree(tmp_path, "tests/test_repo.py", body)) == {}


def test_dart_git_spawn_without_environment_is_found_and_with_it_is_not(tmp_path: Path) -> None:
    assert len(find_unscrubbed_git_spawns(_tree(tmp_path, "test/repo_test.dart", BAD_DART))) == 1
    assert find_unscrubbed_git_spawns(_tree(tmp_path, "test/repo_test.dart", GOOD_DART)) == {}


def test_a_git_spawn_outside_a_test_file_is_not_found(tmp_path: Path) -> None:
    assert find_unscrubbed_git_spawns(_tree(tmp_path, "src/release.py", BAD_PY)) == {}


def test_a_recorded_spawn_passes_and_a_new_one_fails(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/test_repo.py", BAD_PY)
    with pytest.raises(AssertionError):
        assert_no_new_unscrubbed_git_spawns(tmp_path)
    key = next(iter(find_unscrubbed_git_spawns(tmp_path)))
    _tree(tmp_path, "tool/meta/baselines/git_child_env.json", json.dumps({"accepted": {key: "runs in a tmp repo with no hooks"}}))
    assert_no_new_unscrubbed_git_spawns(tmp_path)


def test_dart_scrubbed_environment_merged_into_the_parent_is_found(tmp_path: Path) -> None:
    (description,) = find_unscrubbed_git_spawns(_tree(tmp_path, "test/repo_test.dart", MERGED_DART)).values()
    assert "includeParentEnvironment: false" in description


def test_dart_scrubbed_map_literal_without_a_nested_call_is_not_found(tmp_path: Path) -> None:
    assert find_unscrubbed_git_spawns(_tree(tmp_path, "test/repo_test.dart", LITERAL_DART)) == {}
    unscrubbed = LITERAL_DART.replace("if (!e.key.startsWith('GIT_')) ", "")
    (description,) = find_unscrubbed_git_spawns(_tree(tmp_path, "test/repo_test.dart", unscrubbed)).values()
    assert "neither drops GIT_*" in description
