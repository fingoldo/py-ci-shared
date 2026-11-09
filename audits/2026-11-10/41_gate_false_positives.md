# Dimension 41: gate false positives (round 2026-11-10)

Read-only audit of `src/py_ci_shared` and `tests`. The 2026-11-07 round (41-F1 to 41-F14) and the tracker rows for it were read first and are not re-reported. Each finding below was checked by running the real finder on a fixture repository under the scratchpad (`a41/r*/pkg/*.py`), not by reading code alone. No file under `src/` or `tests/` was edited. Because this auditor is read-only, each disposition is a proposed fix for a later pass, not an applied change.

Probes that came back clean (no false positive, so no finding): `possibly_unbound_local` on `while True`/break, `try/raise`, `sys.exit` in an else branch, `for`/`else`, `with`, and `match` (r1); `pytest.fail`, `os._exit` and `raise SystemExit` in a `try` handler (r5); `unread_function_params` on interface methods, `raise NotImplementedError` stubs and docstring-only bodies (r2); `hardcoded_seed_in_library` on the `if rng is None:` fallback, `seed if seed is not None else 0` and `seed or 0` (r7); `systemexit_in_library` on a `main()` behind a `__main__` guard (r7); `swallowed_exceptions` on `except FileNotFoundError: pass` and `log.debug` in a handler (r6).

---

## 41-F1 (P3) printed_advice treats the noun "supply" as an instruction

- Location: `src/py_ci_shared/printed_advice.py:38`, the alternative `\bsupply (?:a |an |more )?\w+` inside `ADVICE_RE`. The optional article is optional, so any "supply <word>" matches.
- Evidence: fixture `a41/r8/pkg/msgs.py:6` is `log.warning("No supply data for %s", ticker)`. Running `find_printed_advice` on it returns `pkg/msgs.py::report#1 6 'supply data'`. The message reports a missing data feed and gives the reader no action, yet it is registered as advice, so the meta test demands a table entry and a test that "performs the advice". The same rule also matches the descriptive phrases "supply chain", "supply voltage" and "supply side" in any repository whose domain uses these words.
- Scenario: a data pipeline logs "No supply data for ticker X" and the repository meta test `assert_printed_advice_registered` fails with an unregistered key. The author either adds a meaningless test to the table or edits the message to avoid the word, which is the wrong fix.
- Correct behaviour: "supply" counts as advice only in an imperative with a parameter, such as "supply a <name>" or "supply more <name>" where the next word is a parameter-like identifier. A bare "supply <noun>" that is followed by "data", "chain", "side" or "voltage" is not advice.
- Proposed disposition: RESOLVED-by-fix (proposed). Drop the bare `supply \w+` branch and keep `supply (?:a |an |more )\w+` with the article or "more" required, then add a `tests/` case that asserts "No supply data for %s" yields no PrintedAdvice and "supply more groups" still does. Not applied, because this auditor is read-only.
- Severity note: P3. It produces a failing meta test on correct code, with a workaround that is cheap but misleading.

## 41-F2 (P3) systemexit_in_library attributes `os._exit()` to SystemExit in its message

- Location: `src/py_ci_shared/systemexit_in_library.py:69` (the `_QUALIFIED_EXITS` map that includes `os._exit`) and `:162` (the message template, which always says "SystemExit passes `except Exception` and ends only the calling thread").
- Evidence: fixture `a41/r5/pkg/noret.py:15` is `os._exit(1)` inside an `except ValueError:` handler in a function `b`. The finder reports `pkg/noret.py:15: [systemexit-in-library] b: os._exit() in a library function (...); SystemExit passes `except Exception` and ends only the calling thread.` That statement is false for this call: `os._exit` raises no exception at all, it terminates the process immediately without running `finally` blocks, `atexit` handlers or buffered-stream flushes.
- Scenario: a maintainer reads the message, concludes that wrapping the call in `except Exception` would make it safe, and is told by the finder's own text to "raise a normal exception", which is the same wrong premise. The real hazard (no cleanup, process killed) is never named.
- Correct behaviour: for `os._exit` (and only that call), the message should say that the call ends the process without raising, skipping `finally` and `atexit`, and should not mention SystemExit. The rule itself can remain, since a library calling `os._exit` is still worth flagging.
- Proposed disposition: RESOLVED-by-fix (proposed). Branch the message on `kind` at line 162: for `os._exit()` use "os._exit() ends the process without raising or running finally/atexit handlers; move it to main()". Add a test that pins the wording for `os._exit` in a library function, and a second test that `sys.exit` still gets the SystemExit wording. Not applied, because this auditor is read-only.
- Severity note: P3. The finding still fires on code that should be flagged, so the defect is the wrong explanation, not the verdict.

---

## Summary

Two new findings in dimension 41. Both are wrong explanations on code the gate should judge, not wrong verdicts: 41-F1 flags a descriptive "supply" phrase as advice, and 41-F2 gives a false mechanism for `os._exit`. Neither blocks a push today, but both produce misleading output that a consumer would act on. The rest of the gates probed (listed above) produced no false positive on the legitimate patterns tested.

Counts by severity: P0 0, P1 0, P2 0, P3 2.
