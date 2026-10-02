# Round 2026-10-03 / 30: workflows, docs, own test suite

Tree: e53587c (master). Read-only audit. Findings from audits/implemented/2026-09-24 (workflow-level `permissions`,
`py-ci-shared-ref` inputs, CANARY-53 RELEASE_TOKEN) are not repeated.

Tool runs:
- `uvx --from actionlint-py actionlint`: exit 0, no findings.
- `uvx zizmor .github` (default persona): 6 low, all `self-repository` on self-ci.yml:151/159/165/170/180/191
  (`./.github/...` vs `$/...`). Style only, the same-repo ref is not a risk: **false positive for security**, cosmetic.
- `uvx zizmor --persona=auditor .github` (online): 34 findings. Triage:
  - `anonymous-definition` x15 (info): unnamed jobs. Cosmetic, false positive for security.
  - `secrets-outside-env` ci-health.yml:39, consumer-pins.yml:42, corpus-drift.yml:61 (CONSUMER_READ_TOKEN, read-only
    token, scheduled triggers only): low real risk, accept. release.yml:78/89 (RELEASE_TOKEN): **true positive**, see WF-9.
  - `ref-version-mismatch` release.yml:72: **false positive**. The SHA is v7.0.1 (verified below); zizmor misparses the
    comment `# v7.0.1  # zizmor: ignore[artipacked]`.
  - `undocumented-permissions` docs.yml:66, release.yml:69: cosmetic.
  - `concurrency-limits` config-drift-check.yml:9: true but harmless (weekly, read-only). See WF-14.
- Action pins: I checked each `# vX.Y.Z` comment with `gh api repos/<o>/<r>/git/ref/tags/<tag>`, peeling the
  annotated codecov tag. All 8 match and each one is the latest release: checkout v7.0.1 3d3c42e, setup-python v7.0.0
  5fda3b9, setup-uv v10.2.0 c18668a, upload-artifact v7.0.1 043fb46, download-artifact v8.0.1 3e5f45b,
  upload-pages-artifact v5.0.0 fc324d3, deploy-pages v5.0.1 368f825, codecov-action v7.1.1 (tag object 2320916, peeled
  303a32d). No finding.
- Own suite, local Windows / Python 3.14, `-n 4 -p no:randomly --durations=200`: 4 failed, 5060 passed, 2583 s wall
  time. 3 of the 4 failures are crashed xdist workers. See WF-12 and WF-13.

---

### WF-1 (High) -- lint-advisory's pip-audit audits pip-audit's own venv, never the consumer project

**Evidence:** .github/workflows/lint-advisory.yml:218 runs `uvx "pip-audit==${PIP_AUDIT_VERSION}" --desc -f json -o pip-audit-report.json`.
It passes no `-r`, no project path and no `--local`. Under `uvx`, pip-audit runs in its own isolated tool venv and
audits that environment. Reproduced in a scratch directory whose pyproject.toml declares `requests==2.0.0`: the report
lists exactly 28 dependencies, which are pip-audit's own (`cachecontrol`, `cyclonedx-python-lib`, `pip-api`, `rich`, ...,
with a modern `requests`). The output was "No known vulnerabilities found".
**Impact:** every consumer of lint-advisory.yml believes it runs a CVE scan of its dependencies, and the uploaded
pip-audit-report.json looks real. The scan never sees the project. README.md:11 advertises pip-audit as part of the
bundle. tests/test_reusable_workflows.py:74 checks only that the tool is pinned, not what it audits.
**Proposed fix:** audit the project: `uvx pip-audit==X .` (pip-audit resolves a pyproject project), or install the
project and run `python -m pip_audit` in that environment. Add a workflow test that requires a project argument or an
install step.

### WF-2 (Med) -- release.yml's publish job is not re-runnable; a failed v1 move leaves "Latest" published and v1 stale

**Evidence:** release.yml:80-84 runs `gh release create` **before** release.yml:86-97 moves v1. `gh release create`
fails when the release already exists, so re-running the failed job can never get past step 1 to the tag move. This
has already happened: the v1.17.0 run (36104391633) and the v1.18.0 run (36235990551) both concluded `failure` at
"Move the major tag" (annotation: "RELEASE_TOKEN is not set ..."), and the GitHub releases v1.17.0 and v1.18.0 exist.
v1 was then moved by hand, against CLAUDE.md "Nobody moves `v1` by hand".
**Impact:** the release page says "Latest" while @v1 consumers still run the previous release. Recovery always takes a
manual force-push of the tag, the step the workflow exists to replace.
**Proposed fix:** move v1 first, then create the release. Alternatively make the release step idempotent
(`gh release view "$TAG" || gh release create ...`), so "Re-run failed jobs" finishes the job.

### WF-3 (Med) -- Version not bumped after v1.19.0 shipped; registry `since` and CHANGELOG say 1.20.0, and no test catches it

**Evidence:** v1.19.0 is tagged at fe366ce (`git ls-remote`: refs/tags/v1.19.0^{} fe366ce), and HEAD e53587c is 3
commits after it. pyproject.toml:7 and `__init__.__version__` still say `1.19.0`. registry.toml gives `ci_health` and
`ci_install_covers_conftest` `since = "1.20.0"`, and CHANGELOG.md:5 has a `## 1.20.0` section. CLAUDE.md says the
version is "the NEXT release tag" and that test_release_version "holds them ... ahead of the newest tag". The test
actually asserts `>=` (tests/test_release_version.py:47-52, `_key(VERSION) >= _key(newest)`), and
`test_a_tagged_version_points_at_an_ancestor_of_head` returns early only when the tag is NOT an ancestor. So
"version == a shipped tag, HEAD past it" passes.
**Impact:** every commit since 2026-10-02 identifies itself as 1.19.0. A `py-ci-shared @ git+...@<sha>` install
reports a version that is already a different, released tree. The CLAUDE.md rule "`since` = the version in
pyproject.toml" is broken by the two new entries, and nothing enforces it.
**Proposed fix:** bump to 1.20.0. Make the test strict: if `v{VERSION}` is a tag and HEAD != that tag's commit, fail
("bump to the next version"). Add a registry test that every `since <= VERSION`. (All other `since` values were
checked: each equals the first tag containing the module.)

### WF-4 (Med) -- Reusable workflows silently fall back to master when the pinned ref cannot be fetched

**Evidence:** ruff-blocking.yml:339-342, lint-advisory.yml:186-189 and black-filtered.yml:62-64 fetch `PCS_REF`. On
**any** failure (a network blip, GitHub 5xx, a typo'd ref) they print `::warning::` and fetch or install **master**.
black-filtered installs and executes master's `black_filtered_apply` code.
**Impact:** a SHA-pinned consumer can run unreviewed master config and code, and the job stays green with only a
warning. The verdict of a "pinned" gate then depends on network luck. The comment at ruff-blocking.yml:328 says "never
at master".
**Proposed fix:** retry the pinned fetch, then fail. Do not fall back to master. If a fallback is wanted for a ref
that was deleted, gate it behind an explicit input.

### WF-5 (Med) -- self-ci never exercises the consumer fetch path of the reusable workflows

**Evidence:** every "Resolve PY_CI_SHARED_DIR" / "Install py-ci-shared" step short-circuits on
`GITHUB_REPOSITORY = fingoldo/py-ci-shared` (ruff-blocking.yml:333, lint-advisory.yml:180, black-filtered.yml:60).
self-ci's integration jobs (self-ci.yml:150-195) run inside this repository, so they always take the local branch.
The `git fetch "${PCS_REF}"` path, the master fallback and the `pip install git+...@${PCS_REF}` path are never run by
any CI.
**Impact:** the branch every one of the 13 consumers runs is undogfooded. This is the gap the self-ci comment at
self-ci.yml:140-149 says integration jobs exist to close.
**Proposed fix:** add an integration input (for example `force-remote-fetch: true`) or a separate job that sets the
fetch path explicitly, with `py-ci-shared-ref: ${{ github.sha }}`.

### WF-6 (Med) -- release.yml does not require the tagged commit's CI to be green, and runs 3 test files, not "the test suite"

**Evidence:** release.yml:59-60 runs only test_release_version.py, test_package_inventory.py and
test_reusable_workflows.py on one OS and one Python. Nothing checks self-ci's conclusion for the tagged SHA.
README.md:443 says release.yml "runs the test suite", and CLAUDE.md says "runs the tests".
**Impact:** a tag pushed on a master commit whose self-ci is red (or still running) moves v1 for all 13 consumers at
once. README.md:48-49 sells @v1 as "propagates to every consumer at once". The docs overstate the verification.
**Proposed fix:** in `verify`, query `gh api repos/$REPO/commits/$SHA/check-runs` (or the workflow runs for
self-ci.yml at that SHA) and require success. Correct the README and CLAUDE.md wording.

### WF-7 (Med) -- No documented rollback for a bad release on the moving v1 tag

**Evidence:** README.md:48-63 and :431-445 and CLAUDE.md "Versions and releases" describe only moving forward. A grep
for rollback/revert/bad release across README, CHANGELOG and CLAUDE.md finds nothing about releases. CLAUDE.md forbids
moving v1 by hand. release.yml can only move v1 to a newly pushed, version-matching tag, and test_release_version
blocks re-tagging an older version (`>= newest`).
**Impact:** after a bad release, the only sanctioned path is a new commit, a version bump and a new tag. Meanwhile every
@v1 consumer stays broken. Each consumer's local `git fetch` of a moved tag also needs `--force`. A SHA-pinned consumer
is unaffected.
**Proposed fix:** document an emergency procedure (a `workflow_dispatch` input on release.yml that moves v1 to a given
earlier vX.Y.Z, gated by an environment), and say when to use it.

### WF-8 (Med) -- README states the wrong config-fetch mechanism (`github.job_workflow_sha`)

**Evidence:** README.md:432-435 ("the workflow now fetches this repo's configs and RUFF_VERSION at the commit the
workflow itself was loaded from (`github.job_workflow_sha`) ... so a pin pins everything") and README.md:1083 say the
same. No workflow references `job_workflow_sha` (grep of .github is empty). The real mechanism is the
`py-ci-shared-ref` input, which defaults to the literal `"v1.19.0"` (ruff-blocking.yml:23, lint-advisory.yml:19,
black-filtered.yml:16).
**Impact:** a consumer pinned to a SHA after v1.19.0 (e53587c, say) gets v1.19.0's configs, not its pinned commit's,
so "a pin pins everything" is false. With WF-4, a failed fetch gives master.
**Proposed fix:** rewrite both paragraphs to describe the input, its default and the fallback.

### WF-9 (Low) -- RELEASE_TOKEN is not behind a protected environment

**Evidence:** zizmor `secrets-outside-env` release.yml:78/89. The workflow runs the release.yml **of the tagged
commit**, and the "on master" check (release.yml:52-56) is itself part of that file. Anyone who can push a tag on any
branch commit can edit release.yml in that commit and read RELEASE_TOKEN (contents + workflows write). Fork PRs cannot
reach it (no `pull_request_target`, no `workflow_run`, and the tag push needs write access). The publish job checks out
with persist-credentials but runs only `gh` and `git` afterwards: no third-party code.
**Impact:** low for a single-owner repo, but it turns any write-scoped credential into a workflows-scoped one.
**Proposed fix:** put `publish` in an environment `release` with a deployment rule restricted to `v*` tags and the
secret stored there, plus a tag-protection ruleset.

### WF-10 (Low) -- README stale or wrong facts

**Evidence:**
- README.md:11 says the advisory bundle is "codespell/yamllint/bandit/actionlint/vulture/pip-audit". lint-advisory.yml
  runs ruff, mccabe, pip-audit, import-linter, pydoclint and semgrep. Codespell, yamllint, bandit, actionlint and
  vulture are in lint-blocking.yml.
- README.md:12 and :437 say install `...@v1.17.0`; the latest release is v1.19.0.
- README.md:16 lists `release.yml` among things "Deliberately NOT here", yet .github/workflows/release.yml exists. It is
  this repo's own release workflow, not a shared one, and the sentence does not say so.
- README.md:443 "runs the test suite" (see WF-6).
**Proposed fix:** correct these. Derive the install tag in the README from `__version__` in test_release_version, as is
already done for the workflow defaults.

### WF-11 (Low) -- EXEMPT entries whose subject can be seeded as files

**Evidence:** tests/test_gate_teeth.py:270-328 exempts these from canaries as "CI configuration" or "repo layout":
ci_test_dir_reachability, ci_workflow_paths, coverage_config_parity, pytest_addopts_path_runs, gate_config_honesty,
gate_integrity, hook_hygiene, marker_runner_coverage, test_partition_reachability, doc_identifier_parity,
phantom_code_references, effect_assertion_parity, sql_verifier_coverage, audit_path_references,
audit_disposition_parity, disposition_test_references. timezone_honest (ruff over files) and import_layering (files plus
caller rules) are also exempt. The canary harness copies any `*.canary` file to its stripped path (`_copy`, :341-347), and
tests/canary/ci_install_covers_conftest/ already seeds workflow YAML, pyproject and conftest files. A workflow, a tests/
tree or a README is a file corpus.
**Impact:** about 18 corpus-reading gates have no violation/clean/bom/unparsable teeth check, so a gate that silently
stops matching stays green.
**Proposed fix:** add canaries for these. Keep EXEMPT for subjects that really are not files (live DB, git history,
runtime plugins), and reword reasons as "needs X" rather than "subject is repo layout".

### WF-12 (Med) -- Timeout-bound warm-worker tests fail and crash xdist workers under load

**Evidence:** a local run with `-n 4` gives:
- FAILED test_mutation_worker.py::TestTheProtocolChannelIsPrivate::test_a_failing_run_still_reports_its_own_code
  (`assert None == 1`). The test took 133.3 s against `_WarmRunner(timeout=120)`, so `run()` returned None on timeout.
- Workers gw2, gw3 and gw5 crashed ("node down: Not properly terminated") in
  test_mutation_teeth.py::TestConcurrencyChangesSpeedAndNothingElse::{test_four_workers_reach_the_same_verdict_as_one,
  test_survivors_come_back_in_source_order} and test_scaffold.py::test_the_scaffold_passes_the_inventory_and_fails_until_filled_in.
CI (serial, 4 cores) is green, so this depends on load.
**Impact:** the suite is not safe to run in parallel, and the warm-runner tests turn a slow machine into a wrong
verdict (None) rather than a skip or a clear timeout error. The crashing tests spawn their own worker pools
(`four_workers`), which oversubscribes the machine under xdist.
**Proposed fix:** mark the pool and warm-runner tests `xdist_group`/serial. Make `_WarmRunner.run` returning None fail
with "timed out after Ns". Give a crashed worker a diagnosable cause (faulthandler dump to file).

### WF-13 (Low) -- Slow tests: top offenders

**Evidence (summed per file, `--durations=200`, local, -n 4):** test_pytest_plugin.py 1012 s; test_mutation_worker.py
540 s; test_checkout_resolution.py 391 s; test_mutation_teeth.py 364 s; test_stale_comment_age.py 275 s;
test_worktree_hygiene.py 255 s; test_adoption_matrix.py 237 s; test_mutation_teeth_regressions.py 237 s;
test_embedded_postgres.py 166 s; test_code_audit_meta.py 131 s.
Single tests: test_pytest_plugin::test_refresh_writes_a_missing_baseline_and_reaches_every_test_through_the_env 252 s;
test_mutation_teeth::...a_slots_mutation_is_killed... 244 s;
test_mutation_teeth_regressions::...a_shadowing_copy_is_detected 216 s;
test_pytest_plugin::test_the_table_loads_the_resource_leak_guard_only_when_asked 173 s;
test_checkout_resolution::test_a_probe_that_fails_is_reported_with_its_output 168 s.
CI is much faster: 99-258 s per leg on run 37055374098, with Windows the slowest at 258 s. So the local times are
mostly Windows process-spawn cost multiplied by the load.
**Proposed fix:** share one pytester/subprocess fixture per module where tests only read the result. Gate the
end-to-end mutation tests behind a marker that runs on one CI leg.

### WF-14 (Low) -- Workflow hygiene leftovers

**Evidence:**
- config-drift-check.yml has no `concurrency` (zizmor).
- self-ci.yml:47/49 use the moving `windows-latest`/`macos-latest` labels, while every ubuntu leg is deliberately pinned.
- ci-health.yml:35, consumer-pins.yml:92, corpus-drift.yml:181 and release.yml:41 install unpinned dependencies (no
  lock), so the release verification and the nightly reports are not reproducible.
- corpus-drift.yml:198 takes "the last successful run" across all refs. A `workflow_dispatch` with `accept: true` run
  from a non-master branch becomes master's baseline. When that run's artifact expires (90 days,
  corpus-drift.yml:241), `download-artifact` fails every night.
- black-filtered.yml:78 hard-codes `black==26.5.1` instead of reading `BLACK_VERSION` from tool_versions.py the way the
  RUFF_VERSION steps do.
**Proposed fix:** add concurrency. Pin the labels or say why not. Install with a constraints/lock file. Filter the
baseline query with `&branch=master` and handle an expired artifact. Read BLACK_VERSION like RUFF_VERSION.

### WF-15 (Low) -- Own-suite tests whose assertion loops can run zero times

**Evidence:** running `find_floorless_loops` over tests/ (the repo's own vacuous-loop scanner, not dogfooded on its own
tests) reports 3:
- tests/test_ci_install_covers_conftest.py:248-251. The inner `if var in inputs` check never fires if the env names stop
  matching, and the test still passes.
- tests/test_reusable_workflows.py:67-71. If the URL string `fingoldo/py-ci-shared.git` changes, every step is skipped
  and the test passes. The test name ("never fetched at master by default") is also stronger than the body: the master
  fallback in the same step satisfies `"PCS_REF" in step_text`.
- tests/test_package_inventory.py:79. False positive in practice: registry.GATES holds 147 entries.
`find_conceded_defect_pins` reports 3 (test_conceded_defect_pins.py:30, test_content_hash_version_bump_gate.py:105,
test_vacuous_loop_assertions.py:108). All 3 are fixture or description text quoting the trigger words: false positives.
`find_xfail_to_defer`: 0.
**Proposed fix:** add a `matched` counter with `assert matched` to the first two. Dogfood vacuous_loop_assertions on
tests/ in `[tool.py_ci_shared]`.

### WF-16 (Low) -- The consumer doctest symptom is not a stdout leak; nothing in py-ci-shared swaps sys.stdout

**Evidence:** the only stream swap in src/ is `contextlib.redirect_stdout/stderr` plus `os.dup2` in
_mutation_worker.py:230-237 and :279. Those run in a separate worker subprocess (`main()`), and no test calls
`_claim_protocol_channel` in-process. pytest_plugin.py and resource_leak_guard.py do not touch the streams,
capture or displayhook (grep for capman, displayhook, sys.stdout assignment and settrace is empty).
`doctest.DocTestRunner.run` installs its own `_SpoofOut` and `sys.__displayhook__` for each run. Probe
(scratchpad/dt_probe.py): with `sys.stdout` replaced by a StringIO and `sys.displayhook` set to a no-op **before**
`doctest.testmod`, the example `>>> flag()` / `True` still passes. Clearing the module state so `flag()` returns
`None` reproduces "Expected: True / Got nothing" exactly.
**Impact:** the leak hunt is pointed at the wrong layer. "Got nothing" means the expression evaluated to None: the
consumer's earlier tests polluted state that the doctested function reads (a cache, registry or global), or
monkeypatched it without restoring.
**Proposed fix:** in the consumer, find which global the failing doctest's function reads and which earlier test
mutates it (bisect with `-p no:randomly` and `--lf` ordering). Optionally have package_doctests restore a module
snapshot, or run each module's doctests in a fresh subprocess.

---

Counts: High 1, Med 8 (WF-2 to WF-8, WF-12), Low 7 (WF-9 to WF-11, WF-13 to WF-16). Total 16.
