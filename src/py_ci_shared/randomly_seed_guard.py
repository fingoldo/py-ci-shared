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

from collections.abc import Callable
from importlib.metadata import entry_points

_SEED_SPACE = 2**32


def _bounded(reseed: Callable[[int], None]) -> Callable[[int], None]:
    def bounded(seed: int) -> None:
        reseed(seed % _SEED_SPACE)

    bounded.__wrapped__ = reseed  # type: ignore[attr-defined]
    return bounded


def bound_randomly_reseeders() -> bool:
    """Wrap pytest-randomly's entry-point reseeders so each receives a seed in ``[0, 2**32)``.

    Call from ``pytest_configure``. Returns whether anything was wrapped; a no-op when pytest-randomly is not
    installed, and idempotent. It fills pytest-randomly's own module-level cache of loaded reseeders, which the
    plugin consults before loading them itself, so the wrap holds for the whole session.
    """
    try:
        import pytest_randomly
    except ImportError:
        return False
    if not hasattr(pytest_randomly, "entrypoint_reseeds"):
        return False  # a version without the cache: nothing here knows how to intercept it
    loaded = pytest_randomly.entrypoint_reseeds
    if loaded is None:
        loaded = [ep.load() for ep in entry_points(group="pytest_randomly.random_seeder")]
    pytest_randomly.entrypoint_reseeds = [r if hasattr(r, "__wrapped__") else _bounded(r) for r in loaded]
    return bool(loaded)
