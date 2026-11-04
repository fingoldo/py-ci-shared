"""skip_as_pass: a SKIPPED print whose block exits 0 is found; a non-zero exit or an exit outside the block is not."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.skip_as_pass import assert_no_new_skip_as_pass, find_skip_as_pass

BAD_SHELL = """#!/bin/sh
if [ -z "$DOCKER" ]; then
  echo "SKIPPED: no docker"
  exit 0
fi
"""

BAD_SHELL_ONE_LINE = 'echo "SKIPPED: no docker"; exit 0\n'

BAD_PYTHON = """import sys
if not shutil_which("docker"):
    print("SKIPPED: no docker")
    sys.exit(0)
"""

GOOD_SHELL_EXIT_NONZERO = """#!/bin/sh
if [ -z "$DOCKER" ]; then
  echo "SKIPPED: no docker"
  exit 1
fi
"""

GOOD_SHELL_EXIT_AFTER_BLOCK = """#!/bin/sh
if [ -z "$DOCKER" ]; then
  echo "SKIPPED: no docker"
fi
exit 0
"""

GOOD_PYTHON_EXIT_NONZERO = """import sys
if not shutil_which("docker"):
    print("SKIPPED: no docker")
    sys.exit(2)
"""

GOOD_PYTHON_EXIT_OUTSIDE = """import sys
if not shutil_which("docker"):
    print("SKIPPED: no docker")
sys.exit(0)
"""


def _tree(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return root


@pytest.mark.parametrize("body", [BAD_SHELL, BAD_SHELL_ONE_LINE], ids=["block", "one-line"])
def test_shell_skip_then_exit_zero_is_found(tmp_path: Path, body: str) -> None:
    found = find_skip_as_pass(_tree(tmp_path, "tool/check_docker.sh", body))
    assert len(found) == 1
    assert next(iter(found)).startswith("tool/check_docker.sh: ")
    assert "SKIPPED" in next(iter(found))


def test_python_skip_then_sys_exit_zero_is_found(tmp_path: Path) -> None:
    found = find_skip_as_pass(_tree(tmp_path, "scripts/run_docker.py", BAD_PYTHON))
    assert list(found) == ['scripts/run_docker.py: print("SKIPPED: no docker")']


@pytest.mark.parametrize("body", [GOOD_SHELL_EXIT_NONZERO, GOOD_SHELL_EXIT_AFTER_BLOCK], ids=["nonzero", "exit-after-fi"])
def test_shell_skip_that_fails_or_exits_outside_its_block_is_not_found(tmp_path: Path, body: str) -> None:
    assert find_skip_as_pass(_tree(tmp_path, "tool/check_docker.sh", body)) == {}


@pytest.mark.parametrize("body", [GOOD_PYTHON_EXIT_NONZERO, GOOD_PYTHON_EXIT_OUTSIDE], ids=["nonzero", "exit-after-block"])
def test_python_skip_that_fails_or_exits_outside_its_block_is_not_found(tmp_path: Path, body: str) -> None:
    assert find_skip_as_pass(_tree(tmp_path, "tool/run_docker.py", body)) == {}


def test_only_guard_directories_are_scanned(tmp_path: Path) -> None:
    assert find_skip_as_pass(_tree(tmp_path, "src/app.sh", BAD_SHELL)) == {}


def test_a_recorded_skip_passes_and_a_new_one_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _tree(tmp_path, "tool/check_docker.sh", BAD_SHELL)
    with pytest.raises(AssertionError):
        assert_no_new_skip_as_pass(tmp_path)  # nothing recorded yet: the finding fails
    assert "1 new violation" in capsys.readouterr().err
    key = next(iter(find_skip_as_pass(tmp_path)))
    baseline = tmp_path / "tool" / "meta" / "baselines" / "skip_as_pass.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps({"accepted": {key: "docker is optional here; the report says so"}}), encoding="utf-8")
    assert_no_new_skip_as_pass(tmp_path)


@pytest.mark.parametrize(
    ("rel", "body"),
    [
        ("tool/a.sh", 'echo "SKIPPED: none"\nexit\n'),
        ("tool/a.sh", 'echo "skipped: none"; exit 0\n'),
        ("tool/a.py", 'import sys\nprint("SKIPPED")\nsys.exit()\n'),
        ("tool/a.py", 'print("SKIPPED")\nraise SystemExit(0)\n'),
        ("tools/a.sh", "echo SKIPPED; exit 0\n"),
        (".github/scripts/a.sh", "echo SKIPPED; exit 0\n"),
    ],
)
def test_more_skip_then_zero_spellings_are_reported_once(tmp_path, rel, body):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    assert len(find_skip_as_pass(tmp_path)) == 1


def test_a_nonzero_exit_after_skipped_is_still_clean(tmp_path):
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / "a.sh").write_text("echo SKIPPED\nexit 1\n", encoding="utf-8")
    (tmp_path / "tool" / "b.py").write_text('import sys\nprint("SKIPPED")\nsys.exit(2)\nraise SystemExit("boom")\n', encoding="utf-8")
    assert find_skip_as_pass(tmp_path) == {}
