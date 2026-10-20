Meta-test profile, 2026-10-21: cProfile of tests/test_self_gates.py (57 tests, one process, serial).

Before: 1365 s. After the scandir walk: 595 s (both under cProfile, same machine).

### PROF-1 (P1) -- gate_cache._stat_signature walks and stats the whole tree for every gate
**Disposition:** RESOLVED -- 94% of the run (1309 of 1390 s) was this function. It now walks with os.scandir, whose entries carry size and time on Windows, so a file costs no extra system call. Entries and order are those of the os.walk version it replaced (checked on the repository and on a tree with links, broken links and skipped directories). Commit 2a7b663.

### PROF-2 (Low) -- config._inside_root resolves every expanded corpus path
**Disposition:** OPEN -- about 17 s of 595 s (about 3%). Each path costs two resolve() calls, about 1.9 ms each on Windows. Hoisting the repo root's resolve saves about half of it; the per-path check against symlinks must stay, so it is not worth a change to the containment rule on its own.

### PROF-3 (P2) -- every gate walks the same tree again
**Disposition:** OPEN, DESIGN DECISION -- after PROF-1 each gate still costs about 5 s of walk, so 56 gates spend about 280 s on the same tree. Removing the repeat needs a signature shared between gates of one run, invalidated when any file changes between gates. Done wrongly it would let a gate read a stale cache entry, so it is not implemented without a decision on the invalidation rule.
