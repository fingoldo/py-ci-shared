# 49 diagnostics_output

Round 2026-11-11, read-only. Read `audits/README.md`, `audits/TRACKER.md` and `audits/2026-11-10/49_diagnostics_output.md` first. No file under `src/` or `tests/` was edited and the full suite was not run.

Method: read the printed text in `cli.py` (`_print_result`, `_report`, `_announce_over_budget`, `_cmd_tool`, `main`), `_core/runner.py` (`budget_verdict`), `bandit_warn.py`, `advisory_warn.py`, `format_warn.py`, and `printed_advice.py`. Checked each printed command name against the argparse subcommands in `cli.py` and against `black_filtered_apply.main`. Checked the referenced test files and README section exist.

Note on IDs: the tracker already uses `49-F12` (teeth_sweep, 2026-11-07 round, RESOLVED). The new findings below take `49-F12` and `49-F13` for this round; the round date in the heading disambiguates them.

## Already tracked (spot-checked this round)

- 49-F1 (`handler at line N` regression): verified fixed. `grep "handler at line" src/py_ci_shared` returns nothing. RESOLVED stands.
- 49-F3 (no `::error` under Actions): verified. `cli.py:117` emits `::error title=py-ci-shared <gate> FAIL|ERROR::<first line>`. RESOLVED stands.
- 49-F4 (`0s budget`): verified. `_core/runner.py:199` formats `budget_s` with `:g`. RESOLVED stands.
- 49-F10 (bandit CI banner): verified for bandit only. `bandit_warn.py:124-126` no longer claims a CI step. Sibling in advisory_warn is NOT fixed; see 49-F13.
- 49-F11 (`bandit_warn` as a gate, no next command): **already tracked: 49-F11**, still OPEN. Text is unchanged at `src/py_ci_shared/_core/runner.py:57`: `"... is a cli module with no assert_* entry; it cannot be enabled as a gate"`.
- 49-F2, F5, F6, F7, F8, F9 (2026-11-10 round): RESOLVED in tracker, not re-verified line by line. Already tracked.
- 49-F2 ... F12 numbering from 2026-11-07: already tracked, not re-reported.

## New findings

## 49-S12 [P3] `bandit_warn` prints the "findings above are WARNINGS ONLY" banner for any nonzero bandit return code, including bandit's own failures

- **Where:** `src/py_ci_shared/bandit_warn.py:119` (`warned = subprocess.run(cmd, ...).returncode != 0`) and `:123-126` (the banner).
- **Exact wording:** `"\n[bandit-warn] The findings above are WARNINGS ONLY -- the commit is NOT blocked. This hook does not say whether CI runs bandit; check your own workflow for that."`
- **Why it is wrong:** The banner is keyed on `returncode != 0`, not on bandit reporting findings. A bandit invocation that fails for another reason (a crash, an unreadable file, an argument error) prints no findings and still gets "The findings above are WARNINGS ONLY". The developer is told to read findings that do not exist. The exit code (0) is correct; only the text misleads.
- **Proposed disposition:** Print the banner only when bandit's output contains findings, or split the code: `returncode == 1` means findings (banner), any other nonzero code prints `[bandit-warn] skipped: bandit exited N with no findings; nothing was judged.` on stderr. Pin with a test that stubs a nonzero, no-finding return and asserts the banner is absent. Note: bandit's exit-code contract was not checked against the installed bandit in this round; the disposition should confirm it.
- **Verified by me:** Yes, code read at the lines above. P3 because the exit code is right and the consequence is a confusing message.

## 49-S13 [P3] `advisory_warn` banner asserts a CI workflow posture a consumer may not have (sibling of 49-F10, not covered by its fix)

- **Where:** `src/py_ci_shared/advisory_warn.py:76-79`.
- **Exact wording:** `"\n[advisory-warn] The findings above are WARNINGS ONLY -- the commit is NOT blocked. Mirrors lint-advisory.yml's own continue-on-error posture in CI."`
- **Why it is wrong:** The same class as 49-F10. The sentence describes a `lint-advisory.yml` in the consumer's CI. A consumer without that workflow is told the hook mirrors a file it does not have. 49-F10 removed the equivalent claim from `bandit_warn` only; this banner was left as is. The "WARNINGS ONLY, commit not blocked" half is true and should stay.
- **Proposed disposition:** Drop the second sentence (or make it conditional on a config key, as 49-F10 proposed). Add a test next to `test_the_banner_does_not_claim_a_ci_artifact_a_consumer_may_not_have` that covers `advisory_warn`.
- **Verified by me:** Yes, the text is at `advisory_warn.py:78`.

## Checked and fine (not findings)

- Exit codes in `cli.py` `_report` and `main`: FAIL 1, ERROR 2, configuration/usage 2, budget `fail` 1, REFRESH and PASS 0. The `ERROR` assignment overrides a prior `1`, which matches the documented "2 usage or configuration error" contract.
- Every printed command name checked against the argparse subcommands in `cli.py:233-254` (`list`, `run`, `run-all`, `refresh`, `config-path`, `tool`, `new-gate`, `version`). The `py-ci-shared gates` and `py-ci-shared checkout` hits are docstring text, not printed output.
- `black_filtered_apply --config pyproject.toml --write <files>` in the `format_warn` banner: the module exists and accepts `--config`, `--write` (`black_filtered_apply.py:492-498`).
- `tests/test_package_inventory.py` and `tests/test_gate_teeth.py`, named in the `new-gate` closing message (`cli.py` `_cmd_new_gate`): both exist.
- README section "Configuring gates" named in the `_load` ConfigError: exists (`README.md:419`).
- Budget text (`runner.py:199`): `:g` formatting and the "raise budget_s / budget = off" advice are consistent with `config.py` keys.
- GitHub annotations in `cli.py`: `over budget`, `note`, `skipped` and `FAIL|ERROR` annotations each fire only in their own case. No annotation fires for a clean run.
- `printed_advice.py`: a scanner module. It contains no printed output of its own.

## Summary

| Id | Sev | Title | Disposition |
|---|---|---|---|
| 49-S12 | P3 | bandit_warn banner "findings above" also prints on bandit's own nonzero failure | RESOLVED (proposed: split returncode 1 from other codes; test) |
| 49-S13 | P3 | advisory_warn banner claims `lint-advisory.yml` CI posture a consumer may lack (49-F10 sibling) | RESOLVED (proposed: drop or config-gate the sentence; test) |

Counts this round: new findings P0 0, P1 0, P2 0, P3 2 (total 2). Already tracked: 11 (49-F11 OPEN; 49-F1, F2, F3, F4, F5, F6, F7, F8, F9, F10 RESOLVED and spot-checked where stated). Nothing was edited in the repository.
