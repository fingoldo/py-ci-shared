# Option B: detectors move into py-ci-shared

Decided 2026-10-21. Target: py-ci-shared owns the detector engine, gates, ratchets and tracker; pyutilz depends
on py-ci-shared only lazily and only in dev; py-ci-shared imports nothing from pyutilz.

## Facts that shape the order

- pyutilz `dev/code_audit`: 102 detector modules, about 20k lines, 102 test files under `tests/code_audit`.
- Two `Finding` types: pyutilz `_base.Finding` (check, severity, file, line, snippet, detail) and py-ci-shared
  `_core.findings.Finding` (path, line, rule, message, key). Consumers read the pyutilz fields, so the first move keeps
  the pyutilz shape and the unification is a later, separate step.
- py-ci-shared imports pyutilz at runtime in two places: `code_audit_meta.py` (`Finding`, `registry`) and
  `deferred_drift.py` (`count_user_deferred_entries`, from `pyutilz.dev.meta_test_utils`).
- `count_user_deferred_entries` drags `meta_test_utils` helpers with it (`_rglob_test_files`,
  `_count_deferred_in_file`, `_DEFERRED_PREFIXES`, a logger).
- Consumers pin pyutilz for `code_audit`; each switch to `py_ci_shared.code_audit` is a pin change and a baseline refresh.

## Stages

1. **Move the deferred-entry counter and its helpers** into py-ci-shared (`py_ci_shared.deferred_entries`). Point
   `deferred_drift.py` at it. pyutilz keeps `meta_test_utils.count_user_deferred_entries` as a lazy re-export.
   Verifiable on its own: py-ci-shared's `deferred_drift` tests and pyutilz's `test_deferred_drift.py`.
2. **Move `_base.py` and `registry.py`** into `py_ci_shared.code_audit`, keeping the pyutilz `Finding` shape. Detectors
   use relative imports (`from ._base import ...`), so they move without edits.
3. **Move the detectors and their tests** in batches of about 20, git mv, with the test imports rewritten. Each batch
   runs its own tests in both repositories before the next.
4. **Unify `Finding`** into one type. This is a consumer-visible change: CHANGELOG `Behaviour change` entry.
5. **pyutilz shim**: `pyutilz/dev/code_audit/__init__.py` re-exports the registry API from py-ci-shared. Submodule paths
   stop resolving at the end of the transition; listed as a Behaviour change.
6. **Consumers** move to `py_ci_shared.code_audit`, one repository at a time, each with a baseline refresh against the
   new pin. Order: the three repositories added today (no pin debt), then mlframe, dash_app_core, glossum_backend_scripts,
   autopsia, llm_bench, algopacksimple.

## Status

- Stage 1 done and committed: `py_ci_shared.deferred_entries` (e969912), pyutilz wrapper (e289f87, in pyutilz). The
  pyutilz pin moves to the py-ci-shared commit that carries `deferred_entries`, and that commit is not yet pushed:
  origin's CI for 6da2436 has been queued for hours. Counts over six trees are unchanged by the move.
- Stage 2 not started. `registry` runs scanners in a process pool and requires picklable functions, so a move
  changes module paths that the parallel path depends on; it needs its own run of `run_all_checked` before and after.
