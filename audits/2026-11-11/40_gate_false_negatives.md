# Dimension 40: gate false negatives (round 2026-11-11)

Read-only audit of `src/py_ci_shared`. No file under `src/` or `tests/` was edited. The only runtime check was a scratch script in the session scratchpad.

Already tracked and not re-reported: the 2026-11-07 40-F1 to 40-F13 rows, the 2026-11-10 40-F1 to 40-F4 rows (40-F1 and 40-F2 RESOLVED, 40-F3 and 40-F4 OPEN), 35-F2 (parse-cache split, RESOLVED).

Checked and not findings: the `_core/runner.py` catch-all reports ERROR, not PASS; `optional_truthiness.py` reports unparsable files; the 2026-11-10 "best-effort skip" class (source_text_assertions, phantom_code_references, nginx_location_headers, stale_source_citations) is already covered by 40-F3 and 40-F4 and is not repeated here.

## 40-S5 (P2) code_audit.run_all_checked is fail-open for every unparsable file: no finding and no `failed` entry

- **File:line:** `src/py_ci_shared/code_audit/_base.py:260-272` (`_safe_parse` returns `None` for `SourceReadError`/`SourceParseError`, logging at DEBUG only). Used by 90 scanners under `src/py_ci_shared/code_audit/`, for example `bare_except.py:56-58` and `broad_except.py:259-261`, each doing `if tree is None: continue`. The aggregator is `code_audit/registry.py:664-704` (`run_all_checked`), which returns `(findings, failed)` where `failed` only holds scanners that raise.
- **Evidence:** scratch run in the session scratchpad. A directory containing `m.py` with a syntax error (a bare `except:` plus an unterminated `y = (`) gives `run_all_checked(d, parallel=False)` returning `findings == []` and `failed == {}`. A control directory with a parsable `n.py` containing the same bare `except: pass` returns 2 findings. So the broken file is invisible to every scanner, and the result is indistinguishable from clean. Nothing in `registry.py` or `_base.py` records the unparsable path (`grep unparse` in `code_audit/` finds only docstrings).
- **Failure scenario:** a consumer repo has `src/app/handler.py` with a bare `except:` on line 12 and also a syntax error elsewhere in the same file (for example a merge-conflict leftover). `run_all` returns no `bare_except` finding for that file, `failed` is empty, and a gate built on it reports clean. The only backstop is that pytest or an import fails on the syntax error, which does not happen when the file is a script or is not imported, so the scanner gap is silent there.
- **Scope note:** this is a different site set from 40-F3. 40-F3 covers the handful of gates that catch `SourceError` directly. 40-F5 covers the shared code_audit aggregator used by 90 scanners, which the 2026-11-10 round did not inspect.
- **Disposition: RESOLVED-by-fix.** `_safe_parse` should record each unreadable or unparsable path in a per-run set (process-safe, because scanners run in a pool). `run_all_checked` should then add one `unparsable: <path>` finding per recorded path (or merge them into `failed` with the path named), so a gate that reads findings as complete treats the run as incomplete. Regression test: a tree with one syntax-error file and one bare `except` in another file must produce a finding, or a non-empty `failed`, that names the broken file. Note for the owner: this changes the output of every consumer with a syntax-error file, so it should be announced in CHANGELOG as a behaviour change.

## Summary

One new finding this round: 40-F5 (P2), the code_audit aggregator silently drops unparsable files for all of its scanners. No P0 or P1 findings. Verified by a scratch run, not only by reading the code.

Counts: P0: 0. P1: 0. P2: 1 (40-F5). P3: 0.
Already tracked items encountered: 4 (2026-11-10 40-F1, 40-F2, 40-F3, 40-F4), plus 35-F2 for the parse-cache split, for 5 in total.
