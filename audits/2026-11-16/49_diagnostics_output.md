# 49 diagnostics_output: round 2026-11-16 (T)

Commit audited: `48e49b1`. Read-only; only this file was created. Scratch work: `scratchpad\audit_T\49\`.

## Scope and method

Started from `audits/2026-11-11/49_diagnostics_output.md` and the TRACKER rows `49-F*`, `49-R*`, `49-S12/S13` (all dispositioned; not re-proposed).

1. A harness (`harness.py`) materializes every `tests/canary/<gate>/violation` and `clean` corpus (175 gates in `CANARIES`), calls the gate's real `assert_*` entry, and records the exception text, type, stdout and stderr. It ran twice, under two different `PYTHONHASHSEED`s. The 175 failure texts were then analysed in bulk: path:line present, fix wording, absolute paths, count vs listed lines, first line, and every flag or command a message names.
2. About 15 scratch consumer repos (`repo1`..`repo15`) driven through the real CLI (`python -m py_ci_shared.cli run|run-all|refresh|list|tool`, with `PYTHONPATH=src;`): missing baseline, seeded baseline, grown/shrunk/stale baseline, unknown gate, gate not enabled, bad config (budget, TOML, duplicate enable), missing required keys, empty tree, unparsable file, `allow_unparsed`, `min_files = 0` with everything excluded, 5000 findings in one gate, `GITHUB_ACTIONS=true`, budget `warn`/`fail`.
3. Encoding: `PYTHONIOENCODING` = utf-8, cp1251, cp1252, ascii, with a repo directory `репо с пробелом` and a source file `данные 数据.py` holding a finding; plus the standalone `python -m` tools.
4. Not covered: the pytest plugin's own item output (`pyproject.toml::<gate>` items), the `bandit_warn`/`advisory_warn`/`format_warn` banners (49-S12/S13 fixed and pinned), mutation tooling output, `release_guard`.

Gates whose failure text was read: all 175 canary gates programmatically (well over the 30 required), plus by hand naive_utcnow, swallowed_exceptions, function_length, skip_as_pass, wallclock_test_waits, git_dependency_pins, timezone_honest, repo_hygiene, value_bearing_asserts, machine_specific_paths, import_cycles, workflow_runner_labels, uncalled_functions, vacuous_loop_assertions, db_transaction_completeness, marker_runner_coverage, source_text_claims, resource_release_paths, spec_bound_doubles, effect_assertion_parity, import_side_effects, pytest_markers, entry_points_resolvable, hook_hygiene, meta_private_imports, gate_integrity, readme_env_var_parity, private_imports, ci_install_covers_conftest, drifted_duplicate_functions, ddl_lock_safety, loop_invariant_cast, sibling_floor_skew, api_floor, ci_workflow_gate, test_partition_reachability, ci_test_dir_reachability.

Headline numbers from the 175-gate sweep: a `path:line` is on the first line of the failure text in 10 of 175 (the only line an Actions annotation uses); 22 failure texts contain an absolute machine path; 10 mix `/` and `\` in one path; all 175 texts are identical across two hash seeds; the `N finding(s)` count in the header matches the listed lines wherever I could parse it (two hits of my first script were false alarms, see the last section).

## 49-T1 (P2) The `::error` annotation under GitHub Actions carries no file, no line, and for 13+ gates no information at all
- **Evidence:** `cli.py:116-118` emits `::error title=py-ci-shared <gate> FAIL::<first message line>` and nothing else: no `file=`, no `line=`. The first line of a gate's failure is usually its header, not a finding. Sweep over the 175 canary failures: a `path:line` is on the first line in 10 of 175. Gates whose first line is only a label include `swallowed_exceptions`, `atomic_write_staging`, `clock_day_boundary`, `comment_repo_paths`, `db_transaction_completeness`, `hash_key_determinism`, `import_cycles`, `local_copy_report`, `nginx_location_headers`, `no_xfail_to_defer`, `pickle_state_completeness`, `sentinel_or_fallback`, `stale_source_citations`. Demonstration (`repo14`, one swallowed exception at `src/a.py:4`):
  ```
  GITHUB_ACTIONS=true python -m py_ci_shared.cli run-all
  FAIL  swallowed_exceptions (5.04s)
        swallowed-exceptions:
        log at WARNING or above, re-raise, ...:
            src/a.py:4: [swallowed-exception] `except Exception` in g swallows a failure of `pass`
  ::error title=py-ci-shared swallowed_exceptions FAIL::swallowed-exceptions:
  ```
  With 5000 findings (`repo2`) the same single annotation is produced; for `naive_utcnow` it is the generic deprecation sentence.
- **Impact:** a PR author sees a red annotation reading `swallowed-exceptions:` and no file in the Files-changed view; the 49-R3 fix made the annotation exist but not useful. Nothing points to the offending line.
- **Proposed fix:** in `_print_result`, parse the message for `path:line:` finding lines and emit one `::error file=<path>,line=<n>,title=py-ci-shared <gate>::<rule/message>` per finding (cap at 10, then one summary annotation, since GitHub shows at most 10 per step), keeping the current line as the fallback for a gate with no parseable finding. Escape `%`, `\r`, `\n` per the workflow-command rules. ADDITIVE (output only; exit codes unchanged).
- **Disposition:** FIXED in ded2bac: `::error` per `path:line` finding with file and line, cap 10 then a summary (`cli._error_annotations`); test `tests/test_cli_annotations.py`.

## 49-T2 (P2) `import_cycles` and ~30 other standalone `main()`s crash with a traceback on a console that cannot encode a path; only `cli._print` is guarded
- **Evidence:** `cli.py:_print` handles `UnicodeEncodeError` (audit 2026-10-03 K-1) but the per-module commands write `Finding.render()` straight to `sys.stdout` (`import_cycles.py:433`, `workflow_runner_labels.py:212`, `closed_audit_rounds.py:169`, `commit_metadata.py:217`, `sibling_floor_skew.py:428`, `consumer_import_census.py:352`, `hook_attestation.py:253`, `schema_snapshot_parity.py:737`, `tracked_secret_shapes.py:330`, ...). A grep for `reconfigure` and `backslashreplace` over `src/py_ci_shared` finds only `cli.py:65` as code. Demonstration:
  ```
  # pkg/a.py: from . import 数据 ; pkg/数据.py: from . import a   (a real import cycle)
  PYTHONIOENCODING=cp1251 python -m py_ci_shared.import_cycles .
  Traceback (most recent call last): ...
    File ".../import_cycles.py", line 433, in main
      sys.stdout.write(shown.render() + "\n")
  UnicodeEncodeError: 'charmap' codec can't encode characters in position 63-64
  exit(cp1251)=1
  ```
  and `PYTHONIOENCODING=cp1252 python -m py_ci_shared.workflow_runner_labels .` over `.github/workflows/сборка.yml` (holding `ubuntu-latest`) ends in `UnicodeEncodeError` (exit 1). The same kind of finding through `py-ci-shared run-all` prints `src/\u0434\u0430\u043d\u043d\u044b\u0435 \u6570\u636e.py:4` and exits correctly.
- **Impact:** on a Russian or Western-European Windows console (cp1251, cp1252: the default for a pre-commit hook) the finding, the remaining findings and the summary line are lost, and exit code 1 equals "findings", so a traceback is indistinguishable from a real result in a hook. This is the failure 2026-10-03 K-1 fixed for one entry point only.
- **Proposed fix:** move `_print`'s guard into one helper (for example `_core/console.py: write_line(text, *, err=False)`) and make every module `main` write through it, or call `sys.stdout.reconfigure(errors="backslashreplace")` once at the top of each `main`; add a test that runs each `main` under `PYTHONIOENCODING=cp1251` against a Cyrillic or CJK-named file. ADDITIVE.
- **Disposition:** FIXED in ded2bac: every module `main` writes via `_core/console.write_text` (escapes unencodable characters); test `tests/test_console_encoding.py`.

## 49-T3 (P2) Gates built on `_text_ratchet.enforce_findings` print each finding twice and the run-all row loses the fix guidance; a stale baseline is reported as "0 new violation(s)"
- **Evidence:** `baseline_ratchet.py:enforce` writes the full report (list, guidance, "add it with a note") to stderr and returns 1; `_text_ratchet.py:87` then raises `AssertionError(f"{label}: {len(new)} new violation(s): {listed}. {baseline.path} accepts only the entries it holds")`, and `run-all` prints that one-line string in the FAIL row. Eight modules go through it (`skip_as_pass`, `wallclock_test_waits`, `git_child_env`, `guard_without_fixture`, `dup_baseline_percent_key`, ...). Demonstration (`repo11`, canary violation, `GITHUB_ACTIONS=true`):
  - stdout row: one 600-character line, findings joined by `;`, ending `...\tool/meta/baselines\skip_as_pass.json accepts only the entries it holds` (an absolute path with mixed separators and no instruction). The guidance sentence `A guard that prints SKIPPED and exits 0 passes with nothing checked. Exit non-zero with the reason, or accept it with a note.` exists only on stderr. The `::error` annotation repeats the same long single line.
  - Stale baseline (`{"accepted": {"tool/gone.sh:3": "..."}}`, clean tree):
    ```
    FAIL  skip_as_pass (1.30s)
          skip_as_pass: 0 new violation(s): the baseline needs a note or a refresh, see the report above. C:\...\tool/meta/baselines\skip_as_pass.json accepts only the entries it holds
    (stderr) ... or the whole backlog was paid off - then run python tool/meta/regen_baselines.py to record that.
    ```
    A FAIL that says "0 new violations", points at a report that is on a different stream, and names `python tool/meta/regen_baselines.py` (`DEFAULT_REFRESH_COMMAND`, `baseline_ratchet.py:58`), a script that does not exist in a repo that uses `py-ci-shared` (noema_app has its own; the default is that layout). `py-ci-shared refresh skip_as_pass` does not help either: it re-runs the gate and prints the same failure (shown in the run).
  - The clean run is also off-channel: `skip_as_pass: no new violations (0 accepted, baselined)` is written with `sys.stdout.write` during the gate, so in `run-all` it is the first line of output, above `PASS  skip_as_pass`, detached from every row (seen in `repo11`: it printed before both PASS rows).
- **Impact:** the developer of a `run-all` failure gets the finding list but not the fix, and in the stale/unjustified/floor cases gets a self-contradictory message and a command that cannot work.
- **Proposed fix:** make `enforce_findings` raise with the whole text (header, findings one per line, guidance, and the real reason when `new` is empty: stale / unjustified / floor) instead of printing to stderr and summarising; give these gates a refresh path reachable from `py-ci-shared refresh <gate>` or print the manual instruction (delete the named stale keys) instead of `regen_baselines.py`; route the "no new violations (N accepted)" line through `outcome.add_note` so it prints under its own row. Text only, no exit-code change: ADDITIVE (CHANGELOG `Nothing to do` if the owner treats a message change as a behaviour change).
- **Disposition:** DEFERRED: needs `enforce_findings` to raise the full text (header, findings, guidance, real reason for stale/unjustified) across 8 gates, and a refresh instruction that works; that changes the failure text contract of those gates and needs the owner to approve the new wording.

## 49-T4 (P2) The refresh command printed in a missing-baseline message is wrong in two ways: it names a label that `py-ci-shared refresh` rejects, and it omits the `--grow` a first seed needs
- **Evidence:** (a) `repo15`, gate `value_bearing_asserts`:
  ```
  FAIL  value_bearing_asserts
        value-asserts: baseline ...\va.json does not exist, ... Create it with: `py-ci-shared refresh value-asserts` (or PY_CI_SHARED_REFRESH=value-asserts)
  $ py-ci-shared refresh value-asserts
  py-ci-shared: gate 'value-asserts' is not enabled in [tool.py_ci_shared] of pyproject.toml (enabled: value_bearing_asserts)   exit=2
  ```
  `refresh_hint(flag, gate)` (`_core/outcome.py:146`) receives the baseline's internal label, and eight modules use a label that is not their registry name: `code_audit_meta` (`code-audit`), `complexity_ratchet` (`complexity`), `db_transaction_completeness` (`db-transaction`), `deferred_drift` (`deferred-debt`), `fail_open_handlers` (`fail-open`), `readme_env_var_parity` (`readme-env-var`), `source_text_claims` (`source-text`), `value_bearing_asserts` (`value-asserts`). The `PY_CI_SHARED_REFRESH=<label>` form works, the CLI form does not. Only `value_bearing_asserts` was run end to end; the other seven come from a grep of `gate="..."` literals against module names, and whether each reaches `refresh_hint` was not traced.
  (b) `repo4`, `function_length`, a tree with one existing over-limit function and no baseline:
  ```
  FAIL  function_length   ... baseline function_length.json does not exist; create it with `py-ci-shared refresh function_length` (or PY_CI_SHARED_REFRESH=function_length) (a clean repo commits {})
  $ py-ci-shared refresh function_length
  FAIL  function_length   ... does not exist, and seeding it would accept 1 current finding(s): ... Fix them, or accept them deliberately: ... --grow to `py-ci-shared refresh`
  ```
  The first message sends the developer to a command that fails with a second message; only the second names `--grow`. The first names the baseline by basename (`function_length.json`), the second by absolute path.
- **Impact:** first adoption of a ratchet in a repo with existing findings hits two dead ends before the working command.
- **Proposed fix:** (a) under `PY_CI_SHARED_CLI=1` print the enabled gate name (the CLI knows it; pass the `GateRun` name through a context variable in `outcome`) instead of the baseline label; (b) when the baseline is missing and the scan has findings, append ` (add --grow: seeding accepts the N current findings)`. ADDITIVE (text only).
- **Disposition:** DEFERRED: (b) missing-baseline `--grow` hint FIXED in ded2bac (`_core/baseline.py`, `tests/test_message_wording.py`). (a) the printed CLI label needs the enabled gate name threaded from `runner` into `refresh_hint` for eight modules; not done.

## 49-T5 (P2) A PASS never says what was checked, and a baseline-accepted finding is reported nowhere on a pass
- **Evidence:** `_print_result` prints `PASS <gate> (<s>s)` and nothing else. `repo4`: `function_length` with `src/a.py::big` accepted at 202 lines prints `PASS  function_length (4.97s)`; functions measured, files read and entries accepted are not shown. Only the `_text_ratchet`/`baseline_ratchet` gates print `N accepted, baselined`, and they print it off-row (49-T3). Vacuous passes that are therefore invisible, all run:
  - `allow_unparsed = true` with `src/broken.py` unparsable (`repo13`): `PASS naive_utcnow`; the dropped file is not mentioned. The default (no switch) fails with a good message; the opt-in drops the file in silence.
  - `min_files = 0` plus `skip_dir_names = ["src"]` (everything excluded): `PASS naive_utcnow` (0 files read).
  - `machine_specific_paths` over a tree holding only `README.md` and `pyproject.toml` passes because `pyproject.toml` itself matches `*.toml` in `config_patterns`: `PASS machine_specific_paths (1.71s)` with no count. The floor of 1 is met by the config file; the count is not shown, so the pass cannot be told from a real one. (Whether that floor should count the config file is dimension 40's question; here only the missing count is raised.)
- **Impact:** the floors catch "a gate that checks nothing", but at the console the reader of a green `run-all` cannot tell 2000 files read from 1. Baseline growth silently accepted (a ceiling at 202 is "PASS") is invisible to a reviewer.
- **Proposed fix:** each `report()`/`_gate_run` tail adds a note via `outcome.add_note`: `read N file(s); M finding(s) accepted by <baseline file>; K unparsed file(s) allowed` (printed as `note:` under the row; always when `allow_unparsed` dropped a file or M > 0; the files-read count can be always shown). ADDITIVE.
- **Disposition:** DEFERRED: a PASS note (files read, findings accepted, unparsed files allowed) needs a note in each gate's report tail across many modules, and the owner has to decide which counts always print.

## 49-T6 (P2) `git_dependency_pins` lists the offending refs without the dependency they belong to; `timezone_honest` prints a tuple repr with no line
- **Evidence:** canary violation, `[project].dependencies`/`optional-dependencies` shape:
  ```
  4 git-URL dependencies are not pinned to a full commit SHA in <D>\pyproject.toml ...:
    release-branch
    <no ref>
    abc1234
    seeduv: branch=dev-branch
  ```
  Three of the four lines are only the ref (from `seedextra@release-branch`, `seednoref` with no ref, `seedshort@abc1234`). Only the `[tool.uv.sources]` shape names the package. `git_dependency_pins.py:149` appends the literal `"<no ref>"`. `timezone_honest` prints `naive-datetime finding(s) in non-test code. ...: [('app/seed_job.py', 'DTZ005')]` with no line although ruff supplies one.
- **Impact:** with more than one such dependency `<no ref>` tells the developer nothing; they re-read the file to find which.
- **Proposed fix:** report `name: ref (pyproject.toml:LINE)` for every shape; render timezone findings as `path:line: DTZ005 <message>`. ADDITIVE.
- **Disposition:** DEFERRED: `dtz_findings` returns `(path, rule)` with no line and its allow-list keys depend on that shape; `git_dependency_pins` needs `pyproject.toml` line tracking. Both change gate output and keys, so they need their own change.

## 49-T7 (P3) `repo_hygiene` and five more sites list only the first 10 to 50 offenders with no "and M more", and name no repair
- **Evidence:** `repo_hygiene.py:353,362,369,380` use `[:20]`. `repo3`, 25 tracked BOM files: header `25 text file(s) start with a UTF-8 BOM`, then `f00.md` ... `f19.md`, nothing after. Same pattern: `ignore_ratchet.py:102` (`unparsed[:20]`), `timezone_honest.py:136` (`unchecked[:50]`), `tracked_secret_shapes.py:253` (`[:20]`), `arb_checks.py:152,154` (`[:10]`). `code_audit_meta.py:324-345` does it correctly (`... and N more`). The BOM/NUL messages explain the harm but not the repair.
- **Impact:** the reader counts 20 lines against a header of 25 and may think the terminal cut the output.
- **Proposed fix:** one helper `_core` `capped(items, limit)` that appends `... and M more`; use it at those sites; add the repair command to the BOM and NUL messages. ADDITIVE.
- **Disposition:** DEFERRED: `repo_hygiene`, `timezone_honest`, `ignore_ratchet`, `arb_checks` now use `capped` (FIXED in ded2bac, `tests/test_message_wording.py`); `tracked_secret_shapes.py:253` still caps with an inline slice and is not yet converted.

## 49-T8 (P3) Under Actions `budget = "fail"` exits 1 on an all-PASS run with no annotation, and no finding ever reaches the job summary
- **Evidence:** `_announce_over_budget` returns before the `::warning` loop and the `GITHUB_STEP_SUMMARY` block when `mode == "fail"` (`cli.py:84-86`). `repo13`, `budget = "fail"`, `budget_s = 0.1`, `GITHUB_ACTIONS=true`: output is `PASS naive_utcnow`, `BUDGET naive_utcnow took 5.4s ...`, `1 passed, 0 failed, 0 skipped, 0 error`, `BUDGET 1 gate(s) over budget (budget = "fail"); exit code 1.`, exit 1, no `::error::` line. `GITHUB_STEP_SUMMARY` is written for the budget list only, never for FAIL rows.
- **Impact:** the run page shows a red job with a summary line saying "0 failed" and no annotation saying why.
- **Proposed fix:** emit `::error title=py-ci-shared gate over budget::<over>` per gate when `mode == "fail"`, and append FAIL/ERROR rows to `GITHUB_STEP_SUMMARY`. ADDITIVE.
- **Disposition:** FIXED in ded2bac: budget `fail` gets a `::error` annotation and a job-summary block; the summary also lists failed gates (`cli._announce_over_budget`, `cli._report`); test `tests/test_cli_annotations.py`.

## 49-T9 (P3) `run-all` has no final verdict word
- **Evidence:** the last line is `1 passed, 0 failed, 0 skipped, 0 error` in every outcome, including exit 1 (budget) and exit 0 with `allow_unparsed` drops. Exit 2 (ERROR) and 1 (FAIL) are not distinguished in text; the exit-code contract exists only in the module docstring and README.
- **Proposed fix:** append `-> OK`, `-> FAILED (exit 1)` or `-> ERROR (exit 2: a gate could not run)` to `_summary_line`; keep the existing head unchanged for greppers. ADDITIVE.
- **Disposition:** FIXED in ded2bac: the summary line ends with `-> OK`, `-> FAILED (exit 1)` or `-> ERROR (exit 2 ...)`; the head is unchanged; test `tests/test_cli_annotations.py`.

## 49-T10 (P3) Not-enabled and cannot-run messages give no instruction; absolute paths and mixed separators remain
- **Evidence:** `py-ci-shared run loc_budget` (a real gate that is not enabled) and `run naive_utcnw` (a typo) print the same text: `gate 'X' is not enabled in [tool.py_ci_shared] of pyproject.toml (enabled: naive_utcnow)`, with no `add "loc_budget" to enable = [...]` and no "did you mean". `run bandit_warn` gets the same text although it can never be a gate (49-R11 fixed the `ERROR` path only). `repo_hygiene` in a non-git directory gives `RuntimeError: git ls-files failed in <abs>: git ls-files -z failed in <abs> (exit 128): fatal: not a git repository ...` (path twice, no hint that the gate needs a git work tree; exit 2). Sweep: 22 of 175 failure texts embed an absolute path and 10 print it with both separators (`C:\...\tool/meta/baselines\skip_as_pass.json`); `drifted_duplicate_functions` lists `<abs>/a.py:1`; `ci_workflow_gate` reports `seed step` with no workflow line. Config errors print the absolute `pyproject.toml` while other messages print `pyproject.toml`; 49-R6 did not settle one wording.
- **Proposed fix:** add the `enable = [...]` instruction and a `difflib.get_close_matches` suggestion to the `_cmd_run` lookup; give `repo_hygiene` a ConfigError "needs a git work tree"; normalise baseline and root paths through one `rel(path, root)` in message builders. ADDITIVE.
- **Disposition:** DEFERRED: the not-enabled part is FIXED in ded2bac (enable hint and near-miss suggestion, `_core/config.py`). Still open: the absolute path and mixed separators in 22 texts, the `repo_hygiene` non-git ConfigError, and `bandit_warn` being accepted as a gate name.

## 49-T11 (P3) Wording and small-output defects
- `refresh` prints `created function_length.json with 1 entries: function-length baseline written to function_length.json` (`repo4`): the file named twice, `1 entries`, basename only (two baselines with the same name in different directories are indistinguishable). `baseline_ratchet.py` uses `entr(ies)` in six messages.
- `py-ci-shared refresh naive_utcnow` (a gate with no baseline) prints `PASS` and exits 0: a refresh that did nothing reads like a successful one. Say `naive_utcnow has no baseline; nothing to refresh`.
- `1 file(s) could not be parsed, so they were not checked: src/broken.py:1: unparsable: invalid syntax` never names the switch (`allow_unparsed`) or the fix.
- `dup_baseline_percent_key`, `git_child_env`, `guard_without_fixture`, `skip_as_pass`, `wallclock_test_waits` print findings as `key (description); key (description)` on one line; `db_transaction_completeness` names its baseline `baseline.json` (basename only).
- **Proposed fix:** correct the strings. ADDITIVE.
- **Disposition:** DEFERRED: `entries` pluralisation FIXED in ded2bac (`entries_word`, applied to `entry_delta` and the two `baseline_ratchet` messages, which were two, not six). Still open: `refresh naive_utcnow` with no baseline printing PASS, the unparsed-file message not naming `allow_unparsed`, the `key (description)` one-line lists in five gates, and the basename-only baseline name in `db_transaction_completeness`.

## Considered and not raised

- Order instability: the 175 violation texts are identical (modulo the temp path) under two different `PYTHONHASHSEED`s, and two consecutive `run-all` over 5000+5000 findings (`repo2`) gave identical output. Not a finding.
- Truncation of large lists in `run-all`/`assert_*`: nothing is truncated; a 5000-finding gate prints 5000 lines (203 KB, one `Failed` exception; `swallowed_exceptions` 482 KB, 5001 lines), the explanation only at the top and the summary 5000 lines below. This is the reverse of 49-T7 (completeness over readability). Not raised as a defect: the developer who asks for the full list gets it. If the owner wants a cap, the helper proposed in 49-T7 is the one place to add it, with an opt-in to print everything.
- Header counts vs listed lines: my parser flagged `ci_install_covers_conftest` (claimed 12) and `drifted_duplicate_functions` (claimed 1); both false alarms (`conftest.py:12` read as a count; the second lists 2 copies of 1 function). No count mismatch found in the 175.
- Exit codes: FAIL 1, ERROR 2, usage 2, budget fail 1, refresh 0 verified again, including ERROR overriding FAIL to 2. Consistent with the docstring and README.
- Duplicate printing of config errors and log lines (49-F8, 49-R5): verified quiet, one line per error.
- `::error` escaping of `%`, `\r`, `\n`: only the first message line is emitted and I could not construct a first line with those characters for any existing gate. NOT REPRODUCED.
- Non-ASCII path and a path with spaces through `py-ci-shared run-all`: correct under utf-8; cp1251, cp1252 and ascii write `\uXXXX` escapes and exit with the right code (the guard works for this entry point; the gap is 49-T2).
- Skip rows (`skipped: <reason>`, `::warning`, `skip = "fail"`, 49-F7) and the `bandit_warn`/`advisory_warn`/`format_warn` banners (49-S12/S13): not re-run this round.
- The source path of the raising module in an `ERROR` row (`(at ...\repo_hygiene.py:136; PY_CI_SHARED_TRACEBACK=1 ...)`): it names the module that raised and is useful.
- Gate runtime of 2 to 13 s on a one-file tree on this loaded machine: dimension 48.
- A harness note: an `rm` of a scratch glob was refused by the sandbox safety check; nothing was deleted and `src/py_ci_shared` was verified intact (295 entries).

## Summary

| id | sev | title |
|---|---|---|
| 49-T1 | P2 | `::error` annotation has no file or line and, for 13+ gates, no information (the first line is a label) |
| 49-T2 | P2 | `import_cycles` and ~30 other module `main`s crash on cp1251/cp1252 consoles; only `cli._print` is guarded |
| 49-T3 | P2 | `_text_ratchet` gates print findings twice, the run-all row loses the guidance, a stale baseline reads "0 new violation(s)", the fix command does not exist |
| 49-T4 | P2 | The printed refresh command names a label `refresh` rejects (8 gates) and omits `--grow` on a first seed |
| 49-T5 | P2 | A PASS never says files read or findings accepted; `allow_unparsed` and `min_files = 0` passes are invisible |
| 49-T6 | P2 | `git_dependency_pins` and `timezone_honest` list findings without the dependency or the line |
| 49-T7 | P3 | `repo_hygiene` and five more sites cap lists at 10 to 50 with no "and M more" |
| 49-T8 | P3 | Under Actions `budget = "fail"` exits 1 with no annotation; findings never reach the job summary |
| 49-T9 | P3 | `run-all` has no final verdict word |
| 49-T10 | P3 | Not-enabled message has no instruction or suggestion; absolute paths and mixed separators in 22 of 175 texts |
| 49-T11 | P3 | Wording: duplicated file name, `1 entries`, no-op refresh reads as PASS, unparsed-file message names no switch |

Counts: P0 0, P1 0, P2 6, P3 5 (total 11).
