# Dispositions: 2026-10-03 audit, agent 10 (code added since v1.17.0)

Every RESOLVED fix has a regression test, and each test was checked by running it against the pre-fix module (HEAD's copy
swapped in): it failed there and passes now. Run on CPython 3.14 with `PYTHONPATH=src`. Owned elsewhere: N-6, N-7
(ci_health) and N-13, N-14 (version/release workflow).

### N-1
**Disposition:** RESOLVED -- `unsaved_paths` now walks an untracked (`??`) directory as it already walked an ignored one,
and reports a directory holding its own `.git` as unsaved itself (its history exists nowhere else):
`src/py_ci_shared/worktree_hygiene.py:228` (`_unsaved_in_directory`). Test:
`tests/test_worktree_hygiene.py::test_an_untracked_nested_repository_is_unsaved_work` (the audit's fixture; the old code
returned `['loose.txt']`). The complexity baseline entry for `unsaved_paths` went down from 13 to 11.

### N-2
**Disposition:** RESOLVED -- `function_complexity.assert_complexity_does_not_grow` scans once, fails on `scan.unparsed`, and
refuses a refresh while a file is unparsable. New `allow_unparsed` switch (on both complexity gates) tolerates the file and
keeps its baseline entries through a check and a refresh (`complexity_ratchet.held_by_unparsed`):
`src/py_ci_shared/function_complexity.py:55,108`, `src/py_ci_shared/complexity_ratchet.py:151`. Both entries were removed
from `tests/baselines/gate_entry_contract.json`. Tests: `tests/test_function_complexity.py::test_an_unparsable_file_fails_instead_of_being_dropped`,
`::test_a_refresh_is_refused_while_a_file_is_unparsable_and_allow_unparsed_keeps_its_entries`,
`tests/test_complexity_ratchet.py::test_allow_unparsed_tolerates_the_file_and_keeps_its_entries_through_a_refresh`.

### N-3
**Disposition:** RESOLVED -- `ModuleIndex` stores a copy of the memoised name set, so the submodule pass no longer
writes into the shared `tree_memo` value: `src/py_ci_shared/unresolved_imports.py:164`. Test:
`tests/test_unresolved_imports.py::test_a_second_index_does_not_inherit_the_first_indexs_submodules` (two indexes in one
process, `pkg/sub.py` deleted in between; the old code found nothing).

### N-4
**Disposition:** RESOLVED -- `add_project` reads `dynamic = ["dependencies"]` / `["optional-dependencies"]` from
`[tool.setuptools.dynamic]` `file =` lists through `requirements_file`; a dynamic table it cannot read goes to
`provided.unresolved` (finding becomes `ci-install-unevaluated`, never `missing`). `_declared` counts dynamic extras too.
A file naming its own project stops recursing (`Provided.reading`). `src/py_ci_shared/_ci_install_parts.py:315`. Tests:
`tests/test_ci_install_covers_conftest.py::test_dynamic_dependencies_are_read_from_their_setuptools_files`,
`::test_a_dynamic_table_that_cannot_be_read_is_unevaluated_not_missing`.

### N-5
**Disposition:** RESOLVED -- the mapping is now deterministic: the built-in table, the caller's `aliases` and names the
repo declares or installs anywhere. Installed metadata no longer decides the rule or the key's distribution; it only adds
a hint to an unmapped finding's message (`src/py_ci_shared/ci_install_covers_conftest.py:797`). `installed_dist` was
removed. The key format is unchanged, so existing consumer baselines keep matching. The test suite already patched the
metadata away for this reason. Test: `::test_the_rule_and_key_do_not_depend_on_the_interpreter_running_the_gate`
(keys identical with and without `installed_map` returning gitpython/requests). Real consumers: mlframe 1, pyutilz 2,
glossum_backend_scripts 0 findings, identical before and after.

### N-6
**Disposition:** WON'T FIX here -- ci_health is owned by the release/workflow agent in this round; not touched.

### N-7
**Disposition:** WON'T FIX here -- ci_health is owned by the release/workflow agent in this round; not touched.

### N-8
**Disposition:** RESOLVED -- `RepoReport.unresolvable_pins` (fixed pins the resolver was asked about and returned None
for) makes `failing` true with an `unresolvable-pin` finding. A `RefResolver(None)` resolves nothing and is not consulted.
`src/py_ci_shared/adoption_matrix.py:696`. Test: `tests/test_adoption_matrix.py::test_a_pin_to_a_tag_or_sha_that_does_not_exist_fails`
(tag `v1.9.9` and an unknown 40-hex SHA).

### N-9
**Disposition:** RESOLVED -- `compare` emits a failing `repo-gone` drift for every repo in the previous snapshot that is
missing tonight; a finder that errors with no earlier count and no earlier error fails as `errored`; one that errored last
night too is reported every night as informational `still-errored`. `src/py_ci_shared/corpus_drift.py:264,280`;
`FAILING_KINDS` gained `repo-gone`. Tests: `tests/test_corpus_drift.py::test_a_repo_missing_from_tonight_fails`,
`::test_a_new_finder_that_errors_from_its_first_night_fails`,
`::test_a_finder_that_starts_to_raise_fails_and_one_that_always_raised_is_reported_without_failing`
(the old "always raised" and "repo missing" expectations were re-framed).

### N-10
**Disposition:** RESOLVED -- a restore is safe only when an identity check of the SAME stream against a non-None value
encloses it (`if`/`while`) or precedes it as an exiting `if`, in the same function (nested defs excluded), or when the
guard reads a name the function computed from such a check. `sys` is resolved through `import sys as X`, and
`setattr(sys, "stdout"|"stderr", v)` counts as an assignment. `src/py_ci_shared/standard_stream_restore.py:136-220`.
Tests: `tests/test_standard_stream_restore.py::test_an_identity_check_that_does_not_guard_the_restore_does_not_exempt_it`
(the audit's four shapes plus an identity assert after the restore). On pyutilz the new semantics find a real one the old
gate missed: `tests/test_pythonlib_extra2.py:455` restores `sys.stdout` unconditionally and was exempted by an unrelated
`assert sys.stdout is original_stdout`.

### N-11
**Disposition:** RESOLVED -- design decision: the gate reports only a RESTORE, an assignment whose value is a location
saved from that stream earlier in the module (`old = sys.stderr`, `self._old = sys.stdout`, `_before = (sys.stdout,
sys.stderr)`, subscripts by their container). Installing a stream (a call result such as `io.TextIOWrapper(...)`, a fresh
devnull) restores nothing and is not reported; a value of unknown origin (a parameter) is not reported either, a documented
narrowing. `contextlib.redirect_*` stays reported. `allow` accepts `path::function` (qualified name, `<module>` at top
level) besides `path:line`. Verified read-only on worktrees of the real consumers: mlframe 13 -> 11 (the guarded
`tests/conftest.py:1088` restore is gone; the unguarded colorama fixture restore at `tests/test_colorama_reinit_patch.py:27`
and 9 redirects stay), glossum_backend_scripts 30 -> 1 (29 CLI wrappers gone, the redirect stays), pyutilz 1 -> 2 (+ the
true positive in N-10). Tests: `::test_the_consumer_false_positive_shapes_are_clean`,
`::test_the_assert_names_the_site_and_honours_allow`; canary `tests/canary/standard_stream_restore/clean/wrapper.py.canary`
(the old gate failed the clean control with it).

### N-12
**Disposition:** RESOLVED -- `exists`/`supported` mark an environment probe only when no name in the `if` was assigned
from a call in the test (`_PROBE_UNLESS_COMPUTED`, `src/py_ci_shared/nondiscriminating_shapes.py:88`); the median detector
counts only per-element companions (`assert_*` calls, `all`/`max`/`allclose`/`array_equal`/..., or `==` between values;
not `is not None`, `len(x) > 0`, `.shape ==`): `_checks_elements`, line 180. Tests:
`tests/test_nondiscriminating_shapes.py::TestAuditRegressions::test_a_trivial_companion_assert_does_not_disarm_the_median_check`,
`::test_a_per_element_companion_still_disarms_it`, `::test_an_env_word_on_a_computed_name_is_still_a_late_skip`. The old
`a.shape == b.shape` companion expectation was re-framed to a per-element `np.all(...)` companion. The baseline-file
convention (`BASELINE_PATH.exists()`) is still exempt.

### N-13
**Disposition:** WON'T FIX here -- version bump and `test_release_version` are owned by the release agent.

### N-14
**Disposition:** WON'T FIX here -- `release.yml` is owned by the release/workflow agent.

### N-15
**Disposition:** RESOLVED -- fixed upstream in this round as core finding K-9 (`1a06b5b`): `_core/node_index.py` keys
`_INDEX`/`_MEMO` weakly on the tree and keeps the root out of the stored lists. I had made the same change; on rebase the
upstream version and its test (`tests/test_core_node_index.py::test_a_replaced_tree_is_freed_with_its_index_and_memos`)
were kept and my duplicate dropped. My version had failed on the old code the same way (50 ad-hoc trees left 50 entries).

### N-16
**Disposition:** RESOLVED -- the wrapper carries a private marker `_py_ci_shared_bounded`, tested instead of
`__wrapped__`: `src/py_ci_shared/randomly_seed_guard.py:22`. Test:
`tests/test_randomly_seed_guard.py::test_a_reseeder_decorated_with_functools_wraps_is_still_bounded`.

### N-17
**Disposition:** RESOLVED -- `shape_reasons` raises `ValueError` for an `extra_shapes` slug not in `OPT_IN_SHAPES` (a bare
string is one slug), and `nonempty-only-assert` accepts `len(x) != 0`, `0 != len(x)` and a bare `len(x)`:
`src/py_ci_shared/nondiscriminating_shapes.py:206,322`. Bare truthiness of a variable (`assert rows`) was left out: it is
also the sole assert of many value checks on scalars, and the audit marked it optional. Tests:
`tests/test_nondiscriminating_shapes.py::test_an_unknown_extra_shape_slug_raises`,
`::TestNonemptyOnlyAssert::test_a_sole_nonemptiness_assertion_is_found[len(result) != 0|0 != len(result)|len(result)]`.

### N-18
**Disposition:** RESOLVED -- with `dataclass_classes` given, every pydantic field name (required, `default_factory` and
two-model-conflict fields included) counts as declared for the UNDECLARED test; a non-dataclass entry raises `TypeError`:
`src/py_ci_shared/config_getattr_default_parity.py:128,169`. Tests:
`tests/test_config_getattr_default_parity.py::test_required_factory_and_conflicting_pydantic_fields_are_not_undeclared`,
`::test_a_non_dataclass_in_dataclass_classes_raises`.

### N-19
**Disposition:** RESOLVED in part -- `complexity_ratchet.load_complexity_baseline` (`src/py_ci_shared/complexity_ratchet.py:139`)
raises `BaselineError` "baseline X is unreadable (...); fix or delete it" on a bad JSON or a wrong shape, and both
complexity gates report it through `pytest.fail` in the check and in a refresh, instead of a raw `JSONDecodeError`.
Tests: `tests/test_function_complexity.py::test_a_corrupt_baseline_is_named_unreadable_not_a_json_traceback[False|True]`,
`tests/test_complexity_ratchet.py::test_a_corrupt_baseline_is_named_unreadable_not_a_json_traceback[False|True]`.
OPEN for the core owner: `_core/baseline.py` `Baseline.regenerate` (line ~283 on origin/master `e10f533`) still turns a
`BaselineError` into `previous_counts = None` and so renders the "does not exist" seeding message; `_core/baseline.py` is
outside this agent's files.

### N-20
**Disposition:** RESOLVED -- string literals of a module-level `pytest_plugins` count as conftest imports
(`_ImportVisitor.loaded_modules`, `src/py_ci_shared/_ci_install_parts.py:629`), `-p name` / `-pname` on the pytest command
line are required too (`-p no:x` skipped; `_plugin_options`, `src/py_ci_shared/ci_install_covers_conftest.py:909`), and a job
that calls no pytest but runs `tox`, `nox`, `python -m tox|nox` or a `make` target containing test/check gets a
`ci-install-unevaluated` finding, silenced per job by `acknowledge` (`_delegated_findings`, line 896). Tests:
`::test_pytest_plugins_and_dash_p_plugins_are_required`,
`::test_a_job_that_runs_its_tests_through_tox_nox_or_make_is_reported_unevaluated[tox -e py|nox -s tests|make test|python -m tox]`.

### N-21
**Disposition:** RESOLVED -- the early warning is a dedicated `StaleCommentAdvisory(UserWarning)` emitted under its own
`always` filter inside `catch_warnings`, so a blanket `error` filter cannot turn it into a failure while pytest still
records it: `src/py_ci_shared/stale_comment_age.py:57`; docstrings updated. Test:
`tests/test_stale_comment_age.py::TestEarlyWarning::test_an_advisory_does_not_fail_under_warnings_as_errors`.

### N-22
**Disposition:** RESOLVED -- README documents `warn_days` / `find_comments_going_stale` / `StaleCommentAdvisory` in the
catalogue row (README.md:876) and `extra_shapes=["nonempty-only-assert"]` in the `nondiscriminating_shapes` section
(README.md:945).
