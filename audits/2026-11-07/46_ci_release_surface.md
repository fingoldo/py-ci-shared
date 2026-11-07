# 46_ci_release_surface

Round 2026-11-07, dimension 46. Every finding is OPEN until its Disposition line says otherwise. Read-only audit; commands were run against fingoldo/py-ci-shared on 2026-11-06 between 22:50 and 23:00 UTC.

## 46-F1 (P1) release_guard ci-green with an abbreviated SHA reports "self-ci never ran" for a commit whose run succeeded or is queued

- Evidence: release_guard.py:271 queries repos/R/actions/workflows/self-ci.yml/runs?head_sha=SHA; GitHub matches head_sha on the full 40 characters only. Nothing validates --sha (main(), lines 303-306).
$ gh api "repos/fingoldo/py-ci-shared/actions/workflows/self-ci.yml/runs?head_sha=213ce774e6e2c79586d18225dbcf841043538b5a" --jq .total_count  -> 1
$ same with head_sha=213ce77 -> 0
$ python .github/scripts/release_guard.py ci-green --repo fingoldo/py-ci-shared --sha <full 213ce77> -> "self-ci succeeded: .../runs/42281707584", rc=0
$ same with --sha 213ce77 -> "::error::self-ci never ran for this commit (tag a commit that was pushed to master)", rc=1
$ full SHA of the queued commit e873e5d -> "::error::self-ci is still running for this commit", rc=1 (pending, as designed). The short SHA of a queued commit gives "never ran" too.
This is observation (a): the wrong message came from short SHAs, not from the run state.
- Failure scenario: A maintainer (or a script) runs ci-green by hand with a 7-character SHA, is told CI never ran for a green commit, and either re-pushes (cancelling the real run, see 46-F2) or stops trusting the guard. In release.yml SHA is github.sha (full), so the workflow itself is safe; the CLI is the trap.
- Proposed fix: In main() validate --sha with re.fullmatch('[0-9a-f]{40}') and exit 2 with 'pass the full 40-character SHA' otherwise, or resolve it through GET repos/R/commits/SHA first. Distinguish 'no runs for a valid SHA' (red, never ran) from 'bad SHA'. Add a test with a 7-character SHA.
- AST/offline checkable: yes (argparse type check in release_guard.py)
- False-positive risk: low
- Disposition: RESOLVED 9e32bc2: --sha must be 40 lowercase hex (exit 2 with the reason); the test with a 7-character SHA failed before the fix and passes after.

## 46-F2 (P1) A newer push cancels the queued self-ci of the previous commit; a release commit can be left with only a cancelled run, and ci-green then fails with no recovery short of a new commit

- Evidence: self-ci.yml:10-12 concurrency group = workflow-ref, cancel-in-progress false for push. With false the RUNNING run is kept but a second PENDING run replaces (cancels) the earlier pending one. Demonstrated:
$ gh api repos/fingoldo/py-ci-shared/actions/runs/42224519080 -> conclusion cancelled, created 13:19:05, run_started_at 14:23:11 (attempt 2), updated 14:30:09
$ python .github/scripts/release_guard.py ci-green --repo fingoldo/py-ci-shared --sha <full ecc72ae4> -> "::error::self-ci did not succeed for this commit: cancelled .../42224519080", rc=1
release.yml verify would be red for a tag on that commit; the tag is already pushed and cannot be re-pointed (CLAUDE.md: never re-point a tag).
- Failure scenario: The bump-to-next-release commit is pushed, a docs commit lands 10 minutes later while CI is still queued (queues are hours long, 46-F5); the bump commit's run is cancelled, the tag is pushed on it, Release/verify fails 'cancelled'. Re-running a run that GitHub cancelled before it started is not reliably possible.
- Proposed fix: Group push runs per commit: group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.sha }} (cancel-in-progress stays false for push), so no push run is ever replaced. Add workflow_dispatch to self-ci so a SHA with only a cancelled run can get a fresh one, and have ci-green's cancelled-only message say so.
- AST/offline checkable: yes (yaml: self-ci concurrency group contains github.sha for push)
- False-positive risk: low
- Disposition: RESOLVED 9e32bc2: self-ci push runs grouped per commit (github.sha), workflow_dispatch added, ci-green names the recovery command when only cancelled runs exist; tested in test_release_version.py.

## 46-F3 (P1) A failing preview leg (continue-on-error job) leaves the run green: Windows 3.9 was red for days and every reader of the signal (ci-green, ci-health, run list) saw success

- Evidence: self-ci.yml:34 continue-on-error: ${{ matrix.preview == true }} at JOB level. GitHub then reports the job as failure but the RUN as success.
$ gh run view 42248836894 --json conclusion -> success
$ gh run view 42248836894 --json jobs -> 'test (windows-latest, 3.9, true)  failure  failed step: Run tests'; the same for runs 42254329289 and 42258798436 (all three 'completed success').
ci_verdict (release_guard.py:98-108) and ci_health.GREEN_CONCLUSIONS (ci_health.py:89) read only the run conclusion; neither looks at jobs. The leg went green only at 213ce77 (run 42281707584, after 'Fix Windows 3.9 leg failures').
- Failure scenario: A real Windows/3.9 regression ships in a release: the leg that exists to catch it fails, the run is green, ci-green passes, nobody opens the job list. 'Non-blocking first' (TRACKER 34-F11) has no promotion date, so a preview leg stays advisory indefinitely.
- Proposed fix: Add a final job preview-report (needs: test, if: always()) that lists failed preview legs of this run via the jobs API (actions: read), writes them to GITHUB_STEP_SUMMARY and emits ::warning::/::error::, and make ci-green print failed jobs of a green run as warnings. Record a promotion rule (preview leg green for N consecutive master runs becomes blocking).
- AST/offline checkable: partly (yaml: every continue-on-error matrix leg has a reporter job)
- False-positive risk: medium
- Disposition: RESOLVED 9e32bc2: preview-report job (needs test, if always) lists failed legs in the step summary and as warnings; ci-green prints failed jobs of a green run; a test requires a reporter for the continue-on-error matrix. DEFERRED: the promotion rule (10 consecutive green master runs) is written in the workflow comment but not automated.

## 46-F4 (P1) py-ci-shared's own scheduled workflows are watched by nothing: corpus-drift succeeded 2 times in its last 51 runs, and ci-health ignores cancelled runs and this repo

- Evidence: $ gh run list -w corpus-drift.yml -L 12 -> Nov 3, 4, 5, 6 cancelled; Nov 1, 2 failure; Oct 31 and two Oct 30 dispatches cancelled; Oct 28-30 failure. 'success' count over the last 51 runs: 2.
$ gh run view 42210259206 --json jobs -> step "Snapshot tonight's counts" = cancelled; run 12:10:34 to 13:40:58 = the 90-minute job timeout (corpus-drift.yml:35); there is no step timeout, so one slow finder consumes the job and nothing is compared.
configs/consumers.toml lists 13 repos and not fingoldo/py-ci-shared (grep 'repo =' shows none), and ci_health.IGNORED_CONCLUSIONS = {cancelled, skipped, stale} (ci_health.py:88), so a workflow cancelled every night never turns red there even if it were listed.
The snapshot artifact has retention-days: 90; with no success the 'Find the last successful run' loop (corpus-drift.yml:74-86) ends with run_id empty.
- Failure scenario: The nightly drift detector has been dead for 10+ days while its history reads 'cancelled', not red. When the last live baseline expires, Compare runs with no previous file. Gate-count jumps in consumers go unseen.
- Proposed fix: Put a timeout-minutes on the snapshot step and log the slowest finders; treat N consecutive cancelled scheduled runs as red in ci_health; add fingoldo/py-ci-shared to consumers.toml (or a self-watch step) so its own scheduled workflows are checked; in ci_health a schedule workflow with no success in its last K runs is red regardless of cancellations.
- AST/offline checkable: partly (ci_health unit; consumers.toml lists its own repo)
- False-positive risk: low
- Disposition: RESOLVED 9e32bc2: ci_health treats 3 consecutive cancelled scheduled runs as red; configs/self_watch.toml plus a self-watch step in ci-health.yml watches this repo; the corpus-drift snapshot step has timeout-minutes 75. DEFERRED: logging the slowest finders in corpus_drift, waits on a profile of the 90-minute snapshot.

## 46-F5 (P2) release.yml waits at most 1800 s for self-ci, but a self-ci run can take 2 hours of queue plus run, so a release fails 'still running'

- Evidence: release.yml verify: ci-green ... --wait 1800 (30 min), timeout-minutes 60. Observed (gh api .../runs/ID/jobs, created_at vs started_at): run 42220965657 had ubuntu-24.04 jobs queued 115 and 121 min, and the 10-second job integration-ruff-blocking-remote-fetch queued 115 min; the run ran 12:52 to 14:57. Run 42233235560: jobs queued 33-52 min. Each push starts 21 jobs at once (10 test legs plus 11 reusable-workflow jobs), mostly on one label, ubuntu-24.04. Run 42307269695 for HEAD e873e5d was still queued at 22:53 UTC.
The guard itself behaves: it exits 1 with '::error::self-ci is still running for this commit'.
- Failure scenario: A tag is pushed soon after the bump commit while CI is queued: verify spends 30 minutes and fails, and 'pending' is reported as an error identical to 'red'. Re-running failed jobs works but only if the maintainer notices. It also widens the window in which 46-F2 cancels the run.
- Proposed fix: Raise --wait to cover the observed worst case (e.g. 7200) and verify timeout-minutes accordingly, or exit with a distinct message/code for 'pending, re-run later'. Reduce the fan-out (run the 11 integration jobs on push to master only, or merge them into fewer jobs) to cut demand on the single runner label.
- AST/offline checkable: yes (yaml: --wait value versus documented queue bound)
- False-positive risk: low
- Disposition: RESOLVED 9e32bc2: release verify waits 6000 s with a 120-minute job timeout (tested). DEFERRED: reducing the 11 integration job fan-out and a distinct pending exit code.

## 46-F6 (P2) Between the 'Bump the next release' commit and the tag, master's reusable workflows default py-ci-shared-ref to a tag that does not exist; a consumer pinned to such a SHA fails at install, by design without fallback

- Evidence: $ grep default .github/workflows/{black-filtered,ruff-blocking,lint-advisory}.yml -> default: "v1.23.9" (HEAD e873e5d)
$ git ls-remote --tags origin v1.23.9 -> (empty); newest tag v1.23.8.
black-filtered.yml:84 and ruff-blocking.yml:100: "py-ci-shared ref ... could not be installed ... There is no fallback to master." (three attempts, 15 and 30 s sleeps).
Consumers pin master SHAs (sibling checkouts: 'master 92d20e7' 5 pins, 'v1.23.8 (master, not tagged yet)' 6 pins). Those SHAs predate the bump (e13229e defaults v1.23.8) so they work today; a pin taken after the bump and before the tag breaks.
- Failure scenario: A consumer bumps its pin to the newest master SHA to pick up an unreleased fix (routine here) and lands in the bumped-but-untagged window: every ruff/black/lint job of that consumer fails on install until the tag exists.
- Proposed fix: Tag within minutes of the bump (bump and tag as one operation, prepare --write then tag), or default py-ci-shared-ref to something that always resolves. Minimum: ci_pin_version_skew / consumer-pins warn on a pin whose workflow default names a tag absent from known_releases.
- AST/offline checkable: yes (default ref is among git tags, offline)
- False-positive risk: medium
- Disposition: RESOLVED 9e32bc2: same fix as 43-F3 (defaults stay at the last published tag until prepare; only the prepared commit names the unpublished tag, a window CLAUDE.md tells the maintainer to close within minutes).

## 46-F7 (P2) gates.yml's py-ci-shared-ref default is tied to the version by no test, and `prepare --check` is run by no CI job: HEAD has three workflows at 1.23.9 and gates.yml at 1.23.8

- Evidence: $ python .github/scripts/release_guard.py prepare 1.23.9 --check -> "release 1.23.9 (newest published: v1.23.8): would change README.md, .github/workflows/gates.yml", rc=1
tests/test_release_version.py: test_each_reusable_workflow_defaults_to_its_own_release is parametrized over ["black-filtered.yml", "ruff-blocking.yml", "lint-advisory.yml"], 3 of the 4 files in release_guard.PREP_FILES. tests/test_reusable_workflows.py:446-455 tests gates.yml's `enabled` default and the run-all step, not the ref. grep 'prepare|--check' over .github/workflows/*.yml finds nothing.
gates.yml:35 default: "v1.23.8". CLAUDE.md line 47 says 'the three reusable workflows' (four are edited).
- Failure scenario: The 1.23.9 bump is committed by hand (not via prepare --write), the tests pass, release.yml passes, and gates.yml shipped in v1.23.9 installs the v1.23.8 package: a consumer on @v1 running the new gates workflow runs the old gates.
- Proposed fix: Parametrize the test over release_guard.PREP_FILES[4:] and add `release_guard.py prepare $VERSION --check` to release.yml verify (the tagged commit must be a fixed point of prepare). Fix 'three' in CLAUDE.md and the guard docstring.
- AST/offline checkable: yes
- False-positive risk: low
- Disposition: RESOLVED 9e32bc2: test parametrised over all four workflows, prepare --check added to the release.yml verify job, three fixed to four in CLAUDE.md and the guard docstring.

## 46-F8 (P2) CONSUMER_READ_TOKEN is not set: consumer-pins has been red every day checking nothing, ci-health skips private repos with a warning, corpus-drift has no strict flag

- Evidence: $ gh secret list -R fingoldo/py-ci-shared -> CODECOV_TOKEN, RELEASE_TOKEN only. $ gh run list -w consumer-pins.yml -L 5 -> failure Nov 2, 3, 4, 5, 6; the Nov 6 log: warning 'CONSUMER_READ_TOKEN not set' then 'Process completed with exit code 1'. ci-health (failure the same 5 days) is red for mlframe/pyutilz (public) and logs the same warning for private repos (ci_health.py:473 is a warning, not an error). corpus-drift.yml 'Check out the corpus repos' passes no --fail-on-skipped-private (consumer-pins.yml does).
- Failure scenario: The pin-lag signal for the private consumers is not produced; the daily red run is accepted noise, which is how a real pin-skew failure would be missed. ci-health 'green' means 'public repos only'. corpus-drift would build its baseline from a subset.
- Proposed fix: Create the fine-grained token (actions:read, contents:read, metadata:read on the private repos) and store it. Make ci-health write a red row (not a warning) when a private repo is skipped for want of a token, and pass --fail-on-skipped-private in corpus-drift.
- AST/offline checkable: partly
- False-positive risk: low
- Disposition: RESOLVED in code, owner action pending: Code commit: "Strict private consumers, public names for consumed internals, registry summary test". ci_health --fail-on-skipped-private writes a RED row (NOT CHECKED), an ::error:: annotation and exit 1 for a private repo skipped for want of CONSUMER_READ_TOKEN; ci-health.yml and corpus-drift.yml pass it like consumer-pins.yml, and a manual run can set allow-skipped-private. Public consumers are still checked out and scanned without the token. The fine-grained secret did not exist when this was written (gh secret list showed only CODECOV_TOKEN), so until the owner creates it the three scheduled jobs are red by design.

## 46-F9 (P2) RELEASE_TOKEN is a repository secret, not a `release` environment secret, so the environment's branch/tag policy does not protect it

- Evidence: $ gh secret list -R fingoldo/py-ci-shared --env release -> (empty); the repository secrets include RELEASE_TOKEN (created 2026-10-04). Environment `release` has a custom policy (master branch, v* tag) but any workflow on any branch or same-repo PR can read ${{ secrets.RELEASE_TOKEN }}. release.yml comments and CLAUDE.md say moving it into the environment is the owner's pending repository setting. The token carries contents and workflows write (it force-pushes v1 over workflow files).
- Failure scenario: A compromised action, a dependabot PR, or a collaborator's branch with a one-line workflow uses the token to force-move v1 (what every @v1 consumer runs) to arbitrary code.
- Proposed fix: Move RELEASE_TOKEN into the release environment's secrets and delete the repository-level copy; add a test that release.yml is the only workflow naming secrets.RELEASE_TOKEN.
- AST/offline checkable: yes (only release.yml references RELEASE_TOKEN)
- False-positive risk: low
- Disposition: PARTLY RESOLVED 9e32bc2: test that release.yml is the only workflow naming secrets.RELEASE_TOKEN. DEFERRED: moving the secret into the release environment is an owner repository setting.

## 46-F10 (P2) A SHA pin with a `# vX.Y.Z` comment passes ci_pin_version_skew even when the SHA is not that release's commit; three different SHAs claim v1.23.8

- Evidence: ci_pin_version_skew.py docstring: 'Offline, so only the comment is compared'. In sibling checkouts e13229e (13 pins), adba0d7 (9) and 64e63f5 (9) all carry '# v1.23.8'; the tag v1.23.8 is 630d5b7 (git ls-remote shows the annotated object c0008fd, ^{commit} is 630d5b7). `git tag --points-at` is empty for e13229e, adba0d7 and 64e63f5; each is an ancestor of 630d5b7, not it. This is case (e) of the brief. adoption_matrix with --resolve-in counts releases behind by commit and does not compare the comment to the tag commit.
- Failure scenario: A consumer believes it runs v1.23.8 (comment, ci_pin_version_skew green) while its SHA is an earlier master commit with older gates; behaviour differs from the release notes, and the 'lags by >2' arithmetic (commit-based) disagrees with the comment-based one.
- Proposed fix: In consumer-pins (online, --resolve-in) add a rule: a pin comment vX.Y.Z must resolve (git rev-parse vX.Y.Z^{commit}) to the pinned SHA; otherwise the comment must use the accepted 'master <sha7>' form. Expose it as ci-pin-comment-not-release.
- AST/offline checkable: yes (adoption_matrix with a tag resolver)
- False-positive risk: low
- Disposition: DEFERRED: needs Pin to carry the trailing vX.Y.Z comment through the pin readers in adoption_matrix plus a tag resolver rule (ci-pin-comment-not-release); waits on that refactor. Same work as 47-F2.

## 46-F11 (P3) ci_verdict accepts any success run for the SHA of any event and branch, including a pull_request run that tested the merge ref

- Evidence: release_guard.py:98-102 any(r.get('conclusion') in GREEN for r in runs); the request URL (line 271) has no event or branch filter. A pull_request run's head_sha is the PR head but it ran on refs/pull/N/merge. A fast-forward merge gives the master SHA a green PR run without any push run on master.
- Failure scenario: verify passes on a PR-merge-ref run for a commit whose push-to-master run was cancelled or red (46-F2/F3 flavour). Low likelihood on a single-owner repo.
- Proposed fix: Request &event=push&branch=master (the docstring already says 'tag a commit that was pushed to master') and report which events were seen when none match.
- AST/offline checkable: yes
- False-positive risk: low
- Disposition: RESOLVED 9e32bc2: ci-green queries branch=master (the event filter is left open so a workflow_dispatch run on master counts).

## 46-F12 (P3) rollback accepts any existing vX.Y.Z tag, including one whose Release run failed and was never published

- Evidence: release_guard.py:72-78 rollback_problem checks release_key and membership in git tags only. `gh run list -w release.yml` shows v1.23.2 and v1.23.0 as failure. Demonstrated: `rollback v9.9.9` -> "::error::v9.9.9 is not an existing tag"; `rollback v1.0.0` -> "v1.0.0: ok to move v1 to it" (23 releases old, no age or publication check). prepare already asks the releases API (_published_release); rollback does not.
- Failure scenario: Under stress an emergency rollback is pointed at a half-published or ancient tag and every @v1 consumer jumps to code that never passed Release verify.
- Proposed fix: Require `gh release view TAG` (or a successful Release run for the tag) and warn when the target is more than N releases behind; list the newest published releases in the error message.
- AST/offline checkable: yes
- False-positive risk: low
- Disposition: RESOLVED 9e32bc2: rollback --repo requires a GitHub release for the target and lists the newest published ones (release.yml passes the repo). The age warning was not added.

## 46-F13 (P3) install-command inputs are expanded into `run:` with ${{ }} in docs.yml, mypy-full.yml and lint-blocking.yml (zizmor template-injection, suppressed)

- Evidence: docs.yml:47 `run: ${{ inputs.install-command }}`, mypy-full.yml:73, lint-blocking.yml:269 (deptry-install-command). Each is suppressed with '# zizmor: ignore[template-injection]' and a comment that the value is first-party. Only a workflow_call caller can set it, so there is no external injection today.
- Failure scenario: A consumer wires `install-command: pip install ${{ github.event.inputs.extra }}` and anyone with dispatch rights runs code with the consumer job's token.
- Proposed fix: Pass the command through env (INSTALL_COMMAND) and run `bash -c "$INSTALL_COMMAND"` as gates.yml:88-92 already does (bash -c keeps multi-line semantics); the suppressions can then go.
- AST/offline checkable: yes (grep run: lines containing inputs.)
- False-positive risk: low
- Disposition: RESOLVED 9e32bc2: docs.yml, mypy-full.yml and lint-blocking.yml pass the commands through env and run bash -eo pipefail -c; suppressions removed; tests/test_workflow_install_commands.py.

## 46-F14 (P3) No ruleset or branch protection on master and no SHA-pin enforcement: red pushes reach master and any action version can be introduced

- Evidence: $ gh api repos/fingoldo/py-ci-shared/branches/master/protection -> 404 'Branch not protected'; gh api .../rulesets -> empty; gh api .../actions/permissions -> sha_pinning_required false, allowed_actions all. gh run list shows 12 consecutive failing self-ci runs on master on Nov 5 (run 42096219313 back to 41951045206 and the PR runs between): red commits were pushed, and the only barrier to tagging one is ci-green at release time.
- Failure scenario: A red or unreviewed commit reaches master; the barrier is the local pre-push hook plus release verify. An unpinned `uses:` can be added with nothing at the platform level refusing it (the pin is enforced only by a repo test).
- Proposed fix: Enable 'require actions to be pinned to a full SHA' and a ruleset requiring the self-ci aggregate (not the preview legs) on master, with the owner as bypass actor so the one-person workflow stays unblocked.
- AST/offline checkable: no (repository settings)
- False-positive risk: low
- Disposition: WON'T FIX in code: repository settings (ruleset, SHA pinning enforcement) are an owner action; waits on the owner.
