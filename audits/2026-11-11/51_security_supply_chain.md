# 51 security_supply_chain (round 2026-11-11)

Read-only audit. Scope: secret handling, release environment and deployment policy, pinned SHAs and tool pins, subprocess and
shell injection, path traversal, unsafe deserialisation, release trust model. Prior rounds read first: 2026-11-07 (51-F1..F9)
and 2026-11-10 (51-F1..F5), plus audits/TRACKER.md and CLAUDE.md. No secret values were read or printed. No remote API was called.

## Summary

| ID | Severity | Status | One line |
|----|----------|--------|----------|
| 51-S6 | P3 | new, open | `_remote_commit` passes a lock-file URL to `git ls-remote` as a positional with no `--`, so a URL starting with `-` is parsed as a git option |

Counts: new findings 1 (P0: 0, P1: 0, P2: 0, P3: 1).
Already tracked or accepted and re-confirmed in code this round: 2026-11-10 51-F1, 51-F3, 51-F4, 51-F5 (tracker RESOLVED; F1, F4, F5 checked
in source). 2026-11-10 51-F2 is ACCEPTED: CLAUDE.md, owner 2026-11-10. 2026-11-07 51-F8 is ACCEPTED: CLAUDE.md, required reviewer
not configured. 2026-11-07 51-F9 is the verified-clean record. Tracked: 14 rows in total across the two rounds.

---

## 51-S6 [P3] `_remote_commit` passes an untrusted URL to `git ls-remote` as an unguarded positional

- Evidence:
  - src/py_ci_shared/version_tag_currency.py:253-255: `run_git(None, "ls-remote", url, f"refs/tags/{ref}^{{}}", ...)`. The `url`
    argument is the second argv element, with no `--` before it.
  - src/py_ci_shared/version_tag_currency.py:284: `url = str(entry.get("url") or "")`, read from the `url` key of a
    `pubspec.lock` git entry, and passed to `_remote_commit` at :291. Nothing checks its shape (no `https://` or `git@` prefix,
    no leading-dash check).
  - src/py_ci_shared/_core/git.py:109-117: `run_git` builds argv from `*args` and does not insert `--`.
- Attacker-controlled input and path: a `pubspec.lock` in the repository being checked, with its `packages.<name>.url` set to a value
  beginning `--upload-pack=` (for example `--upload-pack=<command>`). `find_lock_pin_problems` (:269) reads it, and `_remote_commit`
  runs `git ls-remote --upload-pack=<command> refs/tags/... refs/heads/...`. git takes the first positional as an option, and the
  next positional becomes the repository, so git executes the command through the upload-pack hook. The input is repository
  content, not a network response.
- Impact and realism: code execution in the job that runs the version-currency check. That job runs the same PR tree's tests and
  scripts, so it does not cross a trust boundary the PR does not already have. It matters only where that job holds a credential
  the tests do not (for example a consumer-side token in env). Fork PRs get no secrets. The same-trust argument from 51-F-round
  item 7 (guard_population) applies, which is why this is P3 and not higher.
- Proposed disposition: WON'T FIX is not recommended. Make it RESOLVED with a one-line change: insert `--` before `url`
  (`run_git(None, "ls-remote", "--", url, ...)`), or reject a `url` that starts with `-` in `find_lock_pin_problems` before the
  call. Test: a `pubspec.lock` with `url: "--upload-pack=touch <marker>"` must produce a problem line, and the marker file must not
  be created.
- AST-checkable: True (a `run_git`/`subprocess` call whose git subcommand takes a positional URL without a preceding `--`); FP risk: low.

---

## Examined, no finding (this round)

1. Secrets in argv: `_consumers.py:99-106` passes the consumer token through `GIT_CONFIG_*` env, not argv. release.yml:158-160
   and :204-206 now pass the RELEASE_TOKEN header through `GIT_CONFIG_*` env (51-F4 fix confirmed). No `--token` argv form was found
   in src or .github.
2. Secrets in logs: no `print`/`echo` of a token variable in src or .github. The `::error::` and `::warning::` lines name the secret
   only as a missing variable, with no value.
3. Token redirects: every bearer-token request uses `_SameHostRedirects` (ci_health.py:404-406; config_drift_check.py:72-73;
   sibling_floor_skew.py:277-279). No plain `urlopen` carries Authorization (51-F1 confirmed).
4. Release environment: release.yml:120-122 and :177 (`environment: release`), RELEASE_TOKEN only at :145 and :195, both as `env:`
   values of the step that needs them. The publish job's checkout keeps no credential (`persist-credentials: false`, :131). The
   rollback job is limited to `refs/heads/master` (:171). `rollback_problem` (release_guard.py:73) checks `TARGET` against
   existing `vX.Y.Z` tags, and the regex at :33 anchors the shape. Accepted: 51-F2 (2026-11-10), 51-F8 (2026-11-07).
5. Pins: every `uses:` line in .github/workflows is pinned to a full 40-character SHA with a version comment. The grep for
   `uses: ...@` without a 40-hex SHA returned nothing. Tool pins: lint-blocking.yml:189-287, lint-advisory.yml:166-260,
   mypy-beachhead.yml:71, mypy-full.yml:71, black-filtered.yml:104 each use an exact `==` pin from an input or env. release.yml:65
   and self-ci.yml use `.github/constraints/dev.txt` (51-F5 fix confirmed).
6. Ref inputs reaching a shell: `PCS_REF` (gates.yml:68-82, black-filtered.yml:67-78) is matched against
   `-*|*[!A-Za-z0-9._/-]*` before `git+https://...@${PCS_REF}`. Shell-interpolated `${{ inputs.* }}` inside `run:` was not
   found (`grep "run:.*\${{"` returned nothing); inputs reach scripts through `env:` (51-F5 of 2026-11-10 holds).
7. `pull_request_target`: not present in any workflow. self-ci.yml is the only `pull_request` trigger, with contents:read.
   Every workflow sets `permissions: contents: read`, and only release.yml raises a job to `contents: write`.
8. Archive extraction: `consumer_import_census._extract_ref` (:287-304) rejects absolute and `..` members, rejects setuid/setgid,
   and uses `filter="data"` on 3.12+. `stale_baseline_sweep._snapshot` (:142-147) extracts a `git archive` zip of the repo's own HEAD
   with `zipfile.extractall`, which strips `..` and absolute names. `embedded_postgres` (:391-393, :433-435) extracts pinned,
   SHA-256-verified wheels (`_verified_wheel` before unpack). None of these is reachable with attacker-chosen member names.
9. Deletes and rmtree: `embedded_postgres.py:436-446` removes only entries the wheel brings (bin/, lib/, share/). `_mutation_runner.py`
   rmtree (:425-447) and `checkout_resolution.py:104-106` operate on paths under a caller-created workdir. None is fed by a config
   value that can name a parent directory.
10. Deserialisation: `yaml.load` calls (ci_default_branch_never_cancelled.py:400, ci_install_covers_conftest.py:207) use a
    `SafeLoader` subclass. `pickle` in code_audit/registry.py:534-540 is a `pickle.dumps` probe only; no `pickle.load` of
    untrusted bytes was found. `_lock_git_entry` uses `yaml.safe_load`.
11. Subprocess: no `shell=True`, `os.system` or `bash -c` in src apart from guard_population.py:220, which is the item-7 case in the
    2026-11-10 round (repository text, same trust as the PR's tests). `black_filtered_apply.py:389` places `--` before file
    arguments.
12. Consumer names and paths: `_consumers.py:53` (`_NAME`) and :133-139 keep a consumer directory directly under `dest`.

## Proposed next steps (for the owner, not applied in this round)

1. 51-F6: one-line `--` insertion in version_tag_currency.py:253-255 with the test above. It can ride with the 2026-11-10 hardening
   commit if that is still pending.
