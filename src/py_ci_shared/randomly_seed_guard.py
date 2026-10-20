"""Keep pytest-randomly's per-test seed inside the 32-bit range its third-party reseeders accept.

pytest-randomly (4.0.1 through 5.0.0, checked 2026-09-24) computes each test's seed as ``randomly_seed + offset``,
where the offset is a CRC32 of the node id, so the sum reaches past 2**32 about half the time. It reduces the value
itself before seeding numpy (``np_random.seed(seed % 2**32)``), but hands the RAW value to every function registered
under the ``pytest_randomly.random_seeder`` entry point. thinc (installed with spaCy) registers
``fix_random_seed``, which calls ``numpy.random.seed(seed)`` and raises "Seed must be between 0 and 2**32 - 1" -- at
setup and teardown of roughly half of all tests, in every project on an interpreter that has spaCy and
pytest-randomly together. Disabling the plugin hides it and throws away test-order randomisation; this reduces the
seed the same way pytest-randomly already does for numpy, so ordering and per-test determinism are unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import EntryPoint, entry_points
from typing import Any

logger = logging.getLogger(__name__)

_SEED_SPACE = 2**32

#: Set on every wrapper this module makes. ``__wrapped__`` cannot serve as the marker: every
#: ``functools.wraps``-decorated reseeder carries it, and such a reseeder would then never be bounded.
_MARKER = "_py_ci_shared_bounded"


def _bounded(reseed: Callable[[int], None]) -> Callable[[int], None]:
    def bounded(seed: int) -> None:
        reseed(seed % _SEED_SPACE)

    bounded.__wrapped__ = reseed  # type: ignore[attr-defined]
    setattr(bounded, _MARKER, True)
    return bounded


def _entry_points_in(group: str) -> list[EntryPoint]:
    """Entry points of *group* on every supported Python: ``entry_points(group=...)`` exists only from 3.10, and 3.9
    returns a dict of groups instead."""
    found = entry_points()
    if hasattr(found, "select"):
        return list(found.select(group=group))
    return list(found.get(group, ()))  # type: ignore[union-attr]


def _randomly_inactive(config: Any) -> bool:
    """True when *config* says pytest-randomly is not running in this session (``-p no:randomly``, or it is not registered)."""
    manager = getattr(config, "pluginmanager", None)
    if manager is None:
        return False  # no session to ask: keep the old behaviour
    return bool(manager.is_blocked("randomly") or not manager.has_plugin("randomly"))


def bound_randomly_reseeders(config: Any = None) -> bool:
    """Wrap pytest-randomly's entry-point reseeders so each receives a seed in ``[0, 2**32)``.

    Call from ``pytest_configure`` with the session's *config*. Returns whether anything was wrapped; a no-op when pytest-randomly is not
    installed or not active in the session (loading the reseeders imports every library that registers one, thinc and torch among them: 13 s
    on a machine with those installed, for a plugin that never reseeds anything), and idempotent. It fills pytest-randomly's own module-level cache of loaded reseeders, which the
    plugin consults before loading them itself, so the wrap holds for the whole session.
    """
    if _randomly_inactive(config):
        return False
    try:
        import pytest_randomly
    except ImportError as exc:
        logger.debug("pytest-randomly is not installed: %s", type(exc).__name__)
        return False
    if not hasattr(pytest_randomly, "entrypoint_reseeds"):
        return False  # a version without the cache: nothing here knows how to intercept it
    loaded = pytest_randomly.entrypoint_reseeds
    if loaded is None:
        loaded = [ep.load() for ep in _entry_points_in("pytest_randomly.random_seeder")]
    pytest_randomly.entrypoint_reseeds = [r if getattr(r, _MARKER, False) else _bounded(r) for r in loaded]
    return bool(loaded)
