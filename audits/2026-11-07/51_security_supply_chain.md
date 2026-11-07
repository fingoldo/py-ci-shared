# 51 security_supply_chain (round 2026-11-07)

Read-only audit; evidence is file:line plus commands. No secret values were printed.

## 51-F1 [P2] config_drift_check._fetch_pyproject sends a Bearer token with urllib's default redirect handler

- Evidence: src/py_ci_shared/config_drift_check.py:60-70: headers carry Authorization Bearer for urls starting https://api.github.com/, then urllib.request.urlopen(Request(...)) with no custom opener (grep for _SameHost|build_opener in that file: no match). Contrast src/py_ci_shared/ci_health.py:326-339 where _SameHostRedirects exists because CPython copies Authorization to the redirect target.
- Failure scenario: A contents-API call answered with a 302 to another host (CDN, proxy fault, API change) delivers CONSUMER_READ_TOKEN (read access to every private consumer repo) to that host.
- Proposed fix: Reuse ci_health's same-host opener (move _SameHostRedirects to a shared module) in config_drift_check; test with a local redirecting server asserting no Authorization header reaches the second host.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED: SameHostRedirects moved to src/py_ci_shared/_http_safety.py (ci_health keeps the _SameHostRedirects alias) and config_drift_check._fetch_pyproject opens through it; tests/test_config_drift_redirect.py (local redirecting servers) failed before the fix. Commit: 'Harden supply chain: 51-F1..F8' on py-ci-shared master

## 51-F2 [P2] lint-blocking tool pins fall back to unpinned uvx/pip when the PIN input is empty

- Evidence: .github/workflows/lint-blocking.yml:182,195,205,214,225,238,283: 'if [ -n "$PIN" ]; then uvx "zizmor==$PIN" .; else uvx zizmor .; fi' (same shape for actionlint, codespell, yamllint, bandit, vulture, deptry). Defaults exist (lines 96-124) but a caller passing '' gets the newest PyPI release; black-filtered.yml:93-96 documents this exact drift as a past incident.
- Failure scenario: A compromised or merely breaking new release of a lint tool runs in every consumer CI with the checkout (and, for deptry, the installed project) in scope; a red from an unreviewed release looks like a real finding.
- Proposed fix: Drop the else branches: empty pin falls back to the declared default or fails with ::error::. Meta-test: every uvx / uv pip install in .github/workflows is == pinned on every executed path.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED: the unpinned else branches are gone from lint-blocking.yml; a 'Require pinned tool versions' step refuses empty *-version inputs; the input defaults are held equal to tool_versions.py by tests/test_security_supply_chain_workflows.py. Same commit as 51-F1

## 51-F3 [P3] install-pyutilz passes the ref to git fetch/checkout without the shape guard the other workflows use

- Evidence: .github/actions/install-pyutilz/action.yml:41-45: git -C pyutilz fetch --depth 1 origin "${INPUTS_PYUTILZ_REF}" and checkout with no case guard and no '--'. gates.yml:73-75 and lint-advisory.yml:138 have the guard.
- Failure scenario: A ref starting with '-' from the caller's pyproject pin is parsed by git as an option (--upload-pack=... runs a command). Needs a hostile pin in the caller's own repo, so low likelihood.
- Proposed fix: Add the same case guard, use checkout --detach and '--' separators.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED: install-pyutilz refuses any ref that is not 40 lowercase hex, uses 'fetch -- ref' and 'checkout --detach'; behavioural test runs the step script in bash. Same commit as 51-F1

## 51-F4 [P3] pyutilz cloned from the default branch and not verified against a full SHA

- Evidence: .github/actions/install-pyutilz/action.yml:34 'git clone --depth 1 https://github.com/fingoldo/pyutilz.git' then checkout of the input; nothing asserts HEAD equals a 40-hex pin.
- Failure scenario: If the pin is a tag or branch (allowed), the code installed into consumer CI changes without any consumer commit.
- Proposed fix: Reject non-40-hex refs, or assert git rev-parse HEAD equals the pin after checkout.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED: only a full SHA is accepted (branch/tag refused) and HEAD is asserted equal to the pin after checkout. Same commit as 51-F1

## 51-F5 [P3] Direct ${{ inputs.* }} substitution into run: in docs.yml, mypy-full.yml, lint-blocking.yml

- Evidence: .github/workflows/docs.yml:47 'run: ${{ inputs.install-command }}', mypy-full.yml:73, lint-blocking.yml:269; zizmor ignore at docs.yml:44 with the reason 'caller-controlled'. gates.yml:90-93 uses env + bash -c instead, so the repo is inconsistent.
- Failure scenario: Exploitable only if a consumer wires the input from untrusted context (PR title, head_ref); the reusable workflow offers no protection and the 'trusted' claim is unenforced.
- Proposed fix: Switch to env + bash -c "$INSTALL_COMMAND" as gates.yml does (keeps multi-line script semantics), or document that the input must be a literal.
- AST-checkable: True; FP risk: medium
- Disposition: RESOLVED: docs.yml, mypy-full.yml and lint-blocking.yml pass the install command via env INSTALL_COMMAND and run bash -e -c; the zizmor ignores are removed; test_install_command_inputs_never_interpolated_into_run. Same commit as 51-F1

## 51-F6 [P3] github.action_path interpolated inside a run: script

- Evidence: .github/actions/install-pyutilz/action.yml:31: python "${{ github.action_path }}/resolve_pin.py" pyproject.toml
- Failure scenario: action_path is runner-controlled, so no practical injection; zizmor may flag it and a path with quotes would break the command.
- Proposed fix: Pass via env ACTION_PATH and use "$ACTION_PATH".
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED: ACTION_PATH env var in install-pyutilz; test_no_action_path_interpolated_into_run covers every workflow and the action. Same commit as 51-F1

## 51-F7 [P3] tarfile.extractall without a filter on Python < 3.12

- Evidence: src/py_ci_shared/consumer_import_census.py:214-222: filter='data' only when sys.version_info >= (3,12); else branch relies on a manual isfile/isdir plus '..'/absolute check, marked nosec B202. Input is git archive of a local library.
- Failure scenario: Mode bits (setuid) are not stripped on 3.9-3.11, the supported floor; practical risk near nil since the archive is the user's own repo, but the guard is hand-rolled.
- Proposed fix: Reject members with setuid/setgid bits in the manual check, or extract per member via extractfile on all versions.
- AST-checkable: True; FP risk: medium
- Disposition: RESOLVED: _extract_ref rejects setuid/setgid members on every Python version before extraction; tests in tests/test_consumer_import_census.py. Same commit as 51-F1

## 51-F8 [P3] release trust depends on repository settings the code cannot verify

- Evidence: .github/workflows/release.yml:111-113 comment: environment protection and moving RELEASE_TOKEN into the environment are settings the owner sets. Token read at release.yml:138 and :187; rollback job gated to refs/heads/master (line ~169). A fine-grained contents+workflows token force-pushes the v1 tag every consumer fetches.
- Failure scenario: If RELEASE_TOKEN is still a repository secret and the environment has no v* tag rule, any workflow in this repo (including one added on a branch) can read it; compromise moves @v1 for all consumers.
- Proposed fix: Owner action: confirm the token lives only in the release environment with a v* deployment rule and a required reviewer; add a ci-health style check of gh api repos/.../environments/release that fails when protection is absent.
- AST-checkable: False; FP risk: low
- Disposition: WON'T FIX (owner decision 2026-11-07): the owner accepts the residual risk and does not configure the release environment. The risk is documented, not mitigated: while RELEASE_TOKEN stays a repository secret and the environment has no rule, any workflow in this repository (including one added on a branch) can read it, and a compromise could move @v1 for every consumer. Documented in CLAUDE.md 'Versions and releases'. To close it later: Settings > Environments > release (required reviewer, deployment rule limited to master and v* tags), move RELEASE_TOKEN into it, add 'environment: release' to the release.yml jobs that read it, and add a ci-health check of gh api repos/fingoldo/py-ci-shared/environments/release.

## 51-F9 [P3] Verified clean record: no pull_request_target, actions SHA-pinned, least-privilege permissions, safe deserialization

- Evidence: grep pull_request_target in .github: no match; grep for uses: lines with a non-40-hex ref: empty; all 14 workflows declare top-level 'permissions: contents: read'; release.yml uses persist-credentials:false and passes the token as a one-shot extraheader; run_git (src/py_ci_shared/_core/git.py:109-137) strips GIT_DIR location vars via git_env unless inherit_location; yaml.load uses a SafeLoader subclass (ci_install_covers_conftest.py:206); no shell=True/pickle.load/eval/os.system found in src.
- Failure scenario: Not a defect; recorded so the next round does not re-audit.
- Proposed fix: None. Confirm existing meta-tests cover SHA pins and top-level permissions.
- AST-checkable: True; FP risk: low
- Disposition: WON'T FIX: verified-clean record, no defect

