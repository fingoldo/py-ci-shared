"""Unit tests for embedded_postgres: a throwaway server, a loud skip, and the main checkout's files."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.embedded_postgres import embedded_postgres, find_pg_bin, main_checkout_file, run


def test_no_binaries_is_a_loud_skip_with_the_chosen_exit_code(capsys):
    assert run(["python", "-c", "raise SystemExit(9)"], env_var="X_DSN", bin_dir=None, missing_exit=3) == 3
    err = capsys.readouterr().err
    assert "SKIPPED" in err and "did NOT run" in err


def test_find_pg_bin_takes_a_directory_only_when_both_binaries_are_there(tmp_path):
    exe = ".exe" if os.name == "nt" else ""
    full, half = tmp_path / "full", tmp_path / "half"
    full.mkdir()
    half.mkdir()
    for name in ("initdb", "pg_ctl"):
        (full / f"{name}{exe}").write_text("", encoding="utf-8")
    (half / f"pg_ctl{exe}").write_text("", encoding="utf-8")
    assert find_pg_bin(str(full)) == full
    assert find_pg_bin(str(half)) != half


def test_the_fetch_cache_is_searched(tmp_path, monkeypatch):
    """A hook on a machine with no PG_BIN still finds binaries that `fetch` unpacked earlier."""
    exe = ".exe" if os.name == "nt" else ""
    monkeypatch.delenv("PG_BIN", raising=False)
    monkeypatch.setenv("PY_CI_SHARED_CACHE", str(tmp_path))
    bin_dir = tmp_path / "pginstall" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("initdb", "pg_ctl"):
        (bin_dir / f"{name}{exe}").write_text("", encoding="utf-8")
    from py_ci_shared.embedded_postgres import cache_bin_dir, fetch

    assert cache_bin_dir() == bin_dir
    assert find_pg_bin() == bin_dir
    assert fetch() == bin_dir, "fetch is idempotent: an unpacked cache is not downloaded again"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_main_checkout_file_is_found_from_a_linked_worktree(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    (main / "sub").mkdir()
    (main / "sub" / ".env").write_text("A=1\n", encoding="utf-8")
    _git(main, "worktree", "add", "-q", "--detach", str(tmp_path / "wt"))
    found = main_checkout_file("sub/.env", cwd=tmp_path / "wt")
    assert found is not None and found.resolve() == (main / "sub" / ".env").resolve()
    assert main_checkout_file("sub/absent", cwd=tmp_path / "wt") is None


def test_outside_a_repository_there_is_no_main_checkout(tmp_path):
    assert main_checkout_file(".env", cwd=tmp_path) is None


@pytest.mark.skipif(find_pg_bin() is None, reason="no Postgres binaries here (PG_BIN / pgserver / PATH)")
def test_a_real_server_starts_accepts_a_connection_and_is_gone_after():
    with embedded_postgres(find_pg_bin()) as dsn:  # type: ignore[arg-type]
        port = int(re.search(r"port=(\d+)", dsn).group(1))  # type: ignore[union-attr]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2)


@pytest.mark.skipif(find_pg_bin() is None, reason="no Postgres binaries here (PG_BIN / pgserver / PATH)")
def test_run_hands_the_command_its_dsn():
    code = run([sys.executable, "-c", "import os; raise SystemExit(0 if 'port=' in os.environ['X_DSN'] else 5)"], env_var="X_DSN", bin_dir=find_pg_bin())
    assert code == 0 and "X_DSN" not in os.environ
