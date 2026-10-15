# Gate `validation_errors_echo_input` (2026-10-15)

## Rule

A 422 must not carry the value it refused. FastAPI puts a pydantic validator's error message into the `msg` of a 422
response, and by default it also returns each error's `input` (the rejected value). Clients log 422 bodies, so both
paths put request content, such as clinician text or a patient's age, into client logs.

Module: `src/py_ci_shared/validation_errors_echo_input.py`. Entries: `find_validation_errors_echo_input(root, *, min_files=1,
allow_unparsed=False, use_git=None)` and `assert_validation_errors_echo_input(root, *, baseline_path=None, refresh=False, ...)`.
It is opt-in. The finding key is `path::function::shape`, with no line number, and the baseline is the shared shrink-only
`Baseline`.

## Exact scope

### Shape `raise-echoes-value`

- **Validator**: a function decorated with pydantic's `field_validator`, `validator`, `model_validator` or `root_validator`.
  The decorator must resolve to `pydantic.*` through imports. The value is the first positional parameter that is not
  `self`/`cls`. A `model_validator(mode="after")` has no such parameter, so it is out of scope. Its `self.field` messages
  are mostly model configuration, and including it added 22 `self` findings in mlframe, 0 of them request text.
- **Helper**: a function the value is passed to, followed transitively to a fixed point and matched by name across the
  scanned corpus. Two call forms count:
  - a bare-name call `helper(value)`, which matches module-level functions;
  - `self.x(value)` or `cls.x(value)`, which matches methods.

  The parameter at that position or keyword is the value. `obj.split(value)` and `re.match(p, value)` are not helpers.
  Before this restriction, `re.split(",", v)` pulled every method named `split` into scope.
- **Taint**: the value spreads to any name assigned from an expression that reaches it. That includes slices, subscripts,
  `float(v)`, `next(x for x in v ...)`, `for`/comprehension targets, walrus and `with ... as`. With
  `for k, val in v.items()` and `for i, item in enumerate(v)`, only `val`/`item` are tainted, because a key or an index
  says where, not what.
- **Reported**: `raise ValueError/TypeError/AssertionError/PydanticCustomError(...)`, or `assert cond, msg`, whose
  arguments reach the value. This covers f-strings, `%`, `.format`, concatenation, the bare value and a
  `PydanticCustomError` context dict.
- **Not reported**: a value inside `len`/`type`/`isinstance`/`issubclass`/`bool`/`id`/`callable`/`hasattr`. Also not
  reported: a message that names only the field (`info.field_name`) or a dict key.
- **Not judged by name**: an undecorated `validate_x`/`check_x` is out of scope. CLI checkers legitimately name the path
  they could not open.

### Shape `handler-echoes-input`

- **Which handlers**: a function registered for `RequestValidationError` that calls `.errors()`. Registration is either
  `@<app>.exception_handler(RequestValidationError)` or `<app>.add_exception_handler(RequestValidationError, fn)` in the
  same file.
- **What counts as removing `input`**:
  - a dict comprehension filtering `k != "input"` or `k not in (..., "input")`;
  - `.pop("input", ...)`;
  - `del e["input"]`;
  - an error rebuilt as a dict literal of string keys that includes `loc`/`msg` and leaves out `"input"`;
  - `.errors(include_input=False)`.
- **When it is a finding**: no removal, or only removals that sit in a branch of an `if` or a conditional expression.
  A conditional removal drops `input` for some errors only.
- **Not reported**: a handler that never calls `.errors()`, or one registered for another exception type.

## Evidence

All three were in autopsia (pre-fix tree `23cbe21c`, fixed by local commit `c1d82f66`, which is not yet on origin):

- `bounded_entries` raised `ValueError(f"entry too long (...): {too_long[:40]!r}...")`. This is clinician text.
- `_validate_context_plausibility` raised `f"context[{key!r}] must be in [0, 130], got {val!r}"`. This is a patient's age.
- `_validation_error_handler` dropped `input` only when `type` ended in `too_long`.

## Teeth

- **Canary** `tests/canary/validation_errors_echo_input/`, registered in `CANARIES` in `tests/test_gate_teeth.py`:
  - `violation/` holds a helper echoing a slice, a `.items()` value echo and the conditional-drop handler (3 findings);
  - `clean/` holds the `len()`-only message, a message naming the field and key, and a handler that drops `input` for
    every error (0 findings);
  - `bom/` (BOM plus the violation) gives the same 3 findings;
  - `unparsable/` makes `UnparsedFilesError`.
- **Unit tests**: `tests/test_validation_errors_echo_input.py`, 30 tests. They cover each formatting form, negative
  controls (len/type/field name/key/index), v1, root, before and PydanticCustomError validators, the out-of-scope
  lookalikes, cross-file and keyword helper chains, all three handler violations and five clean handlers, line-free
  keys, baseline accept/refuse/shrink-only, BOM, unparsable and the empty floor.
- **Runs**: `test_package_inventory`, `test_gate_teeth`, `test_release_version`, `test_gate_entry_contract`,
  `test_corpus_drift` and the new tests: 1410 passed. mypy is clean on the module, ruff is clean, and black 26.5.1 has
  been applied.

## Per-repo findings (read-only runs, `allow_unparsed=True`, no repo edited)

| Repo | Files parsed | Findings |
|---|---|---|
| autopsia pre-fix (`23cbe21c`, `autopsia/`) | 1017 | 8 |
| autopsia_getrm working tree (HEAD `c1d82f66`, the fix) | 2276 | 4 |
| glossum_backend_scripts | 2048 | 8 |
| mlframe | 7547 | 41 |
| pyutilz | 662 | 0 |
| social/upwork/new_scraper/production_scrapers | 626 | 1 |

### autopsia pre-fix: 8 findings

All three evidence cases are reported:
- `api.py:521` handler (conditional drop);
- `api_models_clinical_inputs.py:30` `bounded_entries`;
- `api_models_common.py:58,60` `_validate_context_plausibility`.

The fix removes those 4. The other 4 are still in the fixed tree.

### autopsia fixed tree: 4 remaining findings, all true positives the fix did not cover

- `autopsia/api_models_clinical_inputs.py:48` `ClinicalMeasurementIn._value_finite`: `f"measured value must be finite,
  got {value}"`. This echoes a patient's lab value and is the most sensitive of the four.
- `autopsia/api_models.py:316` `AnalyzeRequest._reliability_in_unit_interval`: `got {offender[1]}`, a reliability
  number, plus the finding id.
- `autopsia/api_models_clinical.py:627` `ParseComplaintRequest._model_is_in_the_vetted_pool`: `model {v!r}`. This is a
  model id chosen by the caller and is low sensitivity.
- `autopsia/ingest/attempt_log.py:239` `require_private_endpoint` (a helper reached from the validator above):
  `model {model!r}`. Also a model id.

### glossum_backend_scripts: 8 findings

- `glossum/api/routers/tracking.py:67` `InteractionEvent._must_be_a_uuid`: `got {v!r}`.
- `glossum/api/routers/tracking.py:108` `FeedbackRequest._content_id_must_be_a_uuid`.
- `glossum/core/models.py:224` `SprintRequest._sense_ids_must_be_uuids`.
- `glossum/config.py`, 5 findings, all settings validators (process configuration, not request input):
  - 278 `validate_log_level`
  - 288 `validate_thinking_effort`
  - 314 `validate_generation_temperature`
  - 321 `validate_log_format`
  - 484 `validate_threshold_values`

The first three echo request input into an API 422.

### mlframe: 41 findings, all in configuration models (no HTTP surface)

- `src/mlframe/data/datasets/spec.py:502` `TargetSpec._check_n_classes`.
- `src/mlframe/feature_selection/wrappers/rfecv/_configs.py`, 8 findings:
  - 82, 90, 98 `SearchConfig._ck_*`
  - 159, 167, 175, 183 `FIConfig._ck_*`
  - 227 `RobustnessConfig._ck_action`
- `src/mlframe/reporting/output.py`, 7 findings at 79, 86, 89, 91, 97, 100, 106: `parse_plot_output_dsl`, a helper
  reached from a validator.
- `src/mlframe/training/_composite_target_discovery_config.py`, 9 findings at 109, 121, 134, 147, 160, 590, 604, 618,
  639: `_normalise_*`.
- `src/mlframe/training/_feature_selection_config.py:163` `_reject_flat_names_of_the_previous_layout`.
- `src/mlframe/training/_model_configs.py`, 2 findings:
  - 149 `LinearModelConfig.normalize_model_type`
  - 204 `MLPConfig.normalize_precision`
- `src/mlframe/training/_preprocessing_configs.py`, 8 findings:
  - 280, 282 `_coerce_window_bound`
  - 295, 297 `_validate_reuse_splits`
  - 432 `normalize_scaler_name`
  - 452 `normalize_imputer_strategy`
  - 742 `_validate_pysr_precision`
  - 753 `_validate_pysr_operator_preset`
- `src/mlframe/training/_reporting_configs.py`, 4 findings:
  - 396, 399 `_validate_title_template`
  - 455, 458 `_validate_panel_template`
- `src/mlframe/training/_training_runtime_configs.py:229` `TrainingConfig.normalize_mlframe_models`.

These match the rule but carry no request data. The gate is opt-in, and mlframe should not adopt it, or should adopt it
only with a baseline.

### production_scrapers: 1 finding

- `live_config.py:254` `TrafficConfig._check_rps_values`: a configuration validator, not request input.

### pyutilz: 0 findings

## Notes

- The gate cannot tell request models from configuration models. It belongs in services that answer HTTP requests, and
  those services can scope it with `root` (for example, the API package).
- Runtime is about 44 s on glossum's 2048 files with the scan done twice (the measurement script parses once more for
  the file count). That is acceptable for a CI gate.
- autopsia's fix is commit `c1d82f66` in `Redline/autopsia_getrm`, which is not on `origin/master` yet (`git ls-remote`
  shows `23cbe21c`). The fixed-tree run used the working tree.
