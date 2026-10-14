"""How many test or scan workers this machine can spare right now.

A fixed ``-n 2`` is wrong on a machine another job is already saturating, and ``-n auto`` is wrong on one shared
with other sessions. The budget is a share of the *free* physical cores: a core counts as free in proportion to the
CPU that is idle, and hyperthreads do not count, because a second logical core on a busy physical core gives almost
no extra throughput for CPU-bound work.

``psutil`` is optional. Without it the budget falls back to half of the physical cores, measured as if idle, so a
checkout without ``psutil`` still gets a sensible value and never fewer than one worker.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

ENV_OVERRIDE = "PY_CI_WORKERS"


def worker_count_from(physical_cores: int, busy_percent: float, share: float = 0.5) -> int:
    """The worker count for ``physical_cores`` of which ``busy_percent`` is in use: ``share`` of the free ones, never below one.

    Pure, so the arithmetic is testable without a live machine. ``busy_percent`` is clamped to [0, 100].
    """
    if physical_cores < 1:
        return 1
    busy = min(100.0, max(0.0, busy_percent))
    free = physical_cores * (100.0 - busy) / 100.0
    return max(1, int(free * share))


def _physical_cores() -> int:
    try:
        import psutil  # type: ignore[import-not-found]

        cores = psutil.cpu_count(logical=False)
        if cores:
            return int(cores)
    except ImportError:
        pass
    logical = os.cpu_count() or 2
    return max(1, logical // 2)


def _busy_percent(interval: float) -> float:
    try:
        import psutil  # type: ignore[import-not-found]

        return float(psutil.cpu_percent(interval=interval))
    except ImportError as exc:
        logger.debug("psutil is not installed, busy percent is 0: %s", type(exc).__name__)
        return 0.0


def available_workers(share: float = 0.5, sample_seconds: float = 0.5) -> int:
    """The worker count to use now, honouring the ``PY_CI_WORKERS`` override when it is set to a positive integer.

    Samples the CPU for ``sample_seconds``, so call it once per run, not once per test.
    """
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override.isdigit() and int(override) >= 1:
        return int(override)
    return worker_count_from(_physical_cores(), _busy_percent(sample_seconds), share)


def main(argv: list[str] | None = None) -> int:
    """Print the worker count for this machine, for use in shell commands and hook entries."""
    args = sys.argv[1:] if argv is None else argv
    share = float(args[0]) if args else 0.5
    sys.stdout.write(str(available_workers(share)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
