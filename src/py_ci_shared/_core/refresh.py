"""Is a baseline refresh requested? One answer that holds under xdist and ``pytest.main([...])``.

``REFRESH_FLAG in sys.argv`` (six copies before this module, audit ARCH-21) is wrong twice: an xdist worker's
``sys.argv`` is ``['-c']``, and ``pytest.main([...])`` never touches ``sys.argv``. Sources, in order:

1. a pytest ``request`` or ``config`` passed in: the named option, then the generic ``--py-ci-refresh`` list;
2. env var ``PY_CI_SHARED_REFRESH``: comma list of flags (``--refresh-x-baseline``, ``refresh-x-baseline`` or the
   short gate name ``x``) or ``all``. Environment is inherited by xdist workers and subprocesses;
3. ``sys.argv``, as a last resort for callers that pass nothing.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional
from collections.abc import Iterable

ENV_VAR = "PY_CI_SHARED_REFRESH"
GENERIC_OPTION = "--py-ci-refresh"


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
    for flag in flags:
        _add(parser, flag, action="store_true", default=False, help=f"rewrite the {help_suffix} instead of comparing")


def _add(parser: Any, name: str, **kwargs: Any) -> None:
    try:
        parser.addoption(name, **kwargs)
    except ValueError:
        pass  # already registered (a repo with more than one conftest.py in the chain)
