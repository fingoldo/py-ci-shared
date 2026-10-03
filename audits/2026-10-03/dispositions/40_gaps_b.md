# Dispositions: 40_gaps.md (G-6 .. G-14)

G-1 .. G-5 are dispositioned separately. Every gate below was run over the current default branch of the ten consumer
checkouts (local worktrees of mlframe, pyutilz, llm_bench, glossum_backend_scripts, noema_app at origin HEAD on
2026-10-03; shallow clones of autopsia, social, dash_app_core, algopacksimple, claude-usage-notifier) and, where git
history is the subject, over every commit since 2026-09-01 of the five local repos. "TP" below means an instance of the
defect class the gate names, confirmed by reading the line.

### G-6

**Disposition:** RESOLVED -- new gate + CLI `consumer_import_census` (`src/py_ci_shared/consumer_import_census.py`):
`find_consumer_import_breaks` at line 153, `assert_consumer_imports_resolve`, `python -m
py_ci_shared.consumer_import_census --library . --package <pkg> --repos-file|--consumer ... [--since-ref <tag>]`
(`--since-ref` extracts the reference with `git archive` and reports only regressions). Resolution goes through
`unresolved_imports.ModuleIndex`; the library's alias map is read from module-level dict literals of
`<package>/__init__.py` whose values are dotted modules inside the package (`library_alias_map`, line 72), so pyutilz's
`_MODULE_ALIASES` is honoured; module `__getattr__`, star imports and consumer `try/except ImportError` guards are not
judged. Validation: seed replayed (pyutilz `89eb1f6^` vs glossum at `5a05cefe`): 1/1, exactly
`from pyutilz.llm.claude_code_provider import _RATE_LIMIT_PATTERN`. pyutilz HEAD vs the 9 other consumers: 1 finding,
TP (`social/upwork/upwork_zendesk.py:28 from pyutilz.python import imitate_delay`: `pyutilz.python` never existed in
pyutilz history; the module cannot import). Without the alias map the same run reports 102 false "module gone" lines
on mlframe alone; with it, 0. mlframe, py_ci_shared and llm_bench as libraries vs all others: 0. Tests:
`tests/test_consumer_import_census.py` (22, including `test_with_a_reference_only_regressions_are_reported`,
`test_cli_since_ref_reports_only_what_broke_after_the_ref`); canary `tests/canary/consumer_import_census/`.

### G-7

**Disposition:** RESOLVED -- new module `src/py_ci_shared/resource_leak_checks.py` (`take_state`, `state_leaks` line
152, `restore_state`) with the checks `streams` (identity of `sys.stdout/stderr/stdin`; a wrapper of the old stream
that appears while a library is first imported, colorama `init()`, is excused; a replaced stream is not), `cwd`,
`sys_path` and `warnings` (additions during a library's first import excused, removals and reorders never). Wired into
`resource_leak_guard.py` with a 7-line diff on top of the core agent's K-5 version: `ALL_CHECKS` gains the four names,
the before-setup snapshot also takes `take_state`, teardown adds `state_leaks` and restores with `restore_state`
(`resource_leak_guard.py:322`). Measured behaviour: under pytest's default `--capture=fd` a stream swapped in the call
phase is already put back by pytest's own capture at the end of the phase, so the check fires under `-s` (mlframe's
setting, which is how the colorama leak survived) and for swaps in fixture finalizers in every capture mode; both are
pinned by tests. Adoption: `adoption_matrix._config_modules` now counts `[tool.py_ci_shared] resource_leak_guard = true`
as using the plugin (it only saw `enable` and gate tables; `adoption_matrix.py:407`). Tests:
`tests/test_resource_leak_checks.py` (14: direct API, inner pytest sessions through the guard with `-s` and
`--capture=fd`, restoring tools and capture fixtures as negative controls, allowlist, narrowed checks, the adoption
key). Not done here: a `local_copy_report` signature for mlframe's hand-rolled stream guard. WON'T FIX in this round:
that guard lives in `tests/conftest.py`, and `local_copy_report.find_local_copies` only judges `test_*.py` files
(`local_copy_report.py`, the `startswith("test_")` filter), so a signature alone can never match it; widening the
scanner's file set is a change to another module's contract and is left to its owner.

### G-8

**Disposition:** RESOLVED -- new gate `vendored_internal_imports` (`src/py_ci_shared/vendored_internal_imports.py`,
`vendored_part` line 58, `assert_no_vendored_internal_imports`). Flags `import`/`from`/`importlib.import_module` of a
path whose second or later segment is `externals|_externals|_vendor|vendor|_vendored|vendored|extern`, plus
`requests.packages`/`urllib3.packages`; the repo's own packages (root or `src/`) and relative imports are first-party;
the standalone-first fallback (vendored import inside an `except ImportError` whose `try` imports the same standalone
name) is accepted without a marker; anything else needs `# vendored-ok: <reason>`. Validation over the ten consumers:
mlframe 7, all TP (`joblib.externals.loky` x6 in `_step_pairmi.py:61`, `training/__init__.py:113`,
`_tiny_rerank_process.py:142` and three tests, plus `test_rerank_worker_processes.py:125`, a deliberate regression test
that needs the marker); the fixed cloudpickle line `_tiny_rerank_process.py:37` is the accepted fallback shape; mlframe's
own `filters._vendored.infonet` is first-party and not reported. The other nine repos and py-ci-shared: 0. Tests:
`tests/test_vendored_internal_imports.py` (25); canary `tests/canary/vendored_internal_imports/`.

### G-9

**Disposition:** RESOLVED -- new gate `external_fact_tables` (`src/py_ci_shared/external_fact_tables.py`,
`find_external_fact_table_problems` line 198, `find_expiring_fact_tables`, `find_undeclared_fact_tables`,
`assert_external_fact_tables_current`). Declaration convention chosen as least invasive: the tables are listed once in
the standard gate table, `[tool.py_ci_shared.gates.external_fact_tables] tables = {"src/pkg/mod.py:_PRICING" = 45}`
(or the same mapping from a meta test), so the library modules only need the citation comment most of pyutilz's
tables already carry; a renamed or deleted table is a finding. Citation = the contiguous comment block above the
assignment or comments inside the literal before its first entry, holding an `http(s)://` URL or `domain.tld/path`
and an ISO date (newest date wins). Findings: gone, uncited, undated, future-dated, older than its limit; expiring
within `warn_days` are printed, never failed (no `warnings.warn`, so `-W error` cannot turn it red). Validation,
declaring every table the name advisory finds: pyutilz 9 candidates, 3 findings, all TP (undated
`openai_provider.py:70 _CACHE_HIT_COST` ("from the same pricing page"), `openai_provider.py:169 _CONTEXT_WINDOW`,
`xai_provider.py:80 _CACHE_HIT_COST`); the other 6 pass in their current wording, including
`docs.x.ai/docs/models, 2026-09-26` inside the literal. glossum `glossum/llm/models.py:585 PROVIDER_PRICING` undated
(TP); social `llm_pricing.py:53` dated without a URL (TP). The NAME advisory alone is imprecise, as the report
predicted: autopsia `DEFAULT_MISS_COST/DEFAULT_WORK_COST` and glossum `PHASE0_MAX_OUTPUT_REDUCE_BY_STAGE` are internal
(3 of 13 candidates), which is why it is only an advisory and the gate works from the declaration. Tests:
`tests/test_external_fact_tables.py` (26); canary `tests/canary/external_fact_tables/`.

### G-10

**Disposition:** RESOLVED -- new CLI `required_check_contexts` (`src/py_ci_shared/required_check_contexts.py`,
`check_repo` line 87), a separate module rather than an edit to `ci_health` (reuses its `default_api`/`ApiError`).
Required contexts (`contexts` and `checks[].context`) are compared with the check-run names and status contexts of the
5 newest commits of the CI branch: one commit is not enough, the first live run reported mlframe's
`CI required checks` missing because CI on the head commit was still queued and had not created that job. 404 =
`unprotected`; 403 (including gh's "Upgrade to GitHub Pro") = `unreadable` with a `::warning::` line, exit 1 under
`--strict`. Live run 2026-10-03 over `configs/consumers.toml`: ok 2 (mlframe, pyutilz: 3 required each, all produced),
unprotected 2 (llm_bench, claude-usage-notifier), unreadable 9, missing 0. Tests: `tests/test_required_check_contexts.py`
(7, fake API: the `mypy-full / mypy-full` seed, older-commit and status contexts, the four error classes, CLI exits).

### G-11

**Disposition:** RESOLVED -- new gate + autofix `workflow_runner_labels` (`src/py_ci_shared/workflow_runner_labels.py`,
`find_workflow_runner_label_problems` line 83, `fix_workflow_runner_labels`, `python -m
py_ci_shared.workflow_runner_labels [--fix] [--label ubuntu=ubuntu-22.04] <repo>`), a separate module rather than an
edit to `ci_workflow_paths`. Rules: `ubuntu|windows|macos-latest` anywhere in workflow YAML outside comments unless the
line has `# moving-label-ok: <reason>`; and a repo with pinned non-local `uses:` and no Dependabot `github-actions`
entry or Renovate config. Counts today: mlframe 45, pyutilz 22, social 11, glossum 10, llm_bench 7, algopacksimple 5,
dash_app_core 3, claude-usage-notifier 2, autopsia 1 (106 occurrences, matrix values and `COVERAGE_OS` strings
included, so above the report's 82 label count), and the update-channel finding in 7 repos (all but mlframe, pyutilz
and noema_app, which has no workflows); py-ci-shared itself has 2 (`self-ci.yml:47,49`) and no channel. `--fix` was
run on copies of the llm_bench, mlframe, pyutilz, glossum and social `.github` trees: only the label lines change, CRLF
is kept, every YAML still parses, and a new `dependabot.yml` is written (an existing one is extended only when
`updates:` is its last top-level key). The pins (`ubuntu-24.04`, `windows-2025`, `macos-15`) are what `-latest`
pointed at when written and are overridable. Checking each action's `runs.using` stays rejected (report, rejected 2).
Tests: `tests/test_workflow_runner_labels.py` (7).

### G-12

**Disposition:** RESOLVED -- the precondition landed while this round ran (N-21 on origin/master `4f2ca30`:
`StaleCommentAdvisory` emitted under its own `always` filter, so `-W error` cannot fail it). `assert_no_stale_todos`
now defaults to `warn_days=7` (`src/py_ci_shared/stale_comment_age.py`, signature of `assert_no_stale_todos`; module and
function docstrings say how to turn it off with `warn_days=0`). Consumers need no change: the warning is printed in
pytest's warnings summary and returned, never raised. Test: `tests/test_stale_comment_age.py::TestEarlyWarning::
test_the_early_warning_is_on_by_default_and_warn_days_zero_is_silent` (re-framed from
`test_default_warn_days_zero_is_silent`, which pinned the old default; 62/62 in that file pass). Not done: the
`ci_health`-style summary line and the precision sample of the commented-out-code classifier on consumer baselines;
the warning cannot fail a build, so a noisy classifier costs reading time, not red runs, and the sample is left to the
classifier's owner.

### G-13

**Disposition:** RESOLVED -- new gate + CLI `commit_metadata` (`src/py_ci_shared/commit_metadata.py`, `check_message`
line 111, `find_commit_metadata_problems`, `assert_commit_metadata`, `python -m py_ci_shared.commit_metadata
--message-file <f> | --range A..B [--forbid Key[: glob]]`). Rules: `bom-subject` (U+FEFF anywhere in the subject),
`invisible-subject` (first character a control, format, private-use or whitespace character), `forbidden-trailer`
(case-insensitive key, optional value glob). The list defaults to EMPTY; it is read from
`[tool.py_ci_shared.gates.commit_metadata] forbidden_trailers` (the plugin's own table, so both CLI and gate read one
place). Merges and `*[bot]` authors are skipped; the bot suffix is matched literally (a first draft used `fnmatch`,
where `[bot]` is a character class that skipped every author ending in b, o or t, and found 91 of 100 glossum trailers;
pinned by `test_bots_and_merges_are_skipped_but_bot_suffix_is_literal`). Validation over 2026-09-01..HEAD of the five
local repos (1504 non-merge commits): BOM 1/1, pyutilz `086771d`, 0 false positives; with `--forbid Co-Authored-By`:
mlframe 251, pyutilz 44, llm_bench 3, glossum 100, noema_app 20, equal to git's own
`%(trailers:key=Co-authored-by)` count minus the 2 dependabot commits in pyutilz. Tests: `tests/test_commit_metadata.py`
(20, on real throwaway repositories).

### G-14

**Disposition:** RESOLVED -- new gate + CLI `closed_audit_rounds` (`src/py_ci_shared/closed_audit_rounds.py`,
`find_closed_round_edits` line 107, `assert_closed_audit_rounds_append_only`, `python -m py_ci_shared.closed_audit_rounds
--base <ref>`), a separate module rather than an `audit_round_format` option. Files under `audits/implemented/` at the
merge base may only gain lines; deleted/rewritten lines (per hunk), deleted files and files moved out are findings;
moving an open round in is not; line-ending-only changes are ignored; a commit touching the file with an
`Audit-Edit: <reason>` trailer exempts it. Prototype measurement per commit over 2026-09-01..HEAD of py-ci-shared,
pyutilz, glossum and noema_app (29 commits touching a closed round): strict append-only flagged 9, all disposition or
tracker-status updates (e.g. pyutilz `804ee77`, `824efc8` revising `**Disposition**` lines, glossum `bc71060`
restructuring TRACKER tables). That is a false-positive rate the proposal did not anticipate, so `Disposition` lines and
rows of `TRACKER*.md` tables are mutable by design; a table row anywhere else stays frozen. After that: 1 flagged,
glossum `928b2795a9` rewriting a closed report's evidence sentence (the class itself, legitimately edited, which under
the gate needs the trailer). Tests: `tests/test_closed_audit_rounds.py` (9, real repositories).

## Shared files touched (minimal, sorted insertions)

`src/py_ci_shared/registry.toml` (8 entries, `since = "1.20.0"`, version not bumped), `README.md` (catalogue rows,
regenerated), `tests/test_gate_teeth.py` (3 `CANARIES`, 4 `EXEMPT`, `_FACT_DAY`), `src/py_ci_shared/corpus_drift.py`
(`NON_CORPUS`: closed_audit_rounds, commit_metadata, consumer_import_census, external_fact_tables; the vendored and
workflow finders bind from a repo root), `src/py_ci_shared/resource_leak_guard.py` (wiring),
`src/py_ci_shared/adoption_matrix.py` (2 lines), `src/py_ci_shared/stale_comment_age.py` (default `warn_days=7`, docstrings) and
`tests/test_stale_comment_age.py` (one re-framed test), `pyproject.toml` (`pytest_markers` exclude for the leak-check test
file, whose inner sessions carry the plugin's marker), `CHANGELOG.md` (one Unreleased entry).
