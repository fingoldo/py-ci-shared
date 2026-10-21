# Wallclock-wait and fail-open baseline review (2026-10-21)

Scope: the 18 entries of `tests/baselines/wallclock_test_waits.json` and the 1 entry of `tests/baselines/fail_open_handlers.json`. Shrink-only: only fixed entries were removed; no entry was added.

Result: 1 wallclock entry FIXED and removed; 17 wallclock entries KEPT; 1 fail-open entry KEPT.

Verification for the KEPT wallclock entries (all 17 still match the gate's findings exactly): `find_wallclock_test_waits` run on the repo gives found 17, accepted 17, new none, stale none. The KEPT fail-open entry is verified by the self-gate tests below.

### WC-1 (Low) -- tests/test_mutation_teeth_regressions.py: time.sleep(0.2) -- polling a grandchild pid with a real sleep

**Disposition:** FIXED -- the test polled `os.kill(pid, 0)` / `tasklist` in a `time.sleep(0.2)` loop until the grandchild was gone. It now calls `psutil.Process(pid).wait(timeout=10)` (psutil is a declared dependency; `importorskip` as in `test_resource_leak_guard.py`), treating `NoSuchProcess` as dead and `TimeoutExpired` as a failure. The `_alive` helper and the now-unused `time` import were removed. Entry removed from `tests/baselines/wallclock_test_waits.json`.

**Verification:**
- `python -m ruff check tests/test_mutation_teeth_regressions.py` -- All checks passed.
- `python -m mypy src/py_ci_shared` -- Success: no issues found in 252 source files.
- `python -m pytest tests/test_mutation_teeth_regressions.py -k TimeoutsKillTheTree -n 4 --no-cov` -- 4 passed.
- `python -m pytest tests/test_self_gates.py -k "wallclock or fail_open" -n 4 --no-cov` -- 2 passed.
- Direct gate run (`find_wallclock_test_waits` on the repo against the edited baseline): found 17, accepted 17, new none, stale none.

### WC-2 (Low) -- tests/test_lock_held_across_io.py: "time.sleep(1)", -- sleep text in a parametrized call list

**Disposition:** KEPT -- the line is an element of a pytest parametrize list of source snippets. The gate scans the snippet text inside the test module; the call is never executed. Not a wait.

### WC-3 (Low) -- tests/test_mutation_teeth_regressions.py: child = subprocess.Popen(...time.sleep(120)) -- grandchild script text

**Disposition:** KEPT -- this line is inside a string literal that is the script run by `run_with_deadline`. The 5 s timeout under test must kill the process tree including this grandchild. The test process never executes the line and never waits on it.

### WC-4 (Low) -- tests/test_mutation_teeth_regressions.py: time.sleep(120) -- grandchild script text

**Disposition:** KEPT -- same string-literal script as above; the parent sleeps 120 s only so the 5 s deadline has something to kill.

### WC-5 (Low) -- tests/test_resource_leak_guard.py: assert "child process" ... "time.sleep(4)" -- assertion on the fixture's text

**Disposition:** KEPT -- the assertion checks that the guard's error message quotes the leaked child's command line. The string is data, not a wait.

### WC-6 (Low) -- tests/test_resource_leak_guard.py: subprocess.Popen([... "time.sleep(4)"]) -- leaked-child fixture

**Disposition:** KEPT -- this is the pytester source for a test that leaks a child process on purpose so the guard detects it. The test does not wait for the child; the guard kills it at teardown.

### WC-7 (Low) -- tests/test_wallclock_test_waits.py: _tree(... "test/support/helper.dart" ... Future.delayed ...) -- gate fixture

**Disposition:** KEPT -- string written into a temporary tree that this gate's own tests scan. Not executed.

### WC-8 (Low) -- tests/test_wallclock_test_waits.py: _tree(... "test/widget_test.dart" ... Future.delayed(300)) -- gate fixture

**Disposition:** KEPT -- same: gate self-test fixture written to `tmp_path`, scanned, never run.

### WC-9 (Low) -- tests/test_wallclock_test_waits.py: _tree(... "tests/test_poll.py" ... "# time.sleep(5) was removed\n # time.sleep(2)") -- gate fixture

**Disposition:** KEPT -- gate fixture asserting that comment lines are not findings.

### WC-10 (Low) -- tests/test_wallclock_test_waits.py: _tree(... "time.sleep(0.5) # wait for the child") -- gate fixture

**Disposition:** KEPT -- gate fixture asserting a trailing comment does not hide a finding.

### WC-11 (Low) -- tests/test_wallclock_test_waits.py: _tree(... "time.sleep(0.5)\n") -- gate fixture

**Disposition:** KEPT -- gate fixture for the basic finding.

### WC-12 (Low) -- tests/test_wallclock_test_waits.py: _tree(... docstring "Do not time.sleep(1) here.") -- gate fixture

**Disposition:** KEPT -- gate fixture asserting a docstring mention is not a finding.

### WC-13 (Low) -- tests/test_wallclock_test_waits.py: accepted = {"tests/test_poll.py: time.sleep(0.5)": ...} -- gate fixture

**Disposition:** KEPT -- the dict is the accepted-baseline fixture for the recorded-wait test.

### WC-14 (Low) -- tests/test_wallclock_test_waits.py: assert ... == ["test/support/helper.dart: ..."] -- expected-finding literal

**Disposition:** KEPT -- expected output of the gate for the fixture above.

### WC-15 (Low) -- tests/test_wallclock_test_waits.py: assert ... == ["test/widget_test.dart: ..."] -- expected-finding literal

**Disposition:** KEPT -- expected output of the gate for the fixture above.

### WC-16 (Low) -- tests/test_wallclock_test_waits.py: assert ... == ["tests/conftest.py: time.sleep(0.2)"] -- expected-finding literal

**Disposition:** KEPT -- expected output of the gate for the conftest fixture.

### WC-17 (Low) -- tests/test_wallclock_test_waits.py: assert ... == ["tests/test_poll.py: time.sleep(0.5) # wait for the child"] -- expected-finding literal

**Disposition:** KEPT -- expected output of the gate for the trailing-comment fixture.

### WC-18 (Low) -- tests/test_wallclock_test_waits.py: assert ... == ["tests/test_poll.py: time.sleep(0.5)"] -- expected-finding literal

**Disposition:** KEPT -- expected output of the gate for the basic fixture.

### WC-19 (Low) -- src/py_ci_shared/spec_bound_doubles.py::files_driving::admit_on_error (medium) -- fail-closed keep of an unreadable file

**Disposition:** KEPT -- `files_driving` catches `SourceError` and appends the path, so an unreadable test file stays in the scan and the parse that follows reports it by path. The rule's target is a selection gate that keeps a candidate when its evaluation fails and so lets the failing candidate through; here the kept element is the one the scan must fail on, so the handler is fail-closed. The rule fires on the shape, not the direction, and no code change removes the finding without making the scan drop errors. The baseline reason is made precise below.

**Verification:**
- `python -m pytest tests/test_fail_open_handlers.py -n 4 --no-cov` -- 32 passed.
- `python -m pytest tests/test_self_gates.py -k "fail_open or wallclock" -n 4 --no-cov` -- 2 passed (repo self-gate, baseline still matches).
- `python -m json.tool tests/baselines/fail_open_handlers.json` -- valid JSON.
