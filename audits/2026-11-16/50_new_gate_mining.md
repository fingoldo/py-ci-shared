# 50 new_gate_mining: round 2026-11-16 (T)

Read-only. Only this file was created in the repo. No consumer file was touched.

## Scope, method, what I ran

Base commit audited: `48e49b1` (HEAD moved to `05fa71f` during the run; no `src/` change affects this report). CUDA/GPU rules are excluded (dimensions 54 and 55).

Sources mined (all read-only):

- CLAUDE.md of mlframe (97 KB), pyutilz, glossum_backend_scripts, autopsia (worktree `autopsia_wt42`, 59 KB), polyvocab_app (release process, Dart/web, no Python rule), py-ci-shared.
- `tests/test_meta/` of mlframe (272 files), pyutilz (84), glossum_backend_scripts (160), llm_bench (60). I listed the files that do NOT import `py_ci_shared` (scratch `lo_small.txt`, `lo_ml.txt`): pyutilz 31, glossum 72, llm_bench 20, mlframe 144. Those are the "exists only in one consumer" set. I read the docstring of about 60 of them.
- `mlframe/audits/full_audit_2026-08-05/META_TEST_PROPOSALS.md` (per-finding meta-test ideas), `full_audit_2026-07-21/META_TEST_PROPOSALS.md`, and the previous round `audits/2026-11-11/50_new_gate_mining.md` plus TRACKER rows 50-*.
- The catalogue: `registry.toml` (261 entries) and every `code_audit/*.py` scanner (364-line dump in scratch `catalogue.txt`), the shared `ruff-base.toml` select/ignore lists, and targeted greps of `src/py_ci_shared` for each candidate's key words.

Prototype detectors (throwaway, not shipped), all in scratch `C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\audit_T\50\`:
`proto.py` (D1 to D20 over AST, one pass), `proto2.py` and `glob_fix.py` (stale tool-config entries), `proto3.py` (D21 to D26), `d1.py` (refined SQL AND/OR), `d5.py` (refined tree walk), `d16.py` (comment metadata with mlframe's exact regexes), `wf.py` and `wf2.py` (workflows), `shrink.py` (git history). Run with `python -I`.

Measurement snapshot (consumers move; a separate session is editing mlframe): mlframe `d2ef3afd6` plus its working tree at run time, pyutilz `7418463`, glossum_backend_scripts `5ef0e1a5`, llm_bench `d5990de`, autopsia worktree `23c730598`. "src" means `mlframe/src`, `pyutilz/src`, `llm_bench/src`; for the SQL and route shapes the origin repo (glossum `glossum/` package and `scripts/`) is added because mlframe and pyutilz carry almost no SQL or HTTP routes. Test-shaped gates were measured over `tests/` and say so. A hit is "TP" when the shape is a real instance of the rule, "FP" when it is not.

Not covered: algopacksimple, noema_app, social (Python), autopsia `src` (only its CLAUDE.md and meta-test list were read); no consumer unit-test suite was run; the shipped catalogue was not mutation-tested.

## Already covered or already dispositioned (checked before writing any candidate)

| Rule found in a consumer | Where the central catalogue already has it |
|---|---|
| f-string in `logger.x()` (pyutilz, mlframe, llm_bench `logger_lazy_formatting`) | ruff `G` family selected in `configs/ruff-base.toml:43` |
| `ast.unparse` / newer stdlib API on an old floor (pyutilz `py38_ast_compat`, mlframe `python_version_floor_respected`) | `api_floor` |
| bare `pickle.load`, `torch.load` (mlframe `no_bare_pickle_load`) | `unsafe_deserialization` |
| hard-coded DSN / model-cache path / `D:` drive (glossum `no_hardcoded_db_url`, `no_hardcoded_model_cache_paths`) | `machine_specific_paths` |
| raw UPDATE/DELETE without a row-count check (glossum `writes_detect_zero_rows`) | `raw_write_row_count` |
| tests must not reach the production DB (glossum) | `destructive_tests_throwaway_only`, `checkpoint_isolation` |
| mocks must be spec-bound (glossum `sdk_mocks_use_spec`) | `spec_bound_doubles` |
| write-only table / column (glossum `no_write_only_tables`) | `column_no_write_path` |
| silent default return, broad except without logging, non-neutral fallbacks (glossum, mlframe `broad_except_logging_*`) | `swallowed_exceptions`, `code_audit.non_neutral_except_fallback`, `latched_availability_flags` |
| clear function never called (mlframe `clear_fe_deadline`, 2026-08-05) | `uncalled_functions` (that incident is exactly its shape) |
| module-level cache mutated without a lock (mlframe `no_unlocked_module_cache`) | `module_cache_thread_safety`, `check_then_act_outside_lock` |
| `rglob` on test, deptry install, LOC budgets, markers, ruff pin, mypy beachhead | `loc_budget`, `pytest_markers`, `pinned_tool_versions`, `hook_hygiene` |
| `.parquet`/JSON sort_keys, cancellation-prone moments, polars null equality, epsilon denominators | `hash_key_determinism`, `cancellation_prone_moments`, `polars_null_equality`, `epsilon_padded_denominators` |
| text `open()` without `encoding=` (pyutilz `encoding_consistency`) | 2026-11-11 50-S7, WON'T FIX (tracker row 37). Not re-proposed. |
| `while True` with sleep, dict mutated while iterated | 2026-11-11 50-S8, 50-S9 (tracker rows 38, 39). Not re-proposed. |
| items 50-R1 to 50-R6 | DEFERRED rows 89 to 94. Not re-proposed. |

One earlier "covered" claim is wrong and is raised as a new finding: raise-without-`from` is NOT covered by ruff B904 (see 50-T11).

## Ranking of the survivors

Score = incidents prevented (I, 1 to 3) + noise (N, 3 = no false positive seen) + ease to write (E, 3 = one AST pass). Sorted by total, then by I.

| rank | id | gate | I | N | E | total | current hits (TP/FP) |
|---|---|---|---|---|---|---|---|
| 1 | 50-T1 | `estimator_mixin_order` | 3 | 3 | 3 | 9 | src 1 TP, tests 58 TP, 0 FP |
| 2 | 50-T2 | `sql_where_mixed_and_or` | 3 | 3 | 2 | 8 | 0 TP (refined), 3 FP in the crude pass, all explained |
| 3 | 50-T3 | `required_call_kwargs` (asyncpg timeout, Stanza package) | 3 | 2 | 3 | 8 | 19 TP by rule (10 asyncpg, 9 Stanza), 0 FP, all in glossum scripts |
| 4 | 50-T4 | `workflow_installs_pinned` | 2 | 3 | 3 | 8 | 2 TP, 0 FP |
| 5 | 50-T5 | `fit_at_test_module_import` | 2 | 3 | 3 | 8 | 0 |
| 6 | 50-T6 | `route_literal_after_catchall` | 2 | 3 | 2 | 7 | 0 |
| 7 | 50-T7 | `global_write_via_reexport_alias` | 2 | 3 | 2 | 7 | 0 |
| 8 | 50-T8 | `thread_lifecycle` | 2 | 3 | 2 | 7 | 0 src (4 test-file FP) |
| 9 | 50-T9 | `tool_config_dead_entries` | 2 | 2 | 2 | 6 | 3 TP, 0 FP after excluding defensive excludes |
| 10 | 50-T10 | `deprecated_docstring_without_warn` | 1 | 2 | 3 | 6 | 2 TP, 1 FP (3 hits) |
| extra | 50-T12 | `append_only_log_shrink` (git) | 3 | see finding | 2 | see finding | see finding |

## Findings

## 50-T1 (P2) estimator_mixin_order: an sklearn mixin placed after `BaseEstimator` silently breaks `is_classifier`

- **Rule:** in a class statement, every sklearn mixin (`ClassifierMixin`, `RegressorMixin`, `TransformerMixin`, `ClusterMixin`, `OutlierMixin`, `DensityMixin`, `BiclusterMixin`, `MultiOutputMixin`, `MetaEstimatorMixin`, `OneToOneFeatureMixin`, `ClassNamePrefixFeaturesOutMixin`, `SelectorMixin`) precedes `BaseEstimator` in the bases.
- **Incident:** mlframe 2026-09-01 audit (`tests/test_meta/test_sklearn_mixins_come_first.py`). `BaseEstimator.__sklearn_tags__` is terminal in the MRO, so a mixin after it is never reached. Under sklearn >= 1.6 `is_classifier` and `is_regressor` answer False with no error at import or fit. 66 classes had the order; visible symptoms were `TypeError: estimator must be a sklearn classifier or regressor`, `GridSearchCV`/`cross_val_score` choosing plain `KFold` instead of `StratifiedKFold`, and default scoring picking the wrong metric.
- **Not a duplicate:** `ml_api_contracts` has three rules (best-iteration-count, argmax-vs-raw-label, init-rewrites-param); none inspects base order (`ml_api_contracts.py:1-17`). Only mlframe has the check (local-only list, `lo_ml.txt`).
- **Detection:** AST, hard. `ClassDef.bases` resolved through `_core.ImportAliases` to `sklearn.base.*`; flag when the index of `BaseEstimator` is lower than the index of any mixin. v1 reads direct bases only. Suppression: `# mixin-order-ok: <reason>` on the `class` line.
- **Violation canary** (`tests/canary/estimator_mixin_order/violation/m.py.canary`):
  ```python
  from sklearn.base import BaseEstimator, ClassifierMixin

  class Bad(BaseEstimator, ClassifierMixin):
      def fit(self, X, y):
          return self
  ```
- **Clean canary:**
  ```python
  from sklearn.base import BaseEstimator, ClassifierMixin

  class Good(ClassifierMixin, BaseEstimator):
      def fit(self, X, y):
          return self

  class Waived(BaseEstimator, ClassifierMixin):  # mixin-order-ok: tag override is intentional here
      pass
  ```
- **Measured** (`proto.py` D10): mlframe src 1, mlframe tests 58, pyutilz src 0, llm_bench src 0. First five: `mlframe/training/composite/_benchmarks/bench_missing_aware_predict_iter133.py:58 class FakeComp(BaseEstimator, RegressorMixin)`, and tests `test_evaluation_fixes.py:471`, `test_hetero_vote.py:86`, `test_predict_passthrough_replay.py:38`: 5 of 5 TP (the shape is an MRO fact, so it cannot be a false positive); the severity of a test fake is low. mlframe's own gate scans src only, which is why 58 test fakes remain.
- **Impact:** every sklearn consumer (mlframe, autopsia, the ML repos); a silent wrong-CV or wrong-scorer, no error.
- **Proposed fix:** new gate `src/py_ci_shared/estimator_mixin_order.py`, `assert_estimator_mixin_order(root, ...)`, `min_files`/`allow_unparsed` per `CLAUDE.md`; registry entry; canary dir; `CANARIES` entry. ADDITIVE.
- **Disposition:** FIXED: gate `estimator_mixin_order` built (src/py_ci_shared/estimator_mixin_order.py, tests/test_estimator_mixin_order.py, canaries, fragment; red run recorded). Manifest: audits/2026-11-16/manifests/estimator_mixin_order.json.

## 50-T2 (P2) sql_where_mixed_and_or: a WHERE whose top level mixes AND and OR

- **Rule:** in a SQL string constant, a `WHERE` whose own parenthesis level contains both `AND` and `OR` must bracket one side.
- **Incident:** glossum N-9 (2026-09-10, `tests/test_meta/test_sql_and_or_precedence.py`): `lang AND text AND sense IN (...) OR sense IN (...)`. AND binds tighter, so the second arm stood alone without the language and text filters and one verdict was written onto every cognate of the lemma. It read as intended and passed every mock-based test.
- **Not a duplicate:** `code_audit/sql_lint.py` has exactly three scanners: `scan_sql_aggregate_before_cast`, `scan_sql_limit_without_order_by`, `scan_sql_offset_pagination` (`sql_lint.py:24`). No precedence rule. `sql_verify` executes statements on a server, which cannot see a precedence slip that returns plausible rows.
- **Detection:** AST + a small tokenizer: collect string constants (and f-strings with `{}` placeholders) containing `UPDATE|DELETE|SELECT` and `WHERE`; strip `--` and `/* */` comments and quoted literals; collapse `BETWEEN x AND y` and `CASE ... END`; split on `;`; for each `WHERE` track parenthesis depth to the next clause keyword (`ORDER BY`, `GROUP BY`, `LIMIT`, `RETURNING`, `HAVING`, `UNION`) and flag a level holding both. Skip docstrings. Hard. Suppression: `# sql-precedence-ok: <reason>`.
- **Violation canary:**
  ```python
  UPDATE_SQL = """
  UPDATE verdicts SET status = :s
  WHERE lang = :l AND text = :t AND sense IN (1, 2) OR sense IN (3, 4)
  """
  ```
- **Clean canary:**
  ```python
  UPDATE_SQL = """
  UPDATE verdicts SET status = :s
  WHERE lang = :l AND text = :t AND (sense IN (1, 2) OR sense IN (3, 4))
  """
  SIMPLE = "SELECT 1 FROM t WHERE a = 1 AND b BETWEEN 2 AND 3"
  ONLY_OR = "SELECT 1 FROM t WHERE a IS NULL OR a = 1"
  ```
- **Measured:** the crude first pass (`proto.py` D1) gave 5 glossum hits, all FP (SQL comments containing the word "or", a `""" + CONST + """` concatenation, an LLM prompt). The refined pass (`d1.py`, comments stripped, depth-aware) gave: mlframe src 2, pyutilz src 0, glossum package 0, glossum scripts 1, llm_bench 0. All 3 remaining are FP and each is fixed by a rule the spec now carries: `mlframe/data/datasets/ground_truth.py:168` and `estimator/_estimator.py:58` are class docstrings (skip docstrings), `scripts/create_vocab_functions.py:13` is a multi-statement plpgsql script (split on `;`, skip `$$` bodies). Expected true figure on today's trees: 0 hits, because glossum fixed N-9; the value is the canary and the next UPDATE.
- **Impact:** wrong rows written by a plausible-looking statement; a data-integrity class that passes unit tests with mocks.
- **Proposed fix:** a fourth scanner in `code_audit/sql_lint.py` (`scan_sql_where_mixed_and_or`) registered in `code_audit/registry.py`, or a standalone `sql_where_precedence` gate. ADDITIVE.
- **Disposition:** BUILT, HARD: `src/py_ci_shared/sql_where_mixed_and_or.py` with `tests/test_sql_where_mixed_and_or.py`, canaries under `tests/canary/sql_where_mixed_and_or/`, fragment `tests/_gate_teeth_canaries_sql_where_mixed_and_or.py`, manifest `audits/2026-11-16/manifests/sql_where_mixed_and_or.json`. Measured read-only: pyutilz/src 0, mlframe/src 0. Not registered in registry.toml or the README (owner-owned files).

## 50-T3 (P2) required_call_kwargs: a configured call must carry a configured keyword

- **Rule:** a table of `(callable, any-of keywords)`; a call to the callable that passes none of them (and no `**kwargs` forwarding) is a finding. Default preset: `asyncpg.create_pool` needs `command_timeout`, `asyncpg.connect` needs `timeout`. A consumer adds its own rows (glossum: `stanza.Pipeline` needs `package`).
- **Incidents:** (1) llm_bench `tests/test_meta/test_asyncpg_pool_timeout.py`: `asyncpg.create_pool(dsn, min_size, max_size)` with no `command_timeout` lets one hung query hold a pool connection forever until the fixed-size pool starves; its docstring says the shared `network_timeout` scanner "doesn't (yet) recognize asyncpg". (2) glossum CLAUDE.md "Stanza": on 2026-09-07 a seven-language comparison ran a bare `stanza.Pipeline(lang, ...)`, silently using the small models, and almost decided which tags stay in the LLM prompt on invalid numbers. The same sentence in CLAUDE.md lists scripts that still do it.
- **Not a duplicate:** `code_audit/network_timeout.py` contains no `asyncpg` or `command_timeout` (grep returned nothing). `connection_liveness_kwargs` is about TCP keepalives, a different keyword. `kwarg_forwarding` is about wrappers dropping optional arguments, not about a required one being absent.
- **Detection:** AST, hard for `src`, advisory (`--paths`) for one-off `scripts/`. Resolve the callee through `ImportAliases` so `import asyncpg as apg` and `from asyncpg import create_pool` both resolve. `**kw` counts as present. Suppression `# call-kwarg-ok: <reason>`.
- **Violation canary:**
  ```python
  import asyncpg

  async def open_pool(dsn):
      return await asyncpg.create_pool(dsn, min_size=1, max_size=4)
  ```
- **Clean canary:**
  ```python
  import asyncpg as apg

  async def open_pool(dsn):
      return await apg.create_pool(dsn, min_size=1, max_size=4, command_timeout=30)

  async def open_one(dsn, **kw):
      return await apg.connect(dsn, **kw)
  ```
- **Measured** (`proto.py` D9, D20; string-name matching only, so aliased imports would add hits): mlframe src 0, pyutilz src 0, llm_bench src 0 (already fixed), glossum package 0, glossum scripts 10 asyncpg + 9 Stanza. First five asyncpg: `batch_stanza_parse.py:502`, `push_content_snapshot.py:110` and `:119`, `ru_stress_check_report.py:142` and `:154`: 5 of 5 TP by the rule (ad hoc DB probe scripts; low severity there, high severity in a library pool). First four Stanza: `is_lang_grammar_covered.py:656`, `test_ka_corrector.py:106`, `add_de_sentences.py:175`, `generate_ka_additional.py:453`: 4 of 4 TP, and three of them are the scripts CLAUDE.md names.
- **Impact:** a pool that starves with no self-recovery; a measurement made on the wrong model.
- **Proposed fix:** new gate `required_call_kwargs.py` with a `RULES` constant and a `rules=` parameter (`[{"call": "...", "any_of": [...]}]`), preset `ASYNCPG`. ADDITIVE. The user's feedback memory ("fail closed", "no artificial caps") argues for shipping the asyncpg preset blocking and leaving Stanza as an example in the docstring, since `package=` is glossum policy.
- **Disposition:** BUILT, HARD in pyutilz (commit 255a772, `pyproject.toml`): `required_call_kwargs` is a hard gate, enabled per the owner's 2026-11-16 decision. Still outside this disposition: the Stanza row (glossum policy for `scripts/`) and alias resolution against `ImportAliases`.

## 50-T4 (P2) workflow_installs_pinned: a CI step that clones or pip-installs a first-party repo from git without a commit

- **Rule:** in `.github/workflows/*.yml`, every `git clone <url>` must be followed (within the same run block) by a checkout of a 40-hex SHA or a variable, and every `pip install "x @ git+https://..."` must carry `@<40-hex>` or `@${VAR}`.
- **Incident:** mlframe `X_CICD_DEPENDENCIES-1` (2026-08-05): a floating clone of pyutilz in CI means the same tree gets different dependency code on different runs, and mlframe carried 16 pyutilz pins across 9 workflow files naming two commits (cited in `git_dependency_pins.py:299`).
- **Not a duplicate:** `git_dependency_pins` checks `pyproject.toml` (`assert_all_git_dependencies_pinned`) and compares pins that already exist across files (`assert_pins_agree`); a workflow line with NO pin at all is outside both. `adoption_sha_ref` covers only the py-ci-shared reusable-workflow ref.
- **Detection:** line scan of workflow files (no YAML needed), hard, ADDITIVE as a new function in `git_dependency_pins.py`: `assert_workflow_installs_pinned(workflows_dir)`. Suppression: `# unpinned-ok: <reason>` on the line.
- **Violation canary** (`.canary` workflow text):
  ```yaml
  steps:
    - run: |
        git clone https://github.com/example/shared-lib.git
        pip install ./shared-lib
    - run: pip install "shared-lib @ git+https://github.com/example/shared-lib.git"
  ```
- **Clean canary:**
  ```yaml
  steps:
    - run: |
        git clone https://github.com/example/shared-lib.git
        git -C shared-lib checkout 6b512f2b6f7c9e353ee1831d0130b8b194c1706d
    - run: pip install "shared-lib @ git+https://github.com/example/shared-lib.git@${SHARED_SHA}"
  ```
- **Measured** (`wf2.py`, ten repos: mlframe, pyutilz, llm_bench, glossum, algopacksimple, noema_app, polyvocab_app, social, py-ci-shared, autopsia): 2 hits, both TP: `social/.github/workflows/production-scrapers-ci.yml:79` and `:150`, `pip install "pyutilz @ git+https://github.com/fingoldo/pyutilz.git"`. 0 FP. The first crude version flagged 20 clone lines as unpinned; all were followed by a `checkout <sha>` on the next line, which is why the spec requires the follow-up check.
- **Impact:** a non-reproducible CI run on the consumer that has it (social); a future clone line added without a pin.
- **Proposed fix:** as above. ADDITIVE.
- **Disposition:** BUILT, HARD: `src/py_ci_shared/workflow_installs_pinned.py` (line and block scan; `run:` and any `key: |` block scalar are one block; comment lines skipped), tests `tests/test_workflow_installs_pinned.py`, canaries `tests/canary/workflow_installs_pinned/`, fragment `tests/_gate_teeth_canaries_workflow_installs_pinned.py`, manifest `audits/2026-11-16/manifests/workflow_installs_pinned.json`. Measured read-only: pyutilz/.github/workflows 0, mlframe/.github/workflows 0. The two first-pass mlframe hits were false positives (checkout in a `install-command: |` block), and the pyutilz hit was a YAML comment; both are fixed and tested. Not registered in registry.toml or the README (owner-owned files).

## 50-T5 (P2) fit_at_test_module_import: a test module that trains a model while it is imported

- **Rule:** at module or class-body level of a test file (outside any `def`/`lambda`), no call to `.fit(`, `.fit_transform(`, `.fit_predict(` (extendable list).
- **Incident:** mlframe `test_no_fit_at_test_module_import.py`: every shard imports every test module to collect it, so one full fit at import time kept the macOS shards in collection until the job cap cancelled them, three hours with nothing run.
- **Not a duplicate:** `import_side_effects` and `import_time_global_side_effects` police production modules (the latter lists `logging.disable`, root-logger surgery); `source_text_ban` and `no_source_text_tests` are about assertions. No gate scans test-module top level for heavy calls. Only mlframe has the check.
- **Detection:** AST over `tests/`, hard, with an extensible `heavy_calls` list. Suppression `# import-fit-ok: <reason>`.
- **Violation canary:**
  ```python
  from sklearn.linear_model import LogisticRegression
  import numpy as np

  MODEL = LogisticRegression().fit(np.zeros((10, 2)), np.arange(10) % 2)

  def test_uses_model():
      assert MODEL is not None
  ```
- **Clean canary:**
  ```python
  import pytest
  from sklearn.linear_model import LogisticRegression
  import numpy as np

  @pytest.fixture(scope="module")
  def model():
      return LogisticRegression().fit(np.zeros((10, 2)), np.arange(10) % 2)

  def test_uses_model(model):
      assert model is not None
  ```
- **Measured** (`proto.py` D11, tests only): mlframe tests (5501 files) 0, pyutilz tests (438) 0. Zero current hits because mlframe fixed it and guards it locally; 0 FP.
- **Impact:** hours of dead CI on a shard matrix; caught only after the cap fires.
- **Proposed fix:** new gate `fit_at_test_module_import.py`. ADDITIVE.
- **Disposition:** RESOLVED (branch worktree-agent-af3f114ee9f596024, src/py_ci_shared/fit_at_test_module_import.py; 15 targeted tests green; 6 canary teeth green; measured 0 hits in mlframe, pyutilz, glossum_backend_scripts and llm_bench test trees; manifest audits/2026-11-16/manifests/fit_at_test_module_import.json)

## 50-T6 (P2) route_literal_after_catchall: a literal route registered after a same-prefix parameter route

- **Rule:** in one module, for the same HTTP method and the same router object, a route `P/{param}` (no `:path` converter) decorated before `P/literal` shadows the literal one, because Starlette matches in registration order.
- **Incident:** glossum 2026-07-21 API audit finding 14 (`test_routers_literal_before_catchall.py`, generalising `test_words_router_ordering.py`): `/words/search` swallowed by `/words/{sense_id}`. Every request to the literal route was served by the catch-all with no error.
- **Not a duplicate:** the catalogue has `free_text_in_get_route` and `edge_function_hygiene` (HTTP, different rule); nothing reads route order.
- **Detection:** AST over decorators `@router.get/post/put/delete/patch("<path>")` in source order; segment-wise comparison (same segment count, equal literals elsewhere, a `{x}` segment earlier and a literal later). Hard for FastAPI/Starlette consumers (opt-in by `--router-names`). Suppression `# route-order-ok: <reason>`. Includes-order across modules is out of scope (the glossum runtime test covers it).
- **Violation canary:**
  ```python
  from fastapi import APIRouter
  router = APIRouter()

  @router.get("/words/{sense_id}")
  async def one(sense_id: str): ...

  @router.get("/words/search")
  async def search(): ...
  ```
- **Clean canary:** the same two handlers in the reverse order; a `{path:path}` catch-all registered first with a different prefix.
- **Measured** (`proto.py` D3): glossum package 0, glossum scripts 0, mlframe src 0, pyutilz src 0, llm_bench src 0. Zero today because glossum fixed it and keeps a runtime test; the AST variant adds protection to the other FastAPI consumers (autopsia, noema), which were not measured.
- **Impact:** a silently dead endpoint.
- **Proposed fix:** new gate `route_literal_after_catchall.py`. ADDITIVE.
- **Disposition:** BUILT, HARD, OPT-IN: `src/py_ci_shared/route_literal_after_catchall.py` with `router_names=` required (an empty tuple raises), tests `tests/test_route_literal_after_catchall.py`, canaries `tests/canary/route_literal_after_catchall/`, fragment `tests/_gate_teeth_canaries_route_literal_after_catchall.py`, manifest `audits/2026-11-16/manifests/route_literal_after_catchall.json`. Same router object and method only; `{x:path}` is never a shadow. Measured read-only with router names router, app, api, r: pyutilz/src 0, mlframe/src 0. Not registered in registry.toml or the README (owner-owned files).

## 50-T7 (P2) global_write_via_reexport_alias: `alias.CONST = value` where the module bound to `alias` only re-exports `CONST`

- **Rule:** an assignment `alias._NAME = value` (ALL_CAPS, usually private) where `alias` comes from `from . import mod as alias` and `mod` binds `_NAME` only through `from .sibling import _NAME` rewrites the facade's copy; the sibling's real global, which other code reads, is untouched. Write through the owning module.
- **Incident:** mlframe 2026-07-18, `_gpu_resident_radix_ktc.py` (docstring of `test_module_global_write_via_reexport_alias.py`); the rule also exists as a manual, advisory semgrep hook (`.semgrep.yml`), so it is not enforced on every shard.
- **Not a duplicate:** `patch_target_is_a_reexport`, `reexport_patch_target` and `patch_target_read_at_call` concern monkeypatch calls in TESTS (`reexport_patch_target.py:24`, `_PATCH_CALLS = {setattr, patch, object}`); `runtime_registry_mutation` concerns registries. A production-code attribute assignment is not covered.
- **Detection:** AST. Collect `Assign/AugAssign` targets `Name.ATTR` with ATTR matching `_?[A-Z][A-Z0-9_]*`; resolve `Name` through relative `ImportFrom` to the sibling file; parse the sibling; flag when ATTR is bound there only by an `ImportFrom` and not by a module-level assignment. Hard. Suppression `# reexport-write-ok: <reason>`.
- **Violation canary** (two files in the canary dir, `pkg/facade.py.canary` and `pkg/user.py.canary`):
  ```python
  # facade.py
  from .impl import _LIMIT

  # user.py
  from . import facade as _f

  def set_limit(v):
      _f._LIMIT = v
  ```
- **Clean canary:** the same, with `from . import impl as _i` and `_i._LIMIT = v`.
- **Measured** (`proto.py` D12 + `resolve_alias_writes`): mlframe src 0, pyutilz src 0, glossum 0, llm_bench 0 (mlframe fixed its instance). 0 FP.
- **Impact:** a setting that appears to apply and does not; silent.
- **Proposed fix:** new gate `global_write_via_reexport_alias.py`. ADDITIVE.
- **Disposition:** BUILT, HARD: `src/py_ci_shared/global_write_via_reexport_alias.py` (AST; `from . import mod as alias` and `from .sub import mod` resolve to sibling files; the facade is a re-exporter only when NAME is bound by an import and never by a module-level assignment, def or class), tests `tests/test_global_write_via_reexport_alias.py`, canaries `tests/canary/global_write_via_reexport_alias/`, fragment `tests/_gate_teeth_canaries_global_write_via_reexport_alias.py`, manifest `audits/2026-11-16/manifests/global_write_via_reexport_alias.json`. Measured read-only: pyutilz/src 0, mlframe/src 0. Not registered in registry.toml or the README (owner-owned files).

## 50-T8 (P3) thread_lifecycle: a non-daemon `threading.Thread` with no join, no `.daemon = True` and no context-manager owner

- **Rule:** every `threading.Thread(...)` is (a) `daemon=True`, or (b) joined in the same function, or (c) owned by a class defining `__enter__` and `__exit__`, or (d) has `.daemon = True` assigned in the function.
- **Incident:** pyutilz `system.hardware_monitor.UtilizationMonitor` (2026-07-21 round 2): a background sampler thread hung interpreter exit whenever a caller forgot `.stop()`; found by `test_thread_daemon_or_lifecycle_managed.py`.
- **Not a duplicate:** `resource_leak_guard` is a runtime pytest plugin that errors on a leaked thread at teardown; it sees only threads a test leaks, not library code never exercised. `code_audit/_base.py` mentions `daemon` for its own cache. No static rule exists.
- **Detection:** AST, hard, `# thread-ok: <reason>`.
- **Violation canary:**
  ```python
  import threading

  def start_monitor(fn):
      t = threading.Thread(target=fn)
      t.start()
  ```
- **Clean canary:** `threading.Thread(target=fn, daemon=True)`; a function that `t.join()`s; a class with `__enter__`/`__exit__`.
- **Measured** (`proto.py` D4): mlframe src 0, pyutilz src 0, glossum 0, llm_bench 0 (all clean, matching pyutilz's own "verified zero false positives" note); mlframe tests 4, all FP-looking: `test_concurrency_state_fixes.py:60,63,264,267` start threads whose joins sit in a different helper (my join test is function-local), so the spec needs "joined anywhere in the module" for tests or a tests exclusion.
- **Impact:** a hung process exit in CI or a worker.
- **Proposed fix:** new gate `thread_lifecycle.py` (src only by default). ADDITIVE.
- **Disposition:** BUILT, HARD: `src/py_ci_shared/thread_lifecycle.py` (AST; src-only scope by default, a test file gets the module-wide join rule only with include_tests=True; a thread in a list and joined through `for t in list:` is accepted), tests `tests/test_thread_lifecycle.py` (12), canaries `tests/canary/thread_lifecycle/`, fragment `tests/_gate_teeth_canaries_thread_lifecycle.py`, manifest `audits/2026-11-16/manifests/thread_lifecycle.json`. Measured read-only on src: pyutilz/src 0, mlframe/src 0 (the first pass had 4 list-then-join false positives, now fixed). Not registered in registry.toml or the README (owner-owned files).

## 50-T9 (P2) tool_config_dead_entries: an ignore, omit or override entry in `pyproject.toml` that names nothing that exists

- **Rule:** an entry of `[tool.ruff.lint.per-file-ignores]` (glob), `[tool.coverage.run] omit` (glob), `[tool.deptry.per_rule_ignores] DEP002` (distribution name) and a first-party `[[tool.mypy.overrides]] module` pattern must match at least one tracked file, a declared dependency, or a first-party module.
- **Incident:** mlframe `test_deptry_ignores_not_stale.py` (2026-07-18 architecture review): deptry's ignore list has no expiry; an ignore for a package removed from every declaration stays forever and nobody notices. The same is true of any glob that stopped matching after a directory rename.
- **Not a duplicate:** `ignore_ratchet` watches the CODES an ignore list holds, not whether its path globs still match; `baseline_hygiene` is about baseline files; `gate_integrity.py:208` only lists `per-file-ignores` as a key it knows (grep result). `tool_config_env_vars_set_in_every_tool_job` is about `$VAR`.
- **Detection:** TOML + `git ls-files`, hard with a `# stale-ok` escape via an allow list; ADDITIVE, a new gate `tool_config_dead_entries.py`. Boilerplate patterns (`*/__pycache__/*`, `.venv/*`, `*/site-packages/*`) and the `exclude` lists (defensive by design) are excluded.
- **Violation canary:** a `pyproject.toml` with `[tool.ruff.lint.per-file-ignores]` `"legacy/**" = ["T201"]` in a tree with no `legacy/` directory, and `[tool.deptry.per_rule_ignores] DEP002 = ["gone-package"]` with no such dependency.
- **Clean canary:** the same file where `legacy/` exists and `gone-package` is declared.
- **Measured** (`glob_fix.py`, `proto2.py`; globs translated with `**/` as zero-or-more directories, tracked files only): mlframe 3 after removing boilerplate and `exclude` entries: `per-file-ignores "src/mlframe/**/benchmarks/**"` (the real directories are `_benchmarks`, the line before it already covers them), `coverage omit "legacy/*"` (no `legacy/` directory), and `DEP002 "cupy"` (the project declares `cupy-cuda12x`, so the ignore targets a distribution that is not declared). pyutilz 0, llm_bench 0, glossum 0 (213 entries), algopacksimple 0 outside `exclude`. The first pass had 9 hits in mlframe of which 6 were FP (my glob matching treated `**` as one or more directories and flagged `docs/**/*.ipynb`, and a third-party `numba.*` override), which is why the spec fixes the glob semantics and limits the mypy rule to patterns that look first-party. Judgement on the first three: 2 clearly TP (the benchmarks glob, `legacy/*`), 1 needs the owner (`cupy`: the ignore may be intended for the import name, which deptry's DEP002 does not use).
- **Impact:** configuration that looks like it protects something and does not.
- **Proposed fix:** as above. ADDITIVE.
- **Disposition:** BUILT, HARD: `src/py_ci_shared/tool_config_dead_entries.py` (ruff per-file-ignore keys, coverage omit globs, DEP002 names against declared dependencies with PEP 503 normalisation, first-party mypy override modules; `**/` = zero or more directories; `*` stays in one segment for ruff and crosses directories for coverage; slash-less ruff keys match a basename), tests `tests/test_tool_config_dead_entries.py` (9), canaries `tests/canary/tool_config_dead_entries/`, fragment `tests/_gate_teeth_canaries_tool_config_dead_entries.py`, manifest `audits/2026-11-16/manifests/tool_config_dead_entries.json`. The cupy DEP002 entry is a reasoned exemption in `DEFAULT_EXEMPTIONS` (owner decision pending). Measured read-only on tracked files, allow list on: pyutilz 1 (`py-ci-shared` undeclared), mlframe 3 (`legacy/*`, `py-ci-shared`, `src/mlframe/**/benchmarks/**`); raw with the allow list off, mlframe 4 (adds cupy). Not registered in registry.toml or the README (owner-owned files).

## 50-T10 (P3) deprecated_docstring_without_warn: a function documented as deprecated that never calls `warnings.warn`

- **Rule:** a public function or method whose docstring's first paragraph contains "deprecat" must call `*.warn(...)` or carry a `deprecated` decorator.
- **Incident:** pyutilz `pandaslib.frames.remove_stale_columns` ("Deprecated alias for ...", never warned; 2026-07-21 round 2, `test_deprecation_warnings_present.py`): a docstring-only notice is invisible to IDEs, to `-W error::DeprecationWarning` sweeps and to callers.
- **Not a duplicate:** the only shared deprecation-related code is `naive_utcnow` and `code_audit/registry.py:743` (its own warning); no static rule for the docstring/warn pair.
- **Detection:** AST, advisory first (warn-only, `# deprecated-ok: <reason>`), hard after one cycle.
- **Violation canary:**
  ```python
  def old_name(x):
      """Deprecated alias of :func:`new_name`."""
      return new_name(x)
  ```
- **Clean canary:**
  ```python
  import warnings

  def old_name(x):
      """Deprecated alias of :func:`new_name`."""
      warnings.warn("old_name is deprecated; use new_name", DeprecationWarning, stacklevel=2)
      return new_name(x)
  ```
- **Measured** (`proto.py` D6, non-test files): mlframe src 1, pyutilz src 0, glossum package 2, llm_bench 0. mlframe `training/composite/hpo.py:205` `cv_score` ("Deprecated alias of selection_score", a property that only returns): TP. glossum `importers/ruwordnet_importer/__init__.py:74` ("DEPRECATED: ... This function is disabled"): plausible TP (the body may raise instead; the spec would accept a `raise`). glossum `refsuite/sources/learner_errors_en.py:147` ("span.deprecated" in prose): FP, removed by matching only a first sentence that starts with "Deprecated" or "DEPRECATED". 2 TP, 1 FP of 3.
- **Impact:** removals that surprise callers.
- **Proposed fix:** new gate `deprecated_docstring_without_warn.py`. ADDITIVE.
- **Disposition:** BUILT, HARD in pyutilz (commit 255a772, `pyproject.toml`): `deprecated_docstring_without_warn` is a hard gate from its first run, per the owner's decision (no advisory cycle).

## 50-T11 (P3) The 2026-11-11 claim "raise without `from` is COVERED by ruff B904" is wrong: `configs/ruff-base.toml` ignores B904

- **Evidence:** `src/py_ci_shared/configs/ruff-base.toml:72`: `"B904",   # raise ... from inside except: stylistic, not a correctness issue`. Demonstration (ruff 0.16.10):
  ```
  $ python -m ruff check --isolated --select B904 b904/m.py      (m.py: except ValueError: raise RuntimeError("bad"))
  B904 Within an `except` clause, raise exceptions with `raise ... from err` or `raise ... from None` ...
  $ python -m ruff check --config .../configs/ruff-base.toml b904/m.py
  All checks passed!
  ```
  Measured with `--isolated --select B904`: mlframe/src 10, pyutilz/src 6, llm_bench/src 0. First five in pyutilz (`llm/base.py:503`, `llm/claude_code_provider.py:962` and `:979`, `llm/gemini_provider.py:528`, `web/proxy/decodo.py:254`) are all real wrapped re-raises that lose the original `__cause__` display in logs.
  The 2026-11-11 table (`audits/2026-11-11/50_new_gate_mining.md`, "Already covered or tracked") says consumers select the `B` family so B904 is on; they inherit the base config, which switches it off.
- **Impact:** a statement in the audit record that is false; the rule it said was covered is not enforced anywhere. The ignore is a reasoned shared decision ("stylistic"), so this is not a request to flip it: whether a lost `__cause__` matters (it is shown in tracebacks as "During handling of the above exception, another exception occurred" instead of "The above exception was the direct cause") is the owner's call.
- **Proposed fix:** (a) record the correction in TRACKER (documentation only), (b) offer the rule as an opt-in report-only count in `format_warn` or a `ruff-strict.toml` fragment instead of re-adding it to the blocking base. ADDITIVE if (b). Not a behaviour change.
- **Disposition:** RESOLVED (corrected): configs/ruff-base.toml line 72 disables B904; the gate it is covered by is none. The 2026-11-11 claim is withdrawn.

## 50-T12 (P1) append_only_log_shrink: a commit that replaces an append-only data file with a shorter one

- **Rule:** for configured append-only paths (globs; JSONL, `.jsonl.gz`, ledgers), no commit on the first-parent chain of a range may have fewer rows than its parent, unless the commit message carries a trailer `Append-Only-Shrink: <reason>`. Rows are lines, or lines after gunzip for `.gz`.
- **Incident:** autopsia `CLAUDE.md` (the section "A clean git merge on a binary append-only log can still silently drop data"): commit `62721781` (2026-08-18) overwrote a 2849-row `article_verdicts_checkpoint.jsonl.gz` with the 1542-row stale copy of another session, in one plain commit (`Bin 218942 -> 123412 bytes`). The existing `merge_gz_jsonl_union.py --verify` only compares a MERGE commit with its two parents and cannot see it; CLAUDE.md says "There is no cheap structural guard against THIS shape yet ... the real fix is likely a pre-commit hook or a periodic full-history sweep". The data were paid LLM answers, so the loss has a direct cost.
- **Not a duplicate:** `deletion_gates` (an audited removal stays removed), `closed_audit_rounds` (files only gain lines, but for audit rounds), `atomic_write_staging` (write-then-rename), `checkpoint_isolation` (tests do not touch the real state), `hook_attestation` (commit trailers). None compares a data file's row count between parent and child. The only trailer-checking precedent is `closed_audit_rounds` (`Audit-Edit:`), which this gate would follow.
- **Detection:** git (`_core.git.run_git`), hard, as a pre-push and CI gate over `origin/default..HEAD`; per-commit `git show <sha>:<path>`, gunzip when the path ends in `.gz`, count newlines; compare with the first parent; the trailer `Append-Only-Shrink:` waives. Config: `paths` list, `max_commits`.
- **Violation canary:** a throwaway repo built by `tests/_git_repo_helper.py` with commit A (`log.jsonl` with 3 lines) and commit B replacing it with 1 line.
- **Clean canary:** the same, with B appending a fourth line, and a variant where B shrinks the file and carries the trailer.
- **Measured:** `shrink.py` over the first-parent history of the autopsia worktree (`23c730598`, read-only `git log`/`git show`): `bench/results/article_verdicts_checkpoint.jsonl.gz`, 68 commits touching it, 3 shrink commits: `a12306063` (2824 -> 1524 rows, a merge of origin/master), `4784f0ac2` (2837 -> 1527, a merge), `05a1209a4` (2889 -> 1586, "Track C batch 30"). All 3 are real row losses (about 1300 rows each), 0 FP; two are merge commits, which is the case `--verify` was built for, so a first-parent walk also catches merges that bypassed it. `bench/track_b_llm_cost_ledger.jsonl`, 400 commits, 0 shrinks. The documented `62721781` was not among them because the history walk was capped at 68 commits touching the file and that one may be outside the first-parent chain; I did not chase it. Noise on this evidence: 3 hits, 3 TP. Rank by the score table: I=3, N=3, E=2, total 8.
- **Impact:** irreversible loss of paid data on a shared branch, silent at commit and merge time.
- **Proposed fix:** new gate `append_only_log_shrink.py` (history scan, `find_*`/`assert_*` per `CLAUDE.md`, needs a `corpus_drift.BINDINGS` entry or `NON_CORPUS` reason). ADDITIVE.
- **Disposition:** RESOLVED (branch worktree-agent-af3f114ee9f596024, src/py_ci_shared/append_only_log_shrink.py; 15 targeted tests green; 4 canary teeth green; measured read-only on autopsia_wt37 checkpoint path: 3 hits, 3 TP (2889 to 1586, 2837 to 1527, 2824 to 1524 rows); manifest audits/2026-11-16/manifests/append_only_log_shrink.json)

## Second-tier survivors (measured, not in the top ten)

These are real consumer rules not in the catalogue, ranked below the ten for noise or cost; each is specified at one paragraph level so a later round can build it without re-mining.

## 50-T13 (P3) sql_like_trailing_wildcard: `LIKE '<prefix>{param}%'` with no separator before the `%`

- Incident: glossum finding #30 (`test_like_pattern_audit.py`): `LIKE 'gls-en-{lemma}%'` matched `gls-en-{lemma}able` and `-ed` as well; the policy there treats a fixed prefix as fine and flags a bind or placeholder directly followed by `%`.
- Not covered: `sql_lint.py` has no LIKE rule.
- Detection: regex over string constants and f-strings (`{}` for the placeholder): `LIKE\s+'[^']*[A-Za-z0-9_]\{\}%'` and the bind forms `:name%`, `%s%`. Advisory; `# like-ok`.
- Canary: violation `f"SELECT 1 FROM t WHERE id LIKE 'gls-{lang}-{lemma}%'"`; clean `f"... LIKE 'gls-{lang}-{lemma}-%'"` and `"... LIKE 'gls-en-%'"`.
- Measured (`proto.py` D2): 0 hits in mlframe, pyutilz, glossum, llm_bench. No FP, no TP: ranked low because nothing exists today and the match is a heuristic.
- Disposition: WON'T FIX: 0 hits in all four measured repos and no consumer demand; the report ranks it low and it is a heuristic, so it is not built.

## 50-T14 (P3) audit_metadata_in_comments: finding IDs, wave markers and date stamps in code comments (opt-in ratchet)

- Source: the mlframe CLAUDE.md rule "No process/audit metadata in code comments: no phase/wave markers, finding IDs, date stamps ... (CRITICAL, repeated complaints)" and `test_no_audit_metadata_in_comments.py` (ratchet with a refresh flag). mlframe is the only repo that enforces it.
- Not covered: `stale_comment_age` is about TODO and commented-out calls; `phantom_code_references` is about comments naming missing symbols.
- Detection: `tokenize` comments only, regexes `finding-id`, `audit-dir`, `Wave N`, `loop iter N`, `20YY-MM-DD`; a `bench-attempt-rejected|VERDICT|REJECTED` comment is a preserved measurement and is skipped. Baseline ratchet, opt-in per repo.
- Measured (`d16.py`, mlframe's exact regexes, comments only): mlframe src 20 (18 finding-id, 2 date), pyutilz src 116 (all dates), glossum package 791 (789 dates, 2 waves), llm_bench 1. First five in pyutilz (`core/disk_cache.py:93,99` version-history table, `database/deltalakes.py:117`, `database/db/execution.py:216`, `dev/attempt_archive.py:507`): TP by the mlframe rule, but pyutilz and glossum do not state this rule in their CLAUDE.md and their house style dates audit comments, so a hard shared gate would be a style imposition. Therefore opt-in baseline only, and not before the owner says the rule is cross-repo.
- Disposition: WON'T FIX (the rule is not proven cross-repo): mlframe's CLAUDE.md states the rule; pyutilz and glossum do not, and their house style dates audit comments, so a shared hard gate would impose a style. Not built; revisit only if the owner confirms the rule is cross-repo.

## 50-T15 (P3) unpruned_tree_walk: a walk that enters `.claude/worktrees`

- Source: pyutilz `test_no_unpruned_tree_walk.py`: agent worktrees under `<repo>/.claude/worktrees` are full copies; one consumer audit test took 19 to 30 minutes with about 20 of them. The helper `iter_files_pruned` exists in pyutilz.
- Not covered: `_core/corpus.py` prunes for py-ci-shared's own scans; a consumer's own meta-test walker is not checked.
- Measured (`d5.py`, receiver is a function parameter, no prune evidence in the file): src: mlframe 3, pyutilz 0, glossum 1, llm_bench 0; tests: mlframe 26 (mostly `tmp_path.rglob`, FP), pyutilz 20 (`tmp_path`, FP), glossum 23, llm_bench 9. Judgement of the first five src hits: 1 plausible TP (`mlframe/_nested_parallel_scan.py:139`, scans the root it is handed), 2 FP (benchmark scripts on a narrow tree), `glossum refsuite/split.py:91` FP (a fixed prompts directory). The meta-test walkers in glossum and llm_bench receive a `src` subtree from the caller, not the repo root, so they cannot enter `.claude`. Precision about 1 in 5 after excluding `tmp_path`: advisory at best, and only for functions whose parameter is documented as a repository root.
- Disposition: BUILT, HARD, NARROW (commit ec5c235): `unpruned_tree_walk` checks only functions whose docstring names a parameter as the repo root; measured 0 in pyutilz/src and mlframe/src. See the 50-T15 section for the full record.

## 50-T16 (P3) logger_exception_outside_except: `logger.exception(...)` not lexically inside an `except`

- Source: pyutilz `test_exception_logging_preserves_traceback.py` (a heartbeat callback logged from another thread and printed "NoneType: None").
- Measured (`proto.py` D8): mlframe 0, pyutilz 0, glossum 1 (`glossum/api/main.py:393`, FP: a Starlette exception handler that the framework calls inside the `except` of its middleware). The test file in pyutilz itself documents that its broader design produced 38 hits and was cut back. Not worth a shared gate; recorded so nobody re-mines it.
- Disposition: WON'T FIX: 0 true positives and 1 framework false positive (glossum api/main.py:393, a Starlette exception handler the framework calls inside its except); the report records it as not worth a shared gate.

## 50-T17 (P3) workflow_pull_request_only_gate: a workflow that runs pytest, mypy or ruff and triggers only on `pull_request`, in a repo that pushes straight to master

- Source: mlframe `test_blocking_workflows_run_on_push.py`: twelve whole-project mypy errors reached master between 2026-09-11 and 2026-09-22 because `mypy-full.yml` had no automatic trigger.
- Not covered: `ci_default_branch_never_cancelled` (cancellation), `ci_workflow_gate` (`continue-on-error`), `test_partition_reachability` (declared partitions).
- Measured (`wf.py`, workflows whose text names pytest/mypy/ruff/py-ci-shared, trigger set has `pull_request` and not `push`): mlframe 2 (`dep-floors.yml` +schedule, `dependabot-auto-merge.yml`), pyutilz 1 (`dep-floors.yml` +schedule), others 0. Both `dep-floors.yml` hits run on a schedule so they do run unattended; `dependabot-auto-merge.yml` is PR-only by design. Precision 0 of 3 as stated, so a gate needs a per-repo "pushes to default" flag and an allow list; leave for a later round.
- Disposition: DEFERRED TO THE NEXT ROUND: 0 of 3 hits as stated (both mlframe hits run on a schedule; dependabot-auto-merge is PR-only by design). Needs a per-repo default-branch-push flag and an allow list, which the next round must design.

## Rejected after measurement (do not re-mine)

| prototype | source | measured hits | first-five judgement | decision |
|---|---|---|---|---|
| D7 module dict inserted but never popped (pyutilz `dict_cache_has_eviction`) | pyutilz test | mlframe src 82, glossum pkg 5, glossum scripts 29 | 0 of 5 TP (`_FACTS`, `_DEPENDENCY_FILES`, `_PACKAGES`, `_FILTER_CACHE`, `_ENCODE_MEMO`: all keyed by a bounded set) | noise ~100%; the pyutilz test needs a hand-curated whitelist for the same reason |
| D18 `popitem(last=False)` without `# evict-ok` (mlframe `bounded_eviction_drops_no_pending_work`) | mlframe test | mlframe src 31, pyutilz src 3 | 0 of 5 TP (LRU caches) | the real defect (an evicted entry that was pending work) is semantic; a marker on every LRU is adoption burden with no signal |
| D19 any `pl.Categorical` use (mlframe polars string-cache trap) | mlframe claude.md | mlframe src 44, pyutilz 1 | 0 of 5 TP (casts, dtype checks) | the trap is comparing two independently built frames, not using the dtype |
| D15 `df.drop(x)` with one positional argument (mlframe 2026-08-02 axis=0 bug) | mlframe claude.md | mlframe src 5, tests 2 | 0 of 5 TP (polars `drop`, drop of a row label by design) | receiver type is unknowable statically |
| D21 `x is <builtin type>` | META_TEST_PROPOSALS REPORTING_A-1 | mlframe src 11, pyutilz 3 | `type(x) is tuple` idiom, intentional | no |
| D22 `A or B if C else D` without parentheses | REPORTING_B-3 | 0 | none | zero hits; ruff has no rule; revisit only if a hit appears |
| D23 `pd.DataFrame(rows)` then `sort_values` without an empty guard | COMPETITION_EVALUATION-8 | mlframe src 8 (crude) | detector is too loose to judge | not built |
| D24 `kneighbors(X)` on the array the model was fitted on | FE_TRANSFORMER_B-3 | mlframe src 19 | 0 of 5 TP (SMOTE-style minority queries drop the self match on purpose) | no |
| D26 `a[:-n]` where `n` is a parameter | CORE_INFRA_MISC-1 | mlframe src 8, pyutilz 1 | 0 of 5 real (window or shift, validated above) | no |
| encoding-less `open()` | pyutilz `encoding_consistency` | not re-measured | | 2026-11-11 50-S7 WON'T FIX stands |

## Summary

| id | sev | title |
|---|---|---|
| 50-T1 | P2 | estimator_mixin_order: a sklearn mixin after `BaseEstimator` silently breaks `is_classifier` |
| 50-T2 | P2 | sql_where_mixed_and_or: AND and OR at one WHERE level |
| 50-T3 | P2 | required_call_kwargs: asyncpg timeout, Stanza package |
| 50-T4 | P2 | workflow_installs_pinned: unpinned first-party clone or git install in a workflow |
| 50-T5 | P2 | fit_at_test_module_import |
| 50-T6 | P2 | route_literal_after_catchall |
| 50-T7 | P2 | global_write_via_reexport_alias |
| 50-T8 | P3 | thread_lifecycle |
| 50-T9 | P2 | tool_config_dead_entries |
| 50-T10 | P3 | deprecated_docstring_without_warn |
| 50-T11 | P3 | the "B904 covered" claim of 2026-11-11 is wrong: the base ruff config ignores it |
| 50-T12 | P1 | append_only_log_shrink |
| 50-T13 | P3 | sql_like_trailing_wildcard |
| 50-T14 | P3 | audit_metadata_in_comments (opt-in ratchet) |
| 50-T15 | P3 | unpruned_tree_walk |
| 50-T16 | P3 | logger_exception_outside_except (recorded, not worth building) |
| 50-T17 | P3 | workflow_pull_request_only_gate |

## Considered and not raised

- `logger_lazy_formatting` (3 consumers) is ruff `G`, selected in the base config; the local copies are redundant, which `local_copy_report` is meant to show.
- `no_bare_pickle_load`, `py38_ast_compat`/`python_version_floor_respected`, `no_hardcoded_db_url`, `no_hardcoded_model_cache_paths`, `writes_detect_zero_rows`, `no_write_only_tables`, `sdk_mocks_use_spec`: each maps to a shipped gate (table at the top).
- `clear_fe_deadline` never called (mlframe 2026-08-05): `uncalled_functions` flags a function with no caller; not a new rule.
- `store_params_in_object` postfix mismatch, `enum_exhaustiveness`, `every_roster_is_pruned_by_every_drop_pass`, `fe_deadline_crosses_worker_boundaries`, `persist_after_mutate`, `transform_calls_use_gateway`, `composite_authoritative_sources`, glossum's verdict/saver/parser gates (`verdict_columns_have_a_reader`, `saver_writes_have_a_blast_radius_test`, `cross_system_revisions_are_guarded`, `pending_migrations_are_not_a_silent_queue`, `data_loss_is_not_logged_below_warning`): each depends on one project's vocabulary (a named function, table or list) and has no shape that generalises without a configuration the central gate cannot guess. Left as consumer-local tests.
- `no_tick_isinstance_offset_check` (pandas 3 `Tick` hierarchy), `no_asarray_for_length` (a `LazyHostCodes` type): one library version or one project class; a `source_text_ban`-style consumer config covers them.
- `no_shared_mypy_cache_dir`, `precommit_ci_scope_parity`, `precommit_no_duplicate_blocking_mypy`, `install_pyutilz_workflows_disable_uv_sources`, `workflow_polars_install_keeps_runtime_matched`: consumer-specific hook or workflow wiring; `hook_hygiene` and `uv_lock_parity` carry the general part.
- The `ruff` `PD` family and `T20`/`G` are already selected or deliberately off; no new ruff rule is proposed.
- The 20 `scripts`-folder hits of 50-T3 are not a reason to make the gate blocking for `scripts/`; the spec keeps them advisory.
