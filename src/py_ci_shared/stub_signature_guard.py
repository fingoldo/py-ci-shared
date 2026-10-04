"""pytest plugin: ``monkeypatch.setattr`` refuses a stub that cannot accept every parameter of the callable it replaces.

The run-time half of :mod:`py_ci_shared.stub_signature_parity` (the rule, the motivating cases and the relation to
``spec_bound_doubles`` are there). Checked against the LIVE object, so a re-exported, decorated or instance-bound
target is seen as the caller sees it, which the static scan cannot do.

Opt-in: ``stub_signature_guard = true`` in ``[tool.py_ci_shared]`` (the ``py_ci_shared`` plugin then loads it), or
``-p py_ci_shared.stub_signature_guard``. While loaded, ``pytest.MonkeyPatch.setattr`` compares the original value of
the attribute with the new one when both are callables with a signature; a mismatch raises
:class:`StubSignatureMismatchError` (an ``AssertionError``) at the ``setattr`` line, naming the test, the target and the
missing parameters. The error is raised in the test, not in production code, so no ``except Exception`` there can
turn it into "Unexpected error".

Not checked: a class or a ``Mock`` as either side (a Mock's ``(*args, **kwargs)`` accepts anything), a
``staticmethod``/``classmethod``/``property`` on a class, and a target without an inspectable signature.

Exemptions: ``@pytest.mark.stub_signature_allow("pkg.mod.name", ...)`` (``fnmatch`` globs on the dotted target) or
the ini option ``stub_signature_allow`` (one glob per line); ``@pytest.mark.no_stub_signature_check`` skips a test.
"""

from __future__ import annotations

import fnmatch
import inspect
from typing import Any, Optional

import pytest

from .stub_signature_parity import describe, missing_parameters, params_of_callable

__all__ = ["StubSignatureMismatchError", "check_stub"]

_CURRENT: dict[str, Any] = {"item": None, "config": None}
_NOTSET = object()


class StubSignatureMismatchError(AssertionError):
    """A monkeypatched stub cannot accept a parameter of the callable it replaces."""


def _label(obj: Any, name: str) -> str:
    owner = getattr(obj, "__name__", None) or type(obj).__name__
    module = getattr(obj, "__module__", None) if inspect.isclass(obj) else None
    return f"{module}.{owner}.{name}" if module else f"{owner}.{name}"


def _is_mock(value: Any) -> bool:
    return type(value).__module__.startswith("unittest.mock")


def check_stub(target: Any, name: str, original: Any, stub: Any) -> Optional[str]:
    """Why *stub* cannot stand in for *original* (``target.name``), or None when it can or the pair is not checked."""
    if not inspect.isroutine(original) or not callable(stub) or inspect.isclass(stub) or _is_mock(stub) or _is_mock(original):
        return None
    if inspect.isclass(target) and isinstance(inspect.getattr_static(target, name, None), (staticmethod, classmethod, property)):
        return None
    real = params_of_callable(original)
    fake = params_of_callable(stub)
    if real is None or fake is None:
        return None
    missing = missing_parameters(real, fake)
    if not missing:
        return None
    stub_name = getattr(stub, "__qualname__", repr(stub))
    return f"stub {stub_name}{describe(fake)} replaces {_label(target, name)}{describe(real)} but cannot accept {', '.join(missing)}"


def _allowed(dotted: str) -> bool:
    item = _CURRENT["item"]
    patterns: list[str] = []
    config = _CURRENT["config"]
    if config is not None:
        patterns += [str(p) for p in config.getini("stub_signature_allow") or []]
    if item is not None:
        if item.get_closest_marker("no_stub_signature_check") is not None:
            return True
        for mark in item.iter_markers("stub_signature_allow"):
            patterns += [str(a) for a in mark.args]
    return any(fnmatch.fnmatchcase(dotted, p) or fnmatch.fnmatchcase(dotted.rsplit(".", 1)[-1], p) for p in patterns)


def _original(monkeypatch: Any, target: Any, name: str) -> Any:
    """The value before THIS test's first patch of ``target.name``: a second setattr is checked against the real one."""
    for obj, attr, old in getattr(monkeypatch, "_setattr", []):
        if obj is target and attr == name:
            return old
    return getattr(target, name, _NOTSET)


_REAL_SETATTR = pytest.MonkeyPatch.setattr


def _checked_setattr(self: Any, target: Any, name: Any = _NOTSET, value: Any = _NOTSET, raising: bool = True) -> None:
    obj, attr, new = target, name, value
    if value is _NOTSET and isinstance(target, str) and name is not _NOTSET:
        from _pytest.monkeypatch import derive_importpath

        attr, obj = derive_importpath(target, raising)
        new = name
    if isinstance(attr, str) and new is not _NOTSET:
        original = _original(self, obj, attr)
        if original is not _NOTSET:
            problem = check_stub(obj, attr, original, new)
            if problem is not None and not _allowed(_label(obj, attr)):
                item = _CURRENT["item"]
                where = f"{item.nodeid}: " if item is not None else ""
                raise StubSignatureMismatchError(
                    f"{where}{problem}. Give the stub the missing parameters (or **kwargs); "
                    "a call passing them would raise TypeError inside the code under test"
                )
    if value is _NOTSET:
        return _REAL_SETATTR(self, target, name, raising=raising)
    return _REAL_SETATTR(self, target, name, value, raising=raising)


def pytest_addoption(parser: Any) -> None:
    parser.addini("stub_signature_allow", "stub_signature_guard: dotted targets (globs) whose stubs are not checked", type="linelist", default=[])


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "stub_signature_allow(*globs): stub_signature_guard does not check stubs of these targets")
    config.addinivalue_line("markers", "no_stub_signature_check: stub_signature_guard skips this test")
    _CURRENT["config"] = config
    pytest.MonkeyPatch.setattr = _checked_setattr  # type: ignore[method-assign]

    def _restore() -> None:
        pytest.MonkeyPatch.setattr = _REAL_SETATTR  # type: ignore[method-assign]
        _CURRENT.update(item=None, config=None)

    config.add_cleanup(_restore)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: Any, nextitem: Any) -> Any:
    _CURRENT["item"] = item
    try:
        yield
    finally:
        _CURRENT["item"] = None
