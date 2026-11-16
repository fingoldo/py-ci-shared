# 51 security_supply_chain: round 2026-11-16 (T)

Scope: workflow permissions and token scope, expression injection, action pins and their currency, secret exposure, dependency pins and hashes, the `tool`
command surface, file-scanning gates (symlinks, huge files), subprocess/eval/pickle/yaml, the mutation runner. Commit audited: 48e49b1 (master).
Read first: audits/2026-11-11/51, 2026-11-07/51, TRACKER rows 51-*, 46-F14, 46-R18. Nothing was exploited; demos used scratch inputs under
scratchpad\audit_T\51\ (demo.py, big.py).

Commands run: `gh api` GET on repos/fingoldo/py-ci-shared (actions/permissions, actions/permissions/workflow, environments, actions/secrets,
branches/master/protection, fork-pr-contributor-approval) and releases/latest of the 8 pinned actions; Grep for `uses:`, `${{`, `shell=True|eval|exec|
pickle|yaml.load|subprocess`, `rglob|os.walk`; reading corpus.py, source.py, cli.py `_cmd_tool`, corpus_drift.py, _mutation_runner.py, setup_env.py.
Not covered: the pytest-plugin and hook entry points beyond the grep; no secret values were read.

Re-confirmed this round (no new evidence, not re-raised): release environment without required reviewer (accepted), release gates from tagged commit
(accepted), master without branch protection and no SHA-pin enforcement (46-F14 WON'T FIX), tool pins without a dependabot pip channel (46-R18).

## 51-T1 (P3) `iter_files_pruned` lists symlinked files; `iter_files` skips them
- **Evidence:** corpus.py:184 states "Symlinked files are listed, as rglob did"; `_walk` (corpus.py:168-175) yields every name in `filenames`, and os.walk
  reports a file symlink there. `iter_files` (corpus.py:275-281) drops symlinks with `_is_regular_file` and says why (a link outside the checkout would put
  that file's text into findings and make the run machine-dependent). Demo (symlink repo/link.py -> outside/secret.py):
  `pruned: ['link.py']` and `iter_files: []`. Callers of iter_files_pruned that then read the files: meta_test_utils.py:99,141,414, deferred_entries.py:37,
  llm_output_caps.py:123, dart_scanners.py:715, contract_ledger.py:148, destructive_tests_throwaway_only.py:349, code_audit/_todo_markers.py:43,
  column_no_write_path.py:107, ci_install_covers_entry_imports.py:283.
- **Impact:** a repo under scan with a symlink to a file outside it (/etc/..., a home file) has that file read and quoted in findings by those gates, and
  two gates over the same tree disagree about what the corpus is. Same-trust (repository content), hence P3.
- **Proposed fix:** in `iter_files_pruned` drop entries where `_is_regular_file` is false (a FIFO in the tree would otherwise block a read
  on POSIX; not reproducible on this Windows host). BEHAVIOUR CHANGE (a gate that scanned a symlinked file stops seeing it; CHANGELOG "Behaviour change", What to do: replace the link by the file).
- **Disposition:** FIXED (this round): `iter_files_pruned` drops symlinks as `iter_files` does; red then green in tests/test_core_corpus_symlinks.py. changelog Behaviour change.

## 51-T2 (P3) No size cap on files read by the scanning gates
- **Evidence:** `read_bytes_cached` (source.py:98) does `Path.read_bytes()` whole; `_BYTES_MAX_FILE` bounds only the cache (source.py:100).
  Demo, one 260 MiB `.py` of comment lines through `parse_source`: `size MiB 260`, `seconds 6.6 peak MiB 780` (tracemalloc), about 3x the file size, no refusal.
- **Impact:** a huge tracked file (or several, read in parallel by corpus_drift with --workers 4) can exhaust a runner and read as a crash instead of a finding. Repo content, so same-trust; P3.
- **Proposed fix:** a `MAX_SCANNED_BYTES` (for example 16 MiB, env-overridable) in `read_bytes_cached`/`read_text_if_not_binary` raising `SourceReadError("larger than N bytes, not scanned")` so the file is reported, never silently skipped. BEHAVIOUR CHANGE (a legitimately huge generated .py turns red; What to do: exclude it or raise the env cap).
- **Disposition:** FIXED (this round): `MAX_SCANNED_BYTES` (16 MiB, env `PY_CI_SHARED_MAX_SCANNED_BYTES`) enforced by `read_source`, `read_text_if_not_binary` and `parse_source`; red then green in tests/test_core_source.py::TestSizeCap. changelog Behaviour change. Residual: `repo_hygiene` and `gate_cache` still read raw bytes through `read_bytes_cached` without this cap (`repo_hygiene` treats an unreadable file as no finding, `gate_cache` hashes it as unreadable).

## 51-T3 (P3) CONSUMER_READ_TOKEN is a job-level `env:` in corpus-drift's snapshot job
- **Evidence:** corpus-drift.yml:73-75 sets `CONSUMER_READ_TOKEN` (and ALLOW_SKIPPED_PRIVATE) in the job `env:`, so it reaches every step: `pip install -e`
  (:90, runs the package build backend), the checkout step that needs it, and "Snapshot this repo's counts", which runs every corpus-shaped
  `find_*` over the checked-out consumer trees. Every other token user scopes it to the step (consumer-pins.yml:48, config-drift-check.yml:45, ci-health.yml:44, release.yml:145,197).
  The finders that execute repository text are excluded from the snapshot (corpus_drift.py skip list: guard_population "runs shell guard scripts", constant_relations, entry_points_resolvable, git_dependency_pins), so I could NOT show the token read by consumer content today: this is least privilege, not an exploit. All corpus repos are the owner's (configs/consumers.toml).
- **Impact:** if a future finder executes or imports repository content, or a corpus repo is added that is not the owner's, the read token (contents:read on the private repos) is in that process environment.
- **Proposed fix:** move the env lines onto the "Check out the corpus repos" step. ADDITIVE. A meta-test could pin "CONSUMER_READ_TOKEN / RELEASE_TOKEN appear only under a step-level env".
- **Disposition:** FIXED (this round): `CONSUMER_READ_TOKEN` moved to the corpus checkout step; guarded by `test_a_token_is_scoped_to_the_step_that_needs_it_never_the_job` in tests/test_security_supply_chain_workflows.py. Additive, no Behaviour change entry.

## 51-T4 (P3) Dependency pins carry no hashes; tools run through `uvx` resolve their transitive dependencies live
- **Evidence:** .github/constraints/runtime.txt and dev.txt hold `name==version` only (no `hash`/`--require-hashes` in .github/constraints); lint-blocking.yml:189-287, lint-advisory.yml:166-260, ruff-blocking.yml:126, mypy-beachhead.yml:71, black-filtered.yml:104 pin the tool itself with `==` but uvx resolves its dependencies at run time, with no hash or lock.
  `uv pip install --system "py-ci-shared @ git+...@${PCS_REF}"` (gates.yml:79, black-filtered.yml:78) installs a git ref, and a tag is movable (the shape guard stops option injection, not a moved tag).
- **Impact:** a compromised transitive release between pin bumps runs in jobs that hold only `contents: read` and no repo secret, so the exposure is the verdict and the log, not the release token. The release verify job (release.yml:65) shares the unhashed set but runs before the environment job. P3.
- **Proposed fix:** generate hashed constraints (`uv pip compile --generate-hashes`) for runtime.txt/dev.txt and install with `--require-hashes`; overlaps the deferred 46-R18 and should ride with it. ADDITIVE for this repo (consumer-facing reusable workflows keep the `==` pins).
- **Disposition:** WON'T FIX (coordinator item 10). Reason: the cost is not justified by the benefit. The exposure is limited to jobs that hold only `contents: read` and no repo secret, the verdict and the log; a hash set would need regenerating on every bump across the lint and gate workflows, and the verify job's shared set would need the same work. Verifying it would need `uv pip compile --generate-hashes` against the live index and a full `--require-hashes` CI install, which this session cannot run offline.

## 51-T5 (P3) Repository setting `sha_pinning_required` is false; the pin rule lives only in a test
- **Evidence:** `gh api repos/fingoldo/py-ci-shared/actions/permissions` -> `{"enabled":true,"allowed_actions":"all","sha_pinning_required":false}`. All 8 distinct actions in .github are pinned to a 40-hex SHA and every pin equals `releases/latest` today (checkout v7.0.1, setup-python v7.1.0, setup-uv v10.3.3, codecov v7.1.1, upload-artifact v7.0.2, download-artifact v8.0.2, upload-pages-artifact v5.0.0, deploy-pages v5.0.1), so nothing is stale.
  Other read-only API results: default workflow permissions `read`, `can_approve_pull_request_reviews:false`, fork-PR approval `first_time_contributors`, repo secrets are exactly CODECOV_TOKEN and CONSUMER_READ_TOKEN (RELEASE_TOKEN is the environment secret), environment `release` has a custom branch policy and no required reviewer (accepted).
- **Impact:** the setting would refuse an unpinned `uses:` at run time even if the meta-test were bypassed; this is the settings half of 46-F14 (WON'T FIX in code).
- **Proposed fix:** owner turns on "Require actions to be pinned to a full-length commit SHA" (no code change, no consumer effect), or re-confirms 46-F14.
- **Disposition:** RESOLVED: `sha_pinning_required` is true on fingoldo/py-ci-shared (set through the Actions permissions API, 2026-11-17). Checked first: every `uses:` in py-ci-shared's workflows is already a full SHA, so the setting changes no run here. Consumers set their own repo setting.

## Considered and not raised
- Expression injection: no `run:` line contains `${{`; every `${{ inputs.* }}` is an `env:` value (lint-advisory.yml:175-266 and the others). `matrix.name` in corpus-drift run blocks comes from the `plan` output built from configs/consumers.toml (repo-controlled), not from an event field. No `github.event.*` or `head_ref` in `run:`.
- `pull_request_target` / `workflow_run`: absent; the grep hits for `workflow_runs` are API paths in release_guard.py and corpus-drift.yml:162.
- Fork exposure of secrets: self-ci is the only PR trigger; secrets are referenced only in schedule/dispatch/release workflows; `persist-credentials: false` on every checkout; uploaded artifacts hold counts, reports and the adoption matrix, no `.git`.
- `tool` surface: `_cmd_tool` (cli.py:185) resolves the module from the static registry (`registry.by_name`), never from config. The only gates running a string from repo content are guard_population (`bash -c` on guard text, accepted item 7 of 2026-11-10) and gates.yml `install-command` (a workflow_call input from the consumer's own workflow).
- Ruff/vermin/mypy argv built from scan paths (timezone_honest.py:116, ignore_ratchet.py:96, api_floor.py:228): a path beginning `-` fails the existence check or cannot exist as a path. No `shell=True`, `os.system`, `eval`, `exec` or `pickle.load` in src (grep); `yaml.load` uses a SafeLoader subclass (two sites); import_module calls (corpus_drift:188, gate_population_canary:142, git_dependency_pins:420) import registered names or the caller's own declared modules.
- Mutation runner: `_pytest_env` (_mutation_runner.py:66-98) passes the whole `os.environ` to mutant test runs, so a token in the invoking job env is visible to consumer test code; that is the consumer's own tests (same trust as pytest) and the callers hold no secret.
- Token handling: GIT_CONFIG_* env transport and same-host redirect handler re-confirmed (51-R1, 51-R4); rollback limited to master (release.yml:176).
- setup_env.py: value rejected for newlines, plist and environment.d values escaped.
- Directory symlinks: `os.walk(followlinks=False)`, and `iter_files` drops file symlinks; the leak is only T1.

## Summary
| id | sev | title |
|----|-----|-------|
| 51-T1 | P3 | iter_files_pruned lists symlinked files that iter_files skips |
| 51-T2 | P3 | no size cap on files read by the scanning gates (260 MiB file: 780 MiB peak) |
| 51-T3 | P3 | CONSUMER_READ_TOKEN is a job-level env in corpus-drift's snapshot job |
| 51-T4 | P3 | constraints and uvx tool dependencies carry no hashes |
| 51-T5 | P3 | repo setting sha_pinning_required is false (pins are current) |
