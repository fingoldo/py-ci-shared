"""Opt-in pytest plugin: no failure report prints a credential, and a ``.env`` loaded by collection does not stay loaded.

pytest prints every operand of a failing assert, captured stdout/stderr and every log record, so a DSN that reaches
any of them lands in the terminal, in a ``> file 2>&1`` capture and in CI logs. Real cases: the Upwork dashboard's
``test_an_unset_dsn_is_none`` printed the full DSN, and a py-ci-shared meta-test printed every ``.env`` value (fixed
in ``import_side_effects`` by 8d47b8f). :mod:`py_ci_shared.secret_assertion_operands` keeps such assertions out of the
tests; this plugin covers the next one that slips through.

Enable with ``secret_safe_test_output = true`` in ``[tool.py_ci_shared]`` (the ``py_ci_shared`` plugin then loads it)
or ``-p py_ci_shared.secret_safe_test_output``. It does three things:

* Redaction. Every test and collection report (the failure text, captured output and log sections, which junitxml and
  the terminal both read) has ``scheme://user:PASSWORD@`` and libpq ``password=...`` blanked, and every value of an
  environment variable whose NAME looks secret (:data:`SECRET_NAME`: KEY, TOKEN, SECRET, PASSWORD, DSN, CREDENTIAL, ...;
  a ``*_URL`` only when its value holds credentials) replaced by ``<redacted NAME>``. Values are remembered from the
  start of the session, after collection, after each configured fixture and at report time, so a value a test saw
  and the environment no longer holds is still redacted. ``-s`` output goes straight to the terminal and cannot be.
* Environment restore. ``os.environ`` is put back after collection (a test module that calls ``load_dotenv()`` at
  import leaves every deployment value in the process for every later test) and after the setup of each fixture named
  in ini ``secret_safe_env_fixtures``; the keys (never values) are listed in the terminal summary.
* Per-test leaks. A test whose setup, body or teardown leaves ``os.environ`` changed is reported by key and the change
  restored. What a module/session-scoped fixture sets up is that fixture's, not the test's. ini
  ``secret_safe_env_leaks``: ``report`` (default: terminal summary), ``fail`` (a teardown error) or ``off``.

ini: ``secret_safe_restore_environ`` (bool, default true), ``secret_safe_env_fixtures`` (fixture names),
``secret_safe_env_leaks``, ``secret_safe_secret_names`` (extra regexes for secret variable names),
``secret_safe_min_length`` (shorter values are not redacted by value, default 6), ``secret_safe_keep_environ`` (globs
never restored or reported, on top of :data:`DEFAULT_KEEP`: ``KMP_*``, ``OMP_*``, ``MKL_*``, ``PYTEST_*``, ``COV_CORE_*``).
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional

import pytest

from .secret_assertion_operands import SECRET_NAME

__all__ = ["DEFAULT_KEEP", "SECRET_NAME", "redact", "secret_values"]

#: ``scheme://user:password@``: the URL form of a credential.
_URL_CREDENTIALS = re.compile(r"(?P<head>\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/@:'\"]*:)[^\s@'\"/]+@")
#: libpq ``password=...`` and its quoted form.
_KEYWORD_PASSWORD = re.compile(r"(?P<key>\bpassword\s*=\s*)('[^']*'|\"[^\"]*\"|[^\s'\"&,]+)", re.IGNORECASE)
_DEFAULT_MIN_LENGTH = 6
_LEAK_MODES = ("report", "fail", "off")
#: Variables a library sets for itself on import (an OpenMP runtime's ``KMP_INIT_AT_FORK``) or the runner sets: never
#: restored nor reported, unless the name looks secret. ini ``secret_safe_keep_environ`` adds globs.
DEFAULT_KEEP = ("KMP_*", "OMP_*", "MKL_*", "PYTEST_*", "COV_CORE_*")

_STATE = pytest.StashKey["_SessionState"]()
_TEST_ENV = pytest.StashKey[dict[str, str]]()


class _SessionState:
    def __init__(self, names: "list[re.Pattern[str]]", min_length: int) -> None:
        self.names = names
        self.min_length = min_length
        self.values: dict[str, str] = {}  # secret value -> variable name
        self.restored: list[str] = []  # summary lines, keys only
        self.leaks: list[str] = []
        self.fixture_keys: set[str] = set()  # changed by a non-function fixture's setup: that fixture's, not a test's
        self.keep: tuple[str, ...] = DEFAULT_KEEP

    def changed(self, before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
        """Keys whose value differs, less the kept library/runner variables."""
        keys = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        return [k for k in keys if SECRET_NAME.search(k) or not any(fnmatch.fnmatchcase(k, g) for g in self.keep)]

    def remember(self, env: Mapping[str, str]) -> None:
        self.values.update(secret_values(env, self.names, self.min_length))


def _is_secret_name(name: str, value: str, extra: Iterable["re.Pattern[str]"] = ()) -> bool:
    if SECRET_NAME.search(name) or any(p.search(name) for p in extra):
        return True
    return name.upper().endswith(("_URL", "_URI")) and bool(_URL_CREDENTIALS.search(value))


def secret_values(env: Mapping[str, str], extra_names: Iterable["re.Pattern[str]"] = (), min_length: int = _DEFAULT_MIN_LENGTH) -> dict[str, str]:
    """``{value: name}`` for every variable of *env* whose name looks secret and whose value is long enough to redact."""
    extra = list(extra_names)
    return {v: k for k, v in env.items() if len(v) >= min_length and _is_secret_name(k, v, extra)}


def redact(text: str, values: Optional[Mapping[str, str]] = None) -> str:
    """*text* with every known secret value, URL credential and libpq password blanked; scheme, user and host stay."""
    for value in sorted(values or {}, key=len, reverse=True):  # longest first: a DSN before the password inside it
        if value in text:
            text = text.replace(value, f"<redacted {(values or {})[value]}>")
    text = _URL_CREDENTIALS.sub(r"\g<head>***@", text)
    return _KEYWORD_PASSWORD.sub(r"\g<key>***", text)


def _restore(snapshot: Mapping[str, str], keys: Iterable[str]) -> None:
    for key in keys:
        if key in snapshot:
            os.environ[key] = snapshot[key]
        else:
            os.environ.pop(key, None)


# ------------------------------------------------------------------------------------------------------------ pytest hooks


def pytest_addoption(parser: Any) -> None:
    parser.addini("secret_safe_restore_environ", "secret_safe_test_output: restore os.environ after collection", type="bool", default=True)
    parser.addini(
        "secret_safe_env_fixtures", "secret_safe_test_output: fixtures whose setup loads a .env; os.environ is restored after", type="linelist", default=[]
    )
    parser.addini("secret_safe_env_leaks", "secret_safe_test_output: a test that leaves os.environ changed: report, fail or off", default="report")
    parser.addini("secret_safe_secret_names", "secret_safe_test_output: extra regexes for secret environment variable names", type="linelist", default=[])
    parser.addini(
        "secret_safe_keep_environ", "secret_safe_test_output: globs of variables never restored (adds to KMP_*, OMP_*, ...)", type="linelist", default=[]
    )
    parser.addini("secret_safe_min_length", "secret_safe_test_output: shortest value redacted by value", default=str(_DEFAULT_MIN_LENGTH))


def pytest_configure(config: Any) -> None:
    mode = str(config.getini("secret_safe_env_leaks"))
    if mode not in _LEAK_MODES:
        raise pytest.UsageError(f"secret_safe_env_leaks = {mode!r}; expected one of {_LEAK_MODES}")
    try:
        names = [re.compile(p) for p in config.getini("secret_safe_secret_names")]
        min_length = int(str(config.getini("secret_safe_min_length")))
    except (re.error, ValueError) as exc:
        raise pytest.UsageError(f"secret_safe_test_output: bad ini value: {exc}") from exc
    state = _SessionState(names, min_length)
    state.keep = DEFAULT_KEEP + tuple(config.getini("secret_safe_keep_environ"))
    state.remember(os.environ)
    config.stash[_STATE] = state


def _state(config: Any) -> Optional[_SessionState]:
    state: Optional[_SessionState] = config.stash.get(_STATE, None)
    return state


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session: Any) -> Any:
    before = dict(os.environ)
    yield
    state = _state(session.config)
    if state is None:
        return
    state.remember(os.environ)
    changed = state.changed(before, os.environ)
    if changed and session.config.getini("secret_safe_restore_environ"):
        _restore(before, changed)
        state.restored.append(f"collection changed {', '.join(changed)}; restored")


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef: Any, request: Any) -> Any:
    before = dict(os.environ)
    yield
    state = _state(request.config)
    if state is None:
        return
    changed = state.changed(before, os.environ)
    if not changed:
        return
    state.remember(os.environ)
    if fixturedef.argname in request.config.getini("secret_safe_env_fixtures"):
        _restore(before, changed)
        state.restored.append(f"fixture {fixturedef.argname} changed {', '.join(changed)}; restored")
    elif fixturedef.scope != "function":
        state.fixture_keys.update(changed)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: Any) -> None:
    item.stash[_TEST_ENV] = dict(os.environ)


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: Any, nextitem: Any) -> None:
    state = _state(item.config)
    before = item.stash.get(_TEST_ENV, None)
    mode = str(item.config.getini("secret_safe_env_leaks"))
    if state is None or before is None or mode == "off":
        return
    state.remember(os.environ)
    leaked = [k for k in state.changed(before, os.environ) if k not in state.fixture_keys]
    if not leaked:
        return
    _restore(before, leaked)
    line = f"{item.nodeid} left os.environ changed: {', '.join(leaked)}; restored"
    state.leaks.append(line)
    if mode == "fail":
        raise pytest.fail.Exception(line + ". Use monkeypatch.setenv/delenv, which undoes itself.", pytrace=False)


def _redact_report(report: Any, state: _SessionState) -> None:
    state.remember(os.environ)
    if report.longrepr is not None:
        text = str(report.longrepr)
        cleaned = redact(text, state.values)
        if cleaned != text:
            report.longrepr = cleaned
    report.sections = [(name, redact(content, state.values)) for name, content in report.sections]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Any:
    outcome = yield
    state = _state(item.config)
    if state is not None:
        _redact_report(outcome.get_result(), state)


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector: Any) -> Any:
    outcome = yield
    state = _state(collector.config)
    if state is not None:
        _redact_report(outcome.get_result(), state)


def pytest_terminal_summary(terminalreporter: Any, exitstatus: Any, config: Any) -> None:
    state = _state(config)
    if state is None or not (state.restored or state.leaks):
        return
    terminalreporter.section("secret_safe_test_output: os.environ", sep="-")
    for line in state.restored + state.leaks:
        terminalreporter.write_line(line)
