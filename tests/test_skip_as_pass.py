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


def test_a_warning_that_mentions_skipped_is_not_a_skip_marker(tmp_path: Path) -> None:
    _tree(tmp_path, "tool/hooks.sh", 'echo "warning: hook checks were skipped for this push"\nexit 0\n')
    assert find_skip_as_pass(tmp_path) == {}


def test_a_same_indent_return_ends_the_block_so_a_later_exit_is_not_its_exit(tmp_path: Path) -> None:
    body = 'import sys\n\n\ndef f():\n    print("SKIPPED: no docker")\n    return\n    sys.exit(0)\n'
    assert find_skip_as_pass(_tree(tmp_path, "scripts/run_docker.py", body)) == {}


def test_a_deeper_return_does_not_end_the_block_so_the_exit_at_the_print_indent_is_found(tmp_path: Path) -> None:
    body = 'import sys\n\n\ndef f(y):\n    print("SKIPPED: no docker")\n    if y:\n        return\n    sys.exit(0)\n'
    assert list(find_skip_as_pass(_tree(tmp_path, "scripts/run_docker.py", body))) == ['scripts/run_docker.py: print("SKIPPED: no docker")']


# ---- audit 41-F12: a block parser instead of a line shape ----------------------------------------------------------------------------------

PROGRESS_AFTER_CHECKS = 'set -e\npytest tests\necho "SKIPPED stale cache"\nexit 0\n'
TWO_STEPS = """jobs:
  a:
    steps:
      - name: optional
        run: |
          echo "SKIPPED optional upload"
      - name: done
        run: |
          pytest
          exit 0
"""


def test_a_skipped_progress_message_after_real_checks_is_not_a_guard(tmp_path: Path) -> None:
    assert find_skip_as_pass(_tree(tmp_path, "tool/c.sh", PROGRESS_AFTER_CHECKS)) == {}


@pytest.mark.parametrize(
    "body",
    [
        'set -e\ncd "$(dirname "$0")"\necho "SKIPPED: no docker"\nexit 0\n',
        'pytest\n[ -z "$X" ] || echo "SKIPPED stale cache"\nexit 0\n',
        'pytest\nif [ -z "$X" ]; then\n  echo "SKIPPED stale cache"\n  exit 0\nfi\n',
        'pytest\ncommand -v docker >/dev/null || { echo "SKIPPED: no docker"; exit 0; }\n',
        'pytest\ncase "$X" in\n  a) echo "SKIPPED a"; exit 0 ;;\nesac\n',
        'check() {\n  echo "SKIPPED in function"\n  exit 0\n}\n',
    ],
)
def test_negative_controls_a_real_guard_is_still_found(tmp_path: Path, body: str) -> None:
    assert len(find_skip_as_pass(_tree(tmp_path, "tool/c.sh", body))) == 1


def test_a_function_defined_earlier_is_not_a_check_that_ran(tmp_path: Path) -> None:
    body = 'helper() {\n  pytest\n}\necho "SKIPPED: no docker"\nexit 0\n'
    assert len(find_skip_as_pass(_tree(tmp_path, "tool/c.sh", body))) == 1


def test_a_heredoc_body_is_not_shell(tmp_path: Path) -> None:
    body = 'cat <<EOF\nexit 0\nEOF\necho "SKIPPED: x"\n'
    assert find_skip_as_pass(_tree(tmp_path, "tool/c.sh", body)) == {}


def test_a_workflow_step_is_its_own_script(tmp_path: Path) -> None:
    assert find_skip_as_pass(_tree(tmp_path, ".github/workflows/w.yml", TWO_STEPS)) == {}


def test_negative_control_a_workflow_guard_step_is_found_with_its_file_line(tmp_path: Path) -> None:
    text = TWO_STEPS.replace('echo "SKIPPED optional upload"', 'echo "SKIPPED: no token"\n          exit 0')
    found = find_skip_as_pass(_tree(tmp_path, ".github/workflows/w.yml", text))
    assert list(found) == ['.github/workflows/w.yml: echo "SKIPPED: no token"']
    assert "line 6 " in next(iter(found.values()))


def test_a_python_step_in_a_workflow_uses_the_python_block_rule(tmp_path: Path) -> None:
    text = "steps:\n  - shell: python\n    run: |\n      import sys\n      print('SKIPPED: no token')\n      sys.exit(0)\n"
    assert len(find_skip_as_pass(_tree(tmp_path, ".github/workflows/w.yml", text))) == 1
