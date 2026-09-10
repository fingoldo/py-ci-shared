"""A check that could not run must say so, never report clean.

Both cases were found on this package's own CI, where they read as healthy:

* timezone_honest ran ``python -m ruff`` in a test job with no ruff. That exits 1 ("No module named
  ruff") with nothing on stdout, exit 1 means "findings", none parsed, so the result was an empty set.
* guard_population replayed guards with ``bash``, which on the Windows runner could not start, and
  skipped every guard whose command failed -- so each one passed without examining anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared import guard_population, timezone_honest


def test_a_ruff_that_is_not_installed_is_an_error_not_a_clean_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "job.py").write_text("x = 1\n", encoding="utf-8")

    def no_ruff(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="No module named ruff")

    monkeypatch.setattr(timezone_honest.subprocess, "run", no_ruff)

    with pytest.raises(RuntimeError, match=r"checked nothing.*No module named ruff"):
        timezone_honest.dtz_findings(tmp_path, scan_paths=["."])


def _guard(tmp_path: Path, body: str) -> tuple[Path, Path]:
    tool = tmp_path / "tool"
    tool.mkdir()
    (tool / "check-x.sh").write_text(body, encoding="utf-8")
    (tmp_path / "lib").mkdir()
    return tool, tmp_path


def test_a_bash_that_cannot_start_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool, root = _guard(tmp_path, "#!/bin/sh\ngrep -rl 'Anything' lib\n")
    monkeypatch.setattr(guard_population, "_bash", lambda: str(tmp_path / "no-such-bash.exe"))

    problems = guard_population.find_guards_with_empty_population(tool, root)

    assert len(problems) == 1 and "could not be run" in problems[0]


def test_a_selection_command_that_fails_is_reported(tmp_path: Path) -> None:
    tool, root = _guard(tmp_path, "#!/bin/sh\ngrep -rl 'Anything' no_such_dir\n")

    problems = guard_population.find_guards_with_empty_population(tool, root)

    assert len(problems) == 1 and "exited 2" in problems[0]


@pytest.mark.skipif(sys.platform != "win32", reason="the WSL launcher only exists on Windows")
def test_on_windows_the_git_bash_is_chosen_over_the_wsl_launcher() -> None:
    assert "system32" not in guard_population._bash().lower()
