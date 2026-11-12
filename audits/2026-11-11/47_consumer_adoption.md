# 47 consumer_adoption (round 2026-11-11)

Read-only. Files read: `src/py_ci_shared/adoption_matrix.py` (main, `scan_repo`, `RepoReport`, `releases_behind`, `load_repo_list`, `_unique_names`), `adoption_sha_ref.py`, `_adoption_waivers.py`, `_consumers.py` (`checkout`, `main`, `write_repos_file`), `ci_health.py` (exit and billing paths), `configs/consumers.toml`, `configs/pin_waivers.toml`, `.github/workflows/consumer-pins.yml`, `.github/workflows/ci-health.yml`.

Already tracked (re-checked, not re-reported): 47-F1 to 47-F5 (2026-11-07) and 47-F6 to 47-F12 (2026-11-10). Their tracker rows are at audits/TRACKER.md:1170-1174 and :36-42. Status of the tracker rows as of this round:

- 47-F6 (moving-tag `v1` reported as moving-pin): already tracked: 47-F6. Still OPEN; the code at adoption_matrix.py:96, :150-151, :679 matches the prior report.
- 47-F7 (waiver file has no rows, 47-F1 disposition false): already tracked: 47-F7. Re-confirmed: `configs/pin_waivers.toml` has no `[[waiver]]` table, line 5 says so. Tracker row :37 is OPEN.
- 47-F8 (no `--resolve-in` = checks off): already tracked: 47-F8. Code now exits 1 (adoption_matrix.py:1000-1001 area, `resolver is None and not args.advisory`), so the tracker's RESOLVED status is consistent with the code.
- 47-F9 (skipped private consumers missing from output): already tracked: 47-F9. Marked RESOLVED in the tracker. A new finding below (47-F13) shows the resolution does not hold on one path.
- 47-F10 (between-release SHA without ref input): already tracked: 47-F10 (report-only, RESOLVED in tracker). Code confirmed: `adoption_sha_ref.py:26-39`.
- 47-F11, 47-F12: already tracked: 47-F11, 47-F12. Both marked RESOLVED in the tracker.

## 47-S13 [P2] The "Not checked" table is appended after the matrix step, so a red matrix run drops it from the summary and artifact

- Evidence: `.github/workflows/consumer-pins.yml:62-69`. The "Adoption matrix" step is one `run:` block: `adoption_matrix` (line 64), then `_consumers not-checked ... >> adoption-matrix.md` (line 67), then `adoption_sha_ref` (line 69). GitHub runs `run:` steps with `bash -e` by default. `adoption_matrix` exits 1 whenever a repo fails (`adoption_matrix.py` main, `return 0 if args.advisory or not failing else 1`). When it exits 1, the `not-checked` line never runs. The "Job summary" (line 77-80) and "Upload the matrix" (line 82-89) steps then publish a file without the "Not checked" section. The table is appended in one place only (line 67).
- Failure scenario: a manual run with `allow-skipped-private: true` (token expired) checks the public consumers. The matrix finds any real finding, for example the daily 47-F6 or 47-F7 `moving-pin` or `stale-pin`, and exits 1. The summary and artifact list the checked repos and omit the "Not checked" section. The skipped private repos disappear from the output, which is the case 47-F9 was written to close. Only the earlier `::warning` annotation remains.
- Why this is not already covered: the tracker's 47-F9 row (audits/TRACKER.md:39) says `_consumers not-checked` appends a table, and its test covers the report text, not the ordering of a failing step. The 47-F6 and 47-F7 findings keep this job red every day, so the failing path is the normal one.
- Proposed disposition: move the `not-checked` append (and the sha_ref step) so they run whether or not the matrix passed, for example `python -m py_ci_shared.adoption_matrix ... || matrix_rc=$?` then the append, then `exit ${matrix_rc:-0}`, or put the not-checked output in its own `if: always()` step that reads `repos.toml`. Add a test that reads the workflow and checks the append is not after a bare `adoption_matrix` line.
- ast_checkable: True; fp_risk: low. Disposition: OPEN. Partly reopens 47-F9.

## 47-S14 [P2] The stale-pin check is silently off, with exit 0, when the resolver has no release tags or git fails

- Evidence: `adoption_matrix.py:181-190`. `releases_behind` returns None when `releases()` is empty (line 184-185) and when `git tag --list v* --merged <sha>` fails (line 187-188). `scan_repo` at `:727-729` stores a `behind` entry only when the result is not None. `stale_pins` (`:644`) reads `self.behind.get(p.ref, (0, ""))`, so a missing entry counts as 0 behind and never fails. `report.known_tags` (`:731`) records that the resolver saw tags, but `stale_pins`, `failing` (`:656-663`) and `findings` (`:682`) never consult it. `comment_mismatches` does use `known_tags` (`:654`, and `_adoption_pin_comments`), so the stale check is the odd one out. The 47-F8 fix covers only the case where `--resolve-in` is absent. It does not cover a present resolver with no tags.
- Failure scenario: the `--resolve-in` checkout has no release tags, because a shallow clone was used, the tags were not fetched, or the path points at a clone with no tags. Every fixed pin is resolved to a SHA, so `unresolvable-pin` does not fire either. The run reports no stale pin and exits 0, and the consumer-pins job is green while consumers are many releases behind. Nothing is printed to stderr about it, unlike the missing-`--resolve-in` case at `:972-974`.
- Proposed disposition: when `resolver` is set and `resolver.releases()` is empty, write a stderr line and exit 1 (same rule as 47-F8: the lag check did not run, so exit 0 claims it passed). When `releases_behind` returns None for a resolved pin, add it to a `lag-unchecked` list in the report and fail on it. Add a test with a tagless temporary git repo that expects exit 1.
- ast_checkable: True; fp_risk: low. Disposition: OPEN.

## 47-S15 [P3] Waiver rows are silently unused when the name matches no repo or a name is duplicated

- Evidence: `_adoption_waivers.py:41` `out[waiver.name] = waiver`: a second `[[waiver]]` table with the same name replaces the first with no error, so the first row's dates and reason vanish. `adoption_matrix.py:973` looks the waiver up by repo name with `waivers.get(name)`. A waiver whose name matches no repo gets no note at all (`waived_allowance` is never called for it), so the row grants nothing and nothing says so. `_unique_names` (`adoption_matrix.py:934-938`) renames a repo to `parent/name` when two roots share a name, so a waiver keyed on the plain name never matches it. The committed file is covered by `tests/test_adoption_waivers.py:58-63`, which checks that each name appears in `consumers.toml`. That test does not check a runtime `--waivers` file, and it does not check duplicates.
- Failure scenario: a consumer's waiver is written under a name that differs from its `consumers.toml` name, or a renewal is added as a second row with the same name. The renewal is discarded (or the old row is), the run keeps failing with a stale-pin, and the owner sees no line saying the waiver was unused.
- Proposed disposition: `load_waivers` raises CoreError on a duplicate name; `main` writes a `pin-waiver-unused` note for each waiver whose name matched no report. Small change, with a test per case.
- ast_checkable: True; fp_risk: low. Disposition: OPEN.

## Checked and not reported

- `_consumers.checkout` (`_consumers.py:121-145`) and `main` (`:190-215`): a failed clone exits 1, and `--fail-on-skipped-private` exits 1 on a skipped private repo. The zero-clone case exits 1. No defect.
- `ci_health.exit_code` (`ci_health.py:309-314`) and the billing path: billing-only red is reported apart and not failed, which is the documented design. Probe-limit overflow and unreadable annotations fail closed (`:104-105`, `:484-488`). `--only` with no match exits 2 (N-7, RESOLVED).
- `adoption_sha_ref.main` (`adoption_sha_ref.py:52-65`) always exits 0. It is report-only by design (47-F10), and the docstring says so. Not a finding.
- `consumer-pins.yml` checkout step: a missing token without `allow-skipped-private` fails at `_consumers --fail-on-skipped-private` (line 56), which is the intended behaviour.

## Summary and counts

New findings in this round: 3.

| Severity | Count | IDs |
|---|---|---|
| P0 | 0 | |
| P1 | 0 | |
| P2 | 2 | 47-F13, 47-F14 |
| P3 | 1 | 47-F15 |

Already tracked: 12 IDs re-checked (47-F1 to 47-F12). Of these, 47-F6 and 47-F7 (P1) are still OPEN and are the reason the consumer-pins job stays red. 47-F9 is RESOLVED in the tracker, and 47-F13 partly reopens it.

Not verified: a live adoption_matrix run over real consumer checkouts, per the 2026-11-10 round's note. No remote API was called. The full pytest suite was not run.
