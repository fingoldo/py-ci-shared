"""Tests for py_ci_shared.setup_env: what each platform writes, re-runs after the clone moves, awkward paths, and
the console-script entry point. Nothing touches the real home directory, registry or launchd."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

from py_ci_shared import setup_env

_TRICKY = '/home/u/a&b "q" $HOME/it\'s <x>'


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    return h


@pytest.fixture
def runs(monkeypatch):
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(setup_env.subprocess, "run", _run)
    return calls


class _Raw:
    """A clone path whose str() is exactly *value* on every OS (a Path would turn / into a backslash on Windows)."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


def _main_on(monkeypatch, system: str, value: str) -> int:
    monkeypatch.setattr(setup_env.platform, "system", lambda: system)
    monkeypatch.setattr(setup_env, "_repo_root", lambda: _Raw(value))
    return setup_env.main([])


class TestShellProfile:
    def test_first_run_appends_a_tagged_quoted_export(self, home, runs, monkeypatch):
        (home / ".bashrc").write_text("alias ll='ls -l'\n", encoding="utf-8")
        assert _main_on(monkeypatch, "Linux", "/opt/pcs") == 0
        text = (home / ".bashrc").read_text(encoding="utf-8")
        assert text == "alias ll='ls -l'\n\n# added by py_ci_shared.setup_env\nexport PY_CI_SHARED_DIR=/opt/pcs\n"

    def test_a_rerun_after_moving_the_clone_replaces_the_stale_line(self, home, runs, monkeypatch):
        (home / ".zshrc").write_text("x=1\n", encoding="utf-8")
        _main_on(monkeypatch, "Darwin", "/old/place")
        _main_on(monkeypatch, "Darwin", "/new/place")
        text = (home / ".zshrc").read_text(encoding="utf-8")
        assert "/old/place" not in text and text.count("export PY_CI_SHARED_DIR=/new/place") == 1
        before = text
        _main_on(monkeypatch, "Darwin", "/new/place")
        assert (home / ".zshrc").read_text(encoding="utf-8") == before

    def test_a_user_written_export_is_left_alone(self, home, runs, monkeypatch, capsys):
        (home / ".bashrc").write_text('export PY_CI_SHARED_DIR="$HOME/pcs"\n', encoding="utf-8")
        _main_on(monkeypatch, "Linux", "/opt/pcs")
        assert (home / ".bashrc").read_text(encoding="utf-8") == 'export PY_CI_SHARED_DIR="$HOME/pcs"\n'
        assert "left unchanged" in capsys.readouterr().err

    def test_the_value_is_shell_quoted(self, home, runs, monkeypatch):
        (home / ".bashrc").write_text("", encoding="utf-8")
        _main_on(monkeypatch, "Linux", _TRICKY)
        line = next(ln for ln in (home / ".bashrc").read_text(encoding="utf-8").splitlines() if ln.startswith("export"))
        if sys.platform != "win32":
            out = subprocess.run(["sh", "-c", line + '; printf %s "$PY_CI_SHARED_DIR"'], capture_output=True, text=True, check=True).stdout
            assert out == _TRICKY
        assert line == "export PY_CI_SHARED_DIR=" + setup_env.shlex.quote(_TRICKY)

    def test_a_profile_that_is_not_utf8_is_updated_byte_for_byte(self, home, runs, monkeypatch):
        (home / ".bashrc").write_bytes(b"# caf\xe9 latin-1\n")
        assert _main_on(monkeypatch, "Linux", "/opt/pcs") == 0
        raw = (home / ".bashrc").read_bytes()
        assert raw.startswith(b"# caf\xe9 latin-1\n") and raw.endswith(b"export PY_CI_SHARED_DIR=/opt/pcs\n")


class TestPlatforms:
    def test_linux_environment_d_escapes_dollar_and_backslash(self, home, runs, monkeypatch):
        _main_on(monkeypatch, "Linux", "/a/$x\\y")
        conf = (home / ".config" / "environment.d" / "50-py-ci-shared.conf").read_text(encoding="utf-8")
        assert conf == "PY_CI_SHARED_DIR=/a/\\$x\\\\y\n"

    def test_macos_plist_is_valid_xml_with_the_exact_value(self, home, runs, monkeypatch):
        assert _main_on(monkeypatch, "Darwin", _TRICKY) == 0
        plist = home / "Library" / "LaunchAgents" / "dev.py-ci-shared.setenv.plist"
        root = ElementTree.fromstring(plist.read_text(encoding="utf-8").split("\n", 2)[2])
        assert [s.text for s in root.iter("string")][-2:] == ["PY_CI_SHARED_DIR", _TRICKY]
        assert ["launchctl", "setenv", "PY_CI_SHARED_DIR", _TRICKY] in runs
        assert runs[-1][:3] == ["launchctl", "load", "-w"]

    def test_windows_uses_setx_with_the_value_as_one_argument(self, home, runs, monkeypatch):
        assert _main_on(monkeypatch, "Windows", _TRICKY) == 0
        assert runs == [["setx", "PY_CI_SHARED_DIR", _TRICKY]]

    def test_an_unknown_platform_and_a_missing_clone_exit_one(self, home, runs, monkeypatch, capsys):
        assert _main_on(monkeypatch, "Plan9", "/x") == 1
        monkeypatch.setattr(setup_env, "_repo_root", lambda: (_ for _ in ()).throw(FileNotFoundError("no configs")))
        assert setup_env.main([]) == 1
        assert "no configs" in capsys.readouterr().err

    def test_a_failing_command_exits_one(self, home, monkeypatch, capsys):
        def _fail(args, **kwargs):
            raise subprocess.CalledProcessError(1, args, "", "denied")

        monkeypatch.setattr(setup_env.subprocess, "run", _fail)
        assert _main_on(monkeypatch, "Windows", "/x") == 1
        assert "Failed to persist" in capsys.readouterr().err


def test_the_console_script_entry_point_resolves_to_main():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'py-ci-setup-env = "py_ci_shared.setup_env:main"' in text
    assert callable(setup_env.main)


def test_repo_root_is_this_clone():
    assert (setup_env._repo_root() / "configs" / "ruff-base.toml").is_file()
