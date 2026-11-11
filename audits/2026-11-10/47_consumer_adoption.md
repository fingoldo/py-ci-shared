# 47 consumer_adoption (round 2026-11-10)

Read-only. Excluded as already reported: 47-F1 to 47-F5 (2026-11-07 round, including the pin lag, the wrong `# v1.23.8` comments, the Behaviour change action clauses, the more-findings count and the corpus flags). Only the waiver file and the disposition of 47-F1 are re-checked below, because the tracker's claim there is contradicted by the file.

## 47-R6 [P1] The documented upgrade path (`@v1`) is reported as a failing `moving-pin` by the consumer-pins job

- Evidence: README.md:49-51 says consumers use the moving `@v1` tag and "no per-repo SHA bump". README.md:605-606 says `uses: ...@v1` is the normal form. `adoption_matrix.py:96` `_MOVING_TAG = r"^v?\d+(\.\d+)?$"` and `:150-151` classify `v1` as `moving-tag`; `:91` `MOVING_KINDS` includes `moving-tag`; `:134` `Pin.moving` uses it; `:661` `failing` includes `bool(self.moving_pins)`; `:679` emits `moving-pin` with the text "it changes without a commit here", which gives no next step. `consumer-pins.yml` runs this matrix daily.
- Failure scenario: a consumer follows README.md:49-51 and switches to `@v1`. The daily consumer-pins job turns red on that repo with a message that says the pin is wrong, not what to do. The owner then sees a red job for the documented setup, and the real findings in the same job get ignored.
- Proposed disposition: owner decision. Recommendation: treat `moving-tag` (`v1`) as acceptable for first-party consumers, because README.md:75-77 says a SHA pin is only needed against third-party upstreams. Keep `branch` and `unpinned` failing. Also add an action to the `moving-pin` message. Either change `MOVING_KINDS` or correct README.md:49-51 and 605-606. Pick one; the two currently contradict each other.
- ast_checkable: True; fp_risk: low. Disposition: OPEN (owner decision on the policy; the code change follows it).

## 47-R7 [P1] The waiver file has no waiver rows, so the "PARTLY RESOLVED" disposition of 47-F1 is not true in the repo

- Evidence: `configs/pin_waivers.toml` contains no `[[waiver]]` table. The only match for `[[waiver]]` in the repo is the comment at `configs/pin_waivers.toml:5`: "No consumer needs a waiver today: every pin is within the tolerance." That comment is false for the consumers 47-F1 names, which lag 6 to 12 releases against `--allow-behind 2`. The tracker row 47-F1 (audits/TRACKER.md:1099) and the 2026-11-07 file say "waivers run to 2026-12-07" for flutter_app_core, polyvocab_app, noema_app, flutter_uptime_monitor, claude-usage-notifier and autopsia. `_adoption_waivers.py:30-41` and `adoption_matrix.py:969-973` only read rows that exist, so the job uses the default tolerance 2 for every repo. `consumer-pins.yml` therefore still fails on those repos.
- Failure scenario: the owner reads the tracker, believes the daily job accepts the lag until 2026-12-07, and does not act on the job's red state. The job stays red every day, the real findings in it are not read, and the 2026-12-07 date passes with nothing recorded.
- Proposed disposition: correct the 47-F1 disposition in audits/TRACKER.md:1099 to say no waiver rows exist. Then the owner decides per consumer: add a dated `[[waiver]]` row (name, oldest, until, reason) with a reason, or bump the pin. Do not add rows without a reason, because the file header says each row is work in the consumer repo.
- ast_checkable: True; fp_risk: low. Disposition: OPEN.

## 47-R8 [P2] Running the matrix without `--resolve-in` turns the stale-pin, unresolvable-pin and waiver checks off silently, with exit 0

- Evidence: `adoption_matrix.py:25` (the usage example in the module docstring) runs `python -m py_ci_shared.adoption_matrix ../mlframe ../pyutilz` with no `--resolve-in`. `adoption_matrix.py:967` sets `resolver = None` in that case. `adoption_matrix.py:727-729` fills `report.behind` only when a `RefResolver` exists, so `stale_pins` (`:644`) is always empty and `unresolvable_pins` (`:649`) is always empty. `adoption_matrix.py:985-988` warns on stderr only about the pin comments, not about these checks. The `--allow-behind` help text (`:956`) says "needs --resolve-in" but nothing enforces or reports it.
- Failure scenario: a developer runs the documented example against a consumer whose pin is 12 releases behind, gets exit 0 and no warning, and believes the pin is current. The same run in CI (which passes `--resolve-in .`, consumer-pins.yml) fails.
- Proposed disposition: when a fixed pin exists and `resolver is None`, write to stderr that stale and unresolvable pins were not checked and that `--resolve-in` is needed. Better: make `--allow-behind` or `--waivers` without `--resolve-in` a usage error (exit 2), following the 0/1/2 convention in README.md "Stable within v1".
- ast_checkable: True; fp_risk: low. Disposition: OPEN.

## 47-R9 [P2] Private consumers skipped for want of a token are missing from the adoption matrix and its job summary

- Evidence: `_consumers.py` `write_repos_file` (the function that writes the repos list read by adoption_matrix) takes only `result.cloned`, so skipped repos are never listed. The skip is reported only as a `::warning` line in `_consumers.py` `main` (the `consumer skipped` annotation). `adoption_matrix.py` contains no mention of skipped or not-checked repos (`grep skipped` returns none in that file), and `render_markdown` has no such section. `consumer-pins.yml:55-56` writes the matrix into `$GITHUB_STEP_SUMMARY` when `allow-skipped-private` is set, so the summary lists only the cloned repos.
- Failure scenario: a token expires and a run is started with `allow-skipped-private` (the documented way to run without the secret). The job is green, the summary shows some consumers, and it does not say which ones were not checked. Only the annotation mentions it.
- Proposed disposition: write the skipped consumers into the repos file (for example as `skipped = [...]`) and render a "not checked" table in `render_markdown`, so the summary and the artifact name every unchecked consumer.
- ast_checkable: True; fp_risk: low. Disposition: OPEN.

## 47-R10 [P2] A SHA-pinned `uses:` with no `py-ci-shared-ref` input is not reported, though README says it gets the previous release's configs

- Evidence: README.md:609-612 says a caller pinned to a SHA between releases "gets the previous release's configs unless it also passes `py-ci-shared-ref: <the same SHA>`". `adoption_matrix.py:99` `_USES` captures the `uses:` ref and `:100` `_REF_INPUT` captures the input, but the two are only compared as pins in `pins_agree` (`:672-677`). A `uses: ...@<sha>` with no input is a single pin that agrees with itself, so no finding is produced. The `_REF_INPUT` pattern is not used to pair them anywhere in `adoption_matrix.py` (grep returns only the definition and the docstring).
- Failure scenario: a consumer pins a between-release SHA for `ruff-blocking.yml` and omits the input. Its CI runs the ruff configs of an older release, while the `# vX.Y.Z` comment says a newer one. The matrix reports the repo as clean.
- Proposed disposition: add a finding `uses-sha-without-ref-input` when a `uses:` ref is a commit that is not a release tag (per `resolved` and `tag_commits`) and the same workflow has no `py-ci-shared-ref` equal to it. Report-only first, then fail once the consumers have the input.
- ast_checkable: True; fp_risk: medium (the default input for a release tag is the release itself, so only between-release SHAs should fire). Disposition: OPEN.

## 47-R11 [P3] Matrix failure messages do not say what the consumer should do, and the job summary omits two failure kinds

- Evidence: `adoption_matrix.py:677` `pins-disagree`, `:679` `moving-pin`, `:683` `stale-pin` (the text names the lag but not `--allow-behind`, a waiver, or a bump), and `:685` `unresolvable-pin` ("its install fails" with no fix). Compare `ci_pin_version_skew.py:170` and `:200`, which both say "Bump the pin ...". The summary at `adoption_matrix.py:995` says "disagreeing/moving/stale pins or silent skips" and leaves out `unresolvable-pin` and `ci-pin-comment-not-release` (enforced by `--strict-pin-comments`), so a repo failing only for those is named without a reason.
- Failure scenario: a consumer sees `stale-pin ... 9 releases behind` and cannot tell from the message whether to bump the pin, renew a waiver, or change the job. A repo that fails only on an unresolvable pin is listed under a reason it does not have.
- Proposed disposition: add an "Action:" clause to each of the four messages (bump to vX.Y.Z; add a dated waiver to configs/pin_waivers.toml; push the commit or fix the tag name; pin a SHA that exists upstream). Name every failure kind in the summary line, or print the first kind per repo.
- ast_checkable: True; fp_risk: low. Disposition: OPEN.

## 47-R12 [P3] The lag gate of `ci_pin_version_skew` is silently off unless a caller passes `known_releases`, and no consumer file in the workspace passes it

- Evidence: `ci_pin_version_skew.py:212` and `:244` default `known_releases` to `()`. `ci_pin_version_skew.py:192-193` returns `[]` when there are no releases, with no finding and no note. `grep -rl assert_installed_matches_ci_pin|ci_pin_version_skew` over `C:\Users\Admin\Machine learning` (py, yml, toml, yaml, excluding py-ci-shared) finds no consumer file, so the `ci-pin-lags-release` check (`max_lag` default 2, `:52`) is not wired into any consumer's hooks that this workspace shows. This is a scope limit of the search: consumer repos outside that folder were not inspected.
- Failure scenario: a consumer's local hook reports no lag because the caller never passed the release list, while CI (adoption_matrix) reports the same pin as stale. The two disagree and the local run is green.
- Proposed disposition: when `known_releases` is empty, have the gate return a `ci-pin-lag-unchecked` note, or have the gate read the release list from the py-ci-shared checkout. Confirm in each consumer's hook config whether it passes `known_releases` before deciding.
- ast_checkable: True; fp_risk: low. Disposition: OPEN (confirm consumer usage first).

## Summary and counts

Dimension 47 (consumer adoption), round 2026-11-10. Seven new findings (47-F6 to 47-F12). The 2026-11-07 findings 47-F1 to 47-F5 were not re-reported, except that 47-F7 contests the 47-F1 disposition.

| Severity | Count | IDs |
|---|---|---|
| P0 | 0 | |
| P1 | 2 | 47-F6, 47-F7 |
| P2 | 3 | 47-F8, 47-F9, 47-F10 |
| P3 | 2 | 47-F11, 47-F12 |

Dispositions: 0 resolved; 6 OPEN (47-F6 needs an owner policy decision; 47-F12 needs a check of consumer hook config first); 1 correction to the tracker (47-F7). The consumer-pins job is red today, and 47-F6 and 47-F7 are the two reasons it stays red for consumers that follow the docs.

Not verified: a full adoption_matrix run over real consumer checkouts (an earlier run timed out at 100 s), so the stale-pin counts in 47-F1 are from the 2026-11-07 round and were not recomputed here.
