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
#: Free memory a worker needs. A worker loads the package under test with its numeric and GPU libraries (torch alone maps several GB of DLLs), so
#: a worker that starts on a machine with less than this free starts paging, and the DLL loads of the next worker fail with a misleading error.
RAM_PER_WORKER_BYTES = 4 * 2**30


def worker_count_from(physical_cores: int, busy_percent: float, share: float = 0.5) -> int:
    """The worker count for ``physical_cores`` of which ``busy_percent`` is in use: ``share`` of the free ones, never below one.

    The share is rounded half up, not truncated: truncation turned 1.65 free-core-shares into one worker on a 4-core machine that was
    17.5% busy, which is the case this budget exists for. Pure, so the arithmetic is testable without a live machine. ``busy_percent``
    is clamped to [0, 100].
    """
    if physical_cores < 1:
        return 1
    busy = min(100.0, max(0.0, busy_percent))
    free = physical_cores * (100.0 - busy) / 100.0
    return max(1, int(free * share + 0.5))


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


def worker_count_by_memory(workers: int, free_bytes: "int | None", per_worker_bytes: int = RAM_PER_WORKER_BYTES) -> int:
    """*workers* capped so each has *per_worker_bytes* of free memory, never below one. ``free_bytes=None`` (no measurement) leaves the count as it is.

    Pure, so the cap is testable without a live machine.
    """
    if free_bytes is None or per_worker_bytes <= 0:
        return max(1, workers)
    return max(1, min(workers, int(free_bytes // per_worker_bytes)))


def _free_memory_bytes() -> "int | None":
    try:
        import psutil  # type: ignore[import-not-found]

        return int(psutil.virtual_memory().available)
    except ImportError as exc:
        logger.debug("psutil is not installed, free memory is not measured: %s", type(exc).__name__)
        return None


def available_workers(share: float = 0.5, sample_seconds: float = 0.5) -> int:
    """The worker count to use now, honouring the ``PY_CI_WORKERS`` override when it is set to a positive integer.

    The CPU share gives the first count; it is then capped so each worker has :data:`RAM_PER_WORKER_BYTES` of free memory, never below one.
    Samples the CPU for ``sample_seconds``, so call it once per run, not once per test.
    """
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override.isdigit() and int(override) >= 1:
        return int(override)
    by_cpu = worker_count_from(_physical_cores(), _busy_percent(sample_seconds), share)
    return worker_count_by_memory(by_cpu, _free_memory_bytes())


def main(argv: list[str] | None = None) -> int:
    """Print the worker count for this machine, for use in shell commands and hook entries."""
    args = sys.argv[1:] if argv is None else argv
    share = float(args[0]) if args else 0.5
    sys.stdout.write(str(available_workers(share)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
