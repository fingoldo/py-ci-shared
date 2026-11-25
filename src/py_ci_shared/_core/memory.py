"""How much memory this host has, and what a parsed corpus costs, for the code that decides how much to keep or how many workers to start.

Measured on mlframe (3586 files, 37.7 MB of source in ``src``; 6377 files, 37 MB in ``tests``; CPython 3.11): the parse cache holds a module's text and its tree, about **37 times** the source
bytes (the trees alone 28 times: every ``ast`` node is an object with a ``__dict__``), and the derived tables of ``node_index`` (descendant lists, the type index, ``parent_map``) add about 45 percent of that, so a corpus costs
roughly :data:`TREE_BYTES_PER_SOURCE_BYTE` (55) times its source in every process that scans it. A pool of workers therefore holds N copies of it, and a process that scanned two corpora holds both.
Reading the same trees back from a pickle on disk instead of parsing them was measured and does not help: loading 3586 trees took 19.8 s against 14.8 s to parse them, and the resident size after loading is the same (1065 MB).
"""

from __future__ import annotations

import os
from typing import Optional

#: Bytes of resident memory one source byte costs once parsed and indexed (see the module docstring: 37 for text + trees, 45 percent more for the derived tables).
TREE_BYTES_PER_SOURCE_BYTE = 55
#: Resident bytes of a bare worker process (interpreter, the audit modules, a few caches) before it parses anything.
WORKER_BASE_BYTES = 400 * 2**20


def total_memory_bytes() -> Optional[int]:
    """Physical memory of the host, or ``None`` when ``psutil`` is not installed."""
    try:
        import psutil
    except ImportError:
        return None
    return int(psutil.virtual_memory().total)


def available_memory_bytes() -> Optional[int]:
    """Memory a new allocation can still get (physical memory that is free or reclaimable), or ``None`` when ``psutil`` is not installed."""
    try:
        import psutil
    except ImportError:
        return None
    return int(psutil.virtual_memory().available)


def process_rss_bytes() -> Optional[int]:
    """Resident size of this process, or ``None`` when ``psutil`` is not installed."""
    try:
        import psutil
    except ImportError:
        return None
    return int(psutil.Process(os.getpid()).memory_info().rss)


def parse_cache_budget_bytes() -> Optional[int]:
    """Bytes the parse cache and the tables derived from it may hold before the cache is dropped: ``PY_CI_SHARED_PARSE_CACHE_MB`` when set (``0`` or negative: no limit),
    else half of the physical memory capped at 8 GiB, or ``None`` (no limit) when the host memory is unknown."""
    raw = os.environ.get("PY_CI_SHARED_PARSE_CACHE_MB", "").strip()
    if raw:
        try:
            mb = int(raw)
        except ValueError:
            mb = -1
        return mb * 2**20 if mb > 0 else None
    total = total_memory_bytes()
    if total is None:
        return None
    return min(total // 2, 8 * 2**30)


def workers_that_fit(corpus_bytes: int, margin_bytes: int = 512 * 2**20) -> Optional[int]:
    """How many workers that each parse and index a corpus of ``corpus_bytes`` of source fit in the memory that is available now, or ``None`` when the host memory is unknown.

    The estimate is ``(available - margin) // (corpus_bytes * TREE_BYTES_PER_SOURCE_BYTE + WORKER_BASE_BYTES)``; it is at least 0 (not even one worker fits).
    """
    available = available_memory_bytes()
    if available is None:
        return None
    per_worker = int(corpus_bytes) * TREE_BYTES_PER_SOURCE_BYTE + WORKER_BASE_BYTES
    return max(0, int((available - margin_bytes) // per_worker))
