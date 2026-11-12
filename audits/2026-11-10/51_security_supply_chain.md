# 51 security_supply_chain (round 2026-11-10)

Read-only audit. Scope: dependency pins and hashes, workflow permissions and SHA pins, secret handling, subprocess argument
handling, config-driven paths, release token and environment, writes outside the repo. Already covered and not re-reported:
the 2026-11-07 round (51-F1..F9, all rows in audits/TRACKER.md). No secret values were printed.

## Summary

| ID | Severity | Status | One line |
|----|----------|--------|----------|
| 51-R1 | P3 | RESOLVED | sibling_floor_skew._github_version sends a token through urllib's default redirect handler (the 51-F1 class, one call site not fixed) |
| 51-R2 | P2 | ACCEPTED RISK, overlaps accepted 51-F8 | the release gates live in the tagged commit's own release.yml, so a v* tag from any branch reaches RELEASE_TOKEN |
| 51-R3 | P3 | RESOLVED | self-ci.yml runs unpinned `uvx black`; the 51-F2 meta-test only scans lint-blocking.yml |
| 51-R4 | P3 | RESOLVED | release.yml puts the base64 release token in git's argv in two places, against the module's own design |
| 51-R5 | P3 | RESOLVED | dev extras are floor-only and unconstrained on the release verify path and in self-ci |

Counts: 5 findings (P2: 1, P3: 4). Resolved in this round: 0. Verified-clean items recorded below: 7.

---

## 51-R1 [P3] sibling_floor_skew._github_version sends a token through urllib's default redirect handler

- Evidence: src/py_ci_shared/sibling_floor_skew.py:262-270. Line 265 reads `GITHUB_TOKEN` or `CONSUMER_READ_TOKEN`; line 267
  adds `Authorization: token <value>`; line 269 calls `urllib.request.urlopen(request, timeout=20)` with no opener. The
  51-F1 fix (audits/2026-11-07 row, tracker) moved `SameHostRedirects` into src/py_ci_shared/_http_safety.py and wired it into
  config_drift_check and ci_health only. Grep of src for `urlopen` and `Authorization` shows this is the one remaining
  bearer-token request that follows redirects with CPython's default handler.
- Scenario: raw.githubusercontent.com answers with a redirect to a different host (a proxy or captive portal on the runner, or a
  future move to another CDN host). CPython copies the Authorization header to the new host. When only CONSUMER_READ_TOKEN is
  set, that token is read access to every private consumer repo. Realism is low: raw.githubusercontent.com does not normally
  redirect across hosts, and the call only runs when the sibling floor check has network enabled (sibling_floor_skew.py:290).
  It is the same class the owner already ruled P2 for the API host.
- Proposed disposition: RESOLVED-candidate, small. Open with `urllib.request.build_opener(SameHostRedirects)` (import from
  `_http_safety`). Test: a local server on 127.0.0.1 that redirects to a second local port, asserting the second port receives no
  Authorization header. Add one meta-test: every `urlopen(` or `build_opener` call in src/py_ci_shared that sets an Authorization
  header must go through an opener that has `SameHostRedirects`.
- AST-checkable: True; FP risk: low.

## 51-R2 [P2] The release gates come from the tagged commit, so a v* tag from any branch reaches RELEASE_TOKEN (overlaps accepted 51-F8)

- Evidence:
  - .github/workflows/release.yml:17 triggers on any tag matching `v[0-9]+.[0-9]+.[0-9]+`. GitHub reads the workflow file from the
    tagged commit, so the file that runs is whatever that commit contains.
  - release.yml:77 (`git merge-base --is-ancestor "$TAG" origin/master`) is the only "on master" check. It is a step in the same
    file, so a tagged commit that removes it passes.
  - release.yml:122 and :177 (`environment: release`). The tracker row for 51-F8 records that the deployment policy allows the
    master branch and v* tags. A tag on a feature-branch commit is therefore allowed to use the environment.
  - release.yml:145 and :195 (`RELEASE_TOKEN: ${{ secrets.RELEASE_TOKEN }}`), reached from the publish job (tag push) and the
    rollback job.
- Scenario: a collaborator with write access pushes a feature-branch commit whose release.yml adds a step that echoes or sends
  RELEASE_TOKEN, or that moves the major tag v1 to an arbitrary commit at release.yml:157/:203, then pushes tag v1.23.11 on that
  commit. The verify job passes because it is part of the same file, and the publish job receives the environment secret. A
  master-only commit is not needed. Consumers on @v1 would fetch the attacker's commit.
  This is the same accepted trust model as 51-F8 (repository write access is the trust boundary, and no required reviewer is
  configured). It is recorded here as new evidence: the master-ancestry and self-ci checks are not an independent control, so
  the CLAUDE.md wording that they protect the release is too strong.
- Proposed disposition: owner decision, not a code change in this round. Options, in order of cost:
  (a) a GitHub tag ruleset that limits creation of `v*` tags to repository admins (settings only; no code);
  (b) a required reviewer on the `release` environment (already declined 2026-11-07; the recommendation is to reopen it);
  (c) move the publish decision to a workflow_run job triggered from master, where the workflow file comes from the default branch.
  Recommendation: (a) now, and record in the 51-F8 row that the tag path is also covered by the accepted risk.
- AST-checkable: False; FP risk: low.

## 51-R3 [P3] self-ci.yml runs an unpinned `uvx black`; the 51-F2 meta-test does not scan it

- Evidence: .github/workflows/self-ci.yml:108 `BLACK_CMD: "uvx black"`, used by the smoke step at self-ci.yml:110-111 that runs
  `python -m py_ci_shared.black_filtered_apply`. Consumers pin the same tool at black-filtered.yml:93-104 with a comment naming
  the drift it prevents. tests/test_security_supply_chain_workflows.py:45-50 scans every `uvx` and `uv pip install` line, but
  only in lint-blocking.yml (`WF / "lint-blocking.yml"`), so self-ci.yml is outside it.
- Scenario: each self-ci run resolves the newest black from PyPI and runs it on the PR's tree. A fork PR gets a read token and no
  secrets (self-ci.yml triggers on pull_request), so the impact is a non-reproducible self-check and a tool release that executes
  at install time with contents:read. Realism is low, but the drift is the one the workflow comments already call a past incident.
- Proposed disposition: pin black through tool_versions.py (as the other tools are) and set `BLACK_CMD` to `uvx "black==<pin>"`.
  Widen the meta-test to every workflow in .github/workflows, so the next unpinned `uvx` fails the suite.
- AST-checkable: True; FP risk: low.

## 51-R4 [P3] release.yml puts the base64 release token in git's argv in two places

- Evidence: .github/workflows/release.yml:158-159 and :204-205:
  `auth=$(printf 'x-access-token:%s' "$RELEASE_TOKEN" | base64 -w0)` then
  `git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $auth" push -f origin "refs/tags/$major"`.
  The value is on the command line for the duration of the push. The module that does the same job for consumers says the
  opposite: src/py_ci_shared/_consumers.py:98-110 passes the header through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`
  in the environment, and its docstring states "The token never reaches argv." release.yml:139 describes the same push as a
  "one-shot header" and does not mention argv.
- Scenario: another process on the same runner VM reads /proc/<pid>/cmdline during the push and recovers the token (base64 is an
  encoding, not protection). On GitHub-hosted single-job VMs this needs a process the workflow does not control, so realism is
  low; the fix is cheap and makes the release path follow the module's own rule.
- Proposed disposition: move the header to the environment (`GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0`, `GIT_CONFIG_VALUE_0`) in
  both steps, keeping the token in the `env:` block of the step. Test: a grep-style test in tests/test_security_supply_chain_workflows.py
  that no `git -c` line in release.yml contains `$auth` or `$RELEASE_TOKEN`.
- AST-checkable: True; FP risk: low.

## 51-R5 [P3] Dev extras are floor-only and unconstrained on the release verify path and in self-ci

- Evidence: pyproject.toml:35-80 declares the `dev` extra with `>=` floors only (pytest, black, pytest-cov, numba, numpy and
  others; ruff is the single `==` pin at line 41). .github/workflows/release.yml:65 (verify job) runs
  `uv pip install --system -c .github/constraints/runtime.txt -e ".[dev]"`. The constraints file (.github/constraints/runtime.txt)
  pins runtime deps and pytest only, by its own header. self-ci.yml:97 installs `".[dev]"` with no constraint at all.
  release.yml:64 comment: "the [dev] test tools resolve as in self-ci".
- Scenario: a malicious or broken release of a dev dependency lands on the release verify job at the time of tagging. That job
  has contents:read and no environment; RELEASE_TOKEN appears only at release.yml:145 and :195, in the publish and rollback jobs.
  The impact is therefore a wrong test verdict and code running on the runner, not token access. The release is not
  reproducible from its tag, which is the property the constraints file exists to give.
- Proposed disposition: add a dev constraints block (or a second constraints file) with exact pins for every `dev` entry, used by
  release.yml:65 and self-ci.yml:97. Extend the existing test that holds runtime pins (tests/test_reusable_workflows.py) to
  cover it.
- AST-checkable: True; FP risk: low.

---

## Examined, no finding

1. Hash pins for fetched binaries: src/py_ci_shared/embedded_postgres.py:326-334 pins the pgserver wheel per platform and tzdata
   by SHA-256; `_verified_wheel` (line 359-366) checks the digest before any unpack (lines 396-398, 409-412).
2. Consumer clone token: src/py_ci_shared/_consumers.py:98-110 passes the token through `GIT_CONFIG_*` env, not argv, and
   `_git_clone` returns only git's stderr text (no argv echo). The checkout step does not persist credentials
   (corpus-drift.yml:51, persist-credentials false). Consumer names are checked against `_NAME` and the target is checked to sit
   directly under dest (_consumers.py:133-139).
3. GitHub API client in release_guard.py: `_SameHostRedirects` at .github/scripts/release_guard.py:244-253 is applied in `_github`
   (line 256-266).
4. Subprocess calls: every `subprocess` call in src uses a list argv; `grep shell=True` and `os.system` in src returned nothing.
   The argv builders that take caller input put it after `--` or as a single element (black_filtered_apply.py:385; api_floor.py:228).
5. Release job privileges: RELEASE_TOKEN is referenced only in steps that need it (release.yml:145, :195); the publish job's
   checkout uses persist-credentials false (release.yml:131, 184); the rollback target is checked against existing tags by
   release_guard.py rollback before use.
6. Profile writes: src/py_ci_shared/setup_env.py:130-156 edits ~/.bashrc or ~/.zshrc only by replacing a line it tagged itself;
   an untagged export is left alone (action "user"). Writes outside the repo are owner-run setup, not gate behaviour.
7. Shell text replay: src/py_ci_shared/guard_population.py:111-165 executes the first grep/find/ls line of a guard file through
   `bash -c` (line 220). The text comes from the repo, the same trust level as the tests the same PR runs, so no trust boundary
   is crossed. Recorded so the next round does not re-open it.

Not reported (considered and rejected as unrealistic on this repo): `_reap_orphans` in embedded_postgres.py:115-138 skips
the uid check on Windows, but Windows temp directories are per-user, so another local user cannot plant a `pg-embedded-*`
directory there; the POSIX uid check is present.

## Proposed next steps (for the owner, not applied in this round)

1. 51-F2: decide between a tag ruleset (recommended) and reopening the required reviewer; update the 51-F8 row either way.
2. 51-F1, 51-F3, 51-F4, 51-F5: each is a small code or workflow change with one test. Suggest one commit, "Harden supply chain: 51-F1..F5",
   after owner sign-off.
