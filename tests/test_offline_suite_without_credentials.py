"""``offline_suite_without_credentials``: the plugin run for real in a child pytest over a scratch project with a ``.env``.

The scratch project ships its own tiny ``dotenv`` package, so the test does not depend on python-dotenv being installed
and never reads a real ``.env``. Every value is a fake this file invents.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
FAKE_DSN = "postgresql://reader:FAKEPW0123456789@db.example/jobs"

DOTENV_PACKAGE = """
import os

__all__ = ["load_dotenv", "dotenv_values"]


def dotenv_values(path=".env"):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, _, value = line.strip().partition("=")
            if key:
                out[key] = value
    return out


def load_dotenv(path=".env", override=False):
    for key, value in dotenv_values(path).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return True
"""

# The dashboard's 16b.1 shape: code on the tested path resolves a DSN from .env before the stub the test set up.
GENERATOR = """
import os

from dotenv import load_dotenv


def resolve_dsn():
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("no database DSN configured: set DATABASE_URL")
    return dsn


def generate_letter(job):
    resolve_dsn()
    return f"letter for {job}"
"""

# The timing test's shape: a module checks its proxy settings at import and exits when they are missing.
PROXY_MODULE = """
import os
import sys

import dotenv

dotenv.load_dotenv()
if not os.environ.get("UPWORK_PROXY_DSN"):
    sys.exit("proxy settings missing")
"""

TESTS = """
import pytest

import generator


def test_generator_with_a_stubbed_database():
    assert generator.generate_letter("j1") == "letter for j1"


@pytest.mark.real_database
def test_marked_real_database_keeps_the_env():
    assert generator.resolve_dsn().startswith("postgresql://")


def test_a_test_may_set_its_own(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:fake@h/db")
    assert generator.generate_letter("j2") == "letter for j2"
"""

TIMING_TEST = """
def test_timing():
    import proxy_module  # noqa: F401
"""


def _project(tmp_path: Path, ini: str = "", extra: dict[str, str] | None = None) -> Path:
    files = {
        "dotenv/__init__.py": DOTENV_PACKAGE,
        "generator.py": GENERATOR,
        "proxy_module.py": PROXY_MODULE,
        "test_letters.py": TESTS,
        ".env": f"DATABASE_URL={FAKE_DSN}\nUPWORK_PROXY_DSN=http://u:fake@proxy.example:1\n",
        "pytest.ini": f"[pytest]\nmarkers =\n    real_database: uses the database\n{ini}",
        **(extra or {}),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(text.encode())
    return tmp_path


def _child(repo: Path, *args: str, plugin_on: bool = True) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DATABASE_JOBSTRACKER_URL", "UPWORK_DB_DSN", "UPWORK_PROXY_DSN")}
    env.update({"PYTHONPATH": str(SRC) + os.pathsep + str(repo), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    plugins = ["-p", "py_ci_shared.offline_suite_without_credentials"] if plugin_on else []
    cmd = [sys.executable, "-m", "pytest", *plugins, "-p", "no:cacheprovider", "-rA", *args]
    proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=240)
    return proc.stdout + proc.stderr


def test_without_the_plugin_the_dotenv_makes_the_offline_test_pass(tmp_path):
    """The control: on a machine with a .env, the hidden dependency is invisible."""
    out = _child(_project(tmp_path), "test_letters.py", plugin_on=False)
    assert "3 passed" in out, out


def test_the_hidden_dsn_dependency_fails_and_the_report_names_the_variable_and_the_loader(tmp_path):
    out = _child(_project(tmp_path), "test_letters.py")
    assert "1 failed, 2 passed" in out, out
    assert "FAILED test_letters.py::test_generator_with_a_stubbed_database" in out
    assert "It read these blanked variables and found nothing: DATABASE_URL" in out
    assert "dotenv:load_dotenv (called from generator:" in out or "dotenv.main:load_dotenv" in out or "load_dotenv (called from generator" in out, out
    assert "@pytest.mark.real_database" in out


def test_an_import_time_exit_fails_on_every_machine_and_names_the_loader(tmp_path):
    repo = _project(tmp_path, extra={"test_timing.py": TIMING_TEST})
    assert "1 passed" in _child(repo, "test_timing.py", plugin_on=False)
    out = _child(repo, "test_timing.py")
    assert "1 failed" in out and "proxy settings missing" in out, out
    assert "UPWORK_PROXY_DSN" in out and "load_dotenv (called from proxy_module" in out, out


def test_the_variable_list_marker_and_project_loaders_are_configurable(tmp_path):
    loader = (
        "import os\n\n\ndef read_env_file():\n    return 'postgresql://u:fake@h/db'\n\n\ndef dsn():\n    return os.environ.get('MY_DB') or read_env_file()\n"
    )
    test = (
        "import pytest\nimport myconfig\n\n\ndef test_offline():\n    assert myconfig.dsn() is None\n\n\n"
        "@pytest.mark.live_db\ndef test_live():\n    assert myconfig.dsn() == 'kept'\n"
    )
    ini = "offline_credential_vars = MY_DB\noffline_credentials_marker = live_db\noffline_dotenv_loaders = myconfig:read_env_file\n"
    repo = _project(tmp_path, ini=ini, extra={"myconfig.py": loader, "test_cfg.py": test})
    env_ok = _child_with_env(repo, {"MY_DB": "kept"}, "test_cfg.py")
    assert "2 passed" in env_ok, env_ok


def test_a_conftest_hook_extends_the_defaults(tmp_path):
    conftest = "def pytest_offline_credentials(config, settings):\n    settings.variables.append('SEED_EXTRA_SECRET')\n"
    test = "import os\n\n\ndef test_blank():\n    assert 'SEED_EXTRA_SECRET' not in os.environ\n    assert 'DATABASE_URL' not in os.environ\n"
    repo = _project(tmp_path, extra={"conftest.py": conftest, "test_hook.py": test})
    out = _child_with_env(repo, {"SEED_EXTRA_SECRET": "x", "DATABASE_URL": FAKE_DSN}, "test_hook.py")
    assert "1 passed" in out, out


def test_an_unknown_project_loader_is_a_clear_error(tmp_path):
    repo = _project(tmp_path, ini="offline_dotenv_loaders = generator:no_such_reader\n")
    out = _child(repo, "test_letters.py")
    assert "'generator:no_such_reader' does not exist" in out, out


def _child_with_env(repo: Path, extra_env: dict[str, str], *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DATABASE_JOBSTRACKER_URL", "UPWORK_DB_DSN")}
    env.update({"PYTHONPATH": str(SRC) + os.pathsep + str(repo), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", **extra_env})
    cmd = [sys.executable, "-m", "pytest", "-p", "py_ci_shared.offline_suite_without_credentials", "-p", "no:cacheprovider", "-rA", *args]
    proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=240)
    return proc.stdout + proc.stderr


def test_teeth_without_the_dotenv_stub_the_hidden_dependency_passes_again(tmp_path):
    """Revert the core of the plugin (no loader neutralised): the 16b.1 test goes green on a machine with .env again."""
    plugin_src = (SRC / "py_ci_shared" / "offline_suite_without_credentials.py").read_text(encoding="utf-8")
    old = "        stub = _stub(spec, original, record)\n"
    assert plugin_src.count(old) == 1, "NOT APPLIED: the substitution target moved"
    mutant_pkg = tmp_path / "mutant" / "py_ci_shared_mutant"
    mutant_pkg.mkdir(parents=True)
    (mutant_pkg / "__init__.py").write_bytes(b"")
    (mutant_pkg / "offline_mutant.py").write_bytes(plugin_src.replace(old, "        stub = original\n").encode())
    repo = _project(tmp_path / "repo")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL",)}
    env.update({"PYTHONPATH": os.pathsep.join([str(tmp_path / "mutant"), str(SRC), str(repo)]), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    cmd = [sys.executable, "-m", "pytest", "-p", "py_ci_shared_mutant.offline_mutant", "-p", "no:cacheprovider", "-rA", "test_letters.py"]
    out = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=240)
    text = out.stdout + out.stderr
    assert "3 passed" in text, text


def test_a_dotenv_file_the_test_wrote_itself_is_still_read(tmp_path):
    """The dashboard's safe_db_probe test reads tmp_path/.env with dotenv_values; only the checkout's .env is blanked."""
    test = (
        "import dotenv\n\n\ndef test_reads_its_own(tmp_path):\n"
        "    (tmp_path / 'own.env').write_text('DATABASE_URL=postgresql://u:fake@h/db\\n', encoding='utf-8')\n"
        "    assert dotenv.dotenv_values(tmp_path / 'own.env') == {'DATABASE_URL': 'postgresql://u:fake@h/db'}\n"
        "    assert dotenv.dotenv_values() == {}\n"
    )
    repo = _project(tmp_path, extra={"test_own.py": test})
    out = _child(repo, "test_own.py")
    assert "1 passed" in out, out
