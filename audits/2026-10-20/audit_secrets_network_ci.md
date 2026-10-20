# Security audit: secrets, network fetching, workflows (2026-10-20)

Read-only audit of `py-ci-shared`. No source or test file was edited. Scope: secret handling, HTTP and URL fetching,
the repo's own `.github/workflows/*.yml` and `.github/actions`, and the reusable workflows consumers call.
Line numbers refer to the working tree as read today.

## Summary

| Severity | Count |
|----------|-------|
| P0       | 0     |
| P1       | 0     |
| P2       | 5     |
| Low      | 5     |

No finding gives an outside party access to a secret. The P2 items are defence-in-depth gaps around the release
token and the corpus artifacts. The remaining items are hardening.

## Checked and found sound (no finding)

- **Token in argv or logs (`_consumers.py:93-98`).** The consumer token goes to git through `GIT_CONFIG_*`
  as an `extraheader` scoped to `http.https://github.com/`. It is never in argv. `GIT_TERMINAL_PROMPT=0` is set.
  Failure output is git's stderr only (`_consumers.py:106`), which does not contain the header.
- **Env dumps in reports.** `secret_safe_test_output.py:192` and `:228-232` print variable names only, never values.
  `tracked_secret_shapes.py:22,172` withholds the matched text by design.
- **DSN echo.** `embedded_postgres.py:253-265` refuses non-loopback DSNs and names the reason, not the DSN.
- **TLS and timeouts.** No `verify=False`, `CERT_NONE`, or `allow_redirects` override anywhere in `src/` or `.github/`.
  Every `urlopen` has a timeout: `ci_health.py:338` (`DEFAULT_REQUEST_TIMEOUT`), `release_guard.py:235` (60 s),
  `config_drift_check.py:35` (15 s). Git clone has 600 s (`_consumers.py:101`).
- **SSRF.** `ci_health.py:332` and `release_guard.py:231` build URLs from a fixed base (`api.github.com`) and
  `config_drift_check.py` uses two fixed raw URLs. A consumer cannot change the host. See L-4 for the residual path issue.
- **Action pinning.** Every `uses:` in `.github/workflows` and `.github/actions` is pinned to a 40-character SHA, with
  a version comment. No `pull_request_target` or `workflow_run` trigger exists. `self-ci.yml` runs on `pull_request`
  with `contents: read` and no secrets.
- **Injection in `run:`.** The untrusted-looking values are all passed through `env:`. The three direct
  `run: ${{ inputs.* }}` sites (`docs.yml:47`, `mypy-full.yml:73`, `lint-blocking.yml:246`) take caller-only
  `with:` input. They are accepted by design, and the comments in those files say so.
- **Remote-fetch fallback.** `ruff-blocking.yml:86-98`, `black-filtered.yml:69-82` and `lint-advisory.yml:133-150`
  fail on an unfetchable ref and never fall back to `master`.
- **Persisted credentials.** Every checkout sets `persist-credentials: false`, except the two `publish` and
  `rollback` checkouts in `release.yml`. See P2-1 and P2-2.

## P2 findings

### SEC-1 (P2) -- Release token persisted into a checkout that runs the tagged commit's script

**Disposition:** RESOLVED -- release.yml publish checkout keeps no credential (persist-credentials: false); RELEASE_TOKEN reaches only the push step as a one-shot extraheader.

- **File:** `.github/workflows/release.yml:118-132`
- **Scenario:** The `publish` job checks out with `token: ${{ secrets.RELEASE_TOKEN || github.token }}` (line 126)
  and leaves `persist-credentials` at its default `true`. actions/checkout therefore writes the token into
  `.git/config`. The next step runs `python3 .github/scripts/release_guard.py major-move` (line 132), which is
  code from the checked-out tagged commit. A commit that changes that script, once it reaches `master`, can read
  `.git/config` and send the write-scoped token (contents and workflows write) anywhere. The `release` environment
  protects the secret only by a tag rule, so the tagged commit is the only gate on what runs next to it.
  The tagged commit must be an ancestor of `master` (`release.yml:77`), so this needs malicious code already merged
  to `master`. That is why the severity is P2 and not higher.
- **Fix:** Set `persist-credentials: false` on the `publish` checkout. Pass the token only to the push, for
  example `git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)" push ...`
  from an `env:` value. Alternatively, run `release_guard.py` from `master` with
  `git show origin/master:.github/scripts/release_guard.py | python3 - major-move "$TAG"`.

### SEC-2 (P2) -- Rollback job runs from any branch with the release token

**Disposition:** RESOLVED -- the rollback job runs only from refs/heads/master, with persist-credentials: false and the same one-shot header.

- **File:** `.github/workflows/release.yml:164-194`
- **Scenario:** `rollback` runs on `workflow_dispatch` with no check of the ref. Anyone with write access can
  dispatch it from a feature branch. The job checks out that branch (line 172-175, token persisted as in P2-1),
  and runs that branch's `release_guard.py` (line 180) and then `git push -f origin refs/tags/$major` (line 194)
  with `RELEASE_TOKEN`. If the `release` environment's deployment rule only admits `v*` tags, the job is blocked, but
  nothing in the file enforces that, so the protection depends on a repository setting that is not in the repo.
- **Fix:** Add `if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master'` to the `rollback`
  job, `persist-credentials: false` to its checkout, and pass the token only to the push step.

### SEC-3 (P2) -- Finder exception text, which can hold private consumer source, is uploaded and published

**Disposition:** RESOLVED -- corpus_drift records the exception type name only; tests/test_corpus_drift.py::test_snapshot_errors_never_carry_the_exception_text.

- **File:** `src/py_ci_shared/corpus_drift.py:256` and `.github/workflows/corpus-drift.yml:114-127`
- **Scenario:** `_scan_repo` stores `type(exc).__name__` plus the first 300 characters of the exception message in
  the snapshot JSON (`errors[key]`). Many parser and linter exceptions quote the offending source line, for example
  a `SyntaxError`, a `yaml` error, or a `tomllib` error. The `corpus = true` consumers are checked out with
  `CONSUMER_READ_TOKEN`, so they can be private. The snapshot is uploaded as an artifact kept for 90 days
  (`corpus-drift.yml:119-127`), and `corpus-drift.md`, which contains the same text, is written to
  `$GITHUB_STEP_SUMMARY` (line 117). Anyone who can read this repo's runs can read the private code.
- **Fix:** Record only the exception type name in `errors[key]`, drop the message, and keep the full text in the
  job log only if the consumer is public. Add a unit test that feeds a finder an exception whose message contains a
  marker string and asserts the marker is absent from the snapshot.

### SEC-4 (P2) -- Committed credentials in `.env.example` are echoed to CI logs

**Disposition:** RESOLVED -- env_example_round_trip reports the field name and exception type, never the value; the test asserts the value is absent.

- **File:** `src/py_ci_shared/env_example_round_trip.py:150` (and `:139`)
- **Scenario:** The problem line includes `{name}={value!r}` (the value from the tracked file) and the first line of
  the Pydantic exception, which quotes `input_value=` with the same value. If a real key was committed to
  `.env.example`, it is printed to the pytest output and the pre-commit log, and stays in CI history. The sibling gate
  `tracked_secret_shapes.py:22` deliberately withholds the matched text, so the two gates disagree.
- **Fix:** Print `{name}` and the exception type, not the value: `f"{env_path.name}:{lineno}: {name} does not load: {type(exc).__name__}"`.
  Keep the value out of `first`, and check the message for the value before printing it.

### SEC-5 (P2) -- Consumer token reaches a third host on redirect

**Disposition:** RESOLVED -- ci_health and release_guard use a same-host redirect handler, so Authorization never leaves api.github.com; two tests.

- **File:** `src/py_ci_shared/ci_health.py:332-338`, and the same pattern at `.github/scripts/release_guard.py:231-235`
- **Scenario:** `urllib.request.urlopen` follows redirects and copies the `Authorization: Bearer` header to the new
  request, even when the new host is different. CPython does not strip it, unlike `requests`. If GitHub's API ever
  redirects to another host, or if a DNS or proxy fault sends the request elsewhere, `CONSUMER_READ_TOKEN` (or
  `GITHUB_TOKEN`) is sent there. The odds are low, which is why this is rated P2 and not higher.
- **Fix:** Use a `HTTPRedirectHandler` subclass whose `redirect_request` drops `Authorization` when the new host is not
  `api.github.com`, and pass it to `build_opener`. Or refuse redirects outright, since the GitHub REST endpoints
  used here do not need them.

## Low findings

### SEC-6 (Low) -- Unpinned CI lint tools by default

**Disposition:** DEFERRED -- the lint-blocking *-version inputs default to the empty string; pinning them needs exact versions chosen and tested per tool (tool_versions.py holds only ruff and black), and changing the defaults is a consumer-visible behaviour change that needs a CHANGELOG entry.

- **File:** `.github/workflows/lint-blocking.yml:93-121` (the `*-version` inputs, all `default: ""`), and the `uvx` calls
  around lines 157-260.
- **Scenario:** A consumer that does not set `zizmor-version`, `bandit-version` and so on runs `uvx <tool>`, which
  resolves the newest PyPI release at run time. A compromised or broken release runs in that consumer's CI with the
  job's read permissions. Blast radius is limited (`contents: read`, no secrets in this job), so Low.
- **Fix:** Change the defaults to exact versions, the same ones `tool_versions.py` holds, or make an empty pin an error
  with `::error::`.

### SEC-7 (Low) -- `2>&1 | head -5` under `pipefail` can fail the job spuriously

**Disposition:** RESOLVED -- self-ci writes ruff --show-settings to a file and prints its head, so pipefail cannot fail the step.

- **File:** `.github/workflows/self-ci.yml:141`
- **Scenario:** The job runs with `shell: bash` (line 58), which adds `pipefail`. Once `head -5` exits, `uvx ruff`
  can get SIGPIPE or EPIPE and return non-zero, so the step can go red for no reason, or stay green on a real failure
  depending on timing.
- **Fix:** Drop the pipe: `uvx "ruff==${RUFF_VERSION}" check --config configs/ruff-base.toml --show-settings . > /dev/null`.

### SEC-8 (Low) -- Caller-supplied ref can be read as a git option

**Disposition:** RESOLVED -- ruff-blocking, black-filtered and lint-advisory reject a py-ci-shared-ref that is not letters, digits and ._/- or that starts with -, and fetch with --.

- **File:** `.github/workflows/ruff-blocking.yml:89`, `black-filtered.yml:75`, `lint-advisory.yml:141`
- **Scenario:** `py-ci-shared-ref` is passed to `git fetch --depth 1 <url> "${PCS_REF}"` and into a `uv` requirement.
  A value beginning with `-` is read as an option. The input is set only by the caller's own `with:`, so the
  exposure is limited to a misconfigured caller, which is why this is Low.
- **Fix:** Insert `--` before the ref in `git fetch`, and reject refs that do not match `^[A-Za-z0-9._/-]+$` in the
  `Resolve` step.

### SEC-9 (Low) -- Consumer `repo` is validated only by its slash count

**Disposition:** RESOLVED -- load_consumers validates repo and name (same fix as DS-2); tests/test_consumers_validation.py.

- **File:** `src/py_ci_shared/_consumers.py:69-70` and `:48-49`; `ci_health.py:424` builds the query from it.
- **Scenario:** `Consumer.url` (`https://github.com/{repo}.git`) and the `ci_health` API path use `repo` as is.
  A value such as `a/b?x` or `a/b#` changes the URL path or query. The host cannot change, so this is not SSRF. It
  matters only if someone edits `configs/consumers.toml` maliciously.
- **Fix:** Validate `repo` against `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$` in `load_consumers`.

### SEC-10 (Low) -- Shell word-splitting of `pip-audit` inputs without quotes

**Disposition:** RESOLVED -- lint-advisory splits its pip-audit inputs with read -ra, so a pattern is not expanded against the workspace.

- **File:** `.github/workflows/lint-advisory.yml:183`, `:186`, `:192`
- **Scenario:** `for f in ${PIP_AUDIT_REQUIREMENTS}` and the two sibling loops split and glob the input. A pattern such as
  `*.txt` expands against the runner's working tree, which is what the caller meant only by accident.
- **Fix:** Use `read -ra` on the variable (`IFS=' ' read -ra files <<< "$PIP_AUDIT_REQUIREMENTS"`), which does not glob.

## Notes for the owner

- P2-1 and P2-2 are the highest-value fixes. Both change only `release.yml` (`persist-credentials: false` on two
  checkouts, one `if:` on `rollback`).
- P2-2 is also gated by the `release` environment's deployment rule, which `CLAUDE.md` asks the owner to set (v* tags
  and the default branch only). That rule is a repository setting, not code, so the workflow file should enforce
  the same limit itself.
- Nothing in this audit needed a change to a consumer. Every finding is in this repo.
