# 52 self_hygiene: third pass on py_ci_shared (2026-11-11)

Scope: what the 2026-11-07 and 2026-11-10 rounds did not cover. Read-only. Method: grep for TODO/FIXME/XXX/HACK and debug prints in `src`; list every `noqa` / `type: ignore` / `pragma` marker; an AST scan for private helpers with identical or drifting bodies across `src`; `ruff check --ignore-noqa` on the F401/E731/PERF/N815 suppression sites to see which markers still suppress something; a read of the debt-marker scanners. Nothing was edited.

## Summary

| Sev | New | IDs |
|-----|-----|-----|
| P2 | 0 | |
| P3 | 3 | 52-F15, 52-F16, 52-F17 |

Already tracked: 5 (52-F11, 52-F12, 52-F13, 52-F14 from the 2026-11-10 file, plus the `_todo_markers` duplicate in TRACKER.md).

Checked and clean: no `breakpoint()`, `pdb`, or stray debug `print` in `src`. The `print(` calls found are all `sys.stderr` gate output. The suppression sample (`ci_health.py:239` E731, `code_audit/asymmetric_resource_guard.py:120` N815, `code_audit/__init__.py:534-544` F401 facade names, `dart_scanners.py:91` F401 multi-line import, `config_drift_check.py:119` and `_mutation_runner.py` PERF203) still suppresses a live hit when `--ignore-noqa` is removed; no stale noqa found in that sample. The AST scan found one identical-body helper pair beyond those in 52-F2 (`__init__` in `epsilon_padded_denominators.py:72` and `hash_fed_by_array_copy.py:64`), which is a trivial two-line constructor and not reported.

## Already tracked

- **52-F11** (P2, RESOLVED in TRACKER.md:65): `main(argv)` entry points.
- **52-F12** (P3, OPEN in TRACKER.md:66): unused-argument class not enforced (ARG001/ARG002).
- **52-F13** (P3, RESOLVED for F541, WON'T FIX for B006; TRACKER.md:67): test helper mutable default.
- **52-F14** (P3, WON'T FIX; TRACKER.md:68): three src modules near the 1000-LOC budget.
- **`_todo_markers` copy** (OPEN, TRACKER.md:966): `code_audit/_todo_markers.py:34` duplicates `meta_test_utils.py:401` `scan_todo_markers`, and the regex is duplicated at `meta_test_utils.py:390` and `code_audit/_todo_markers.py:23`. Already tracked; not re-filed.

## 52-S15 [P3] Eight private `_own_nodes` AST walkers re-implement a helper that already exists in `code_audit/_base.py`
- Evidence: `code_audit/_base.py:475` defines the shared walker (`_own_nodes(func)`, stops at nested `def`/`lambda`). Private copies with the same stack-walk shape: `atomic_write_staging.py:81` (stops at def/async def/class/lambda), `single_shot_timing_assertion.py:125` (stops at `_Scoped`), `optional_truthiness.py:121` (stops at def/lambda/class), `env_write_restore.py:68` (stops at `_SCOPE_NODES`), `code_audit/nondiscriminating_test.py:55`, `code_audit/non_neutral_except_fallback.py:183`, `code_audit/retry_loops.py:32` (stops at nested loops, a different rule). Grep counts 11 `def _own_nodes` across `src`.
- Failure scenario: a fix to the scope rule (the nested-function leak the `_base.py` docstring describes) lands in one copy and not the others; the scanners disagree on the same code.
- Proposed disposition: DEFERRED to the owner for classification of each copy. Scope rules differ on purpose in some (`retry_loops.py` stops at loops; `env_write_restore.py` also stops at classes). For the copies whose rule is "stop at nested function or lambda", replace with an import of the `_base` walker, or with the `_base` walker taking a scope tuple. Do not change scanner output without a targeted test run per gate.
- ast_checkable: True; fp_risk: medium (intentionally different scope rules).

## 52-S16 [P3] `_param_names` is duplicated with identical logic in two scanners
- Evidence: `db_transaction_completeness.py:120` and `gpu_timing_sync.py:179` both collect `posonlyargs`, `args`, `kwonlyargs` and `vararg` names of a function node and return them as a set. The `cuda_kernel_pointer_types.py:51` variant is a different function (it parses kernel signature text) and is not part of this finding.
- Failure scenario: a later change (for example adding `kwarg`) lands in one scanner only and the other misses parameters.
- Proposed disposition: move the AST version into the shared `code_audit/_base.py` next to the walker from 52-F15 and import it in both scanners. Classify the name before deleting either copy (repo rule).
- ast_checkable: True; fp_risk: low.

## 52-S17 [P3] `todo_hygiene.py` docstring names the wrong source of truth
- Evidence: `code_audit/todo_hygiene.py:26` says it is a "thin wrapper over `py_ci_shared.meta_test_utils.scan_todo_markers`", but line 8 imports `scan_todo_markers` and `ATTRIBUTION_RE` from `._todo_markers`, not from `meta_test_utils`.
- Failure scenario: a reader edits `meta_test_utils.scan_todo_markers` believing it drives the `todo_hygiene` gate, and the gate is not affected.
- Proposed disposition: RESOLVED on the next touch of the file: correct the docstring to name `code_audit/_todo_markers.py`. The duplication itself stays under the existing tracked row.
- ast_checkable: False (docstring text); fp_risk: none.

## Not examined in this pass
- Uncalled private functions: covered by the `uncalled_functions` gate and 52-F1 (RESOLVED); not re-measured here.
- Baseline and allow-list entries that refer to deleted code: the 2026-11-10 round found all 9 baseline files clean; not re-measured.
- TODO age: the `stale_comment_age` gate handles it; no `src` TODO outside tool-string literals was found by grep.
- Type-ignore staleness (`# type: ignore[...]`) was not verified, because it needs a per-line mypy run that was out of scope for a read-only pass.
