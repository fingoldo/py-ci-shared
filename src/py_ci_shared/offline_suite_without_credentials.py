"""Opt-in pytest plugin: every test not marked ``real_database`` runs as on a checkout with no ``.env``.

A test that passes only because the developer's ``.env`` supplied a DSN is green on that machine and red on every clean
checkout, which is what CI and a fresh worktree are. Real cases: 19 Upwork dashboard tests failed without ``.env`` (audit
16b.1: something on the generator path resolved a DSN before the stub was reached), and a timing test failed without
the proxy variables because a module it imported loads ``.env`` and exits at import when they are missing.

Enable with ``offline_suite_without_credentials = true`` in ``[tool.py_ci_shared]`` (the ``py_ci_shared`` plugin then
loads it) or ``-p py_ci_shared.offline_suite_without_credentials``. For every test without the marker, an autouse
fixture

* deletes the credential variables (ini ``offline_credential_vars``, default :data:`DEFAULT_VARS`) and every variable
  whose name matches ini ``offline_credential_patterns`` (default :data:`DEFAULT_PATTERNS`);
* replaces ``dotenv.load_dotenv`` (returns False, loads nothing) and ``dotenv.dotenv_values`` (returns ``{}``), also
  where a module bound them by ``from dotenv import ...``; a call naming a stream or a file under the temp directory (a
  ``.env`` the test wrote itself) still reaches python-dotenv, and each ``module:attr`` of ini ``offline_dotenv_loaders``
  (a project's own ``.env`` reader; replaced by a stub that returns None);
* records every read of a blanked variable that found nothing, and every call of a neutralised loader. When the test
  fails, the report gets an ``offline credentials`` section naming them: the variable the test silently needed.

A conftest can change the settings with the hook ``pytest_offline_credentials(config, settings)``: *settings* is an
:class:`OfflineSettings` whose lists it edits in place. ``@pytest.mark.real_database`` (ini
``offline_credentials_marker``) exempts a test.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

__all__ = ["DEFAULT_LOADERS", "DEFAULT_PATTERNS", "DEFAULT_VARS", "OfflineSettings"]

DEFAULT_VARS = ("DATABASE_URL", "DATABASE_JOBSTRACKER_URL", "UPWORK_DB_DSN")
#: Names that hold a connection string whatever the project calls them.
DEFAULT_PATTERNS = (r"(^|_)DSN$", r"^DATABASE(_\w+)?_URL$", r"^(PG|POSTGRES_?)PASSWORD$")
#: ``module:attr`` loaders neutralised by default (``python-dotenv``; skipped when it is not installed).
DEFAULT_LOADERS = ("dotenv:load_dotenv", "dotenv:dotenv_values", "dotenv.main:load_dotenv", "dotenv.main:dotenv_values")
DEFAULT_MARKER = "real_database"

_SETTINGS = pytest.StashKey["OfflineSettings"]()
_RECORD = pytest.StashKey["_Record"]()


@dataclass
class OfflineSettings:
    """What the plugin blanks. A ``pytest_offline_credentials`` hook may edit every list in place."""

    marker: str = DEFAULT_MARKER
    variables: list[str] = field(default_factory=lambda: list(DEFAULT_VARS))
    patterns: list[str] = field(default_factory=lambda: list(DEFAULT_PATTERNS))
    loaders: list[str] = field(default_factory=list)  # project loaders, on top of DEFAULT_LOADERS
    compiled: "list[re.Pattern[str]]" = field(default_factory=list)

    def blanked(self, name: str) -> bool:
        return name in self.variables or any(p.search(name) for p in self.compiled)


@dataclass
class _Record:
    reads: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def read(self, name: str) -> None:
        if name not in self.reads:
            self.reads.append(name)

    def call(self, text: str) -> None:
        if text not in self.calls:
            self.calls.append(text)


class _Hookspecs:
    @pytest.hookspec(historic=False)
    def pytest_offline_credentials(self, config: Any, settings: OfflineSettings) -> None:
        """Edit *settings* (variables, patterns, loaders, marker) in place before the first test runs."""


def _recording_environ(real: Any, settings: OfflineSettings, record: _Record) -> Any:
    """An ``os._Environ`` over the SAME storage as *real* that notes a missed lookup of a blanked variable.

    Sharing the storage keeps writes, ``putenv`` and code that bound ``environ`` before the swap all consistent."""

    class _Recording(type(real)):  # type: ignore[misc]
        def __getitem__(self, key: Any) -> Any:
            try:
                return super().__getitem__(key)
            except KeyError:
                if isinstance(key, str) and settings.blanked(key):
                    record.read(key)
                raise

    return _Recording(real._data, real.encodekey, real.decodekey, real.encodevalue, real.decodevalue)


def _caller() -> str:
    frame = sys._getframe(2)
    return f"{frame.f_globals.get('__name__', '?')}:{frame.f_lineno}"


def _test_supplied(args: "tuple[Any, ...]", kwargs: "dict[str, Any]") -> bool:
    """The call names a stream, or a file under the temp directory: a ``.env`` the test wrote, not the developer's.

    The dashboard's ``safe_db_probe`` test writes ``tmp_path / ".env"`` and reads it with ``dotenv_values``; only a
    call that would find the checkout's own ``.env`` (no path, or a path outside the temp directory) is neutralised."""
    if kwargs.get("stream") is not None:
        return True
    target = kwargs.get("dotenv_path", args[0] if args else None)
    if target is None or not isinstance(target, (str, os.PathLike)):
        return False
    try:
        resolved = Path(os.fspath(target)).resolve()
        temp = Path(tempfile.gettempdir()).resolve()
    except (OSError, RuntimeError, TypeError):
        return False
    return resolved == temp or temp in resolved.parents


def _stub(spec: str, original: Callable[..., Any], record: _Record) -> Callable[..., Any]:
    returns: Any = {} if spec.endswith("dotenv_values") else (False if spec.endswith("load_dotenv") else None)

    def neutralised(*args: Any, **kwargs: Any) -> Any:
        if spec.startswith("dotenv") and _test_supplied(args, kwargs):
            return original(*args, **kwargs)
        record.call(f"{spec} (called from {_caller()})")
        return {} if isinstance(returns, dict) else returns

    neutralised.__wrapped__ = original  # type: ignore[attr-defined]
    neutralised.__name__ = getattr(original, "__name__", "neutralised")
    return neutralised


def _resolve(spec: str) -> "tuple[Any, str, Any]":
    """``(owner, attribute, value)`` for ``module:attr.sub``; ImportError/AttributeError when it does not exist."""
    import importlib

    module_name, _, path = spec.partition(":")
    owner: Any = importlib.import_module(module_name)
    parts = path.split(".")
    for part in parts[:-1]:
        owner = getattr(owner, part)
    return owner, parts[-1], getattr(owner, parts[-1])


def _settings(config: Any) -> OfflineSettings:
    cached: Optional[OfflineSettings] = config.stash.get(_SETTINGS, None)
    if cached is not None:
        return cached
    settings = OfflineSettings(marker=str(config.getini("offline_credentials_marker")) or DEFAULT_MARKER)
    if config.getini("offline_credential_vars"):
        settings.variables = list(config.getini("offline_credential_vars"))
    if config.getini("offline_credential_patterns"):
        settings.patterns = list(config.getini("offline_credential_patterns"))
    settings.loaders = list(config.getini("offline_dotenv_loaders"))
    config.hook.pytest_offline_credentials(config=config, settings=settings)
    try:
        settings.compiled = [re.compile(p) for p in settings.patterns]
    except re.error as exc:
        raise pytest.UsageError(f"offline_credential_patterns: {exc}") from exc
    config.stash[_SETTINGS] = settings
    return settings


def pytest_addhooks(pluginmanager: Any) -> None:
    pluginmanager.add_hookspecs(_Hookspecs)


def pytest_addoption(parser: Any) -> None:
    parser.addini("offline_credentials_marker", "offline_suite_without_credentials: the marker that exempts a test", default=DEFAULT_MARKER)
    parser.addini("offline_credential_vars", "offline_suite_without_credentials: variables blanked (replaces the default list)", type="linelist", default=[])
    parser.addini(
        "offline_credential_patterns", "offline_suite_without_credentials: name regexes blanked (replaces the default list)", type="linelist", default=[]
    )
    parser.addini("offline_dotenv_loaders", "offline_suite_without_credentials: module:attr .env readers to neutralise", type="linelist", default=[])


def pytest_configure(config: Any) -> None:
    marker = str(config.getini("offline_credentials_marker")) or DEFAULT_MARKER
    config.addinivalue_line("markers", f"{marker}: the test needs real credentials; offline_suite_without_credentials leaves its environment alone")


def _patch_bound_names(monkeypatch: Any, originals: "dict[int, tuple[Callable[..., Any], Any]]") -> None:
    """Replace ``from dotenv import load_dotenv`` bindings in already imported modules too."""
    for module in list(sys.modules.values()):
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue
        for attr, value in list(namespace.items()):
            hit = originals.get(id(value))
            if hit is not None and value is hit[1]:
                monkeypatch.setattr(module, attr, hit[0])


@pytest.fixture(autouse=True)
def _offline_suite_without_credentials(request: Any, monkeypatch: Any) -> Any:
    settings = _settings(request.config)
    if request.node.get_closest_marker(settings.marker) is not None:
        yield
        return
    record = _Record()
    request.node.stash[_RECORD] = record
    for name in [n for n in list(os.environ) if settings.blanked(n)] + settings.variables:
        monkeypatch.delenv(name, raising=False)
    originals: dict[int, tuple[Callable[..., Any], Any]] = {}
    for spec in (*DEFAULT_LOADERS, *settings.loaders):
        try:
            owner, attr, original = _resolve(spec)
        except ImportError:
            if spec in DEFAULT_LOADERS:
                continue
            raise pytest.UsageError(f"offline_dotenv_loaders: cannot import the module of {spec!r}") from None
        except AttributeError:
            raise pytest.UsageError(f"offline_dotenv_loaders: {spec!r} does not exist") from None
        stub = _stub(spec, original, record)
        monkeypatch.setattr(owner, attr, stub)
        if callable(original) and not isinstance(original, type):
            originals.setdefault(id(original), (stub, original))
    _patch_bound_names(monkeypatch, originals)
    monkeypatch.setattr(os, "environ", _recording_environ(os.environ, settings, record))
    yield


def _explain(record: _Record, settings: OfflineSettings) -> Optional[str]:
    if not (record.reads or record.calls):
        return None
    lines = ["This test ran without credentials (offline_suite_without_credentials)."]
    if record.reads:
        lines.append("It read these blanked variables and found nothing: " + ", ".join(record.reads))
    if record.calls:
        lines.append("It called these neutralised .env loaders:\n  " + "\n  ".join(record.calls))
    lines.append(
        f"Set what it needs with monkeypatch.setenv, stub the code that resolves it, or mark it "
        f"@pytest.mark.{settings.marker} when it truly needs the real ones."
    )
    return "\n".join(lines)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Any:
    outcome = yield
    report = outcome.get_result()
    record = item.stash.get(_RECORD, None)
    if record is None or not report.failed:
        return
    text = _explain(record, _settings(item.config))
    if text is not None:
        report.sections.append(("offline credentials", text))
