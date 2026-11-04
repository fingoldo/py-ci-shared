# 34_ci_release_consumer_surface

Round 2026-11-04. Every finding is OPEN until its Disposition line says otherwise.

## 34-F1 (P1) Ranked adoption matrix: gates adopted by 4-7 of 8 Python consumers but missing in specific repos

- Evidence: Scratch adopt2.py: git grep of py_ci_shared / [tool.py_ci_shared] enable lists over *.py/*.toml/*.yml of the 13 configs/consumers.toml repos vs 219 registry.toml entries. Python consumers = autopsia_master, mlframe, pyutilz, social, dash_app_core, llm_bench, glossum_backend_scripts, algopacksimple. 7/8: code_audit_meta (missing autopsia), swallowed_exceptions (algopacksimple), value_bearing_asserts (dash_app_core). 6/8: check_then_act_outside_lock, fail_open_handlers, git_dependency_pins, loc_budget, lock_held_across_io, no_source_text_tests, sentinel_or_fallback, shared_mutable_placeholder (missing autopsia+algopacksimple); atomic_write_staging, import_cycles, no_xfail_to_defer (missing social+algopacksimple); gate_integrity, module_reload_safety, pytest_markers (missing dash_app_core+algopacksimple); phantom_markdown_links, repo_hygiene, stale_comment_age (missing llm_bench+algopacksimple). 5/8: ci_workflow_gate (missing autopsia,dash_app_core,algopacksimple), hash_key_determinism and stale_source_citations (autopsia,social,algopacksimple), clock_day_boundary (autopsia,pyutilz,algopacksimple), entry_points_resolvable, fail_message_quality, phantom_code_references, source_text_claims. 4/8 with widest holes: ci_workflow_timeout_gate and ci_test_dir_reachability (missing autopsia,dash_app_core,llm_bench,algopacksimple), audit_wave_filenames, hardcoded_token_ceilings, machine_specific_paths, pickle_state_completeness, readme_env_var_parity, rollback_then_continue, uncalled_functions. Gates per consumer: mlframe 125, social 101, glossum 67, pyutilz 60, llm_bench 44, dash_app_core 34, autopsia 32, algopacksimple 10, noema 8, flutter_uptime 3, claude-usage-notifier 3.
- Failure scenario: A gate used by 6 of 8 consumers has a proven false-positive profile, yet the laggards (algopacksimple with 10 gates, dash_app_core, autopsia) leave those defect classes unguarded. dash_app_core and llm_bench carry the identical 9-gate enable list, so a new gate never reaches them unless hand-added.
- Proposed fix: Ship a baseline-python bundle (the 6/8+ gates) in the scaffolder; make adoption_matrix print per repo the gates missing from that bundle and put the ranked list in the consumer-pins job summary.
- AST-checkable: False; FP risk: med
- Disposition: RESOLVED. adoption_matrix counts non-text evidence now: [tool.py_ci_shared] enable lists and gate tables (already), plus pre-commit hook ids of the py-ci-shared repo block (id: repo-hygiene) and gate names listed as strings in .py files (a list/tuple/set holding at least two module names, e.g. tests/test_meta parametrization). New ranked_gaps/is_python_repo and a Ranked gaps section in the report. The ranked matrix over the 13 consumers is committed as audits/2026-11-04/34_adoption_matrix.md (generated from local clones, read-only); PLAN step 8 uses its Ranked gaps and per-repo lists. Not done: the baseline-python scaffolder bundle (the ranking is the input for choosing it; a bundle that enables 20+ gates in a consumer is a step-8 decision per repo, not a default).

## 34-F2 (P1) 29 registered modules are referenced by no consumer (enabled nowhere)

- Evidence: adopt2.py: adoption_matrix, baseline_trend, checkpoint_isolation, ci_health, closed_audit_rounds, commit_metadata, config_drift_check, constant_relations, consumer_import_census, corpus_drift, emitted_vocabulary_literals, external_fact_tables, free_text_in_get_route, hook_attestation, install_safe_hook, local_copy_report, loop_invariant_cast, offline_suite_without_credentials, predictable_tmp_path, protocol_attributes, required_check_contexts, resource_leak_checks, resource_leak_guard, safe_precommit, secret_assertion_operands, sibling_floor_skew, stub_signature_guard, vulture_warn, worktree_hygiene. Distribution of consumers per module: 0:29, 1:72, 2:43, 3:25, 4:10, 5:12, 6:17, 7:3, 8:6, 9:1, 11:1 (72 modules have exactly one adopter). Some are py-ci-shared self-jobs or user-level hooks (legitimately invisible).
- Failure scenario: Real gates among the 29 (predictable_tmp_path, loop_invariant_cast, secret_assertion_operands, stub_signature_guard, resource_leak_checks/guard, protocol_attributes, free_text_in_get_route, checkpoint_isolation, offline_suite_without_credentials, sibling_floor_skew, required_check_contexts, closed_audit_rounds, commit_metadata, baseline_trend, constant_relations) are maintained and mutation-tested but protect nothing.
- Proposed fix: Add a registry marker for self-jobs/user hooks; trial-enable the real gates in mlframe/pyutilz (already corpus-scanned) or retire them; add a meta-test flagging a kind=gate with zero adopters for N releases.
- AST-checkable: False; FP risk: med
- Disposition: RESOLVED for this repo, rest goes to step 8. The report now has an Enabled nowhere list; the committed matrix classifies the 27 unreferenced modules as self jobs, user-level hooks, plumbing or real gates with no adopter (the last are the trial-enable-or-retire candidates for step 8). A registry marker for self-jobs was not added: the classification lives in the generated matrix, where it can change without a registry edit. A meta-test failing on a zero-adopter gate needs the consumer clones and cannot run in this repo's CI; the daily consumer-pins job prints the list instead.

## 34-F3 (P2) config_drift_check hard-codes two consumers instead of reading configs/consumers.toml

- Evidence: src/py_ci_shared/config_drift_check.py:25-28 CONSUMERS = {mlframe, pyutilz} with raw master URLs; configs/consumers.toml lists 13 repos with per-repo branches (main/staging differ).
- Failure scenario: Ruff/mypy config divergence in social, autopsia, glossum, llm_bench, algopacksimple is never reported by the weekly job.
- Proposed fix: Use _consumers.load_consumers, build the raw URL from repo+branch, use the token for private repos, warn when skipped.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. config_drift_check.consumer_urls reads configs/consumers.toml (every consumer, each on its own branch; private repos through the contents API with CONSUMER_READ_TOKEN, passed to the step in config-drift-check.yml; skipped private repos are warned about by name). A consumer without the [tool.ruff]/[tool.mypy] table no longer counts as divergence. Fallback to the old two repos only when the file is not beside the package. tests/test_config_drift_consumers.py.

## 34-F4 (P2) consumer-pins daily job silently degrades to public repos when CONSUMER_READ_TOKEN is absent

- Evidence: .github/workflows/consumer-pins.yml:42-47 only emits ::warning:: and continues; configs/consumers.toml: 9 of 13 consumers private=true. _consumers.py docstring: 'the job stays green'. corpus-drift uses the same checkout.
- Failure scenario: Token expires: pin skew in autopsia/social/noema/etc. never surfaces and the run is green.
- Proposed fix: Exit 1 when any private consumer was skipped, unless an explicit allow-skipped-private input is set.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED. _consumers checkout takes --fail-on-skipped-private (exit 1, names the skipped repos); consumer-pins.yml passes it unless a manual run sets the new allow-skipped-private input. Default CLI behaviour is unchanged (corpus-drift keeps the warning). tests/test_consumers_fail_on_skipped.py. Effect: the first scheduled run without a working CONSUMER_READ_TOKEN is red, which is the point.

## 34-F5 (P2) Pin skew across consumers: six different py-ci-shared SHAs, flutter_app_core at v1.22.8

- Evidence: git grep in consumers: autopsia 0f2ac5c9 (v1.23.3+9), mlframe 2ab4fdf4 (not resolvable in local tag history), pyutilz/llm_bench 670b80cb (v1.23.7), social c8b37d39 (v1.23.7~1), glossum/algopacksimple/claude-usage-notifier 905de1ad (v1.23.5), flutter_app_core c269c9a7 (v1.22.8), polyvocab_app c7988d79 (unresolved). Latest tag v1.23.7.
- Failure scenario: Consumers run different ruff/black/lint-tool pins of the supposedly single-source tool_versions.py; flutter_app_core is 5 releases behind, beyond --allow-behind 2, visible only if private checkout works (see F4).
- Proposed fix: Auto-PR pin bumps on release; list behind repos in the summary even under tolerance.
- AST-checkable: False; FP risk: med
- Disposition: DEFERRED. The summary already shows releases behind per repo whatever the tolerance (adoption_matrix Summary column; the consumer-pins job writes it to the job summary). Auto-PRs bumping pins on release need a token with write access to every consumer repo and touch the consumers, which this round must not; waiting on the owner for that token (step 8 bumps the pins by hand meanwhile).

## 34-F6 (P2) Reusable lint workflows adopted unevenly; dash_app_core uses none

- Evidence: grep consumers' .github: black-filtered (6 repos), lint-blocking (8), ruff-blocking (7), lint-advisory (6), mypy-full (autopsia, llm_bench, mlframe), mypy-beachhead (glossum only), docs (mlframe, pyutilz). dash_app_core has no reusable workflow refs; social lacks black-filtered and lint-advisory; llm_bench lacks ruff-blocking and lint-advisory; pyutilz, algopacksimple, social, glossum have no mypy-full.
- Failure scenario: Type and ruff regressions in those repos go unchecked in CI although the workflow exists.
- Proposed fix: adoption_matrix reports reusable-workflow coverage per Python repo; scaffolder emits the stubs.
- AST-checkable: False; FP risk: med
- Disposition: RESOLVED. The report lists, per Python consumer, the reusable workflows it does not call (Reusable workflows a Python consumer does not call), and the committed matrix records the current holes (dash_app_core none; social lacks black-filtered and lint-advisory; llm_bench lacks ruff-blocking and lint-advisory; mypy-full missing in pyutilz, algopacksimple, social, glossum). The scaffolder stub emission is not done: which repos should adopt which workflow is step 8.

## 34-F7 (P2) No reusable workflow runs the gate suite; gate execution depends on each consumer's own tests

- Evidence: .github/workflows has 7 workflow_call files, all linters. cli.py run-all exists and is driven by [tool.py_ci_shared] enable, but only dash_app_core and llm_bench use that table (identical 9-gate list); the other consumers wire gates by hand-written tests.
- Failure scenario: A consumer CI whose path filter or shards omit the meta tests silently never runs gates; ci_test_dir_reachability and ci_workflow_timeout_gate are themselves adopted by only 4 of 8.
- Proposed fix: Add a reusable gates.yml calling py-ci-shared run-all (ConfigError already fails when none enabled) and flag Python repos without a gate runner in adoption_matrix.
- AST-checkable: False; FP risk: med
- Disposition: PARTLY RESOLVED, rest DEFERRED. adoption_matrix now flags Python consumers without a [tool.py_ci_shared] enable list (six of eight in the committed matrix). A reusable gates.yml is deferred: a new reusable workflow must also join release_guard's default-ref bump, the README list and five workflow meta-tests, and its value depends on step 8 choosing the enable lists first.

## 34-F8 (P3) cli tool subcommand coerces non-int returns to 0; main() maps any KeyError to exit 2

- Evidence: src/py_ci_shared/cli.py _cmd_tool: result if isinstance(result, int) else 0; main(): except (ConfigError, KeyError) wraps _cmd_run/_cmd_tool and prints only the key.
- Failure scenario: A gate main returning a findings list or str exits 0; a KeyError bug inside a gate shows as a usage error without traceback.
- Proposed fix: Reject non-int/None returns; narrow the KeyError catch to registry.by_name.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. cli tool: None or int is the exit code, anything else (a findings list, a str) is a ConfigError with exit 2 and a message saying so; the KeyError catch in main() is gone and only the two lookups that mean 'unknown name' (registry.by_name, config_path) convert to ConfigError, so a KeyError inside a gate keeps its traceback. tests/test_cli_tool_exit_codes.py.

## 34-F9 (P3) Reusable workflows define no concurrency group

- Evidence: grep concurrency: only ci-health, config-drift-check, consumer-pins, corpus-drift, release, self-ci define one; the workflow_call linters leave it to callers (callers not individually audited).
- Failure scenario: Superseded pushes in a consumer burn minutes if its caller lacks a group.
- Proposed fix: Extend ci_workflow_gate to require concurrency in caller workflows invoking shared ones.
- AST-checkable: True; FP risk: med
- Disposition: DEFERRED. Requiring concurrency in caller workflows via ci_workflow_gate makes green consumers red (behaviour change, CHANGELOG 'Behaviour change' rule) and needs a per-consumer audit of which callers lack it; it belongs to step 8, where each consumer is run to green anyway.

## 34-F10 (P3) lint-blocking skips interrogate, deptry and zizmor silently when inputs are empty/false

- Evidence: .github/workflows/lint-blocking.yml inputs: interrogate-source-path default empty (skips entirely), deptry-install-command default empty (skips), run-zizmor default false.
- Failure scenario: A repo calling lint-blocking counts as adopted while 3 of 7 tools never run, with no notice in the log.
- Proposed fix: Emit a ::notice:: and summary line per skipped tool; adoption_matrix reports each caller's with: inputs.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. lint-blocking.yml has a first step 'Report tools skipped by their inputs': a ::notice:: and a job-summary line for each of interrogate, deptry and zizmor that the caller's inputs skip; never fails. Reporting each caller's with: inputs in adoption_matrix is not done (the matrix has no per-job input reader). tests/test_lint_blocking_skip_notice.py, and test_reusable_workflows updated for the new root-level step.

## 34-F11 (P3) OS/Python matrix hole: Windows and macOS legs run 3.11 only; all other workflows 3.11 only

- Evidence: self-ci.yml matrix: ubuntu-24.04 3.9-3.13, windows-latest 3.11, macos-latest 3.11; every other workflow python-version 3.11.
- Failure scenario: A 3.9-or-3.13 break that depends on Windows path handling (tomli fallback path, subprocess quoting) ships.
- Proposed fix: Add windows 3.9 and 3.13 legs, non-blocking first.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED (non-blocking first, as proposed). self-ci.yml gains windows-latest legs for Python 3.9 and 3.13 with preview: true (continue-on-error). They become blocking after a week of green runs; that decision needs CI history this run does not have.

## 34-F12 (P3) adoption measurement may under-count: autopsia shows 32 gates with no [tool.py_ci_shared] table despite 16k files

- Evidence: adopt2.py per-consumer counts; autopsia_master pyproject has no [tool.py_ci_shared]; matching is on the py_ci_shared.<module> text, so parametrized gate-name lists or py-ci-shared run <name> calls are missed.
- Failure scenario: The enabled-nowhere list (F2) and the ranked matrix (F1) may overstate gaps for repos with bespoke harnesses.
- Proposed fix: Make adoption_matrix also match quoted gate names in parametrized lists and CLI invocations, then re-verify against autopsia.
- AST-checkable: True; FP risk: high
- Disposition: RESOLVED. The two readers named in the finding exist now (listed gate names in .py files, pre-commit hook ids); py-ci-shared run <name> CLI calls were already matched. Re-run against the real clones: counts moved mlframe 125 to 128, social 101 to 109, pyutilz 60 to 61; autopsia stays at 32 (it has no parametrized lists and no py-ci-shared pre-commit repo block), so its gap list stands. The ranked matrix records the new counts.
