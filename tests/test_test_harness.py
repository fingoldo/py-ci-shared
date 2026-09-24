"""The shared test harness in conftest.py: git isolation, repo factory, timeouts."""

from __future__ import annotations

import os

from conftest import GIT_ISOLATION, run_git


def test_git_sees_no_global_or_system_config():
    assert os.environ["GIT_CONFIG_GLOBAL"] == os.devnull and os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
    assert GIT_ISOLATION["GIT_CONFIG_KEY_0"] == "commit.gpgsign"


def test_the_repo_factory_commits_unsigned_with_its_own_identity(git_repo):
    repo = git_repo({"a.txt": "x\n"}, tags=("v0.1.0",))
    assert run_git(repo, "config", "--get", "commit.gpgsign").strip() == "false"
    assert run_git(repo, "log", "-1", "--format=%an <%ae> %G?").strip() == "py-ci-shared tests <tests@py-ci-shared.invalid> N"
    assert run_git(repo, "tag", "--list").split() == ["v0.1.0"]
    assert run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "master"


def test_the_write_fixture_writes_lf(tmp_path, write):
    path = write(tmp_path / "d" / "f.txt", "a\nb\n")
    assert path.read_bytes() == b"a\nb\n"


def test_every_test_has_a_deadline(pytestconfig):
    assert pytestconfig.getini("timeout") == "300", "[tool.pytest.ini_options] timeout keeps a hung subprocess from holding CI"


# Test modules of gates still being written in parallel; each is expected to drop its own copy when it lands.
_SRC_INSERT_PENDING: frozenset[str] = frozenset()


def test_src_is_importable_without_a_per_file_path_insert(pytestconfig):
    import re
    from pathlib import Path

    assert "src" in pytestconfig.getini("pythonpath") or any(Path(p).name == "src" for p in pytestconfig.getini("pythonpath"))
    pattern = re.compile(r'^sys\.path\.insert\(0, str\(.*/ "src"\)\)$', re.M)
    tests_dir = Path(__file__).resolve().parent
    carrying = {p.name for p in tests_dir.glob("test_*.py") if pattern.search(p.read_text(encoding="utf-8"))}
    assert (
        carrying <= _SRC_INSERT_PENDING
    ), f"conftest.py and pythonpath already put src on sys.path; remove the insert from {sorted(carrying - _SRC_INSERT_PENDING)}"
