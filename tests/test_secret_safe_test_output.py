"""``secret_safe_test_output``: the plugin run for real in a child pytest. Every secret here is a fake this file invents."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from py_ci_shared import secret_safe_test_output as plugin

SRC = Path(__file__).resolve().parents[1] / "src"
FAKE_PASSWORD = "FAKEPW0123456789"
FAKE_TOKEN = "faketoken-abcdef-0123456789"
FAKE_DSN = f"postgresql://reader:{FAKE_PASSWORD}@db.example/jobs"

# The Upwork dashboard's environment assertion: pytest explains `not in os.environ` by printing the whole environment,
# and the code under test logged the DSN it connected with.
DASHBOARD_LEAK = f"""
import logging
import os


def test_it_leaks():
    os.environ["SCRATCH_DSN"] = "{FAKE_DSN}"
    logging.getLogger("scratch").warning("connecting with %s", os.environ["SCRATCH_DSN"])
    print("token is", os.environ["API_TOKEN"])
    print("built", "{FAKE_DSN.replace('db.example', 'other.example')}")
    assert "SCRATCH_DSN" not in os.environ
"""

# The py-ci-shared meta-test of 8d47b8f: a module that loads a .env at import, and a test that prints the environment.
DOTENV_AT_IMPORT = """
import os

with open(os.path.join(os.path.dirname(__file__), "fake.env"), encoding="utf-8") as fh:
    for line in fh:
        key, _, value = line.strip().partition("=")
        os.environ[key] = value


def test_prints_the_environment():
    assert dict(os.environ) == {}
"""


def _child(tmp_path: Path, files: dict[str, str], *args: str, plugin_on: bool = True, ini: str = "") -> str:
    for name, text in files.items():
        (tmp_path / name).write_bytes(text.encode())
    (tmp_path / "pytest.ini").write_bytes(f"[pytest]\n{ini}".encode())
    env = {k: v for k, v in os.environ.items() if k not in ("SCRATCH_DSN", "SEED_SECRET_KEY", "SEED_DATABASE_URL")}
    env.update({"PYTHONPATH": str(SRC) + os.pathsep + env.get("PYTHONPATH", ""), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "API_TOKEN": FAKE_TOKEN})
    plugins = ["-p", "py_ci_shared.secret_safe_test_output"] if plugin_on else []
    cmd = [sys.executable, "-m", "pytest", *plugins, "-p", "no:cacheprovider", "-vv", "-rA", *args]
    proc = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=240)
    return proc.stdout + proc.stderr


def test_without_the_plugin_the_dashboard_case_prints_the_password(tmp_path):
    """The control: the leak is real, so the redaction test below is not passing on nothing."""
    out = _child(tmp_path, {"test_leak.py": DASHBOARD_LEAK}, plugin_on=False)
    assert "1 failed" in out
    assert FAKE_PASSWORD in out and FAKE_TOKEN in out


def test_the_dashboard_case_reports_the_host_but_not_the_password_or_token(tmp_path):
    out = _child(tmp_path, {"test_leak.py": DASHBOARD_LEAK})
    assert "1 failed" in out, out
    assert "<redacted SCRATCH_DSN>" in out, "the report no longer shows the environment at all, so the redaction is untested"
    assert "reader:***@other.example" in out, "a DSN no variable holds keeps its scheme, user and host"
    assert FAKE_PASSWORD not in out, "a DSN password reached the report"
    assert FAKE_TOKEN not in out, "a secret-named variable's value reached the report"
    assert "<redacted API_TOKEN>" in out


def test_a_dotenv_loaded_at_collection_is_restored_and_its_values_redacted(tmp_path):
    # KMP_SEED_LIB stands for an OpenMP runtime configuring itself on import: kept, so it is not in the restored list.
    fake_env = f"SEED_SECRET_KEY={FAKE_TOKEN}\nSEED_DATABASE_URL={FAKE_DSN}\nKMP_SEED_LIB=1\n"
    out = _child(tmp_path, {"test_meta.py": DOTENV_AT_IMPORT, "fake.env": fake_env})
    assert "1 failed" in out, out
    assert "collection changed SEED_DATABASE_URL, SEED_SECRET_KEY; restored" in out
    assert FAKE_TOKEN not in out and FAKE_PASSWORD not in out


def test_a_configured_session_fixture_has_its_environment_restored(tmp_path):
    conftest = (
        "import os, pytest\n\n\n@pytest.fixture(scope='session', autouse=True)\ndef load_env():\n" f"    os.environ['SEED_SECRET_KEY'] = '{FAKE_TOKEN}'\n"
    )
    test = "import os\n\n\ndef test_clean():\n    assert 'SEED_SECRET_KEY' not in os.environ\n"
    out = _child(tmp_path, {"conftest.py": conftest, "test_x.py": test}, ini="secret_safe_env_fixtures = load_env\n")
    assert "1 passed" in out, out
    assert "fixture load_env changed SEED_SECRET_KEY; restored" in out


def test_a_session_fixture_not_configured_is_not_a_tests_leak(tmp_path):
    conftest = "import os, pytest\n\n\n@pytest.fixture(scope='session', autouse=True)\ndef sets():\n    os.environ['SEED_FIXTURE'] = '1'\n"
    test = "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n"
    out = _child(tmp_path, {"conftest.py": conftest, "test_x.py": test}, ini="secret_safe_env_leaks = fail\n")
    assert "2 passed" in out and "left os.environ changed" not in out, out


def test_a_test_leaking_a_variable_is_reported_by_key_and_fails_in_fail_mode(tmp_path):
    test = (
        f"import os\n\n\ndef test_leaks():\n    os.environ['SEED_LEAKED'] = '{FAKE_TOKEN}'\n\n\ndef test_after():\n    assert 'SEED_LEAKED' not in os.environ\n"
    )
    (tmp_path / "report").mkdir()
    report = _child(tmp_path / "report", {"test_x.py": test})
    assert "2 passed" in report and "test_x.py::test_leaks left os.environ changed: SEED_LEAKED; restored" in report, report
    assert FAKE_TOKEN not in report
    (tmp_path / "fail").mkdir()
    failed = _child(tmp_path / "fail", {"test_x.py": test}, ini="secret_safe_env_leaks = fail\n")
    assert "1 error" in failed and "2 passed" in failed, failed


def test_a_bad_leak_mode_is_a_usage_error(tmp_path):
    out = _child(tmp_path, {"test_x.py": "def test_a():\n    pass\n"}, ini="secret_safe_env_leaks = loud\n")
    assert "secret_safe_env_leaks = 'loud'" in out, out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (FAKE_DSN, "postgresql://reader:***@db.example/jobs"),
        ("host=db password=s3cr3tvalue user=u", "host=db password=*** user=u"),
        ("password='quoted secret'", "password=***"),
        ("https://example.com/path@x", "https://example.com/path@x"),
    ],
)
def test_redact_blanks_the_credential_and_keeps_the_rest(text, expected):
    assert plugin.redact(text) == expected


def test_secret_values_pick_secret_names_and_credentialed_urls_only():
    env = {"API_TOKEN": FAKE_TOKEN, "HOME_URL": "https://example.com", "JOBS_URL": FAKE_DSN, "PATH": "/usr/bin:/bin", "SHORT_KEY": "1"}
    assert plugin.secret_values(env) == {FAKE_TOKEN: "API_TOKEN", FAKE_DSN: "JOBS_URL"}


def test_teeth_without_the_url_rule_the_password_survives(monkeypatch):
    monkeypatch.setattr(plugin, "_URL_CREDENTIALS", __import__("re").compile(r"(?P<head>$^)"))
    assert FAKE_PASSWORD in plugin.redact(FAKE_DSN)
