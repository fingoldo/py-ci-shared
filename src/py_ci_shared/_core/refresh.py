"""Is a baseline refresh requested? One answer that holds under xdist and ``pytest.main([...])``.

``REFRESH_FLAG in sys.argv`` (six copies before this module, audit ARCH-21) is wrong twice: an xdist worker's
``sys.argv`` is ``['-c']``, and ``pytest.main([...])`` never touches ``sys.argv``. Sources, in order:

1. a pytest ``request`` or ``config`` passed in: the named option, then the generic ``--py-ci-refresh`` list;
2. env var ``PY_CI_SHARED_REFRESH``: comma list of flags (``--refresh-x-baseline``, ``refresh-x-baseline`` or the
   short gate name ``x``) or ``all``. Environment is inherited by xdist workers and subprocesses;
3. ``sys.argv``, as a last resort for callers that pass nothing.

A refresh only SHRINKS a baseline (drops entries that no longer fire, lowers counts and ceilings). Growing it, which
includes seeding a missing one with findings, needs the separate opt-in :func:`grow_requested` reads: the pytest option
``--py-ci-refresh-grow``, env ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1`` or ``py-ci-shared refresh --grow``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from typing import Any, Optional

ENV_VAR = "PY_CI_SHARED_REFRESH"
GENERIC_OPTION = "--py-ci-refresh"
GROW_ENV_VAR = "PY_CI_SHARED_REFRESH_ALLOW_GROW"
GROW_OPTION = "--py-ci-refresh-grow"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _aliases(flag: str) -> set[str]:
    bare = flag.lstrip("-")
    short = bare
    if short.startswith("refresh-"):
        short = short[len("refresh-") :]
    if short.endswith("-baseline"):
        short = short[: -len("-baseline")]
    return {flag, bare, "--" + bare, short}


def _tokens(value: object) -> list[str]:
    if value is None or value is False:
        return []
    if value is True:
        return ["all"]
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, Iterable):
        out: list[str] = []
        for v in value:
            out.extend(_tokens(v))
        return out
    return []


def _hit(tokens: Iterable[str], flag: str) -> bool:
    names = _aliases(flag)
    return any(t == "all" or t in names for t in tokens)


def _config_of(request_or_config: Any) -> Any:
    return getattr(request_or_config, "config", request_or_config)


def _getoption(config: Any, name: str) -> Any:
    try:
        return config.getoption(name)
    except (ValueError, AttributeError):  # option not registered in this session
        return None


def refresh_requested(flag: str, request_or_config: Optional[Any] = None) -> bool:
    """True when a refresh of the baseline behind *flag* (e.g. ``"--refresh-value-asserts-baseline"``) is asked for."""
    if request_or_config is not None:
        config = _config_of(request_or_config)
        if _getoption(config, flag):
            return True
        if _hit(_tokens(_getoption(config, GENERIC_OPTION)), flag):
            return True
    if _hit(_tokens(os.environ.get(ENV_VAR)), flag):
        return True
    argv = sys.argv[1:]
    if flag in argv:
        return True
    generic: list[str] = []
    for i, arg in enumerate(argv):
        if arg.startswith(GENERIC_OPTION + "="):
            generic.append(arg.split("=", 1)[1])
        elif arg == GENERIC_OPTION and i + 1 < len(argv):
            generic.append(argv[i + 1])
    return _hit(_tokens(generic), flag)


def grow_requested(request_or_config: Optional[Any] = None) -> bool:
    """True when a refresh may ADD baseline entries or raise counts: ``--py-ci-refresh-grow`` (pytest option or argv) or
    ``PY_CI_SHARED_REFRESH_ALLOW_GROW`` set to 1/true/yes/on. Without it a refresh only removes what no longer fires."""
    if request_or_config is not None and _getoption(_config_of(request_or_config), GROW_OPTION):
        return True
    if os.environ.get(GROW_ENV_VAR, "").strip().lower() in _TRUTHY:
        return True
    return GROW_OPTION in sys.argv[1:]


def grow_hint() -> str:
    """How to opt into a growing refresh, for failure messages."""
    return f"set {GROW_ENV_VAR}=1, pass {GROW_OPTION} to pytest or --grow to `py-ci-shared refresh`, or call with grow=True"


def register_refresh_options(parser: Any, flags: Iterable[str] = (), *, help_suffix: str = "baseline") -> None:
    """Register ``--py-ci-refresh`` and each named flag on a pytest ``parser`` (from ``pytest_addoption``).

    Idempotent: an option some other conftest already registered is left alone.
    """
    _add(
        parser,
        GENERIC_OPTION,
        action="append",
        nargs="?",
        const="all",
        default=[],
        help=f"py-ci-shared: comma list of baseline flags/gate names to rewrite; bare or 'all' for every one (env: {ENV_VAR})",
    )
    _add(
        parser,
        GROW_OPTION,
        action="store_true",
        default=False,
        help=f"py-ci-shared: let a refresh ADD baseline entries and raise counts, not only drop stale ones (env: {GROW_ENV_VAR}=1)",
    )
    for flag in flags:
        _add(parser, flag, action="store_true", default=False, help=f"rewrite the {help_suffix} instead of comparing")


def _registered(parser: Any, name: str) -> bool:
    """Does a pytest ``Parser`` already hold *name* in any group? pytest adds options to argparse only when it builds its
    parser, so on some Python versions a duplicate surfaces there, after ``addoption`` returned."""
    groups = [getattr(parser, "_anonymous", None), *getattr(parser, "_groups", ())]
    for group in groups:
        for option in getattr(group, "options", ()):
            names = option.names() if callable(getattr(option, "names", None)) else ()
            if name in names:
                return True
    return False


def _add(parser: Any, name: str, **kwargs: Any) -> None:
    if _registered(parser, name):
        return
    try:
        parser.addoption(name, **kwargs)
    except (ValueError, argparse.ArgumentError):
        # Already registered: pytest raises ValueError for a clash inside one group, argparse raises ArgumentError for one
        # across groups (this package's pytest11 plugin registers --py-ci-refresh in its own group before any conftest).
        pass
