# 52 self_hygiene: second pass on py_ci_shared (2026-11-10)

Scope: what the 2026-11-07 round did not cover. The 2026-11-07 findings 52-F1 to 52-F10 are not repeated. Method: ruff with rule sets the repo does not enable (ARG001/ARG002, B006, F541), vulture at 60% confidence on src, a read of every tests/baselines/*.json, LOC sizes of src, and the budget and ignore sections of pyproject.toml. Nothing was edited.

## Summary

| Sev | Count | IDs |
|-----|-------|-----|
| P2 | 1 | 52-F11 |
| P3 | 3 | 52-F12, 52-F13, 52-F14 |

Clean (demonstrated): all 9 baseline files are `{}` or hold no entries (`complexity_ratchet`, `fail_open_handlers`, `function_length`, `loc_budget`, `uncalled_functions`, `vacuous_loop_assertions`, `self_api_hygiene`, `wallclock_test_waits` `accepted`, `gate_entry_contract` `entries`), so none hides a live offender and none is stale. The largest src file is 1000 lines, which is the limit itself, not over it. The `uncalled_functions` ignore list has one reason per name.

## 52-F11 [P2] Public `main(argv)` entry points accept `argv` and never read it
- Evidence: `ruff check src --select ARG001` reports `src/py_ci_shared/install_safe_hook.py:100` (`def main(argv: list[str] | None = None)`) and `src/py_ci_shared/setup_env.py:163` (same signature). Both bodies call `_hooks_dir()` / `_repo_root()` and ignore `argv`. A caller who runs `main(["--dry-run"])` gets the same action as `main()`.
- Failure scenario: a consumer or test passes arguments believing they are honoured; the call silently does the default write to the git hooks or the user's shell rc file. The setup path writes persistent user environment state, so this is the worst place for a silently ignored parameter.
- Proposed disposition: either remove the parameter (`def main() -> int`, and fix any caller passing argv) or parse it with argparse and act on it. Add the parameter-read check (see 52-F12) so the class cannot return. Fix in the code, not by baselining.
- ast_checkable: True; fp_risk: low (the `argv` names are the entry-point signature, which only a real read clears).

## 52-F12 [P3] Unused-argument class is not enforced anywhere in the repo
- Evidence: `configs/ruff-base.toml` selects E, W, F, I, N, UP, B, DTZ, A, T20, RUF, G, ISC, PERF, C90, NPY; ARG is not selected. Running ARG001/ARG002 explicitly on `src` gives 37 hits, 0 of them in `tests`. Non-hook hits include `import_cycles.py:160` (`_events(mod, ...)`, `mod` unused), `tool_config_env_vars_set_in_every_tool_job.py:203` (`use_git`, a public keyword), `phantom_code_references.py:588` and `:628` (`token`, `declared`), `source_text_claims.py:278` (`tainted`), `statement_columns_exist_in_ddl.py:125,129` (`line`), `module_reload_safety.py:227` (`tests_dir`), `stale_baseline_sweep.py:130` (`config`), `_mutation_sweep.py:174` (`relative`), `sql_function_privileges.py` (six `stmt` parameters on visitor methods, which may be framework-shaped and are the main false-positive source), and `offline_suite_without_credentials.py:253` (`call`). The 2026-11-07 round fixed two named knobs (52-F7) but found this class by vulture only, and vulture's 100% confidence covers only fully unused names.
- Failure scenario: each dead parameter is a promise the signature makes and the body breaks; the next reader adds a call that depends on it.
- Proposed disposition: add ARG001/ARG002 to a repo-local `[tool.ruff.lint] extend-select` for `src/` only, with per-file ignores for pytest hook names (`nextitem`, `exitstatus`, `config`, `call`, `style`, `reportinfo`) and for visitor methods that take `node`/`stmt` by protocol (`# noqa: ARG002` with a reason, or a per-file ignore for `sql_function_privileges.py` and `code_audit/missed_await.py` and `code_audit/return_annotation.py`). Then remove the true dead parameters after grep over src, tests and workflows. The owner rule to classify before deleting applies to each name. Disposition for the 37 is: fix the ones that are real, ignore with a reason the rest.
- ast_checkable: True; fp_risk: medium (visitor and pytest-hook signatures).

## 52-F13 [P3] Test-suite lint tolerance hides a mutable default in a test helper
- Evidence: `ruff check tests --select B006,B008` reports `tests/test_meta_test_utils_capture_signature.py:114:17` (B006). The shared config ignores B006 by design ("audited case-by-case per call site"), and this repo has no per-site audit record for it. F541 hits in `tests/test_sql_verifier_coverage.py:56` and `tests/test_sql_verify_fragment_matrix.py:131` are the same kind of tolerated style issue; the shared config ignores F541 too.
- Failure scenario: a helper whose default list is mutated by one test leaks state into the next; the test that prints the signature can then pass for the wrong reason.
- Proposed disposition: change the default to `None` (or a tuple) in the helper at line 114 and read it as empty when `None`. The two F541 f-strings are trivially fixed with `ruff check --fix` on those two lines only, not repo-wide. Do not reformat other files (repo rule: no repo-wide reformat without approval).
- ast_checkable: True; fp_risk: low.

## 52-F14 [P3] Three src modules sit at or near the 1000-LOC budget
- Evidence: `wc -l` on src: `adoption_matrix.py` 1000 (the `DEFAULT_LOC_LIMIT = 1000` in `loc_budget.py:80` passes only because the check is strictly over), `statement_columns_exist_in_ddl.py` 994, `mutation_teeth.py` 984. `adoption_matrix.py` defines 62 functions in one file.
- Failure scenario: the next line added to `adoption_matrix.py` fails the budget on an unrelated change, which tempts a rushed split. The owner rule is to split above 1k LOC, so the file is one line from a forced split.
- Proposed disposition: split `adoption_matrix.py` now along its natural seam (the matrix builders vs the rendering), using the freevar_analysis approach the owner rule names. Leave the other two as watch items; record in the finding that their next growth triggers the split.
- ast_checkable: True; fp_risk: low.

## Not examined in this pass
- Duplicated literals and near-duplicate blocks outside the functions covered by 52-F2 (the drift gate was not run on the whole tree in this pass).
- Public-API docstring and `__all__` completeness after the 2026-11-07 round (52-F6 was resolved there; not re-measured).
- Gate enablement count (52-F3 remains DEFERRED in the tracker; no change measured here).
