"""Profile one code-audit session over a repo's package, one scanner at a time, in one process.

Usage: PYTHONPATH=<py-ci-shared>/src python profile_code_audit.py <package-dir> [exclude_dir ...]

Pass 1 (cold caches) times every scanner in registration order, so the first scanner pays the parse of the whole tree and the rest show their own
logic. Pass 2 runs all scanners again under cProfile for a per-function breakdown. The file walk and a cold parse of every file are timed on their own.
"""

from __future__ import annotations

import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

from py_ci_shared import code_audit  # noqa: F401  (registers the scanners)
from py_ci_shared.code_audit import _base, registry
from py_ci_shared._core.source import parse_source

root = Path(sys.argv[1]).resolve()
exclude = frozenset(sys.argv[2:]) or frozenset({"legacy", "benchmarks", "profiling", "_benchmarks"})

t = time.perf_counter()
files = list(_base._iter_py_files(root, exclude))
print(f"walk: {len(files)} files in {time.perf_counter() - t:.3f}s")

t = time.perf_counter()
bad = 0
for f in files:
    try:
        parse_source(f)
    except Exception:
        bad += 1
print(f"cold parse of every file through _core.source: {time.perf_counter() - t:.1f}s ({bad} unparsable)")

names = list(registry._SCANNERS)
rows = []
t0 = time.perf_counter()
for name in names:
    t = time.perf_counter()
    findings, error = registry._run_scanner(name, registry._SCANNERS[name], root, exclude)
    rows.append((time.perf_counter() - t, name, len(findings), error))
total = time.perf_counter() - t0
print(f"\nPASS 1 (cold), {len(names)} scanners, total {total:.1f}s")
for dt, name, n, err in sorted(rows, reverse=True)[:30]:
    print(f"  {dt:7.2f}s  {name:48} findings={n}{'  ERROR ' + err if err else ''}")
print(f"  scanners under 0.5s: {sum(1 for r in rows if r[0] < 0.5)} of {len(rows)}, their total {sum(r[0] for r in rows if r[0] < 0.5):.1f}s")

profiler = cProfile.Profile()
t = time.perf_counter()
profiler.enable()
for name in names:
    registry._run_scanner(name, registry._SCANNERS[name], root, exclude)
profiler.disable()
print(f"\nPASS 2 (warm, under cProfile): {time.perf_counter() - t:.1f}s")
for key in ("cumulative", "tottime"):
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).sort_stats(key).print_stats(35)
    print(f"\n--- top 35 by {key} ---")
    print(out.getvalue()[:6500])
