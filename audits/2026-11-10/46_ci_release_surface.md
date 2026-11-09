# 46_ci_release_surface

Round 2026-11-10, dimension 46 (CI and release surface). Read-only audit. Prior round 2026-11-07 dimension 46 (46-F1..46-F14) and TRACKER.md rows 46-F1..46-F14 were read first and are not re-reported. Every finding below is OPEN until its Disposition line says otherwise.

Scope read: `.github/workflows/*.yml` (all 15 files, triggers, permissions, secrets, pins), `.github/scripts/release_guard.py` (397 lines, full), `.github/actions/{upload-codecov,install-pyutilz}/action.yml`, `.github/dependabot.yml`, `.github/constraints/runtime.txt`, `tests/test_tool_pin_docs.py`, `src/py_ci_shared/corpus_drift.py` (import path), `src/py_ci_shared/_consumers.py` (clone path).

## Checked and clean (no finding)

- Every external `uses:` in `.github/workflows` and the composite actions is pinned to a 40-hex SHA (grep for `uses:` without a 40-hex ref returns only same-repo `./.github/workflows/...` refs).
- Top-level `permissions:` is `contents: read` in every workflow; write scopes are job-level only, in `release.yml:123-124, 178-179` (publish, rollback), `docs.yml:60-62` (Pages deploy, `id-token: write`), and `corpus-drift.yml:42` (`actions: read`).
- `secrets.` is referenced only as `CONSUMER_READ_TOKEN` (ci-health, config-drift-check, consumer-pins, corpus-drift; each in one step's `env:`) and `RELEASE_TOKEN` (release.yml:145, 195, each in one step's `env:`). No `pull_request_target`, no `secrets: inherit`, no `${{ }}` expression inside any `run:` block.
- `inputs.rollback-to` reaches the shell only through `env:` (release.yml:189, 194).
- `py-ci-shared-ref` is regex-validated before use (black-filtered.yml:87).
- corpus-drift does not execute consumer code: `corpus_drift.finders()` imports only modules registered in py-ci-shared's own `registry.toml` (corpus_drift.py:175-181). The consumer clones carry no persisted credential (`_consumers.py:98-110` passes the token through `GIT_CONFIG_*` env for the clone only).
- Release publish path: RELEASE_TOKEN is confined to the one push step, checkout uses `persist-credentials: false` in every job (release.yml:50, 131, 184).

## 46-F15 (P2) ci-green and the release wait loop die on one transient GitHub API error instead of reporting pending

- Evidence: `.github/scripts/release_guard.py:283` (`wait_for_ci`): the poll `api(f"repos/{repo}/actions/workflows/{workflow}/runs?...")` has no try/except. `_github()` (release_guard.py:256-265) calls `urllib.request ... .open()`, which raises `urllib.error.HTTPError` (an `OSError`) on any 4xx/5xx. `main()` line 382 calls `wait_for_ci` with no guard either, so the only handler on this path is none. Only `_warn_failed_legs` (line 331) catches `OSError`, and it runs after a green verdict.
  Demonstrated offline (scratchpad `demo46/demo.py`, run with `python -I`): with the first poll returning `in_progress` and the second poll raising `HTTPError(503)`, `wait_for_ci(...)` raises `HTTPError` out of the function. The CLI therefore exits with a Python traceback (rc 1, no `::error::` line).
- Failure scenario: release.yml verify (`ci-green --wait 6000`, release.yml:100) polls for up to 100 minutes, which is the observed queue-plus-run window (46-F5). One 502/503 from api.github.com in that window aborts verify with a traceback. The release is then reported as a verification failure of an otherwise green commit. The documented recovery in release.yml:7 ("Re-run failed jobs") works, but the maintainer sees a crash and may conclude self-ci is broken. A 503 on the first poll has the same effect. Frequency is low; the cost is a false red on the release path and no message saying why.
- Proposed disposition: wrap each `api(...)` call in `wait_for_ci` in a bounded retry (for example 3 attempts with the existing `sleep`, retrying on `HTTPError` with status >= 500 and on `URLError`); after the retries are exhausted, return `("pending", "GitHub API unreachable: re-run this job")` instead of raising, so the verdict stays one of green/pending/red. Add a test in `tests/test_release_version.py` next to `test_wait_for_ci_polls_until_the_run_finishes` where the fake `api` raises `HTTPError(503)` once and then succeeds (must pass after the fix, must fail before it: the test raises today).
- AST/offline checkable: yes (a unit test with a fake `api` that raises).
- False-positive risk: low.
- Disposition: OPEN.

## 46-F16 (P2) rollback fails open: when the releases API is unreadable, the "must be a published release" check is skipped silently

- Evidence: `.github/scripts/release_guard.py:314-322` (`_release_names`): `except OSError: return None`. `HTTPError` (401, 403, 429 rate limit, 5xx) is an `OSError`, so every API failure becomes `None`. `rollback_problem` (release_guard.py:81) then reads `if published is not None and ...` and skips the check. `main()` prints `"<tag>: ok to move v1 to it"` (line 392) with no mention that the check was skipped.
  Demonstrated offline (`demo46/demo.py`): with the API faked to raise `HTTPError(503)`, `_release_names(...)` returns `None` and `rollback_problem("v1.23.8", ["v1.23.8","v1.23.9"], None)` returns `None` (ok). The published-release requirement added by 46-F12 is therefore bypassed on exactly the kind of day an emergency rollback happens.
- Failure scenario: an incident is in progress; api.github.com returns 403 (rate limit on the shared runner IP) or 503 for the releases endpoint. The emergency `workflow_dispatch` rollback to a tag whose Release run failed (for example v1.23.2, listed as failure in 46-F12) passes the guard, and every `@v1` consumer moves to code that never passed Release verify. The log shows only a successful "ok to move" line.
- Proposed disposition: in `rollback` mode with `--repo` given, treat an unreadable releases API as a refusal with a reason (`::error::cannot read the GitHub releases of R (HTTP 503), so the target cannot be shown to be published; retry the dispatch`), exit 1. If the owner decides an outage must not block a rollback, keep the skip but print `::warning::published-release check skipped: <reason>` so it is visible in the run. Either way, test the 403 case: today the test (`test_rollback_requires_a_published_release_when_the_releases_are_known`, test_release_version.py:366) covers only the known-releases branch. Owner choice between refuse and warn is needed; recommendation: refuse, since a rollback can be re-dispatched in seconds.
- AST/offline checkable: yes (fake `_github` raising `HTTPError`).
- False-positive risk: low.
- Disposition: OPEN (owner choice: refuse or warn; recommendation refuse).

## 46-F17 (P3) when the releases API is unreadable, prepare and the verify job fall back to local tags and blame the wrong version

- Evidence: `.github/scripts/release_guard.py:213-219` (`_published_release`): `except OSError: names = []`, then `return highest(names) or highest(tags) or "v0.0.0"`. In release.yml verify the step is `prepare "${TAG#v}" --check --repo ...` (release.yml:94), with `_git_tags` (line 237) returning the local tags, which include the tag being verified.
  Demonstrated offline (`demo46/demo.py`): with the API unreadable and local tags `["v1.23.8","v1.23.9"]`, `_published_release("o/r", "", [...])` returns `v1.23.9`, the tag under verification. `release_edits` (release_guard.py:200-201) then raises `v1.23.9 is not ahead of the newest published release v1.23.9`, which `_main_prepare` prints as `::error::`, exit 2.
- Failure scenario: the release verify fails on a transient API error with a message that says the version is wrong. The maintainer bumps the version (which is already right) or re-tags, which is the wrong fix. The failure is fail-closed (no release is published), so the impact is confusion and a wasted retag attempt, not a bad release.
- Proposed disposition: make the fallback explicit. When the API raises, `_published_release` should raise a `RuntimeError("cannot read the GitHub releases; pass --published vX.Y.Z")` and `_main_prepare` should report that reason, or the verify step should pass `--published` from the newest tag that is not the tag under test. Test: fake `_github` raising `OSError` in `_published_release` must not return the tag being prepared.
- AST/offline checkable: yes.
- False-positive risk: low.
- Disposition: OPEN.

## 46-F18 (P3) pinned tool versions have no automated update channel: dependabot covers only github-actions

- Evidence: `.github/dependabot.yml` is 16 lines and has one `package-ecosystem: github-actions` entry (line 5). Tool versions live in plain env or shell pins: `uvx "ruff==${RUFF_VERSION}"` (ruff-blocking.yml:126; lint-advisory.yml:166, 172), `uvx "zizmor==$PIN"`, `actionlint-py`, `codespell`, `bandit`, `vulture`, `yamllint` (lint-blocking.yml:189-245), `semgrep`, `pip-audit`, `import-linter`, `pydoclint` (lint-advisory.yml:200-260), `deptry` (lint-blocking.yml:287), and `mypy==2.1.0` (self-ci.yml:156). `.github/constraints/runtime.txt` pins the runtime dependencies that every consumer's reusable run installs under `-c` (black-filtered.yml, gates.yml, self-ci via release.yml:65). None of these is an input dependabot reads.
- Failure scenario: a CVE or a breaking fix lands in ruff, semgrep, pip-audit or a runtime dependency. Nothing opens a PR; the pin stays until a human notices. Since every consumer runs these pins through the reusable workflows, the lag is shared by all 13 consumers. The 51_security_supply_chain dimension may already cover the CVE angle; this finding is the absence of any update path at all, which is the pinning question of dimension 46.
- Proposed disposition: decide per tool. Either (a) add a dependabot `pip` ecosystem entry for `/` with a requirements file that lists the uvx tools pinned to the same versions (dependabot reads `requirements*.txt`; `runtime.txt` may need a rename, which is an owner-visible change), or (b) add a scheduled workflow step that runs `uv pip compile` or `pip index versions` and opens an issue, or (c) record the manual cadence in CLAUDE.md. Recommendation: (b) for the lint tools because dependabot cannot read env-var pins; (a) for runtime.txt. Owner decision on cadence.
- AST/offline checkable: partly (a test that every `==` pin in the workflows is also listed in an update-tracked file).
- False-positive risk: low.
- Disposition: OPEN (owner decision on mechanism).

## 46-F19 (P3) the mypy pin is written three times and only one copy is tested against tool_versions

- Evidence: `self-ci.yml:156` `uv pip install --system "mypy==2.1.0"`; `mypy-full.yml:32` `default: "2.1.0"`; `mypy-beachhead.yml:26` `default: "2.1.0"`. `tests/test_tool_pin_docs.py:23-25` (`test_self_ci_installs_the_pinned_mypy`) checks only the self-ci.yml literal against `tool_versions.MYPY_VERSION` (`tool_versions.py:37`). Nothing checks the two reusable-workflow defaults, which are what every consumer that does not set `mypy-version` gets.
- Failure scenario: the next mypy bump changes `MYPY_VERSION` and self-ci.yml (test passes) but leaves the two reusable defaults at 2.1.0. Consumers that rely on the default keep the old mypy while py-ci-shared's own check moves; the two checks can disagree with no red CI anywhere. Conversely, a bump in the defaults alone leaves self-ci on the old version with no test failure.
- Proposed disposition: extend `test_tool_pin_docs.py` so that the `default:` of `mypy-version` in mypy-full.yml and mypy-beachhead.yml must equal `MYPY_VERSION`. One assertion per file, same shape as the existing self-ci test. Test must fail before the fix only if the defaults are changed to a different value (it is green today, so it is a guard, not a fix; state it as such).
- AST/offline checkable: yes.
- False-positive risk: low.
- Disposition: OPEN.

## 46-F20 (P3) self-ci's black smoke run is unpinned, while black-filtered.yml pins the same invocation

- Evidence: `self-ci.yml:108` `BLACK_CMD: "uvx black"` (unpinned), used by the "Smoke-import and run black_filtered_apply" step (self-ci.yml:106-111) that runs `python -m py_ci_shared.black_filtered_apply --config pyproject.toml src/py_ci_shared/__init__.py`. black-filtered.yml:93-104 documents why it must not be unpinned: "unpinned resolves whatever's newest", and sets `BLACK_CMD="uvx black==${v}"`. `tool_versions.py:33` has the pinned black.
- Failure scenario: a new black release that crashes or changes the `black -q --config` invocation makes self-ci red on master (a red commit on the release path, which 46-F2 and 46-F3 show is already a source of confusion). The release verify then fails on the tagged commit, though nothing in py-ci-shared changed. Likelihood low: the step runs black on one file and checks only exit status.
- Proposed disposition: `BLACK_CMD: "uvx black==${BLACK_VERSION}"` with the version read from `tool_versions.py` in the same way the ruff step reads RUFF_VERSION (self-ci.yml:140-146), and extend `test_tool_pin_docs.py` to assert it. Matches the rule that every release-path tool is pinned.
- AST/offline checkable: yes (grep for `uvx black` without `==`).
- False-positive risk: low.
- Disposition: OPEN.

## 46-F21 (P3) the Codecov composite action has no caller in this repository, and its CODECOV_TOKEN secret is referenced by no workflow

- Evidence: `.github/actions/upload-codecov/action.yml` (the only use of `codecov/codecov-action`, line 37-45) is not referenced by any file under `.github/workflows`; `grep -rn "upload-codecov" .github/workflows` is empty and self-ci.yml has no coverage upload step (its comment at line 136-ish says coverage is measured on one leg, with no upload). `grep -rn CODECOV .github` finds only the action's own description (action.yml:23). `gh secret list` (per the 2026-11-07 audit, 46-F8 evidence) shows CODECOV_TOKEN set in the repository, so the repository holds a secret that nothing reads.
- Failure scenario: a consumer that pins `fingoldo/py-ci-shared/.github/actions/upload-codecov@v1` gets a composite action that this repository never exercises, so a regression in it (the `files`/`file` input rename the description warns about) reaches consumers with no test in this repo's CI. The stale secret is also a standing exposure with no consumer in this repo.
- Proposed disposition: either exercise the action (a self-ci step with `if: matrix.os == 'ubuntu-24.04' && matrix.python == '3.11'` that uploads the real coverage XML, so the release path covers it) or classify it as consumer-only and state that in the action's description. Delete the CODECOV_TOKEN repository secret only if the owner confirms no consumer reads it from this repository (secrets are not inherited across repos, so the answer is expected to be "none"). Per the owner rule on uncalled code, the choice is wire-in or classify, not delete.
- AST/offline checkable: yes (a grep that every `.github/actions/*` is referenced by a workflow or a test).
- False-positive risk: low.
- Disposition: OPEN.

## Summary

| ID | Sev | Title | Status |
|---|---|---|---|
| 46-F15 | P2 | ci-green and the release wait loop raise a traceback on one transient API error instead of pending | OPEN |
| 46-F16 | P2 | rollback fails open: an unreadable releases API skips the published-release check silently | OPEN (owner: refuse or warn) |
| 46-F17 | P3 | prepare falls back to local tags when the API is unreadable and reports the wrong version | OPEN |
| 46-F18 | P3 | pinned tool versions have no automated update channel (dependabot is github-actions only) | OPEN (owner: mechanism) |
| 46-F19 | P3 | mypy pin written three times; the two reusable defaults are untested against tool_versions | OPEN |
| 46-F20 | P3 | self-ci black smoke run is unpinned (uvx black) while black-filtered.yml pins it | OPEN |
| 46-F21 | P3 | Codecov composite action and the CODECOV_TOKEN secret have no caller in this repository | OPEN (wire in or classify) |

Counts: 7 new findings. P0: 0. P1: 0. P2: 2 (46-F15, 46-F16). P3: 5 (46-F17 to 46-F21).

Nothing in the workflows as they stand on master lets a consumer's CI break silently through an unannounced change here: the reusable-workflow defaults move only through `prepare` (guarded by `prepare --check` in release verify), and the consumer-facing refs are SHA or tag pinned. The new findings are on the release path's failure reporting (46-F15, 46-F16, 46-F17) and on the update paths for pins (46-F18, 46-F19, 46-F20), not on a consumer-breaking vector.

Verification note: the offline demonstration script is at `C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\demo46\demo.py`. The GitHub API itself was not called; the failure modes were produced by faking `_github` to raise `HTTPError`, so the demonstrations show the code's behaviour under a given API outcome, not GitHub's actual error rate.
