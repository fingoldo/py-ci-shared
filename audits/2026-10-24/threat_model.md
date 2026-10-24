# py-ci-shared threat-model audit, 2026-10-24

Scope: `master` at 1665d75 (tag v1.23.4). Read-only: no tracked file was edited, nothing committed. Method: grep of the
workflows, the release scripts and `src/py_ci_shared`, then reading each function the grep points at. Severity: High / Med /
Low / Info. Disposition: FIX (a later step changes code), ACCEPTED (reason given), ruled out (checked, no finding).

## Findings

| id | direction | file:line | severity | evidence | disposition |
|---|---|---|---|---|---|
| T-01 | (3) gate bypass | `src/py_ci_shared/code_audit/_base.py:399` | Low | `_EXEMPT_RE` takes any non-empty reason (`\S.*?`), so `-- x` exempts a line; the baseline gate requires 20 characters | FIX: see the replacement below |
| T-02 | (2) workflow input | `docs.yml:47`, `mypy-full.yml:73`, `lint-blocking.yml:246` | Low | `run: ${{ inputs.<install-command> }}` | ACCEPTED: the value is written by the consuming repo's own workflow, not by PR content; no `pull_request_target` in the repo |
| T-03 | (2) permissions | `docs.yml:66-67`; `release.yml:117,172` | Low | write scopes are per job; `RELEASE_TOKEN` is read only in the `release` environment and passed as a one-shot header | ACCEPTED: needed for Pages and the major-tag push |
| T-04 | (3) baseline growth | `_core` shrink-only; `PY_CI_SHARED_REFRESH_ALLOW_GROW` | Low | growth needs `grow=True`, a flag or the env var; no workflow sets it | ACCEPTED for CI; a developer shell can still opt in (documented) |
| T-08 | (5) subprocess | `guard_population.py:109-217` | Info (was open, resolved) | `command` is read from the repo's own `tool/check*.sh` (`find_guards_with_empty_population`, `tool_dir.glob`) and re-run with `bash -c` in `repo_root` | ACCEPTED: the scripts are repo content, and a PR that edits them already runs as CI shell; no new trust boundary |
| T-11 | (3) gate bypass | `src/py_ci_shared/code_audit/redundant_test_fit.py:107` | Low | the marker's reason says "ast.unparse is absent on py3.8", but `pyproject.toml` declares `requires-python = ">=3.9"`, so the fallback is dead code and the reason is false | FIX: replace the getattr fallback with `ast.unparse` and drop the marker (see below) |
| T-12 | (4) fail-open | `src/py_ci_shared/deferred_entries.py:81-83` | Med | a file that does not parse is only logged (`logger.warning`) and returns None, so its deferred entries are not counted; `deferred_drift.py:62-78` compares these counts against the baseline; an unreadable file is reported as an error string, an unparsed one is not | FIX: report unparsed files the same way as unreadable ones (fail the drift gate, naming the files), or raise `UnparsedFilesError` from `_core` |
| T-13 | (4) fail-open | `src/py_ci_shared/optional_truthiness.py:93-96` | Low | an unparseable string annotation is kept as one member and logged only at debug level, so a union written as text can be missed | FIX: log at warning and count it as a finding, not a single member |
| T-05 | (1) supply chain | every `uses:` in `.github/workflows` | Info | all external actions are pinned to a 40-character SHA; no unpinned `uses:` found | ruled out |
| T-06 | (7) release gate | `.github/scripts/release_guard.py:81` `version_problem`; `release.yml:9,20` | Info | version must be strictly ahead of every tag, or equal only on its own tag commit; rollback takes an existing `vX.Y.Z` | ruled out (read, not exercised) |
| T-07 | (4) fail-open | `prompt_field_parity.py:146-155` | Info | `strict=False` returns `[]` on SyntaxError, and no `src` caller passes `strict=False` | ruled out |
| T-09 | (6) hook installers | `install_safe_hook.py` | Info | patches `.git/hooks` only; no home or absolute-path writes found | ruled out |
| T-10 | (5) git calls and shell | `_core/git.py`; `git_child_env.py` | Info | git runs through `run_git` with a timeout and UTF-8; no `shell=True` in `src` or `.github/scripts` | ruled out |

## Exemption markers (`audit-exempt`), reviewed one by one

Counting: the earlier figure of 36 included `.pyc` files under `__pycache__`. In `.py` source there are 16 occurrences: 12
real markers (`# audit-exempt: <check> -- <reason>` on a flagged line) and 4 mentions in regexes and docstrings that are not
exemptions. Each of the 12 was read against the code under it:

- OK (11): `_mutation_runner.py:540` (the loop is bounded by the deadline); `_mutation_worker.py:166,199` and
  `dart_scanners.py:702` (the warning fires once per file: `_IS_LOCAL` and the per-file walk); `registry.py:516` (the
  predicate returns False and the caller runs in-process); `sibling_floor_skew.py:287` (`_git_show_version` returns None on
  every failure path); `docstring_names_a_caller_that_does_not_call.py:13,40,123`, `duplicate_function_body.py:85`,
  `vacuous_matching.py:33` (the flagged text is quoted prose, and the reason says so).
- BAD (1): `redundant_test_fit.py:107` (T-11).

## The three `except SyntaxError` sites outside prompt_field_parity

- `constant_relations.py:235`: OK. The error is re-raised as `RelationError` (loud), not swallowed.
- `deferred_entries.py:81`: T-12 (Med, fail-open).
- `optional_truthiness.py:93`: T-13 (Low).

## Proposed replacements (not applied; for the later FIX step)

- T-01: in `_base.py`, change the regex reason group to `(?P<reason>\S.{18,}?)` (20 characters or more), and keep the
  existing `\s*$` anchor. The 12 real markers all exceed 20 characters, so none of them stops being valid.
- T-11: in `redundant_test_fit.py`, replace line 107 with `_unparse = ast.unparse` and remove the marker.

Counts: High 0, Med 1 (T-12), Low 6 (T-01, T-02, T-03, T-04, T-11, T-13), Info 6 (T-05, T-06, T-07, T-08, T-09, T-10).
Markers: 12 real, 11 OK, 1 bad.
