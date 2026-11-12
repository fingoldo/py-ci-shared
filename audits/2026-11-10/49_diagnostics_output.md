# 49 diagnostics_output

Round 2026-11-10, read-only. Method: read `audits/TRACKER.md` and `audits/2026-11-07/49_diagnostics_output.md` first (12 findings, all RESOLVED; none re-reported). Built a scratch consumer repo under the session scratchpad (`t1`, `t2`) with a `pkg/` that contains `datetime.utcnow()` and a `subprocess ... shell=True` line, then ran `python -m py_ci_shared.cli run | run-all | refresh | tool` against the checkout's `src/` with `PYTHONPATH` set. Outputs below are quoted from those runs. Detector correctness is out of scope.

Caveat on method: `python -I` ignores `PYTHONPATH` and imports the stale copy in `C:\Users\Admin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\py_ci_shared`. The first pass ran that copy by mistake and was discarded. Every quote below comes from the checkout source.

Counts: P1 0, P2 4, P3 7 (total 11).

Checked and fine (not findings): exit codes for FAIL (1), ERROR (2), unknown gate (2), missing `[tool.py_ci_shared]` table (2), unknown `tool` module (2), `tool` on a module with no CLI (2), `config-path` bad name (argparse, 2), no subcommand (2). Stdout carries the verdict rows and stderr carries the logger lines, consistently. The `FAIL` row and the `file:line` finding list are complete and name the file and line. The stale-entry and unreadable-JSON messages name the baseline file. The `refresh` message names the gate and the file. The missing-required-argument ConfigError (49-F3 fix) names the table and the argument. `bandit_warn` with a staged file and a wrong `--src-path` names the path and says nothing was scanned. `_tool_installed` uses `find_spec` (49-F2 fix present). The budget row, its GitHub `::warning::` and the job summary agree.

## 49-R1 [P2] The 49-F8 fix is incomplete: 14 sites still hard-code a `handler at line N` that points at the wrong line

- **Evidence:** `grep -rn "handler at line" src/py_ci_shared --include=*.py` returns 14 sites, for example `adoption_matrix.py:551`:
  `logger.warning("adoption_matrix.py handler at line 472 substituted a fallback for %s: %s", ...)`. Line 472 of that file is a docstring. The same pattern was checked at 8 sites: `baseline_trend.py:209` claims 203 (line 203 is `args.repo,`), `corpus_drift.py:435` claims 387 (a `def` line), `epsilon_padded_denominators.py:124` claims 96 (a docstring), `sibling_floor_skew.py:414` claims 409 (`owner=args.owner,`), `resource_leak_checks.py:79` claims 75 (`def _cwd()`), `schema_snapshot_parity.py:719` claims 717 (`)`), `env_write_restore.py:114` claims 108 (`return False`). That is 8 of 8 sampled numbers wrong. The 49-F8 disposition says the stale prefix was removed from all 13 sites and a meta-test scans for the literal `X.py line N:`. That test does not match the `X.py handler at line N` wording, so it passes while the bug remains. The `substituted a fallback` wording is also false at sites that return `False` or raise and do not substitute.
- **Output (real, when a handler fires):** the line the developer sees names a location that is not the handler.
- **Failure scenario:** A CI log shows `epsilon_padded_denominators.py handler at line 96 substituted a fallback ...`; the developer opens line 96, finds a docstring, and concludes the log is wrong. Nothing was substituted, so the message claims a fallback that did not happen.
- **Proposed fix:** Drop the hard-coded number and the "substituted a fallback" phrasing from all 14 sites. Use `logger.debug` with `%(filename)s:%(lineno)d`, or put the exception type and message in the warning and let the call site's own message stand. Widen the 49-F8 meta-test to match `handler at line` and `substituted a fallback` as well as `X.py line N:`.
- **AST-checkable:** True; FP risk: low (match the literal string `at line [0-9]+` inside a logger call).
- **Disposition:** RESOLVED. Regression of 49-F8 (2026-11-07), which was recorded RESOLVED.

## 49-R2 [P2] A malformed function-length baseline is reported as a stale entry, so the developer deletes the wrong thing

- **Evidence:** `fl.json` containing `["x"]` (a JSON list where a `{key: count}` object is required):
  ```
  FAIL  function_length (0.41s)
        1 function-length problem(s) (fl.json, limit 150):
          x: in the baseline but gone -- remove the entry (`py-ci-shared refresh function_length` (or PY_CI_SHARED_REFRESH=function_length) drops it)
  ```
  Here `x` is the list element, not a key. `fl.json` containing `{"src/a.py::f": 3}` with no such function gives the same shape of message, and that one is correct. A JSON-decode failure, by contrast, is reported well: `baseline .../fl.json is unreadable (JSONDecodeError: ...); fix or delete it`.
- **Failure scenario:** After a bad merge or a hand edit writes a list, the developer is told to remove an entry `x` from the file. The entry is not a key, and the real fault is the file's shape. The refresh hint then rewrites the file, dropping the bad shape silently.
- **Proposed fix:** Validate in `function_length` that the baseline is a dict of str to int before `length_problems`; on failure print `fl.json is not a {"path::function": lines} object (got list)`; exit as a usage error (2) or fail with that message only.
- **AST-checkable:** True; FP risk: low (assert the baseline loader rejects non-dict JSON with a message naming the shape).
- **Disposition:** RESOLVED.

## 49-R3 [P2] Under GitHub Actions a FAIL or ERROR row emits no `::error` annotation; only budget, note and skip rows annotate

- **Evidence:** `GITHUB_ACTIONS=true GITHUB_STEP_SUMMARY=... py-ci-shared run-all` on a repo whose `naive_utcnow` gate fails and whose budget is exceeded printed:
  ```
  FAIL  naive_utcnow (0.31s)
        1 use(s) of `datetime.utcnow()` ... 
          pkg/bad.py:8: datetime.datetime.utcnow()
  BUDGET naive_utcnow took 0.3s, over its 0s budget. ...
  ...
  ::warning title=py-ci-shared gate over budget::naive_utcnow took 0.3s, ...
  ```
  `cli.py` `_print_result` emits `::warning` for a note and for a skip, and `_announce_over_budget` emits one for a budget overrun. Nothing emits `::error` for a FAIL row or an ERROR row, and the job summary written from `GITHUB_STEP_SUMMARY` lists only the budget overruns. Exit code 1 and 2 are correct.
- **Failure scenario:** On a PR a gate fails. The checks page and the annotation list show no error on the changed file. The developer has to open the job log and scroll to the `FAIL` row. Only the warnings are annotated, so the failing gate looks like a soft warning.
- **Proposed fix:** Under `GITHUB_ACTIONS=true`, print `::error title=py-ci-shared <gate>::<first line of message>` for FAILED and ERROR rows, and add a `### Failed gates` section to the job summary, mirroring the budget block. Add `file=`/`line=` when the message starts with `path:line:`.
- **AST-checkable:** True; FP risk: low (a branch on FAILED/ERROR in `_print_result` that does not reach an `::error` print).
- **Disposition:** RESOLVED.

## 49-R4 [P2] The `budget` rounding says `over its 0s budget` for any `budget_s` under 0.5

- **Evidence:** `budget_s = 0.0001` on `naive_utcnow`:
  ```
  BUDGET naive_utcnow took 0.3s, over its 0s budget. Narrow its inputs, raise budget_s in [tool.py_ci_shared.gates.naive_utcnow], or set budget = "off".
  ```
  `runner.budget_verdict` formats the budget with `{result.budget_s:.0f}`. The budget is really 0.0001 seconds, so "0s" is a false number, and "raise budget_s" is the right advice for a value the developer wrote as 0.0001 only by accident.
- **Failure scenario:** A developer who set `budget_s = 0.5` on a 0.7-second gate reads "over its 0s budget" and does not know what the limit is.
- **Proposed fix:** Format with `:g` (or `.3g`) so `0.0001` and `0.5` print as written; keep the took-seconds at `.1f`.
- **AST-checkable:** True; FP risk: low (a `budget_s` rendered with `.0f`).
- **Disposition:** RESOLVED.

## 49-R5 [P3] `run-all` writes `gate configuration invalid` and `gate raised` log lines to stderr with no gate name, duplicating the row below them

- **Evidence:** with a gate table missing its required argument, stderr receives
  ```
  gate configuration invalid, reporting ERROR: ConfigError
  ERROR changelog_promise_parity (0.00s)
        configuration: [tool.py_ci_shared.gates.changelog_promise_parity] is missing required argument(s) ['changelog_path', 'trigger_pattern'] ...
  ```
  and a crashing gate gives `gate raised, reporting ERROR: TypeError` above the `ERROR` row. `_core/runner.py` logs with `logger.warning` and no `%(name)s`. The line is printed even with no `logging` configuration, because of the `lastResort` handler, so every consumer sees it. It says the exception type only, not the gate.
- **Failure scenario:** With several gates enabled, a CI log reads `gate raised, reporting ERROR: TypeError` in a stream where five other gates logged the same line. The developer cannot tell which gate it was without reading the row below.
- **Proposed fix:** Change both lines to `logger.debug`. The `ERROR` row already carries the type and the message, and `PY_CI_SHARED_TRACEBACK=1` gives the full traceback.
- **AST-checkable:** True; FP risk: low (a `logger.warning` in `run_gate`'s except clauses).
- **Disposition:** RESOLVED.

## 49-R6 [P3] Absolute machine paths in ordinary messages: a developer's home path, or a CI runner's path

- **Evidence:** all of these print the full path of the scratch repo, in this run `C:\Users\Admin\AppData\Local\Temp\claude\...\scratchpad\t1`:
  - `py-ci-shared: gate 'nosuch' is not enabled in [tool.py_ci_shared] of C:\Users\...\t1\pyproject.toml (enabled: ...)`
  - `py-ci-shared: C:\Users\...\t2\pyproject.toml has no [tool.py_ci_shared] table; see README 'Configuring gates'`
  - `function-length baseline C:\Users\...\t1\fl.json does not exist; create it with ...`
  - `REFRESH function_length (0.62s)` / `+0 -0 entries (fl.json): function-length baseline written to C:\Users\...\t1\fl.json`
  - `baseline C:\Users\...\fl.json is unreadable (JSONDecodeError: ...); fix or delete it`
- **Failure scenario:** A CI log or a pasted message carries the runner's workspace or a developer's account name. Each message also repeats the path that the caller passed in, so it is harder to read. The refresh line repeats the same path in two places.
- **Proposed fix:** Print repo-relative paths (`pyproject.toml`, `fl.json`) when the path is under `config.repo_root`; keep absolute only for paths outside it. Drop the repeated path in the `REFRESH` line, since the `(fl.json)` already names it.
- **AST-checkable:** False; FP risk: low (a render helper for a repo-root-relative path, checked by a test that the config error does not contain `Users`).
- **Disposition:** RESOLVED.

## 49-R7 [P3] Nested parentheses in the fix hints, and an unexplained `limit 150`

- **Evidence:** `function_length` stale and no-longer-over messages:
  ```
  1 function-length problem(s) (fl.json, limit 150):
    src/a.py::f: in the baseline but gone -- remove the entry (`py-ci-shared refresh function_length` (or PY_CI_SHARED_REFRESH=function_length) drops it)
  ```
  and the missing-baseline message:
  `create it with `py-ci-shared refresh function_length` (or PY_CI_SHARED_REFRESH=function_length) (a clean repo commits {})`
  The `limit 150` comes from the gate's default and is not in the config the developer wrote. The line is not wrapped, and the hint is two parenthesised clauses in a row.
- **Failure scenario:** The developer cannot tell whether `150` is configurable, and has to read the source. Two `(...)` groups in one clause are hard to parse.
- **Proposed fix:** Put the command on its own line: `  fix: py-ci-shared refresh function_length (or PY_CI_SHARED_REFRESH=function_length)`; print `limit 150 (max_lines in [tool.py_ci_shared.gates.function_length])` when the limit is the default or the configured key.
- **AST-checkable:** False; FP risk: low.
- **Disposition:** RESOLVED.

## 49-R8 [P3] `refresh` of a missing baseline reads `+0 -0 entries`, which is the same text as a no-op refresh

- **Evidence:** `py-ci-shared refresh function_length` with no `fl.json` present:
  ```
  REFRESH function_length (0.62s)
        +0 -0 entries (fl.json): function-length baseline written to C:\...\t1\fl.json
  0 passed, 0 failed, 0 skipped, 0 error, 1 refreshed
  ```
  A refresh that creates an empty baseline and a refresh that changed nothing print the same counts. The `created` case is not distinguished.
- **Failure scenario:** A reviewer reading `+0 -0` after adding the baseline file cannot tell that a file was created, so they may not commit it.
- **Proposed fix:** When the file did not exist before, print `created <file> (0 entries)` and keep `+N -M` for an existing file.
- **AST-checkable:** False; FP risk: low.
- **Disposition:** RESOLVED.

## 49-R9 [P3] `bandit_warn`, `advisory_warn` and `format_warn` run by hand with a directory argument exit 0 silently, and a positional path is ignored without a word

- **Evidence:** `py-ci-shared tool bandit_warn --src-path pkg` (no files) printed nothing and exited 0. `py-ci-shared tool bandit_warn pkg` printed `[bandit-warn] no --src-path given and PY_CI_SHARED_SRC_PATH env var not set -- skipping (nothing to scope the scan to)`, although the developer did pass `pkg`, positionally. The positional `pkg` is taken as a staged file name and filtered out. `format_warn --src-path pkg` also printed nothing.
- **Failure scenario:** A developer runs the advisory tool on a package to check it and sees nothing. Silence reads as "clean", when no file was scanned. In a pre-commit hook that is correct (no staged `.py`), but run by hand it is the same as a clean scan.
- **Proposed fix:** When `--src-path` is given and zero files resolve, print `[bandit-warn] 0 .py file(s) under --src-path pkg; nothing scanned` on stderr. Name the positional as ignored when no `.py` is among the args.
- **AST-checkable:** True; FP risk: low (a return of `None` from `resolve_src_files` with no stderr line).
- **Disposition:** RESOLVED.

## 49-R10 [P3] The `bandit_warn` banner asserts a CI step that a consumer may not have

- **Evidence:** `bandit_warn.py:121`:
  `"[bandit-warn] The findings above are WARNINGS ONLY -- the commit is NOT blocked. The same scan (bandit -ll) also runs in CI as a non-blocking step whose full report is uploaded as an artifact."`
  The sentence is hard-coded. It is printed in every consumer, including a consumer whose CI does not run bandit.
- **Failure scenario:** A developer is told the scan also runs in CI and that a full report is uploaded, and goes looking for an artifact that does not exist.
- **Proposed fix:** Drop the CI sentence, or make it conditional on the consumer's config flag. Keep the "WARNINGS ONLY, commit not blocked" half, which is true.
- **AST-checkable:** True; FP risk: low (a literal claim about CI in a banner that is not gated by a config key).
- **Disposition:** RESOLVED.

## 49-R11 [P3] The `ERROR` row for a gate with a fix-hint `e.g. ... = ...` placeholder is correct; the `ERROR` for `bandit_warn` named as a gate is not explained

- **Evidence:** `[tool.py_ci_shared.gates.bandit_warn]` in `run-all`:
  ```
  ERROR bandit_warn (0.03s)
        configuration: bandit_warn is a cli module with no assert_* entry; it cannot be enabled as a gate
  ```
  The message is accurate and names the table key. It does not say where the list of valid gate names comes from (`py-ci-shared list`), which the `tool` error and the unknown-gate error both do. It is the only one of the three that stops short.
- **Failure scenario:** A developer who enabled the advisory tool as a gate is told it "cannot be enabled" and has no next command.
- **Proposed fix:** Append `; run it with \`py-ci-shared tool bandit_warn\` or list gates with \`py-ci-shared list\``.
- **AST-checkable:** False; FP risk: low.
- **Disposition:** RESOLVED.

## Observation, not a finding: a stale installed copy shadows the checkout

`python -m py_ci_shared.cli` run from a consumer directory without `PYTHONPATH` imports the copy in `site-packages`, which is a different version from `src/`. Its messages are the pre-2026-11-07 ones: `cli.py line 201: substituted a fallback after ConfigError: ...` is printed before every configuration error, and `missing 2 required positional arguments` tracebacks appear where the checkout prints the required-argument ConfigError. The checkout's diagnostics are the ones the TRACKER describes, so a developer on this machine reproducing a message may be reading a different version. Proposed handling belongs to the environment, not this repository: reinstall it editable (`pip install -e`) or record the installed version in the output of `py-ci-shared version`.

## Summary

| Id | Sev | Title | Disposition |
|---|---|---|---|
| 49-R1 | P2 | 49-F8 fix incomplete: 14 `handler at line N` sites, 8 of 8 sampled numbers wrong | RESOLVED (regression of a RESOLVED finding) |
| 49-R2 | P2 | Malformed function-length baseline reported as a stale entry | RESOLVED |
| 49-R3 | P2 | No `::error` annotation for FAIL or ERROR under GitHub Actions | RESOLVED |
| 49-R4 | P2 | Budget message prints `0s` for a sub-second `budget_s` | RESOLVED |
| 49-R5 | P3 | Nameless duplicate stderr lines for ConfigError and crashes | RESOLVED |
| 49-R6 | P3 | Absolute machine paths in ordinary messages | RESOLVED |
| 49-R7 | P3 | Nested parentheses in fix hints; unexplained `limit 150` | RESOLVED |
| 49-R8 | P3 | `refresh` of a missing baseline reads `+0 -0` | RESOLVED |
| 49-R9 | P3 | Advisory tools by hand with a directory exit 0 silently | RESOLVED |
| 49-R10 | P3 | bandit banner asserts a CI step consumers may not have | RESOLVED |
| 49-R11 | P3 | `bandit_warn` as a gate: no next command | RESOLVED |

Counts: P1 0, P2 4, P3 7 (total 11). 49-F1 is the only one that reopens a finding recorded RESOLVED in the previous round. Nothing was edited in the repository; the scratch consumer repo is under the session scratchpad.
