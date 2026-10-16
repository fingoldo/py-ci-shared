# import_time_global_side_effects (new opt-in gate, 1.22.13)

## The bug class

Glossum backend, 2026-10-16: `scripts/lang_onboarding/test_unimorph_lemma_grammar.py` ran `warnings.filterwarnings("ignore")`,
`logging.disable(logging.WARNING)` and `sys.stdout = io.TextIOWrapper(...)` at module level. A test imported it for
`extract_verbecc`, so collection ran the statements in every pytest/xdist worker: INFO/WARNING records vanished process-wide,
log-capture tests passed alone and failed in full parallel runs (78 unit tests red on main, a day to find). Fixed in
glossum commit 4fb82791 by moving them into `_configure_script_process()`, called from the `__main__` block.
The pre-fix file (`4fb82791^`) is the `violation` and `bom` canary, the fixed file the `clean` canary
(`tests/canary/import_time_global_side_effects/`). The pre-fix file yields `blanket-warnings-filter`, `logging-disable`
and `sys.stdout-assign`; the fixed one yields nothing (`test_the_original_glossum_bug_is_reported_in_all_three_shapes_and_the_fix_is_clean`).

## The rule

A statement that runs on import (module top level, or inside a module-level `if`/`try`/`with`/`for`/`while`/class body;
not inside a function or lambda, and not in the body of `if __name__ == "__main__":`) that does one of:

| shape (key = `path::shape`) | statement |
|---|---|
| `logging-disable` | `logging.disable(...)` |
| `logging-basicconfig-force` | `logging.basicConfig(..., force=<not literal False>)` |
| `root-logger` | `.setLevel/.addHandler/.removeHandler/.handlers.<mutator>()` or assignment to `.handlers/.level/.disabled` on `logging.getLogger()`, `getLogger("")`, `getLogger("root")`, `logging.root`, or a module-level name bound to one |
| `blanket-warnings-filter` | `filterwarnings("ignore")` / `simplefilter("ignore")` with no category narrower than `Warning`, no message, no module; exempt inside `with warnings.catch_warnings()` |
| `sys.stdout-assign`, `sys.stderr-assign`, `sys.stdin-assign` | assignment to the stream |
| `os-chdir`, `locale-setlocale`, `signal-signal` | the call |

Imports and aliases resolve through `_core.ImportAliases` (`import logging as lg`, `from logging import disable`).
A module that has a `__main__` block is not exempt by that alone. Not covered: `sys.stdout.reconfigure(...)`,
`faulthandler.enable`, `os.environ` edits (that is `import_side_effects`).

Scope: `find_/assert_import_time_global_side_effects(root, *, imported_by=None, baseline_path=None, min_files=1, allow_unparsed=False)`.
Without `imported_by` every module under `root` is checked (this is also the form `corpus_drift` binds, from `root`). With
`imported_by="tests"` only modules that a file under that directory imports are checked: static `import x.y` / `from x.y import z`
(anywhere in the test files, including inside test functions), resolved to a file under `root` by its dotted path from `root`'s own
name or any trailing part of it (so `scripts.lang_onboarding.x`, `lang_onboarding.x` and `x` all match), importing `a.b.c` also reaches
`a/__init__.py` and `a/b/__init__.py`, and followed transitively through the module-level imports of the reached modules (not
imports inside their functions). Unparsable files under either directory fail unless `allow_unparsed`.

Glossum wiring: `assert_import_time_global_side_effects("scripts", imported_by="tests", min_files=...)`.

## `basicConfig` decision

Measured at import time (all-modules mode): glossum `scripts` 31, autopsia `bench` 14, mlframe `src/mlframe` 3, all without
`force=True`, none imported by a test that matters. A plain `basicConfig` does nothing once the root logger has a handler, which pytest's
logging plugin guarantees during collection and tests, so it cannot cause the original bug. Reporting all 48 would be noise;
only `force=True` (which replaces the root handlers) is flagged. Measured `force=True` sites: 0.

## Measurement (read-only, gate at 29a2c0e + this change; tests dir `tests` in each repo)

| repo / root | files scanned | all-modules findings | `imported_by` findings | modules reached by tests |
|---|---|---|---|---|
| glossum_backend_scripts / `glossum` | 505 | 0 | 0 | 501 |
| glossum_backend_scripts / `scripts` | 337 | 25 (23 `sys.stdout-assign`, 1 `sys.stderr-assign`, 1 `os-chdir`) | 0 (the fixed file is clean) | 44 |
| autopsia / `autopsia` | 1017 | 0 | 0 | 926 |
| autopsia / `bench` | 391 | 0 | 0 | 197 |
| mlframe / `src/mlframe` | 3025 | 126 (124 `blanket-warnings-filter`, 2 `logging-disable`) | 5 | n/a |
| mlframe / `scripts` | 17 | 2 (`blanket-warnings-filter`) | 0 | n/a |
| mlframe / `tools` | 1 | 0 | 0 | n/a |
| production_scrapers | directory does not exist | | | |

`imported_by` drops the glossum scripts findings from 25 to 0 (the stdout-replacing scripts are imported by no test), as expected.

The 5 mlframe `imported_by` findings, all real (a test imports each module, e.g. `tests/feature_selection/shap_proxied/test_shap_proxy_bench_imports.py`,
`tests/feature_selection/fe/factory/test_common_helpers.py`; the filter then silences every warning for the rest of the worker, hiding
`pytest.warns`-style expectations and real deprecations):

- `feature_selection/_benchmarks/bench_boruta_auto_dispatch.py:31` `warnings.filterwarnings("ignore")`
- `feature_selection/_benchmarks/bench_shap_proxy_noise_pool_sweep.py:36` `warnings.filterwarnings("ignore")`
- `feature_selection/_benchmarks/bench_shap_proxy_scaling.py:42` `warnings.filterwarnings("ignore")`
- `feature_selection/_benchmarks/fe_operator_factory/brainstorm/h.py:12` `warnings.filterwarnings("ignore")`
- `feature_selection/_benchmarks/fe_operator_factory/stat_study/core.py:11` `warnings.simplefilter("ignore")`

No edits were made to those repos; mlframe's 5 are for its owner session to move into a function or a `catch_warnings` block.
(The mlframe root `src/mlframe` is a package under `src`, so `imported_by` matches by trailing dotted name; the 124 all-modules count
includes benchmarks nothing imports.)

## Files

`src/py_ci_shared/import_time_global_side_effects.py`, `tests/test_import_time_global_side_effects.py` (50 tests),
`tests/canary/import_time_global_side_effects/`, `tests/test_gate_teeth.py` (CANARIES entry, token `unimorph`),
`src/py_ci_shared/registry.toml`, `README.md`, `CHANGELOG.md` (bullet under the existing `## 1.22.13`).
