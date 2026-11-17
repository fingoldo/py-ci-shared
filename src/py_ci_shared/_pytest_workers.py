"""How many xdist workers a pytest run gets when the command line gives none: a count from free RAM, not from the CPU count.

A worker imports the whole project, so the RAM of a small machine is what runs out first. The count is the free memory
minus a reserve, divided by the memory one worker needs, capped by the physical cores minus one for the parent process.
Fewer than :data:`MIN_WORKERS` is serial, because one worker only adds the cost of a subprocess.

The environment can override the plan. ``PY_CI_SHARED_WORKERS`` set to an integer uses that count exactly (``0`` is serial),
and ``CI`` set to a true value is serial: a shared runner's free memory says nothing about a developer machine.
``PY_CI_SHARED_WORKER_GIB`` sets the per-worker estimate. The estimate is a guess until it is measured, so it is named.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

__all__ = ["DEFAULT_WORKER_GIB", "MIN_WORKERS", "RESERVE_FLOOR_GIB", "plan_workers"]

GIB = 1024**3
#: Guess: the resident memory of one worker after collecting a project of this owner's size. Measured values replace it.
DEFAULT_WORKER_GIB = 1.5
#: The memory kept free whatever the total, for the OS, the editor and the parent process.
RESERVE_FLOOR_GIB = 2.0
#: One worker is a subprocess without a parallel gain, so a plan below this is serial.
MIN_WORKERS = 2

_TRUE = {"1", "true", "yes", "on"}


def plan_workers(
    free_bytes: int,
    total_bytes: int,
    physical_cores: int,
    env: Optional[Mapping[str, str]] = None,
) -> int:
    """The worker count for a run with *free_bytes* of RAM free out of *total_bytes* on a machine with *physical_cores* cores.

    The cap is the physical cores minus one for the parent process: hyperthreads share an execution unit, so counting them
    as workers buys little. ``0`` means serial. An explicit ``PY_CI_SHARED_WORKERS`` integer is returned as given (a negative value is serial).
    """
    environ: Mapping[str, str] = env or {}
    explicit = environ.get("PY_CI_SHARED_WORKERS", "").strip()
    if explicit:
        try:
            return max(0, int(explicit))
        except ValueError:
            return 0
    if environ.get("CI", "").strip().lower() in _TRUE:
        return 0
    per_worker = _gib(environ.get("PY_CI_SHARED_WORKER_GIB"), DEFAULT_WORKER_GIB) * GIB
    reserve = max(RESERVE_FLOOR_GIB * GIB, 0.2 * total_bytes)
    by_memory = int((free_bytes - reserve) // per_worker) if free_bytes > reserve else 0
    workers = min(by_memory, max(physical_cores - 1, 0))
    return workers if workers >= MIN_WORKERS else 0


def _gib(text: Optional[str], default: float) -> float:
    try:
        value = float(text) if text else default
    except ValueError:
        return default
    return value if value > 0 else default
