"""Unit tests for embedded_postgres: a throwaway server, a loud skip, and the main checkout's files."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from py_ci_shared.embedded_postgres import embedded_postgres, find_pg_bin, main_checkout_file, run
from py_ci_shared._core.git import git_env


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
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=git_env())


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
@pytest.mark.xdist_group("postgres")
def test_a_real_server_starts_accepts_a_connection_and_is_gone_after():
    with embedded_postgres(find_pg_bin()) as dsn:  # type: ignore[arg-type]
        port = int(re.search(r"port=(\d+)", dsn).group(1))  # type: ignore[union-attr]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2)


def test_run_hands_the_command_its_dsn(monkeypatch):
    """``run`` gives the command the DSN of the server it started, in the named variable. The server is replaced by a stand-in DSN here: the contract is the handoff, not a server."""
    import contextlib

    import py_ci_shared.embedded_postgres as ep

    @contextlib.contextmanager
    def _stand_in(bin_dir, **kwargs):
        yield "host=127.0.0.1 port=5999 user=postgres dbname=postgres"

    monkeypatch.setattr(ep, "embedded_postgres", _stand_in)
    code = run([sys.executable, "-c", "import os; raise SystemExit(0 if 'port=' in os.environ['X_DSN'] else 5)"], env_var="X_DSN", bin_dir=Path(sys.executable))
    dsn_left_set = "X_DSN" in os.environ  # a bool: an operand `os.environ` makes pytest print every variable
    assert code == 0 and not dsn_left_set


def test_a_failing_setup_command_raises_with_its_output(tmp_path):
    from py_ci_shared.embedded_postgres import _quiet

    log = tmp_path / "initdb.log"
    server = tmp_path / "server.log"
    server.write_text("FATAL: could not bind\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as info:
        _quiet([sys.executable, "-c", "print('initdb: invalid locale'); raise SystemExit(3)"], check=True, log=log, also=[server])
    message = str(info.value)
    assert "exit code 3" in message and "initdb: invalid locale" in message and "could not bind" in message
    _quiet([sys.executable, "-c", "print('ok')"], check=True, log=log)


@pytest.mark.parametrize(
    "plat,machine,tag",
    [
        ("win32", "AMD64", "win_amd64"),
        ("linux", "x86_64", "manylinux_2_17_x86_64"),
        ("linux", "aarch64", "manylinux_2_17_aarch64"),
        ("darwin", "arm64", "macosx_11_0_arm64"),
        ("darwin", "x86_64", "macosx_10_9_x86_64"),
        ("win32", "ARM64", None),
        ("sunos5", "x86_64", None),
    ],
)
def test_the_wheel_follows_the_cpu_as_well_as_the_os(plat, machine, tag):
    from py_ci_shared.embedded_postgres import wheel_platform

    assert wheel_platform(plat, machine) == tag


def test_fetch_into_a_custom_dest_leaves_its_parent_alone(tmp_path, monkeypatch):
    import zipfile

    import py_ci_shared.embedded_postgres as ep

    monkeypatch.setattr(ep, "_verified_wheel", _unverified_wheel)

    exe = ".exe" if os.name == "nt" else ""
    tools = tmp_path / "tools"
    (tools / "mine").mkdir(parents=True)
    (tools / "mine" / "keep.txt").write_text("x", encoding="utf-8")
    (tools / "notes.txt").write_text("y", encoding="utf-8")
    (tools / "lib").mkdir()
    (tools / "lib" / "stale.so").write_text("old", encoding="utf-8")

    def fake_download(cmd, check, **_kwargs):
        out = Path(cmd[cmd.index("-d") + 1])
        if "tzdata" in cmd:
            with zipfile.ZipFile(out / "tzdata-2026.5-py2.py3-none-any.whl", "w") as zf:
                zf.writestr("tzdata/zoneinfo/UTC", "")
            return subprocess.CompletedProcess(cmd, 0)
        with zipfile.ZipFile(out / "pgserver-0.1.4-py3-none-any.whl", "w") as zf:
            zf.writestr(f"pgserver/pginstall/bin/initdb{exe}", "")
            zf.writestr(f"pgserver/pginstall/bin/pg_ctl{exe}", "")
            zf.writestr("pgserver/pginstall/lib/libpq.so", "")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ep.subprocess, "run", fake_download)
    monkeypatch.setattr(ep, "wheel_platform", lambda *a, **k: "win_amd64")
    assert ep.fetch(tools / "bin") == tools / "bin"
    assert (tools / "bin" / f"initdb{exe}").is_file() and (tools / "lib" / "libpq.so").is_file()
    assert (tools / "mine" / "keep.txt").is_file() and (tools / "notes.txt").is_file()
    assert not (tools / "lib" / "stale.so").exists()


def _fake_pip(monkeypatch, ep, *, fail_tz: bool = False) -> list[list[str]]:
    """Replace the pip download with a fake that writes a tzdata wheel (or fails for it); the calls are recorded."""
    import zipfile

    calls: list[list[str]] = []

    def fake(cmd, check, **_kwargs):
        calls.append(list(cmd))
        if fail_tz:
            raise subprocess.CalledProcessError(1, cmd)
        out = Path(cmd[cmd.index("-d") + 1])
        with zipfile.ZipFile(out / "tzdata-2026.5-py2.py3-none-any.whl", "w") as zf:
            zf.writestr("tzdata/zoneinfo/UTC", "")
            zf.writestr("tzdata/zoneinfo/Europe/Moscow", "")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ep.subprocess, "run", fake)
    return calls


def _unpacked_pginstall(root: Path) -> Path:
    exe = ".exe" if os.name == "nt" else ""
    (root / "bin").mkdir(parents=True)
    for name in ("initdb", "pg_ctl"):
        (root / "bin" / f"{name}{exe}").write_text("", encoding="utf-8")
    (root / "share" / "postgresql" / "timezonesets").mkdir(parents=True)
    return root / "bin"


def test_a_cache_unpacked_before_the_timezone_step_gets_the_zone_files(tmp_path, monkeypatch):
    """The pgserver build has no timezone/ directory, so SET TimeZone = 'UTC' failed on the embedded server; fetch adds it to an existing cache."""
    import py_ci_shared.embedded_postgres as ep

    monkeypatch.setattr(ep, "_verified_wheel", _unverified_wheel)
    bin_dir = _unpacked_pginstall(tmp_path / "pginstall")
    calls = _fake_pip(monkeypatch, ep)
    assert ep.fetch(bin_dir) == bin_dir
    zones = tmp_path / "pginstall" / "share" / "postgresql" / "timezone"
    assert (zones / "UTC").is_file() and (zones / "Europe" / "Moscow").is_file()
    assert len(calls) == 1 and "tzdata==2026.5" in calls[0]


def test_a_cache_that_has_its_zone_files_is_not_downloaded_again(tmp_path, monkeypatch):
    import py_ci_shared.embedded_postgres as ep

    bin_dir = _unpacked_pginstall(tmp_path / "pginstall")
    (tmp_path / "pginstall" / "share" / "postgresql" / "timezone").mkdir()
    calls = _fake_pip(monkeypatch, ep)
    assert ep.fetch(bin_dir) == bin_dir
    assert calls == []


def test_a_failed_tzdata_download_is_reported_and_does_not_stop_the_server(tmp_path, monkeypatch, capsys):
    import py_ci_shared.embedded_postgres as ep

    bin_dir = _unpacked_pginstall(tmp_path / "pginstall")
    _fake_pip(monkeypatch, ep, fail_tz=True)
    assert ep.fetch(bin_dir) == bin_dir
    assert "no timezone database" in capsys.readouterr().err
    assert not (tmp_path / "pginstall" / "share" / "postgresql" / "timezone").exists()


def test_the_cli_keeps_a_double_dash_that_belongs_to_the_command(capsys, monkeypatch):
    import py_ci_shared.embedded_postgres as ep

    monkeypatch.setattr(ep, "find_pg_bin", lambda explicit=None: None)
    assert ep.main(["run", "--env", "X_DSN", "--missing-exit", "4", "--", "git", "log", "--", "path"]) == 4
    assert "git log -- path" in capsys.readouterr().err


def test_a_server_left_by_a_killed_process_is_reaped_and_a_live_one_is_kept(tmp_path):
    """A test worker killed mid-test never runs its cleanup, so its data directory and server outlive it. A new start removes the directory of a
    dead owner and leaves the directory of a live owner alone (another session or worker). No server is started here: the directories are the subject."""
    import os as _os

    pytest.importorskip("psutil")  # the reaper judges owners by pid; without psutil it is a no-op by design
    from py_ci_shared.embedded_postgres import _reap_orphans

    dead = tmp_path / "pg-embedded-dead"
    (dead / "db").mkdir(parents=True)
    (dead / "owner.pid").write_text("999999", encoding="ascii")  # no such process
    live = tmp_path / "pg-embedded-live"
    (live / "db").mkdir(parents=True)
    (live / "owner.pid").write_text(str(_os.getpid()), encoding="ascii")  # this process is alive
    _reap_orphans(Path(sys.executable), tmp_path)
    assert not dead.exists()
    assert live.is_dir()


def _unverified_wheel(directory, pattern, digest):
    """Stand-in for the digest check, for tests whose fake wheels have no pinned bytes; the check itself is tested below."""
    from pathlib import Path as _Path

    return next(_Path(directory).glob(pattern))


def test_a_wheel_with_its_pinned_digest_is_accepted(tmp_path):
    import hashlib

    import py_ci_shared.embedded_postgres as ep

    wheel = tmp_path / "tzdata-2026.5-py2.py3-none-any.whl"
    wheel.write_bytes(b"zone files")
    digest = hashlib.sha256(b"zone files").hexdigest()
    assert ep._verified_wheel(str(tmp_path), "tzdata-*.whl", digest) == wheel


def test_a_wheel_that_does_not_match_its_pin_is_refused(tmp_path):
    import py_ci_shared.embedded_postgres as ep

    (tmp_path / "tzdata-2026.5-py2.py3-none-any.whl").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="does not match its pinned SHA-256"):
        ep._verified_wheel(str(tmp_path), "tzdata-*.whl", "0" * 64)


def test_fetch_refuses_a_platform_without_a_verified_digest_before_downloading(tmp_path, monkeypatch):
    import py_ci_shared.embedded_postgres as ep

    monkeypatch.setattr(ep, "wheel_platform", lambda *a, **k: "manylinux_2_17_aarch64")
    calls = _fake_pip(monkeypatch, ep)
    with pytest.raises(RuntimeError, match="verified digest"):
        ep.fetch(tmp_path / "pginstall" / "bin")
    # The fake sees every subprocess call in the process: on Windows the platform module runs `ver` through the same hook.
    # The claim is that no wheel was downloaded, so only pip calls count.
    assert [c for c in calls if "pip" in c] == []
