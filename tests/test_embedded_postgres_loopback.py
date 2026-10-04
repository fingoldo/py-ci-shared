"""require_loopback_dsn and throwaway_dsn_from_env: a writing test is pointed only at the local throwaway server.

A free-form environment variable can be overwritten with a production DSN, so a DSN that is not provably loopback fails the
run loudly (never skips), and the refusal never prints the DSN, its host or its password.
"""

from __future__ import annotations

import pytest

from py_ci_shared.embedded_postgres import NotAThrowawayServerError, require_loopback_dsn, throwaway_dsn_from_env

ACCEPTED = [
    "host=127.0.0.1 port=55432 user=postgres dbname=postgres",
    "postgresql://postgres@localhost:55432/postgres",
    "postgresql://postgres@[::1]:55432/postgres",
    "postgres://u:p@127.0.0.2/db",
    "host='127.0.0.1' port=1",
    "host=127.0.0.1,::1 dbname=x",
    "host=localhost hostaddr=127.0.0.1",
    "postgresql://127.0.0.1,localhost/db",
]
# Each secret-looking value must be absent from the refusal.
SECRET = "hunter2-secret"  # pragma: allowlist secret
REFUSED = [
    f"postgresql://user:{SECRET}@db.example-host.co:5432/postgres",
    f"host=10.8.0.1 port=5432 user=u password={SECRET} dbname=jobs",
    f"dbname=jobs password={SECRET}",  # no host: libpq falls back to PGHOST or a local socket
    "postgresql:///jobs",  # URI without a host
    "host=/var/run/postgresql dbname=jobs",  # a unix socket is not the harness
    "host=127.0.0.1 hostaddr=10.0.0.1",  # hostaddr is what libpq dials
    "postgresql://u@127.0.0.1,db.example-host.co/db",  # one remote host among loopback ones
    "postgresql://127.0.0.1/db?host=db.example-host.co",  # a query host overrides the authority
    "host=localhost.example-host.co",  # a name that merely starts with localhost
    "host=127.0.0.1.example-host.co",
    "not a dsn at all ===",
    "host='127.0.0.1",  # unterminated quote
    "",
    "   ",
]


@pytest.mark.parametrize("dsn", ACCEPTED)
def test_a_loopback_tcp_dsn_is_returned_unchanged(dsn):
    assert require_loopback_dsn(dsn) == dsn


@pytest.mark.parametrize("dsn", REFUSED)
def test_anything_else_is_refused_without_echoing_it(dsn):
    with pytest.raises(NotAThrowawayServerError) as raised:
        require_loopback_dsn(dsn)
    message = str(raised.value)
    for leaked in (SECRET, "example-host", "10.8.0.1", "jobs"):
        assert leaked not in message, f"the refusal printed {leaked!r}"


def test_the_refusal_says_which_of_three_things_was_wrong():
    reasons = {
        "unparseable": "not parseable",
        "no host": "names no host",
        "remote": "not a loopback address",
    }
    seen = {}
    for label, dsn in (("unparseable", "not a dsn at all ==="), ("no host", "dbname=x"), ("remote", "host=10.1.1.1")):
        with pytest.raises(NotAThrowawayServerError) as raised:
            require_loopback_dsn(dsn)
        seen[label] = str(raised.value)
    for label, fragment in reasons.items():
        assert fragment in seen[label]


def test_a_non_string_is_refused_not_a_crash():
    with pytest.raises(NotAThrowawayServerError):
        require_loopback_dsn(None)  # type: ignore[arg-type]


def test_the_error_is_a_runtime_error_so_a_test_run_fails_instead_of_skipping():
    assert issubclass(NotAThrowawayServerError, RuntimeError)


def test_an_unset_or_empty_variable_is_none_so_the_test_can_skip_outside_the_harness():
    assert throwaway_dsn_from_env("X_PG_DSN", environ={}) is None
    assert throwaway_dsn_from_env("X_PG_DSN", environ={"X_PG_DSN": ""}) is None


def test_a_set_variable_is_validated_and_returned():
    dsn = "host=127.0.0.1 port=55432 user=postgres dbname=postgres"
    assert throwaway_dsn_from_env("X_PG_DSN", environ={"X_PG_DSN": dsn}) == dsn


def test_a_production_dsn_in_the_variable_names_the_variable_and_never_the_value():
    dsn = f"postgresql://user:{SECRET}@db.example-host.co:5432/postgres"
    with pytest.raises(NotAThrowawayServerError) as raised:
        throwaway_dsn_from_env("X_PG_DSN", environ={"X_PG_DSN": dsn})
    message = str(raised.value)
    assert message.startswith("X_PG_DSN:")
    assert SECRET not in message and "example-host" not in message


def test_the_process_environment_is_read_by_default(monkeypatch):
    monkeypatch.setenv("X_PG_DSN", "host=127.0.0.1 port=1")
    assert throwaway_dsn_from_env("X_PG_DSN") == "host=127.0.0.1 port=1"
    monkeypatch.setenv("X_PG_DSN", "host=10.0.0.9")
    with pytest.raises(NotAThrowawayServerError):
        throwaway_dsn_from_env("X_PG_DSN")
    monkeypatch.delenv("X_PG_DSN")
    assert throwaway_dsn_from_env("X_PG_DSN") is None


def test_the_dsn_the_harness_itself_produces_is_accepted():
    """If `embedded_postgres()` ever yielded a socket path this guard would refuse its own server, and every writing test would fail."""
    from py_ci_shared.embedded_postgres import embedded_postgres, find_pg_bin

    bin_dir = find_pg_bin()
    if bin_dir is None:
        pytest.skip("no Postgres binaries (PG_BIN or `python -m py_ci_shared.embedded_postgres fetch`): the harness DSN shape is not exercised")
    with embedded_postgres(bin_dir) as dsn:
        assert require_loopback_dsn(dsn) == dsn
