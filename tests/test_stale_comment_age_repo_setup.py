"""The stale-comment fixture sets the commit identity from the environment, not with `git config` spawns (audit 48-F15)."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "test_stale_comment_age.py"
_SPEC = importlib.util.spec_from_file_location("stale_comment_age_under_test", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
fixture_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixture_module)


def test_building_a_repo_runs_no_git_config(tmp_path: Path, monkeypatch) -> None:
    """Negative control (48-F15): the old fixture ran two `git config` processes per repository."""
    argvs: list[list[str]] = []
    real = subprocess.run

    def recording(argv, *args, **kwargs):
        argvs.append(list(argv))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(fixture_module.subprocess, "run", recording)
    fixture_module._repo(tmp_path / "r", "lib/a.dart", "void a() {}\n")
    assert [argv for argv in argvs if "config" in argv] == []
    log = subprocess.run(["git", "-C", str(tmp_path / "r"), "log", "--format=%an <%ae>"], capture_output=True, text=True, check=True).stdout
    assert log.strip() == "py-ci-shared tests <tests@py-ci-shared.invalid>"  # the session identity from tests/conftest.py GIT_ISOLATION
