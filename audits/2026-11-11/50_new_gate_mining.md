# 50 New gate mining (round 2026-11-11)

Read-only. No file under src/, tests/ or docs was edited. The full pytest suite was not run.

Sources read: audits/README.md, audits/TRACKER.md (dimension 50 rows), audits/2026-11-10/50_new_gate_mining.md,
the README gate catalogue (line 326 and neighbours), src/py_ci_shared/registry.toml (256 gate entries, names
compared against each candidate), and configs/consumers.toml.

Scan method: two throwaway AST scripts run with `python -I` over the tracked `*.py` files of consumer repos,
excluding files whose path contains `test`. Hit counts are real counts from those scans. Where a scan was
cut short or not run, that is stated per candidate.

Repos scanned: mlframe, pyutilz, llm_bench, algopacksimple, noema_app (all candidate shapes); glossum_backend_scripts
(open-without-encoding and raise-without-from only, the scan timed out on the other shapes). social was scanned for
open-without-encoding and raise-without-from only.

## Already covered or tracked (checked before any candidate)

| id | class | status | where |
|---|---|---|---|
| 2026-11-10 50-F1 | loop whose step can be zero or None never advances | TRACKED, DEFERRED | TRACKER row 54 |
| 2026-11-10 50-F2 | counter advanced before the write it counts | TRACKED, DEFERRED | TRACKER row 55 |
| 2026-11-10 50-F3 | raw UPDATE/DELETE without a row-count check | COVERED by gate `raw_write_row_count` (shipped 1.23.10, README line 326). The TRACKER row 56 still reads OPEN and is stale. Not edited (read-only). | README:326 |
| 2026-11-10 50-F4 | per-item log line in a network page loop | TRACKED, DEFERRED | TRACKER row 57 |
| 2026-11-10 50-F5 | test assertion depends on platform or pandas null repr | TRACKED, DEFERRED | TRACKER row 58 |
| 2026-11-10 50-F6 | doctests holding on one pandas major | TRACKED, WON'T FIX as static gate | TRACKER row 59 |
| `shell=True` with interpolated command | COVERED by bandit (`bandit_warn`, B602). Measured: no real `shell=True` call in the consumer source scanned. | registry.toml `bandit_warn` |
| raise inside `except` without `from` | COVERED by ruff B904. Consumers select the `B` family in their base ruff config (pyutilz pyproject.toml:496, glossum_backend_scripts pyproject.toml:463). | ruff base select |
| lambda stored in a loop, capturing the loop variable | COVERED by ruff B023 (`B` family). | ruff base select |

## Candidates

## 50-S7 (P3) text `open()` without `encoding=` (Windows locale defect)

- Defect class: `open(path)` in text mode with no `encoding=`. On a Windows host the locale codec (cp1251, cp1252) is used, so a UTF-8 file fails or decodes wrongly.
- Real example that motivates it: glossum_backend_scripts commit 9c832ca9 (2026-09-24, "The g2p-mismatch check was off on this machine for every language it covers"). The cause was a third-party read, not a call in glossum source. The workaround is `glossum/llm/utils/_ipa.py:198` (`_utf8_default_text_open`), which patches the default for the duration of the epitran build because panphon reads its table with no encoding.
- Measured on own code (non-test, non-script, non-upwork production files):
  - glossum_backend_scripts: 0 hits.
  - pyutilz: 0 hits in all of src and tests-excluded.
  - mlframe: 14 hits, all under `benchmarks/` or `src/**/_benchmarks/`. The writes are ASCII JSON/text, so the defect cannot fire on them.
  - glossum `upwork/` and `scripts/` subtrees: about 90 hits in total (scrapers, probes). Not production library code.
- Why a gate on own code would not have caught 9c832ca9: the failing read was inside a third-party package. A static check of our own `open()` calls has no hit that would have shown it.
- Expected false-positive rate: low on library code (0 to 14 hits, all benchmarks). High if the benchmark and scraper trees are included, because those are ASCII-only or one-off.
- Disposition: WON'T FIX as a static gate. Reason: zero production hits in own code, and the real defect class (a third-party reader) is not visible to a source check. The fix pattern in `_ipa.py` is the reference if it recurs. Not a dimension-50 item to build.

## 50-S8 (P3) while True loop with a sleep and no exit

- Defect class: `while True:` with a `sleep` and no `break`, `return` or `raise` in its body, so the loop never ends.
- Measured: 0 hits in mlframe, pyutilz, llm_bench, noema_app. 2 hits in algopacksimple: `alor_bot.py:2481` and `live_predictions.py:13`. Both are intentional daemon loops (the live trading loop and the live predictions loop, which has a documented `sys.exit(2)` exit path and a backoff comment).
- Expected false-positive rate: 2 of 2 hits are intentional, so 100% under this heuristic. Daemon loops are the normal case.
- Disposition: WON'T FIX. The shape is correct for daemons. A real defect would need a retry-count check, and that is a different detector (retry loops with no cap), which the 2026-11-10 round did not propose. Not built here.

## 50-S9 (P3) dict or set mutated while the same object is being iterated

- Defect class: `for k in d:` (or `d.keys()`, `d.items()`, `d.values()`, or a plain name) whose body deletes a key or calls `pop`, `remove` or `discard` on the same name. Python raises `RuntimeError` on a changed dict, and silently skips items on a list.
- Measured: 0 hits in mlframe, pyutilz, llm_bench, algopacksimple, noema_app. Not measured in glossum_backend_scripts or social (the scan timed out on glossum; social was not run for this shape).
- Expected false-positive rate: not measurable here (zero hits). The shape is cheap to check.
- Disposition: WON'T FIX on the evidence available. No real example was found in five repos, and a candidate with no real example does not meet the bar for a gate. Re-open if glossum or social produces a hit.

## 50-S10 (P3) handler or command shell-out built from interpolated text

- Covered by bandit (see the table above). Not a new gate.

## Summary

- Candidates: 3 new (50-F7, 50-F8, 50-F9), plus 50-F10 noted as covered. Total IDs in this round: 4.
- Built: 0.
- DEFERRED: 0 (none had a measured hit count that justified a build).
- WON'T FIX: 3 (50-F7, 50-F8, 50-F9).
- Already covered or tracked: 9. Of these, 4 are already covered by a gate or a ruff/bandit rule (2026-11-10 50-F3 by `raw_write_row_count`, `shell=True` by bandit, raise-without-from by ruff B904, lambda in loop by ruff B023). 5 are tracked as DEFERRED or WON'T FIX in TRACKER rows 54 to 59 (2026-11-10 50-F1, 50-F2, 50-F4, 50-F5, 50-F6).
- TRACKER correction for the owner: the row for 2026-11-10 50-F3 (TRACKER line 56) still reads OPEN, but `raw_write_row_count` ships it. Not edited here.
- Caveats: the glossum scan timed out for the loop, dict-mutation and sleep shapes. Social was scanned only for open-without-encoding and raise-without-from. Autopsia and polyvocab_app were not scanned at all. The AST scans did not check guard recognition, so the zero-hit results cover only the shapes exactly as written.
