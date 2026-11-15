# 46 ci_release_surface: round 2026-11-16 (T)

Scope: all 14 workflows, `.github/scripts/release_guard.py` and `release_local.py`, `tests/test_release_version.py`, the release/rollback flow.
Commit audited: `48e49b1`, plus another session's uncommitted edits to `.github/workflows/ci-health.yml`, `ci_health.py`, `CHANGELOG.md`, `tests/test_ci_health.py` (not touched by me).
Method: read `audits/2026-11-11/46_*.md` and the TRACKER rows 46-*; then `gh run list -L 100` (2026-11-08 .. 11-15), `gh run view --log-failed`, `gh api` GET of runs, jobs, activity, releases. No push, tag, rerun or dispatch.
Not covered: lint-advisory, lint-blocking, mypy-* step internals (exercised only through self-ci integration jobs, all green in the sampled runs); dependabot (46-R18 stays deferred); GitHub settings (46-F14).

## Measurements (last 100 runs, 2026-11-08 .. 11-15)

| workflow | runs | result |
|---|---|---|
| CI (self-ci) push/PR | 67 | 10 success, 56 failure, 1 cancelled. Most failures are commits pushed while the suite was red (the 11-09 series, the 11-12 series); the 11-14 pair is a905293 changing the consumer list without its pinned test (fixed by 7262bfb) |
| Consumer CI health | 8 | 8 failure (the last 20 all failed, back to 2026-10-29) |
| Corpus drift | 11 | 6 failure, 2 success, 1 cancelled, plus dispatches; the last two scheduled runs fail at `Compare` |
| Consumer pins | 7 | 7 success |
| Config drift | 1 | success (weekly, Mondays) |
| Release | 2 | 2 failure, both for tags whose release WAS published (T2) |

Longest jobs (run 43665063846, 24 jobs): windows 3.9 13.5 min, windows 3.11 10.7 min, against `timeout-minutes: 20`. Queue before start: up to 325.5 min (macos-latest leg), 210 min (ubuntu legs, run 43429841833). Whole-run wall clock 5.5 h (43665063846) and 3.7 h (43429841833). Always skipped: `integration-gates-remote-install / gates` (by design, `enabled: false`). No cron is missing: consumer-pins, ci-health, corpus-drift and config-drift all produced scheduled runs.

## Incident replay

| incident | would the gates have prevented or clearly reported it? |
|---|---|
| changelog test red on all legs when a release has no new module (run 43401145482: `the registry names no module for the next release; the check would pass vacuously`) | Reported clearly, but only after the push: `release_guard prepare --check` does not run the suite. Fixed by c499d60; nothing structural left. |
| version collided with already-tagged v1.23.11 | `release_local.precondition_problems` (release_local.py:129-130) refuses a tag that exists on the remote and `prepare` refuses "not ahead of the newest published". Clear. The root cause (two sessions on one version) is not gated: T3. |
| consumers monitor red for days | Self-inflicted loop (T1) plus a monitor with no known-red baseline (T4). |
| macOS leg timed out | The leg runs 5-7 min against a 20 min limit; the real exposure is the queue (325 min). The verdict text does not tell a timeout from a failure (release_guard.py:117-122). T5. |

## 46-T1 (P1) Consumer CI health fails every night because its self-watch step reads its own history; the fix is uncommitted
- **Evidence:** `gh run list -w "Consumer CI health" -L 20` shows 20 failures in a row. Run 43716393726 (commit 7262bfb), self-watch step: `python -m py_ci_shared.ci_health configs/self_watch.toml --max-red-days 2 --output ci-health-self.md`, row `| py-ci-shared | Consumer CI health | RED | >=43.2 d (45 runs) | 2026-10-03 |`, then `##[error]fingoldo/py-ci-shared Consumer CI health (.github/workflows/ci-health.yml) has been red since 2026-10-03`. `git show 7262bfb:.github/workflows/ci-health.yml | grep skip-workflow` prints nothing. `git status` shows `M .github/workflows/ci-health.yml` adding `--skip-workflow "Consumer CI health"`, so another session is fixing it but it is not in master. Runs 43595036343, 43433393778, 43232352225, 42633438786 each fail BOTH the `CI health report` and `Self-watch report` steps.
- **Impact:** the 46-F4 self-watch made the daily job permanently red, with no way out even when every consumer is green. A permanently red monitor carries no signal (T4).
- **Proposed fix:** commit the uncommitted change with its test; add a test that parses ci-health.yml and asserts any `ci_health` call whose config names this repository passes `--skip-workflow` with this workflow's own `name:`. ADDITIVE.
- **Disposition:** RESOLVED (05fa71f): `ci_health --skip-workflow`; ci-health.yml's self-watch step skips `Consumer CI health`. Both monitors were run by hand afterwards and passed. (fix exists uncommitted)

## 46-T2 (P2) Both recent releases left a red Release run although the release was published; the local publish and the tag workflow race
- **Evidence:** `gh api repos/fingoldo/py-ci-shared/releases`: v1.23.11 published 2026-11-13T11:15:48Z, v1.23.10 03:42:31Z, author fingoldo (the `release_local.py` path). Release run 43415641422 (tag v1.23.11) started 11:15:45; its verify job ran only at about 14:05 (queue) and failed at "The tagged commit is prepared for this release": `v1.23.11 is not ahead of the newest published release v1.23.11`, exit 2. Release run 43360737423 (v1.23.10) failed `v1.23.10 is not on master`: `gh api repos/.../activity` shows the tag run at 03:42:29 and the master push of the containing merge e45de8e at 03:42:44, so the tag arrived 15 s before the branch.
- **Impact:** a red Release run no longer means "release failed". CLAUDE.md says "Re-run failed jobs" finishes a half-done release, but re-running v1.23.11's verify can never pass once its release exists, because `prepare --check` treats the tag being released as not ahead of itself.
- **Proposed fix:** (a) `release_guard prepare --check` for the tag being released accepts a published release whose tag commit equals HEAD; (b) `release_local` refuses to push a tag whose commit is not yet on `origin/master` (it pushes the branch first only when HEAD is ahead; the v1.23.10 case was a separate merge push); (c) a test for (a) with a fake published list containing the tag at HEAD. Release tooling only, no consumer impact: CHANGELOG line "Nothing to do". BEHAVIOUR CHANGE (internal).
- **Disposition:** OPEN

## 46-T3 (P2) Nothing stops two sessions preparing the same next version; the bump-after-tag step is a hand edit
- **Evidence:** d7d898a "Bump the next release to 1.23.11 after the v1.23.10 tag" (commit date 09:25 +07, 11-13), then 5264e5d "Merge origin/master into master" (committer offset +03) was tagged v1.23.11, then fa4cb3f "Bump the next release to 1.23.12: 1.23.11 is tagged at 5264e5d". Two checkouts worked on the same version. `tests/test_release_version.py:81` catches only the state after the tag; release_local.py:129-130 catches only a tag that already exists when it runs.
- **Impact:** a collision costs a version (v1.22.0 and v1.22.2 did, per CLAUDE.md) and appears only at tag push, where a tag can never be moved.
- **Proposed fix:** `prepare --write` and `release_local` run `git fetch --tags` and refuse when the remote already has a tag for the version in the CHANGELOG Unreleased heading ("version X is being released: use X+1"); dry-run test against a scratch repo with a remote tag. ADDITIVE.
- **Disposition:** OPEN

## 46-T4 (P2) ci-health has no known-red baseline, so it is red every night on standing reds and a new regression cannot show
- **Evidence:** run 42633438786 (11-09) lists RED rows for polyvocab_app CI 111.5 d, algopacksimple CI 97.4 d, social 76 d, flutter_app_core 68 d, glossum_backend_scripts 4 workflows at 66 d; run 43232352225 (11-12) lists mlframe deep-nightly 66.9 d, mlframe CI 15.7 d, pyutilz CI. `grep -n "baseline\|known\|allow" src/py_ci_shared/ci_health.py` finds no known-red option; only `--fail-on-skipped-private` and `--skip-workflow` exist. One GitHub 502 for a single consumer (`ingoldo/mlframe: GET ...: HTTP 502 Bad Gateway`, run 43232352225) is also a failing row of the same job.
- **Impact:** the job's red/green has no information; this is the "monitor red for days because one consumer was broken" incident in general form.
- **Proposed fix:** `ci_health --known-red FILE` (TOML entries `repo`, `workflow`, `since`, `reason`, `expires`): known reds print KNOWN and do not fail until `expires`; an unlisted or expired red exits 1. ADDITIVE (new flag; the workflow opting in is internal).
- **Disposition:** OPEN

## 46-T5 (P2) release verify waits 100 min for self-ci, which queued up to 325 min in the observed window
- **Evidence:** release.yml:38 and :100 (`--wait 6000`). Run 43665063846: macos leg queue 325.5 min, run created 11-15T01:51:53, updated 07:23:00 (5.5 h). Run 43429841833: ubuntu 3.9 leg queue 210.4 min, 3.7 h wall clock. 46-F5 sized the wait from "2 h observed". On timeout the step exits 1 with "self-ci is still running for this commit" (release_guard.py:111), the same code as a real red (distinct pending code was DEFERRED in 46-F5). v1.23.11 was tagged seconds after 5264e5d was pushed.
- **Impact:** a red Release run that is not a failure (and, with T2, indistinguishable from one).
- **Proposed fix:** `ci-green` exits 3 for pending; release.yml turns 3 into an `::warning::` plus a step-summary line "re-run when self-ci finishes" instead of red; or raise the wait to 20000 s with `timeout-minutes: 360`. ADDITIVE.
- **Disposition:** OPEN

## 46-T6 (P2) ci-health and self-watch read only `branch=master`, so failed Release (tag) runs are invisible to them
- **Evidence:** `gh api repos/fingoldo/py-ci-shared/actions/runs/43415641422 --jq .head_branch` gives `v1.23.11`. configs/self_watch.toml has `branch = "master"`; ci_health reads "completed runs on the consumer's CI branch (plus scheduled runs)" (ci_health.py:4). Report row in run 43716393726: `py-ci-shared | Release | green | last success 2026-10-04 | run 37167932873` although Release runs 43415641422 and 43360737423 (11-13) failed.
- **Impact:** a failed Release (dead tag, or publish failure after v1 moved) never turns any monitor red.
- **Proposed fix:** a `tag_workflows = ["Release"]` list in self_watch.toml; ci_health reads `event=push` runs of those workflows regardless of `head_branch`. ADDITIVE.
- **Disposition:** OPEN

## 46-T7 (P3) A change to configs/consumers.toml reached master without its pinned test
- **Evidence:** runs 43617545127 (a905293) and 43619468953 (c2abbc6): `FAILED tests/test_strict_skipped_private.py::test_corpus_includes_the_two_private_repos_and_every_non_corpus_consumer_says_why` on 13 and 14 legs; fixed by 7262bfb 9 h later. 46-F14 (no ruleset) is an owner decision, so this is its cost; release safety is intact (ci-green needs the tagged SHA green).
- **Impact:** the nightly jobs ran against a red master for 9 h.
- **Proposed fix:** a pre-push hook mapping changed config files to the tests that name them (`configs/consumers.toml` -> `tests/test_strict_skipped_private.py` and the consumers-registry tests). ADDITIVE.
- **Disposition:** OPEN

## 46-T8 (P3) corpus-drift stays red until a manual `accept: true` after an intentional corpus change
- **Evidence:** corpus-drift.yml:157-175 takes the baseline from the last `status=success` run; Compare exits non-zero on drift (:187-200). After mlframe left the corpus, run 43708969429 (11-15) fails at `Compare` (`1 finder(s) jumped, went blind or started to error`), and will repeat nightly because a failed run is never the baseline.
- **Impact:** noise that, with T1/T4, is ignored. Not a silent pass.
- **Proposed fix:** treat a consumer added to or removed from `configs/consumers.toml` since the baseline as an accepted change for that consumer only. ADDITIVE.
- **Disposition:** OPEN

## Considered and not raised

- `continue-on-error` hiding a failure: self-ci preview legs have the `preview-report` job and `test_every_continue_on_error_matrix_leg_has_a_reporter_job` (test_release_version.py:462); lint-advisory is advisory by documented design; mypy-full `advisory` is an input. 46-F3 stands.
- Schedules that never ran: none; config-drift weekly runs 2026-09-14 .. 11-09, next due today.
- Always-skipped `integration-gates-remote-install / gates`: deliberate (`enabled: false`, self-ci.yml comment).
- Duplicate work between workflows: the local and remote-fetch integration jobs exercise different paths (intended); the cost is queue time, a runner-capacity matter (fan-out reduction DEFERRED in 46-F5).
- Release job permissions, token handling, persist-credentials, environment gating: unchanged since the 2026-11-11 clean check.
- 46-S22 / 46-S23: resolved uncommitted per TRACKER; not reopened.
- Windows 3.9 timeout headroom (13.5 of 20 min): no `cancelled` or `timed_out` job in the ten run job-lists I pulled for the failed runs; not raised.

## Summary

| id | sev | title |
|---|---|---|
| 46-T1 | P1 | Consumer CI health red every night: self-watch reads its own history; fix uncommitted |
| 46-T2 | P2 | Red Release runs after good releases; local publish vs tag workflow race; rerun cannot pass |
| 46-T3 | P2 | Two sessions can prepare and tag the same next version |
| 46-T4 | P2 | ci-health has no known-red baseline, always red |
| 46-T5 | P2 | release verify waits 100 min, self-ci queued up to 325 min |
| 46-T6 | P2 | ci-health reads only branch=master, tag-run Release failures invisible |
| 46-T7 | P3 | consumers.toml change pushed without its pinned test |
| 46-T8 | P3 | corpus-drift red until manual accept after an intentional corpus change |
