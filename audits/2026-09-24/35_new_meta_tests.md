# Audit: new centralized meta-tests (proposals, each tracked as a finding)

Read-only research. Sources: README.md and the docstrings of all 110 modules in `src/py_ci_shared/`; local meta-tests in
autopsia, dash_app_core, glossum_backend_scripts, llm_bench, pyutilz, mlframe (worktrees skipped); the user memory dir;
`audits/TRACKER.md` (round 2026-09-04). "Local copy" below means a file that does NOT import `py_ci_shared`.

Already central, so not proposed again: import side effects, module reload safety, value-bearing asserts, source-text
claims, naive utcnow / timezone, latched availability flags, epsilon denominators, drifted duplicates, fail-open
handlers, nondiscriminating shapes, vacuous loops, private imports, loc/function budgets, gate integrity/population
canary, mutation teeth, marker/partition reachability, audit-round format, CI workflow gates. Mutable defaults live in
the pyutilz `code_audit` scanner (run via `code_audit_meta`).




   consumer hand-writes wrapper test files. The plugin reads `[tool.py_ci_shared] gates = [...]` plus per-gate config from
   pyproject and generates one test item per gate. Removes wrapper drift (proposal 2); adoption becomes one config line.
   counterpart; one parametrized test asserts each gate flags the seed and passes the clean one, and that applying the
   seed changed the file (feedback_teeth_check_must_assert_substitution_applied). A meta-test over
   `src/py_ci_shared/*.py` enforces that every gate has one.
   refactor that makes a gate inert fails here (per-gate version of `gate_population_canary`).
   pre-commit usable.
   `docs_inventory_parity`; README documents about 25 of 110 modules today.
   carry local copies, feeding proposal 2 automatically.

## Findings

### NEW-1 (High) -- gate `import_cycles`

**Disposition:** RESOLVED -- new gate `import_cycles`: `import-cycle` = a strongly connected component of more than one module in the top-level import graph (imports in a function body, under `if TYPE_CHECKING:` or under `if __name__ == "__main__":` excluded; `import a.b.c` naming an already-loading ancestor is not an edge); `import-order` = the load is simulated once per cycle member imported first, ancestors first, and a `from Y import n` running while `Y` is still executing without `n` bound is reported with the entry point that breaks (the bottom-of-module re-export shape). A `try` catching ImportError survives the failure, a star import or module `__getattr__` in the target is not judged, a submodule in `from pkg import sub` is imported. Every import-order test case is checked against a real interpreter (`python -c "import pkg.b"`). Overlap: pyutilz's `import_cycle` scanner (run through `code_audit_meta`) covers the SCC half only and skips unparsable files; this gate adds the order simulation, the multiset baseline and unparsed-file failure. real-corpus run: mlframe/src/mlframe 18 import-cycle, 0 import-order (2658 parsed); pyutilz/src/pyutilz 0 (266 parsed); autopsia/autopsia 4 import-cycle, 0 import-order (941 parsed; its own docs name four remaining benign cycles); llm_bench 0 (120 parsed). FP sample: the first import-order run reported 3 sites, all false and all fixed before shipping: two were imports under `if __name__ == "__main__":` (autopsia kb/ru_llm_translation_causes.py:390, reason/clinical.py:967) and one was mlframe `_hermite_fe_mi` importing its parent inside `try/except ImportError` (verified by importing each module first in a fresh interpreter: all load). The first cycle run also reported 11 pyutilz package-to-submodule "cycles" made only by the implicit ancestor edge of `import a.b.c`; that edge was dropped. After tuning: 0 import-order findings, and the 22 SCCs are true cycles by construction (of the 16 entries in mlframe's local allowlist `tests/test_meta/test_no_import_cycles.py::_USER_DEFERRED_CYCLES`, 14 are found, 9 exactly and 5 inside a larger SCC; the other 2 are stale entries whose back-edge is now function-local: `reporting/_diagnostics_dispatch_extra.py:59` and `_orth_extra_basis_fe.py:994`); FP rate in a sample of 10 SCCs: 0. regression tests: tests/test_import_cycles.py

- **Bug class:** module-level import cycles and import-order-dependent loading (bottom-of-module re-export)
- **Method:** AST import graph of top-level imports; SCC detection; second rule: a name re-exported below a sibling's top import
- **False-positive risk:** function-local imports excluded; baseline_ratchet for existing SCCs
- **Repos:** all
- **Evidence:** 5 local copies: autopsia/tests/test_meta/test_no_circular_imports.py, test_no_new_module_level_import_cycles.py, test_no_module_depends_on_import_order.py; llm_bench/tests/test_meta/test_no_import_cycles.py; pyutilz/tests/test_meta/test_no_import_cycles.py; mlframe/tests/test_meta/test_no_import_cycles.py
- **Effort:** M
- **Priority:** P0

### NEW-2 (High) -- gate `local_copy_report` (migrate locals onto existing central gates)

**Disposition:** RESOLVED -- [autopsia, algopacksimple, claude-usage-notifier, flutter_uptime_monitor, noema_app: RESOLVED -- ad1's part (autopsia). `test_no_reload_without_snapshot.py`: local AST walk replaced by `module_reload_safety.assert_no_reloads_in_code` over `autopsia`/`bench`/`evidence_core` (covers everything the copy did, plus `sys.modules.pop` and aliased imports) and `assert_no_unpaired_reloads` over `tests/` (new: the copy excluded tests), local copy deleted. `test_no_new_module_level_import_cycles.py`: local graph/ratchet replaced by `import_cycles.assert_no_import_cycles(autopsia, min_files=500)` with `_central_import_cycles_baseline.json` (the name `_import_cycles_baseline.json` already belongs to the pyutilz `scan_import_cycles` ratchet, which stays) seeded by `PY_CI_SHARED_REFRESH=import-cycles` (on 530b82e: 2 cycles, `graph_io <-> graph_loading` and `vocab.structure*`, each with a note; the old local baseline's 3 package-level cycles were `from pkg import sub` false edges), `_module_level_import_cycles_baseline.json` deleted; `test_no_module_depends_on_import_order.py` and `test_documented_commands_are_runnable.py` read the cycle members from the central gate through a new `_shared.modules_in_import_cycles()`; the subprocess probe in `test_no_module_depends_on_import_order.py` is KEPT (it imports each member first in a real interpreter, which the central static simulation does not), as is `test_no_circular_imports.py` (fresh-interpreter import of 19 entry points, a check no central gate makes). `test_no_stdlib_json_import.py`: local walk replaced by `stdlib_json_ban.assert_no_stdlib_json` over the checkout with the two reasoned exceptions and `probes/` skipped (never scanned before either); verified by: ad_autopsia_10.log (54 passed, incl. the rewritten files and both import-order consumers)] [mlframe, pyutilz: RESOLVED -- (mlframe, pyutilz part) import cycles: mlframe `tests/test_meta/test_no_import_cycles.py` rewritten to call `py_ci_shared.import_cycles.assert_no_import_cycles` with a multiset baseline `tests/test_meta/_import_cycles_baseline.json` (17 cycles after 530b82e's `from . import x` fix: the training.core cycle is gone and the training.composite facade cycle shrank from 10 modules to 3; each noted with why it resolves: monolith split, njit top-level import, or a facade cycle with 0 import-order findings; the file keeps its name because 9 source comments cite it); pyutilz `tests/test_meta/test_no_import_cycles.py` deleted, replaced by `test_no_import_cycle` in the new `tests/test_meta/test_shared_gates_adopted.py` (0 cycles, zero tolerance; the central gate adds the import-order simulation the local SCC walk lacked). pyutilz `test_no_bare_except.py` + `_bare_except_baseline.json` and `test_no_unicode_in_console_output.py` + `_console_unicode_baseline.json` deleted: both baselines were empty and `code_audit_meta` already runs the default-on `bare_except` / `console_unicode` scanners over src (the local copies also wrote a missing baseline and skipped, the pattern 1.17.0 bans); their refresh flags removed from tests/conftest.py, test_test_source_parity entries and TESTING.md rows updated, and four docstrings that cited the deleted file now cite `test_logger_lazy_formatting.py` for the same baseline idiom. Kept, with reasons: mlframe `test_no_bare_except.py` (its bare half was already migrated; what remains is the verbose-gated-logging check, which no central gate implements); pyutilz `test_no_module_reload.py` (the central `find_unpaired_reloads` reports 8 of its reviewed test sites and has no per-site allowlist, and the local `sys.modules[...] = ...` write check has no central counterpart); its production half now also runs centrally (`assert_no_reloads_in_code` in test_shared_gates_adopted.py). verified by: meta runs listed in the commit notes.]

- **Bug class:** local copies drift from the central fix
- **Method:** report: local meta-test whose rule matches a central module but does not import it
- **False-positive risk:** report only
- **Repos:** llm_bench, pyutilz, autopsia, glossum, mlframe
- **Evidence:** module reload: llm_bench/tests/test_meta/test_no_unsafe_module_reload.py (538 LOC), pyutilz/tests/test_meta/test_no_module_reload.py, autopsia/tests/test_meta/test_no_reload_without_snapshot.py vs `module_reload_safety`; llm_bench/tests/test_meta/test_no_source_text_proxy_assertions.py (469 LOC) vs `source_text_claims`; glossum test_no_empty_or_tautological_tests.py vs `nondiscriminating_shapes`; mutable-default copies (memory: project_retire_local_mutable_default_copies)
- **Effort:** S
- **Priority:** P0

### NEW-3 (High) -- gate `swallowed_exceptions`

**Disposition:** RESOLVED -- new gate `swallowed_exceptions`: an `except` catching everything (bare, `BaseException`, `Exception`) or an I/O failure (`OSError`/`IOError`/`EnvironmentError`/`PermissionError`/`shutil.Error`, alone or in a tuple, names resolved through `ImportAliases`) whose body only passes/continues/breaks (DEBUG/INFO logging counts as silent with `quiet_log_is_silent=True`), in production code (tests/bench/scripts excluded by default). Exempt: handlers that raise, return, assign or call anything else; optional-dependency import probes; fallbacks (the try body returns on success); best-effort release or metadata work (every effect call is a close/remove/kill/rollback/pop/fsync or a stat/scandir/utime, with argument-building calls and predicates ignored); handlers in a `finally` or in `__del__`/`__exit__`/`close`/`emit`; `# swallow-ok: <reason>` or bandit's `# nosec B110`/`B112` on the except line, the line above or the first body line. Overlap: pyutilz's `bare_except` and `broad_except_swallow` scanners cover the broad half and debug-only handlers; this gate adds the I/O-failure half the proposal's evidence is about. real-corpus run: mlframe/src/mlframe 0 (1665 parsed), pyutilz/src/pyutilz 17 (266 parsed), autopsia/autopsia 4 (941 parsed), glossum_backend_scripts 0 (448 parsed), llm_bench 0 (44 parsed). Tuning: the first run reported 713 (mlframe 619, pyutilz 80, autopsia 14), dominated by `except Exception: logger.debug(...)` handlers these repos adopted as their audit trail (so DEBUG logging is not silent by default), bandit-acknowledged `# nosec B110` sites, import probes, cache-read fallbacks, TOCTOU stat/getmtime skips, taskkill/killpg/rollback cleanup and evict-and-count loops. FP review of all 21 remaining: 15 are the target class (10 pyutilz corpus scanners that skip an unreadable file with `except OSError: continue`, e.g. dev/meta_test_utils.py:715 and dev/code_audit/mojibake.py:75, the same silent-skip class as the unparsed-file problem `_core.scan_python` fixed here; and 4 autopsia cache writes whose failure is swallowed, graph_loading.py:169, ingest/hedge_scope.py:217, kb/dataset_manifest.py:119, kb/ddxplus.py:466, the exact shape of the 21.9 GB cache incident; plus dev/persistence_sweep.py:122), 6 are deliberate best-effort sites a maintainer would mark `# swallow-ok:` (3 kernel-tuning cache reads that skip an unreadable entry, 2 sweep-marker payload writes, 1 stderr drain thread in llm/claude_code_cli.py:354); none is outside the rule's definition. regression tests: tests/test_swallowed_exceptions.py

- **Bug class:** bare `except:` / `except Exception: pass` / `except OSError: pass` continuing with wrong state
- **Method:** AST: handler body is only pass/continue/debug-log, no re-raise and no returned sentinel
- **False-positive risk:** best-effort cleanup is legit: `# swallow-ok: <reason>` + ratchet
- **Repos:** all
- **Evidence:** pyutilz/tests/test_meta/test_no_bare_except.py and mlframe/tests/test_meta/test_no_bare_except.py (2 local copies); memory feedback_silent_correctness_bug_classes; swallowed OSError hid a 25 GB cache for 2 days (reference_np_save_appends_npy_breaks_staging_rename); TRACKER 20-F13, 20-F17
- **Effort:** S
- **Priority:** P0

### NEW-4 (High) -- gate `pickle_state_completeness` (runtime + static)

**Disposition:** RESOLVED -- new gate `pickle_state_completeness`, both halves. Runtime: `assert_pickle_round_trips(factories, warm=...)` builds each consumer-listed object, warms it, pickles, unpickles and warms the clone (so a dropped cache must also be rebuildable), reporting the failing stage per factory; `dumps`/`loads` can be swapped for cloudpickle or joblib; no factories, or a warm-up naming no factory, fails. Static, ratcheted: `state-not-excluded` = a class whose `__getstate__` copies `self.__dict__`/`vars(self)`/`super().__getstate__()` while a method other than `__init__`/`__setstate__` assigns an attribute whose name ends in a runtime-state noun (`_cache`, `_memo`, `_lock`, `_pool`, `_session`, `_conn`, `_executor`, `_compiled`, ...) or starts with `cached_`, never named in `__getstate__` (module- and class-level string collections it refers to are expanded; flags such as `self._caches_warmed = False` are skipped); `unpicklable-without-getstate` = a class with no reducer that lazily assigns a lock/thread/pool/executor/open file/socket/sqlite connection (resolved through `ImportAliases`) outside `__init__`, the half pyutilz's `unpicklable_resource_state` (`__init__` only) misses. real-corpus run: mlframe/src/mlframe 0 (2656 parsed), pyutilz 0 (266), autopsia 0 (941), llm_bench 0 (44), glossum_backend_scripts 7 (448). Tuning: the first run reported 7 in mlframe and 2 in autopsia, 8 of them false because `__getstate__` excluded the attribute through a constant (`xgb_shim.py`/`lgb_shim.py` `self._CACHE_POINTER_ATTRS + self._CACHE_KEY_ATTRS`, autopsia `graph_types.py` `_GRAPH_ATTACHED_MEMOS`) and 1 false because "cache" was a modifier (`MRMR._identity_cache_ycorr_`, a measured correlation); glossum's `_owns_session`/`_caches_warmed` flags were also dropped. The 7 remaining glossum findings were all reviewed and match the rule: `SynsetTranslatorV2.__getstate__` (`glossum/llm/translator_v2.py:246`) drops only `_cache_lock`, so `self.session` (line 320) and five warmed lookup caches (lines 535-538, 551) are copied into every pickle, and `RuWordNetImporter._ensure_connection` (`glossum/importers/ruwordnet_importer/importer.py:153`) opens a `sqlite3` connection lazily in a class with no reducer. FP in the sample: 0 of 7. regression tests: tests/test_pickle_state_completeness.py

- **Bug class:** runtime cache attributes break pickle / joblib fan-out
- **Method:** runtime: consumer-listed factories, warm the object, pickle round trip; static: attribute assigned outside `__init__` named `_cache/_memo/_buf/_handle` absent from `__getstate__` exclusions
- **False-positive risk:** fixtures supplied by consumer; static half ratcheted
- **Repos:** mlframe, pyutilz
- **Evidence:** feedback_runtime_caches_break_pickle; mlframe test_cluster_aggregate_setstate_default.py
- **Effort:** M
- **Priority:** P1

### NEW-5 (High) -- gate `test_resource_leaks` (pytest plugin)

**Disposition:** RESOLVED -- new pytest plugin `resource_leak_guard` (named so, not `test_resource_leaks`, because a `test_*.py` module inside `src/` would be collected as a test file): a test errors at teardown when, after pytest's own teardown (a `trylast` `pytest_runtest_teardown`, so function fixtures are finalized and a scheduler's catch-all cannot swallow it), it has left a live child process (psutil, falling back to `multiprocessing.active_children()`; 1 s exit grace), a running NON-daemon thread (0.5 s join grace), an open inet socket (psutil), `logging.disable` changed, or `os.environ` changed. Processes/threads/sockets are compared with a baseline taken after setup (so module/session fixtures are not the test's leak); logging/env with one taken before setup (so a function fixture must restore them), and both are restored after the report so one leak does not cascade (`leak_guard_restore = false` keeps them). A variable ADDED while the test imported a new non-stdlib, non-pytest module is import-time library configuration and is not reported or removed; a changed or removed variable always is. Allowlist: ini `leak_guard_allow` or `@pytest.mark.leak_guard_allow("thread:<glob>" | "process:<glob>" | "socket:<glob>" | "env:<glob>" | "logging")`; `PYTEST_*`/`COV_CORE_*` always allowed; `@pytest.mark.no_leak_guard`; ini `leak_guard_checks` narrows the checks (an unknown name is a usage error). Works under xdist (`-p` reaches the workers). real-corpus run (consumer tests executed read-only with `-p py_ci_shared.resource_leak_guard -p no:cacheprovider`, basetemp in scratch): pyutilz 10 resource-heavy files (claude-cli process tree, cli logging, caches, psycopg2 pool, git checkpoint cache, llm config) 115 passed, 0 leaks; glossum_backend_scripts `tests/test_llm` + `tests/test_cli` 11,230 passed, 7 teardown errors, all 7 reviewed and real: 6 tests (test_distractor_filter.py, test_distractor_exhaustion.py, test_prompt_audit_sensors_2026_05_04.py) leave HTTPS connections to external hosts (port 443) open, i.e. unit tests reaching the network, and `test_audit_round6_fixes.py::TestRunRoundDryRun::test_dry_run_returns_projection_dict` leaves a connection to the local Postgres (`::1:6433`) open from a "dry run". Tuning: the first glossum run also reported `HF_HOME`, `STANZA_RESOURCES_DIR`, `KMP_DUPLICATE_LIB_OK`, `KMP_INIT_AT_FORK` added by first imports (3 more errors, false), fixed by the import-time rule; psutil is now imported at configure so the plugin's own import is not mistaken for one. FP rate after tuning: 0 of 7. regression tests: tests/test_resource_leak_guard.py (inner sessions via `pytester` in a subprocess). Wiring for the coordinator: do NOT give it its own `pytest11` entry point (that would activate it in every environment that installs py-ci-shared); consumers opt in with `-p py_ci_shared.resource_leak_guard` in `addopts` or `pytest_plugins` in the root conftest, or the main `py_ci_shared.pytest_plugin` can register it when `[tool.py_ci_shared]` asks (`config.pluginmanager.import_plugin("py_ci_shared.resource_leak_guard")`); psutil is an optional dependency (without it the socket check is off and processes fall back to multiprocessing children).

- **Bug class:** tests leaking subprocesses, threads, sockets/DB connections, `logging.disable`, env vars
- **Method:** autouse fixture: snapshot psutil children, threading.enumerate, open sockets, `logging.root.manager.disable`, os.environ diff; fail at teardown
- **False-positive risk:** known daemon threads allowlisted by name
- **Repos:** all
- **Evidence:** feedback_tests_must_not_leak_processes_or_connections (50 leaked trends_rollup runs, upwork); mlframe test_no_module_level_logging_disable.py, test_no_module_level_env_mutation_in_tests.py, test_no_numba_config_env_restore_footgun.py
- **Effort:** M
- **Priority:** P1

### NEW-6 (High) -- gate `atomic_write_staging`

**Disposition:** RESOLVED -- new gate `atomic_write_staging`: `npsave-suffix` = in one function, `np.save`/`np.savez`/`np.savez_compressed` (resolved through `ImportAliases`) writes a path that is later renamed (`os.replace`/`os.rename`/`shutil.move`/`Path.replace`/`.rename`) and whose literal tail, read through the function's own assignments (f-string, `with_suffix`, `with_name`, `/`, `+`), does not end in `.npy`/`.npz`, so the rename misses the file numpy actually wrote (the test first proves numpy's appending behaviour on disk); file objects and unreadable suffixes are not judged. `in-place-rewrite` = `write_text`/`write_bytes`/`open(..., "w")` on a path whose expression or assignment names a cache or baseline, in a function that renames nothing into place; `# atomic-ok: <reason>` exempts a line. Tests, benchmarks and scripts are out of scope by default. real-corpus run: mlframe/src/mlframe 0 (1665 parsed), pyutilz 1 (266), autopsia 5 (941), glossum_backend_scripts 0 (448), llm_bench 0 (44); the fixed autopsia `bench/track_b_ontology_resolver.py` (staging now `...partial.npy`) is clean, which is the npsave rule's negative control on the real incident site. Tuning: the first run reported 12 mlframe `_benchmarks/` result files and 1 glossum script, excluded with the dev directories. All 6 remaining were reviewed and are real in-place cache rewrites with no staging, so a crash mid-write or a concurrent reader sees a partial file (the pyutilz and integrity_sources readers accept any existing, non-empty file): pyutilz `data/git_checkpoint_cache.py:74`; autopsia `ingest/hedge_scope.py:216`, `ingest/integrity_sources.py:318`, `kb/dataset_manifest.py:118`, `vocab/_http.py:123` and `:179`. FP: 0 of 6. regression tests: tests/test_atomic_write_staging.py

- **Bug class:** write-then-`os.replace` staging broken (np.save appends `.npy`; non-atomic cache rewrite)
- **Method:** AST: `np.save(p)` with p not ending `.npy` followed by `os.replace(p, ...)`; direct text-mode rewrite of a cache/baseline file without temp+replace
- **False-positive risk:** low; allow-comment
- **Repos:** mlframe, pyutilz, py-ci-shared itself
- **Evidence:** reference_np_save_appends_npy_breaks_staging_rename; TRACKER 21-F9
- **Effort:** S
- **Priority:** P1

### NEW-7 (High) -- gate `hash_key_determinism`

**Disposition:** RESOLVED -- new gate `hash_key_determinism`: a `json`/`orjson`/`simplejson`/`ujson` `dumps` call (resolved through `ImportAliases`) without `sort_keys=True`/`OPT_SORT_KEYS` whose result, in the same scope, is an argument of a hash (`hashlib.*`, `.update`, `hash`, `zlib.crc32`/`adler32`, `xxhash`/`mmh3`/`blake3`, or a callee named hash/digest/fingerprint/checksum), reaches one through a local name, is a subscript or `.get`/`setdefault`/`pop`/`add` key, or is assigned to a `*key`/`*fingerprint`/`*digest`/`*hash` name. Exempt: `**kwargs` (unprovable), payloads whose order is the value (list/tuple display, comprehension, `sorted(...)`, literal) unless a dict literal is inside, `# unsorted-ok: <reason>`; tests excluded by default. The test file first proves the bug class (equal dicts, different sha256 unsorted, equal sorted). real-corpus run (repo roots, tests excluded): mlframe 0 (2813 parsed), pyutilz 0 (274), autopsia 0 (1234), glossum_backend_scripts 0 (743), llm_bench 0 (51), py-ci-shared src 0 (155). The rule is not blind there: 12 `dumps` calls do reach a hash or key sink (6 in mlframe, e.g. `src/mlframe/training/composite/cache.py:327`; 6 in glossum, e.g. `glossum/llm/content_hash.py:101`), and all 12 sort their keys, so the gate pins a convention the fleet already follows. Tuning: the first run (tests included) reported 3, all false: an mlframe test that builds the unsorted fingerprint on purpose to prove it differs, and two pyutilz `json.dumps(password) in script` substring checks; tests are now excluded by default and `x in container` is no longer a key sink. FP after tuning: 0 of 0 findings. regression tests: tests/test_hash_key_determinism.py

- **Bug class:** JSON serialised for a hash/cache key without key sorting
- **Method:** AST: `json.dumps`/`orjson.dumps` flowing into hashlib `.update`/key variable without `sort_keys=True`/`OPT_SORT_KEYS`
- **False-positive risk:** list payloads allowed
- **Repos:** all
- **Evidence:** feedback_json_hash_sort_keys; TRACKER 20-F1, 21-F1 (cache key incomplete, same round twice)
- **Effort:** S
- **Priority:** P1

### NEW-8 (High) -- gate `sentinel_or_fallback`

**Disposition:** RESOLVED -- new gate `sentinel_or_fallback`: an operand of `or` that reads a DECLARED setting whose falsy values are values (default `max_tokens`, `max_completion_tokens`, `max_output_tokens`, `timeout`, `seed`, `random_state`, `n_jobs`, `limit`, `temperature`, `top_k`, `top_p`, `retries`, `max_retries`, `max_rows`, `n_samples`, `offset`; `names=` replaces the set) as `x.name`, `d["name"]`, `d.get("name")`, `getattr(x, "name", ...)` or a bare name, unless every later operand is a falsy constant (`x.seed or 0` changes nothing); `# falsy-ok: <reason>` exempts; tests excluded by default. Scoped by name on purpose: `optional_truthiness` covers annotated optional parameters and pyutilz's `default_via_or` the general shape with heuristics, where the incident sits as one baselined entry among general findings; this gate makes the named settings zero-tolerance or separately ratcheted. real-corpus run: pyutilz 1 (266 parsed), the incident itself, `llm/_timeouts.py:61` `body.get('max_tokens') or body.get('max_completion_tokens')` (still present: max_tokens=0 falls through to the name-default timeout); mlframe/src/mlframe 0 (2656), autopsia 0 (941), glossum_backend_scripts 0 (743), llm_bench 0 (51). mlframe's many `int(seed or 0)` reads are the harmless tail and correctly not reported. FP: 0 of 1. regression tests: tests/test_sentinel_or_fallback.py

- **Bug class:** `x or default` where 0/""/False is a meaningful sentinel
- **Method:** AST: `BoolOp(Or)` reading a key/attr from a consumer-declared sentinel list (max_tokens, timeout, seed, n_jobs, limit)
- **False-positive risk:** general form is noisy: only declared names; reuse `optional_truthiness`
- **Repos:** pyutilz, glossum, llm_bench
- **Evidence:** reference_max_tokens_sentinel_zero_disables_derived_timeout (pyutilz `_timeout_for`)
- **Effort:** S
- **Priority:** P1

### NEW-9 (High) -- gate `no_xfail_to_defer`

**Disposition:** RESOLVED -- new gate `no_xfail_to_defer`, over test files: `xfail-not-strict` = a `pytest.mark.xfail` (decorator, `pytestmark`, `pytest.param(marks=...)`, aliased imports) without `strict=True` when the repository does not set `xfail_strict` (read from pyproject/pytest.ini/setup.cfg/tox.ini, or passed), and any explicit `strict=False`, except when the reason names an external component (a live model or network may pass by chance); `no-reason` = an xfail or an unconditional skip (`mark.skip`, `pytest.skip()` outside an `if`/`except`) with no reason text; `untracked-reason` = an xfail or `pytest.xfail("...")` whose literal words (f-string parts included) name no external component (libraries, OS, hardware, LLMs, upstream: `EXTERNAL_WORDS` plus `external=`) and no tracked issue (`#123`, `ABC-123`, URL, "issue"); reasons built from a variable holding the text are not judged. `skipif` and `importorskip` are not judged. The count is ratcheted with the multiset baseline. real-corpus run: mlframe/tests 27 (3995 parsed: 22 untracked-reason, 5 xfail-not-strict), autopsia/tests 5 (668), glossum_backend_scripts/tests 0 (939), pyutilz/tests 0 (321), llm_bench 0 (120). All 32 were reviewed: 29 park in-repo work and say so in their own reasons ("FS GAP", "PROD GAP", "PROD BUG: OOF leak ...", "genuine open MRMR regression", "prod guard backlog", autopsia "MEASURED DEFECT", "recorded rather than fixed"), including 5 explicit `strict=False` in mlframe although its pyproject sets `xfail_strict = true`; 3 are borderline (a declared "DataFrame-only" contract in the transformer-parity matrix, a refuted GO/NO-GO value hypothesis, and autopsia's measured embedding-model ordering). Tuning: the first run reported 53; removed were 2 glossum live-LLM xfails that are non-strict because the model is nondeterministic (now exempt when the reason is external), reasons carried in a variable (`reason=gap`, `XFAIL_REASON + ...`), and external words added for LLMs and host-bound timing. Known limit: a reason that merely mentions a library ("... CatBoostRanker ...") passes as external. FP rate over all 32 reviewed: 0 outside the rule's definition, 3 borderline (about 9%). regression tests: tests/test_no_xfail_to_defer.py

- **Bug class:** xfail/skip parking a fixable bug
- **Method:** AST: every xfail/skip needs `reason=` naming an external component or tracked issue, xfail needs `strict=True`; count ratchet
- **False-positive risk:** free-text reasons: ratchet the count
- **Repos:** all (149 test files use xfail in mlframe/pyutilz/autopsia)
- **Evidence:** feedback_no_xfail_to_defer, feedback_dont_accept_documented_skips
- **Effort:** S
- **Priority:** P1

### NEW-10 (High) -- gate `timing_assertions`

**Disposition:** RESOLVED -- split in two. Single-shot wall-clock timing asserts: RESOLVED by existing, pyutilz's `wall_clock_assertion` scanner (default-on in `pyutilz/src/pyutilz/dev/code_audit/registry.py:231`, with `sleep_then_assert` at :232), run centrally through `code_audit_meta`; mlframe's local `tests/test_meta/test_no_single_shot_timing_assertion.py` stays a stricter repo supplement (ratio and relative-race shapes) for the adoption phase. Day boundary: new gate `clock_day_boundary`: inside a `test*` function whose name, docstring, identifiers or strings speak of calendar days, `+`/`-` between a real clock reading (`time.time()`, `datetime.now/utcnow/today()`, `date.today()`, resolved through `ImportAliases`, through `int()`/`float()`, or through a local name) and an offset of at least an hour that is not a whole number of days (seconds literal, products, `timedelta(...)`). Exempt: normalised readings (`// 86400`, `.replace(hour=`, `.date()`), JWT `exp`/`iat`/`nbf` claims, frozen clocks (freeze_time/time_machine decorators, freezer fixtures, `monkeypatch.setattr` of time/datetime), `# clock-ok: <reason>`. The glossum incident shape (`test_it_still_works_later_the_same_day`, cursor minted at `time.time()` and checked at `+ 3600`) is the first regression test. real-corpus run over tests/: mlframe 0 (3995 parsed), pyutilz 0 (321), autopsia 0 (668), glossum_backend_scripts 0 (939; its incident was fixed there), llm_bench 0 (69), dash_app_core 0 (20). Tuning: before the calendar-day condition the fleet had exactly 2 part-of-a-day clock shifts, both false (mlframe `tests/training/test_preprocessing.py:540`, an mtime aged by an hour; pyutilz `tests/test_logginglib_extra.py:74`, a negative duration), so the rule now requires the test to be about days. FP after tuning: 0 of 0. regression tests: tests/test_clock_day_boundary.py

- **Bug class:** single-shot wall-clock ratio asserts; tests on the real clock crossing a day boundary
- **Method:** AST: perf_counter/time.time pair feeding an assert without repeat/min-of-N; now()/time() plus calendar arithmetic without a freezer
- **False-positive risk:** `perf`-marked tests exempt
- **Repos:** mlframe, glossum, upwork
- **Evidence:** mlframe/tests/test_meta/test_no_single_shot_timing_assertion.py (4 flake fixes); glossum_backend_scripts/tests/test_meta/test_no_test_depends_on_the_wall_clock.py; feedback_browser_tests_control_the_clock
- **Effort:** M
- **Priority:** P1

### NEW-11 (Med) -- gate `stale_source_citations`

**Disposition:** RESOLVED -- new gate `stale_source_citations`, three self-verifying rules over comments and strings (read through `tokenize`): `self-citation` = a code string (not a docstring) citing its own file at a line outside its statement, mlframe's local rule generalised (`logging` records the true location); `line-past-end` = a citation of its own file or of a path that resolves to exactly one corpus file whose line is past that file's end (a bare name like `core.py` must share its top-level package with the citing file, since it more often names a library's `core.py`); `symbol-not-at-line` = a citation adjacent to a symbol (`` `sym` (a.py:12) ``, `a.py:12 `sym``, `sym() at a.py:12`, `a.py:12 (sym)`) whose symbol is not within 3 lines of the cited line, which is the proposal's "verify the line holds the cited symbol". Overlap: pyutilz's opt-in `stale_source_citation` reports vanished files and past-end lines without the package guard; this gate adds the self-citation and symbol rules and the multiset ratchet. real-corpus run: mlframe/src/mlframe 13 (2658 parsed), pyutilz 0 (266), autopsia 0 (941), glossum_backend_scripts 0 (1682), llm_bench 0 (120). All 13 are `line-past-end` citations of mlframe modules that were split and shrank (e.g. `training/io.py:69` cites `trainer.py:955`, and `training/trainer.py` now has 907 lines; `training/core/_phase_config_setup.py:153` cites `main.py:96`, and `training/core/main.py` has 41), exact by construction: FP 0 of 13. The symbol rule found no symbol-adjacent citation in these corpora (161 citations in mlframe, 67 in glossum, 9 in autopsia, none in the adjacent-symbol forms), so it guards new ones. Tuning: the first run flagged a docstring (`predict.py:330`) as a self-citation; docstrings are now judged only by `line-past-end`, which reports it correctly (`predict.py:1372` in a 913-line file). A second run reported 8 more past-end citations of bare `core.py`/`ensembling.py` from other subpackages, several naming xgboost/catboost internals; the package guard removed them. regression tests: tests/test_stale_source_citations.py

- **Bug class:** `file.py:NNN` citations in log strings/comments that rot
- **Method:** regex over literals and comments; verify line exists and holds the cited symbol
- **False-positive risk:** skip fixture data
- **Repos:** mlframe, glossum
- **Evidence:** mlframe/tests/test_meta/test_no_stale_source_line_citations.py (183 of 185 citations wrong); glossum_backend_scripts/tests/test_meta/test_no_stale_source_citations.py
- **Effort:** S
- **Priority:** P2

### NEW-12 (Med) -- gate `console_encoding_safety`

**Disposition:** RESOLVED -- by existing, no new gate: pyutilz's `console_unicode` scanner (`pyutilz/src/pyutilz/dev/code_audit/console_unicode.py`, registered default-on at `registry.py:250`, not in `OPT_IN_ONLY`) reports a `print(...)`/`logger.<level>(...)` whose first string literal holds a non-ASCII character, and exempts files (or packages, through an ancestor `__init__.py`) that reconfigure stdio to UTF-8, which is the i18n/entry-point mitigation the proposal asked for; every consumer runs it through `code_audit_meta`. real-corpus run of that scanner (read-only): mlframe/src/mlframe 0, pyutilz/src/pyutilz 0, autopsia/autopsia 0. Building a second copy here would be the drift NEW-2 exists to remove; the remaining local copy, pyutilz/tests/test_meta/test_no_unicode_in_console_output.py, is listed by `local_copy_report` for migration (NEW-2). regression tests: pyutilz/tests/code_audit/test_console_unicode.py (upstream); tests/test_local_copy_report.py::TestByName::test_code_audit_scanner_copies_are_reported_against_code_audit_meta covers the copy detection

- **Bug class:** non-ASCII in print/log crashing on cp1251/cp1252 consoles
- **Method:** AST: non-ASCII literal in print()/logger.* args in production
- **False-positive risk:** i18n modules allowlisted
- **Repos:** pyutilz, autopsia, mlframe
- **Evidence:** pyutilz/tests/test_meta/test_no_unicode_in_console_output.py; feedback_windows_encoding
- **Effort:** S
- **Priority:** P2

### NEW-13 (Med) -- gate `lf_file_writes`

**Disposition:** RESOLVED -- new gate `lf_file_writes`: a text-mode write with no `newline=` (`X.write_text`, `open(X, "w"|"a"|"x")`, `io.open`, `X.open("w")`) whose target is LF-required (a literal ending `.sh/.bash/.yml/.yaml/.gitattributes/.editorconfig`, followed through local/module assignments, or an identifier matching `baseline`); temp-directory targets, binary modes, `write_bytes`, tests and `# lf-ok` lines are exempt; real-corpus run: mlframe 0 (first draft had 5 FPs, all temp or benchmark-result files; fixed by the temp exemption and identifier-only `baseline` matching), pyutilz 0 (first draft 6 FPs in scripts/prove_meta_checks.py writing into a TemporaryDirectory, now exempt), glossum_backend_scripts 0, autopsia 0, py-ci-shared 10 (all 10 inspected: every one a committed multi-line JSON baseline written with `write_text`, true positives); regression tests: tests/test_lf_file_writes.py

- **Bug class:** `Path.write_text` / text-mode open reintroducing CRLF into LF-required files
- **Method:** AST: write_text/open('w') without `newline=""` targeting .yml/.yaml/.sh/baseline json
- **False-positive risk:** ratchet
- **Repos:** py-ci-shared (17 modules use write_text), all
- **Evidence:** feedback_write_bytes_not_write_text_on_windows; feedback_teeth_check_must_assert_substitution_applied
- **Effort:** S
- **Priority:** P2

### NEW-14 (Med) -- gate `module_cache_thread_safety`

**Disposition:** RESOLVED -- new gate `module_cache_thread_safety`: rule `unlocked-cache` flags a module-level dict named like a cache (`cache|memo`) updated by a non-atomic sequence in a sync function (insert plus evict/`move_to_end`/`clear`, or a read-modify-write of a held value such as `C[k] += 1` or `C.setdefault(k, []).append(x)`) in a module that constructs no Lock/RLock/Semaphore/Condition (`strict=True` flags every function-scope write, which is mlframe's local policy); rule `lazy-import-in-delayed` flags `from X import` inside a function passed to `delayed()` (joblib or dask), with `# joblib-import-race-ok` as the opt-out; real-corpus run: mlframe 6 (all 6 inspected: LRU/evict sequences such as `_gate_cache` insert plus `pop(order.pop(0))` and `_DTYPE_PAIRS_MEMO` `move_to_end` plus `popitem`, all true races; strict mode gives 18, the population of mlframe's own baseline gate), pyutilz 3 (all 3 inspected: `_PARSE_CACHE`/`_SRC_LINES_CACHE`/`_COMMENT_TEXTS_CACHE` do `in` + `move_to_end` + `popitem(last=False)` unlocked, true races), glossum_backend_scripts 0 (the first draft flagged 16: clear-only reset hooks, lone memo inserts and async-def writers, now exempt); lazy-import rule 0 in all three (mlframe already enforces it locally); regression tests: tests/test_module_cache_thread_safety.py

- **Bug class:** module-level mutable cache mutated in functions in a file with no Lock; lazy `from X import` inside joblib-threaded workers
- **Method:** AST: module dict mutated inside def with no `threading.Lock` in file; ImportFrom inside a function passed to `delayed()`
- **False-positive risk:** import-time-only caches allowed; ratchet
- **Repos:** mlframe, pyutilz
- **Evidence:** mlframe test_no_unlocked_module_cache.py (3+ independent fixes), test_no_lazy_from_import_under_joblib_delayed.py (NameError race)
- **Effort:** M
- **Priority:** P2

### NEW-15 (Med) -- gate `machine_specific_paths`

**Disposition:** RESOLVED -- new gate `machine_specific_paths`: four rules over `.py` string literals (docstrings and comments excluded) and the non-comment text of `.yml/.yaml/.toml/.cfg/.ini`: `user-home-path` (`C:\Users\<name>`, `/home/<name>/`, `/Users/<name>/`, placeholders and `runner`/`runneradmin` excluded), `drive-path` (absolute `D:`..`Z:` paths), `model-cache-path` (`.cache/huggingface|torch|stanza`, `stanza_resources`), `db-url` (DSN with inline credentials; `${...}`/`password` placeholders and workflow service-container DSNs exempt); regex-source strings, tests, markdown, `# machine-path-ok` lines and caller `allow` regexes exempt; real-corpus run: mlframe 83 (79 drive-path `D:/Temp/...`, `D:/Upd/...` in benchmark scripts, 4 `C:\Users\Admin\Machine learning` defaults; 10 sampled, all real machine-specific paths), pyutilz 2 (both `D:\Upd\...`/`D:\Temp` defaults in scripts/prove_meta_checks.py, true positives), glossum_backend_scripts 118 (116 drive paths, mostly the historical `fix_ka_round*` corpus scripts glossum documents as knowingly pinned plus benchmarks pointing at `D:\Upd\Programming\...`; 2 model-cache fallbacks in glossum/grammar/stanza_paths.py, which glossum allowlists by design; 10 sampled outside the corpus scripts, all true), autopsia 18 (all `C:\Users\Admin\...` data paths in bench/, true); FPs found and fixed during tuning: 5 CI service-container DSNs in glossum workflows, `STANZA_RESOURCES_DIR` env-var name, a regex literal `d:\d\d|\Z` read as a drive path, and drive/model-cache double reports; regression tests: tests/test_machine_specific_paths.py

- **Bug class:** hardcoded drive/user paths, model cache dirs, DB URLs
- **Method:** regex over py/yaml/toml: drive letters, `C:\Users\`, `/home/<u>`, literal `postgres://`
- **False-positive risk:** md docs skipped; allowlist
- **Repos:** all
- **Evidence:** glossum test_no_machine_specific_paths.py, test_no_hardcoded_model_cache_paths.py, test_no_hardcoded_db_url.py
- **Effort:** S
- **Priority:** P2

### NEW-16 (Med) -- gate `hardcoded_token_ceilings`

**Disposition:** RESOLVED -- new gate `hardcoded_token_ceilings`: an int literal given to `max_tokens`/`max_output_tokens`/`max_completion_tokens`/`max_tokens_to_sample`/`maxOutputTokens` as a call keyword, a dict entry or a parameter default, and literal fallbacks (`x or 4096`, `a if c else 512`, `d.get("max_tokens", 1024)`, `getattr(p, "max_output_tokens", 8192)`); `0`, values below `allow_below=16` (liveness probes), tests and `# token-ceiling-ok` lines exempt; real-corpus run: pyutilz 1 (`generate_batch` falls back to `req.get("max_tokens", 1024)`, true), glossum_backend_scripts 2 (`"max_tokens": 4096` request dict and `_call_llm(max_tokens: int = 4096)`, both true and both missed by glossum's local regex gate, which only sees `max_tokens=<n>`), llm_bench 1 (`preflight(max_tokens: int = 1024)`, a hand-picked ceiling), autopsia 3 (`max_tokens=12000` defaults/call, true), mlframe 0; all 7 inspected, 0 FPs; regression tests: tests/test_hardcoded_token_ceilings.py

- **Bug class:** literal `max_tokens` at LLM call sites truncating paid output
- **Method:** AST: max_tokens/max_output_tokens keyword with int literal
- **False-positive risk:** tests skipped
- **Repos:** glossum, llm_bench, pyutilz, autopsia
- **Evidence:** glossum test_no_hardcoded_token_ceilings.py; reference_cold_catalogue_silently_caps_llm_output
- **Effort:** S
- **Priority:** P2

### NEW-17 (Med) -- gate `coverage_config_parity`

**Disposition:** RESOLVED -- new gate `coverage_config_parity`: rule `narrow-run-inherits-fail-under` flags, when pyproject/.coveragerc/setup.cfg/tox.ini sets `fail_under > 0`, every workflow step whose `pytest --cov` (including `--cov` passed through a shell array such as `"${cov_args[@]}"`) or `coverage report|xml|html|json|lcov` runs a narrow subset (paths other than `testpaths`, a positive `-m`/`-k`, `--splits/--group/--shard-id/--num-shards/--lf/--deselect`, or a coverage report in a job with no whole-suite pytest of its own) without its own `--cov-config`/`--rcfile`/`COVERAGE_RCFILE` or an explicit `--cov-fail-under`/`--fail-under` (whose value parity stays with `gate_integrity.assert_coverage_gate_parity`); `continue-on-error` steps and `|| true` commands are skipped; rule `numba-blind-coverage` flags a numba-importing source tree whose CI has no coverage run with `NUMBA_DISABLE_JIT=1`; real-corpus run: pyutilz 0 at HEAD, and 3 on its workflows as of `7086bc4~1` (the numba nightly and both merged codecov-full renders, exactly the two workflows that sank on 2026-09-20 and were fixed by the next two commits), mlframe 0 (no `fail_under`; its numba-coverage workflow sets `NUMBA_DISABLE_JIT`), glossum_backend_scripts 1 (the 6-way `--splits` unit shards run `--cov` through `cov_args` against `fail_under = 70`, true positive; CI outcome not confirmable today because every recent run was blocked by billing), llm_bench 0, autopsia 0; 0 FPs; regression tests: tests/test_coverage_config_parity.py

- **Bug class:** `fail_under` applied to narrow/nightly cov runs; numba-blind coverage
- **Method:** config parity: workflow steps rendering coverage on a subset must pass a derived threshold or own config
- **False-positive risk:** low
- **Repos:** pyutilz, mlframe
- **Evidence:** reference_coverage_fail_under_applies_to_narrow_ci_runs (two workflows sank 2026-09-20); reference_numba_coverage_blind
- **Effort:** S
- **Priority:** P2

### NEW-18 (Med) -- gate `pytest_addopts_path_runs`

**Disposition:** RESOLVED -- new gate `pytest_addopts_path_runs` (a separate module, since the proposal's host `marker_runner_coverage` is not edited here; it reuses that module's public `runners`/`marked_tests`/`expression_selects`/`keyword_selects` and `gate_config_honesty.gate_commands`): every path (file, directory or node id) named by a pre-commit hook or workflow step that reaches at least one test while the runner's effective `-m`/`-k` (its own, or inherited from `addopts` unless the command resets it with `-o addopts=`) deselects all of them; each named path is judged on its own, so a command whose other paths do select something (pytest then exits 0, not 5) is still caught; monorepos are handled through `cd <pkg> &&` prefixes and workflow-level `defaults.run.working-directory`; real-corpus run: social/upwork/new_scraper/realtime_applications (the shipped case: `addopts -m 'not integration'`, 7 runners, 3 naming paths) 0 findings at HEAD, and the would-be first fix recorded in the lesson (a hook naming `tests/integration/test_idf_queries_run_against_postgres.py` without `-m`), injected as a command over the real test tree, is reported ("reaches 6 test(s) and -m 'not integration' (inherited from addopts) deselects every one"); production_scrapers 0, dashboard 0, mlframe 0 (15 runners, 14 naming paths), pyutilz 0 (5 runners), glossum_backend_scripts 0 (5 runners), autopsia 0; 0 FPs; regression tests: tests/test_pytest_addopts_path_runs.py

- **Bug class:** hooks/CI steps naming test paths inherit `addopts -m` and run nothing
- **Method:** extend `marker_runner_coverage`: each path-naming pytest call must override `-m` or collect >0
- **False-positive risk:** low
- **Repos:** glossum, all
- **Evidence:** reference_pytest_addopts_applies_to_every_run
- **Effort:** S
- **Priority:** P2

### NEW-19 (Low) -- gate `rollback_then_continue`

**Disposition:** RESOLVED -- new gate `rollback_then_continue` (generalizes glossum's local copy): an `except` handler calling `.rollback()` on a `try` inside a `for`/`while` whose enclosing loop writes (session `add/merge/delete/flush` on a session-like receiver, `add_all`, `executemany`, `execute_values`, `bulk_*`, `insert()/update()/delete()`, or `execute` of a literal INSERT/UPDATE/DELETE/TRUNCATE/DDL), with no reset or exit in the handler or `finally` (assignment, `.clear()/.pop()`, `raise`, `break`, `return`); exempt when the `try` opens a savepoint (`begin_nested`) or commits its own item (a commit not under a batching `if` such as `i % N`/`len(batch) >= n`), and on `# rollback-continue-ok`; real-corpus run: glossum_backend_scripts 1 (scripts/_manual_synset_matcher_full.py `clear_database`: a TRUNCATE loop with one commit at the end, so a missing table's rollback undoes every earlier TRUNCATE; true positive), production_scrapers 0, realtime_applications 0, dashboard 0, pyutilz 0, mlframe 0; the first draft reported 10 + 7 + 1: every per-item-commit loop (the three sites glossum's own gate still baselines were fixed by its "commit per word" change), read-only probes rolling back an aborted SELECT, and `seen.add(x)` read as a session write, all now exempt; regression tests: tests/test_rollback_then_continue.py

- **Bug class:** rollback() in loop except then continue without resetting accumulators
- **Method:** AST, generalize glossum copy
- **False-positive risk:** low
- **Repos:** glossum, production_scrapers, upwork
- **Evidence:** glossum test_no_rollback_then_continue_in_loop.py
- **Effort:** S
- **Priority:** P3

### NEW-20 (Low) -- gate `reiterated_iterable_params`

**Disposition:** RESOLVED -- new gate `reiterated_iterable_params` (generalizes pyutilz's local copy): a parameter annotated `Iterable/Iterator/Generator` (and async forms; bare, subscripted, string, `Optional`/`Union`/`X | None`, resolved through import aliases) consumed twice or more on one path without being rebound; consumption covers `for`, comprehensions, `list/tuple/set/sorted/sum/min/max/any/all/dict/enumerate/zip/map/filter/iter(p)`, `sep.join(p)`, `.extend/.update(p)`, `x in p`, `*p`, `yield from p`; paths are followed through if/else (larger branch), try and early return/raise, and a consumption inside a loop body or an inner comprehension `for` counts as repeated (the pyutilz copy counted only `for`/comprehension sites and ignored branches); `# reiterable-ok` on the def line opts out; real-corpus run: pyutilz 1 (dev/meta_test_utils.py `optional_scalar_fields(skip: Iterable[str])` tests `f.name in skip` once per field, so a generator is exhausted after the first field; true), mlframe 0, glossum_backend_scripts 0, py-ci-shared 6 (five `assert_*(known: Iterable[str])` that do `set(known)` twice, so a generator makes every stale entry invisible, and mutation_teeth `find_surviving_mutants(lines: Iterable[range] | ...)` which builds two fingerprints from `lines` and passes it on again; all 6 true); 0 FPs among the 7 inspected; regression tests: tests/test_reiterated_iterable_params.py

- **Bug class:** `Iterable` param consumed twice
- **Method:** AST, generalize pyutilz copy
- **False-positive risk:** low
- **Repos:** all
- **Evidence:** pyutilz test_no_reiterated_iterable_params.py
- **Effort:** S
- **Priority:** P3

### NEW-21 (Low) -- gate `numba_seed_range`

**Disposition:** RESOLVED -- new gate `numba_seed_range`: a numba seeding call (callee named `*seed*` together with `numba`/`nb`/`njit`, or a `seed`-named `@njit`/`@jit` function in the same file) whose argument, directly or through a local/module assignment and any `int(...)` wrapper, can reach `2**63`: `struct.unpack("<Q")`/`unpack_from`, `getrandbits(n>=64)`/`secrets.randbits`, `int.from_bytes` of 8+ random or digest bytes, `randint/randrange/integers` with a bound above `2**63` (constant-folded, including `np.iinfo(np.uint64).max`) or `dtype=uint64`; masking (`& ((1<<63)-1)`, `& *MASK*`, `% 2**63`, `>> n`) clears it, `# seed-range-ok` opts out; real-corpus run: mlframe 1 (src/mlframe/feature_selection/filters/_screen_predictors.py:916 restores numba with `int(_numba_restore_seed)` where `_numba_restore_seed = struct.unpack("<Q", os.urandom(8))[0]`: the exact bug fixed in screen.py on 2026-09-06, still live in the split-out `_screen_predictors.py`; true positive that needs an mlframe fix), pyutilz 0; same counts with tests included; 0 FPs; regression tests: tests/test_numba_seed_range.py

- **Bug class:** seeds >= 2**63 fed to njit `np.random.seed`
- **Method:** AST: `getrandbits(64)`/`2**64` flowing to a seed call
- **False-positive risk:** low
- **Repos:** mlframe, pyutilz
- **Evidence:** reference_numba_seed_int64_overflow_silently_breaks_rng_restore
- **Effort:** S
- **Priority:** P3

### NEW-22 (Low) -- gate `stdlib_json_ban` (opt-in)

**Disposition:** RESOLVED -- new opt-in gate `stdlib_json_ban` (runs only where a project calls it): `import json`/`json.*` (any alias), `from json[.x] import ...`, `importlib.import_module("json")` and `__import__("json")`, with relative `from . import json` (a project's own shim) exempt; exceptions are an `allow` map of path or glob to a required reason, and an entry with no reason or no longer matching an importing file fails; `# stdlib-json-ok` is the per-line opt-out; tests are included by default, as in autopsia's local copy; real-corpus run: autopsia 7 with its own three exemptions passed as `allow` (6 one-off scripts under audits/, outside the local gate's scope and skippable with `skip_dir_names={"audits"}`, plus tests/test_meta/test_unasserted_effects.py:30, a real `import json` the local gate does not report), mlframe 178, pyutilz 63, py-ci-shared 39 (the proposal's estimate was 21; those three have not adopted the rule, which is why the gate is opt-in); every hit is an AST-confirmed absolute import of the stdlib module, 10 sampled from mlframe and all 7 from autopsia confirmed, 0 FPs; regression tests: tests/test_stdlib_json_ban.py

- **Bug class:** stdlib json on hot paths (user hard rule)
- **Method:** AST import ban, opt-in
- **False-positive risk:** would flag 21 py-ci-shared modules
- **Repos:** autopsia, mlframe
- **Evidence:** autopsia test_no_stdlib_json_import.py; feedback_orjson_compile_regex
- **Effort:** S
- **Priority:** P3

### NEW-23 (Low) -- gate `polars_null_equality`

**Disposition:** RESOLVED -- new advisory gate `polars_null_equality` (warns with `PolarsNullEqualityWarning` by default; `advisory=False` fails with baseline support; a broken walk always fails): in polars-importing files, `polars-minmax-constant` (`X.min() == X.max()` on the same receiver when `X` is visibly polars: a chain from any polars callable such as `pl.col`, `pl.Series`, `cs.numeric()`, a `.get_column()/.to_series()` chain, or a name assigned once from one), `polars-compare-none` (`pl.col(..) == None`, `.eq(None)`: always null), `polars-null-propagating-eq` (`==`/`!=`/`.eq`/`.ne` between two `pl.col` expressions); `# null-eq-ok` opts out; real-corpus run: mlframe 0 (the first draft, which trusted "the file imports polars", reported 3 FPs, all numpy/pandas `arr.min() == arr.max()` in polars-importing files; fixed by requiring a visibly polars receiver; mlframe's one known instance of the bug class was already fixed with `eq_missing` before its history starts, and that pre-fix shape `cs.numeric().min() == cs.numeric().max()` is a regression case), pyutilz 0, glossum_backend_scripts 0 (326 polars-importing files across mlframe and pyutilz src examined); 0 FPs after tuning; regression tests: tests/test_polars_null_equality.py

- **Bug class:** `min()==max()` / `==` on nullable polars columns
- **Method:** AST heuristic, advisory only
- **False-positive risk:** medium
- **Repos:** mlframe
- **Evidence:** feedback_eq_missing_null_handling
- **Effort:** S
- **Priority:** P3

### NEW-24 (Low) -- gate `plotly_annotation_loop`

**Disposition:** RESOLVED -- new gate `plotly_annotation_loop`: in plotly-importing files, `add_annotation`/`add_shape`/`add_vline`/`add_hline`/`add_vrect`/`add_hrect` inside a `for`/`while` body or a comprehension of the same function; loops over a small literal (literal list/tuple/set, a conditional choosing between such literals, `range(k <= small_loop=10)`, `enumerate`/`zip` of those) and `# plotly-loop-ok` lines are exempt; `add_trace` and matplotlib are not flagged; real-corpus run: mlframe 0 at HEAD (the first draft reported 3 FPs, the `for v in ((h, -h) if symmetric else (h,))` threshold-line loop in reporting/renderers/plotly.py `_bar`, fixed by the conditional-literal exemption), and on the pre-fix sources of the two mlframe batching commits it reports exactly the fixed sites: 3 before `ab8d16cb2` (`_heatmap` per-cell `add_annotation`, `_network` per-edge `add_annotation`, `_line` per-band `add_vrect`: the 534x case) and 2 before `0b8c7e05b` (band `add_vrect` and change-point `add_annotation`); dash_app_core 0, social/upwork/new_scraper/dashboard 0 (8 plotly files), tbankrot_dashboard 0, pyutilz 0, llm_bench 0; 0 FPs after tuning; regression tests: tests/test_plotly_annotation_loop.py

- **Bug class:** `fig.add_annotation` inside a loop (O(n^2))
- **Method:** AST call-in-loop
- **False-positive risk:** low
- **Repos:** mlframe, dash_app_core
- **Evidence:** reference_plotly_add_annotation_on2
- **Effort:** S
- **Priority:** P3

### NEW-25 (Med) -- upstream Dart scanner file-size

**Disposition:** RESOLVED -- `py_ci_shared.dart_scanners.scan_file_size(files, read, ...)`: max_lines=1000 kwarg, keyed by path; files from `dart_files_under`, read through `dart_reader` (_core.read_source); parity with the local tool/meta/scanners.py copy, run over both worktrees: flutter_app_core 0/0, polyvocab_app 4/4 (local/upstream, identical); regression tests: tests/test_dart_scanners_upstreamed.py::TestFileSize, tests/test_dart_scanners_upstreamed.py::TestPlumbing

- **Bug class:** Dart scan hand-copied into flutter_app_core and polyvocab_app tool/meta/scanners.py
- **Repos:** flutter_app_core, polyvocab_app

### NEW-26 (Med) -- upstream Dart scanner empty-catch

**Disposition:** RESOLVED -- `py_ci_shared.dart_scanners.scan_empty_catch(files, read, ...)`: `catch (_)`/`catch (_, _)` with an empty or comment-only body, content-hash keys via _rekey; files from `dart_files_under`, read through `dart_reader` (_core.read_source); parity with the local tool/meta/scanners.py copy, run over both worktrees: flutter_app_core 4/4, polyvocab_app 3/3 (local/upstream, identical); regression tests: tests/test_dart_scanners_upstreamed.py::TestEmptyCatch, tests/test_dart_scanners_upstreamed.py::TestPlumbing

- **Bug class:** Dart scan hand-copied into flutter_app_core and polyvocab_app tool/meta/scanners.py
- **Repos:** flutter_app_core, polyvocab_app

### NEW-27 (Med) -- upstream Dart scanner source-text-assertions

**Disposition:** RESOLVED -- `py_ci_shared.dart_scanners.scan_source_text_assertions(files, read, ...)`: File(/Directory( ... readAsString/readAsLines in test files; files from `dart_files_under`, read through `dart_reader` (_core.read_source); parity with the local tool/meta/scanners.py copy, run over both worktrees: 0/0 in both (local/upstream, identical); regression tests: tests/test_dart_scanners_upstreamed.py::TestSourceTextAssertions, tests/test_dart_scanners_upstreamed.py::TestPlumbing

- **Bug class:** Dart scan hand-copied into flutter_app_core and polyvocab_app tool/meta/scanners.py
- **Repos:** flutter_app_core, polyvocab_app

### NEW-28 (Med) -- upstream Dart scanner import-cycles

**Disposition:** RESOLVED -- `py_ci_shared.dart_scanners.scan_import_cycles(files, read, ...)`: `import` and `export`, either quote, leading whitespace, block comments stripped; `package=`/`lib_root=` kwargs, with package_name() reading pubspec.yaml BOM-safe; files from `dart_files_under`, read through `dart_reader` (_core.read_source); parity with the local tool/meta/scanners.py copy, run over both worktrees: flutter_app_core 1/1, polyvocab_app 4/4 (local/upstream, identical); regression tests: tests/test_dart_scanners_upstreamed.py::TestImportCycles, tests/test_dart_scanners_upstreamed.py::TestPlumbing

- **Bug class:** Dart scan hand-copied into flutter_app_core and polyvocab_app tool/meta/scanners.py
- **Repos:** flutter_app_core, polyvocab_app

### NEW-29 (Med) -- upstream Dart scanner tests-without-assertions

**Disposition:** RESOLVED -- `py_ci_shared.dart_scanners.scan_tests_without_assertions(files, read, ...)`: paren-matched test/testWidgets blocks, comments stripped so a commented-out expect is no assertion, duplicate names keyed `#n` instead of overwritten; files from `dart_files_under`, read through `dart_reader` (_core.read_source); parity with the local tool/meta/scanners.py copy, run over both worktrees: flutter_app_core 0/0, polyvocab_app 3/3 (local/upstream, identical); regression tests: tests/test_dart_scanners_upstreamed.py::TestTestsWithoutAssertions, tests/test_dart_scanners_upstreamed.py::TestPlumbing

- **Bug class:** Dart scan hand-copied into flutter_app_core and polyvocab_app tool/meta/scanners.py
- **Repos:** flutter_app_core, polyvocab_app

### NEW-30 (Med) -- upstream Dart scanner timed-dismissal

**Disposition:** RESOLVED -- `py_ci_shared.dart_scanners.scan_timed_dismissal(files, read, ...)`: Future.delayed ... Navigator.of(...).pop( outside comments; files from `dart_files_under`, read through `dart_reader` (_core.read_source); parity with the local tool/meta/scanners.py copy, run over both worktrees: 0/0 in both (local/upstream, identical); regression tests: tests/test_dart_scanners_upstreamed.py::TestTimedDismissal, tests/test_dart_scanners_upstreamed.py::TestPlumbing

- **Bug class:** Dart scan hand-copied into flutter_app_core and polyvocab_app tool/meta/scanners.py
- **Repos:** flutter_app_core, polyvocab_app

### NEW-31 (Med) -- upstream Dart scanner double-error-reports

**Disposition:** RESOLVED -- `py_ci_shared.dart_scanners.scan_double_error_reports(files, read, ...)`: logger_call=/recorder_call= kwargs (defaults AppLog.error / ErrorService.recordError), string-aware comment strip; files from `dart_files_under`, read through `dart_reader` (_core.read_source); parity with the local tool/meta/scanners.py copy, run over both worktrees: 0/0 in both (local/upstream, identical); regression tests: tests/test_dart_scanners_upstreamed.py::TestDoubleErrorReports, tests/test_dart_scanners_upstreamed.py::TestPlumbing

- **Bug class:** Dart scan hand-copied into flutter_app_core and polyvocab_app tool/meta/scanners.py
- **Repos:** flutter_app_core, polyvocab_app

### NEW-32 (Med) -- upstream Dart scanner unused-test-seams

**Disposition:** RESOLVED -- `py_ci_shared.dart_scanners.scan_unused_test_seams(files, read, ...)`: @visibleForTesting members absent from the test_files= corpus, keyed rel::name; files from `dart_files_under`, read through `dart_reader` (_core.read_source); parity with the local tool/meta/scanners.py copy, run over both worktrees: 0/0 in both (local/upstream, identical); unused-l10n-keys is covered by arb_checks.find_dead_keys, extended to count `l10n` chains split across lines and `?.key` calls (tests/test_arb_checks.py::TestRegisterAndDeadKeys::test_wrapped_and_null_aware_call_forms_count_as_uses), polyvocab_app 0/0 dead keys; regression tests: tests/test_dart_scanners_upstreamed.py::TestUnusedTestSeams, tests/test_dart_scanners_upstreamed.py::TestPlumbing

- **Bug class:** Dart scan hand-copied into flutter_app_core and polyvocab_app tool/meta/scanners.py
- **Repos:** flutter_app_core, polyvocab_app

### NEW-36 (Med) -- scheduled consumer-pin check

**Disposition:** RESOLVED -- `.github/workflows/consumer-pins.yml` runs daily and on `workflow_dispatch`: `python -m py_ci_shared._consumers checkout configs/consumers.toml` shallow-clones the 13 consumers read-only (`CONSUMER_READ_TOKEN` for the private ones; without it they are skipped with a `::warning::`, while a clone that should work and fails exits 1), then `adoption_matrix --resolve-in .` over the full-history checkout fails on disagreeing, moving or stale pins and silent skips. `adoption_matrix` now counts releases behind: `RefResolver.releases()`/`releases_behind()` compare a fixed pin's commit with the `vX.Y.Z` tags (`git tag --merged`), a pin lacking any is a `stale-pin` finding ("N releases behind vX.Y.Z"), `--allow-behind N` tolerates N. The markdown matrix goes to the job summary and an artifact; regression tests: tests/test_adoption_matrix.py::test_a_pin_older_than_the_latest_release_fails_and_says_how_far_behind, tests/test_adoption_matrix.py::test_main_fails_a_stale_pin_unless_allowed, tests/test_consumers.py::test_without_a_token_private_repos_are_skipped_and_public_ones_cloned, tests/test_consumers.py::test_main_warns_on_a_skip_and_fails_on_a_failed_clone

- **Bug class:** a consumer pin drifts, moves or lags a release, and nobody looks until a consumer breaks
- **Repos:** all consumers

### NEW-37 (Med) -- gate scaffolder and CLAUDE.md

**Disposition:** RESOLVED -- `py-ci-shared new-gate <name> [--kind gate|library] [--summary ...]` (`src/py_ci_shared/_scaffold.py`) writes the module on `_core` (`scan_python`, `Finding`, `Baseline`, `ImportAliases`, a `find_*`/`assert_*` pair with `min_files` and `allow_unparsed`), a test file whose seeded-violation, negative-control, BOM, unparsable and empty-corpus tests fail until written, `tests/canary/<name>/{violation,clean,bom,unparsable}/` placeholders with a `CANARIES` entry, the `registry.toml` entry (`since` from `pyproject.toml`) and the README catalogue; it checks every target first and refuses to overwrite. `CLAUDE.md` states the rules for adding a gate; regression tests: tests/test_scaffold.py::test_the_scaffold_passes_the_inventory_and_fails_until_filled_in, tests/test_scaffold.py::test_it_refuses_to_overwrite_and_changes_nothing

- **Bug class:** a new gate that skips the `_core` reader, the canary or the registry
- **Repos:** py-ci-shared

### NEW-38 (Med) -- nightly real-corpus drift job

**Disposition:** RESOLVED -- `src/py_ci_shared/corpus_drift.py` (registered `cli`) runs every `find_*` of the registered gate and library modules whose required parameters bind from a repo root (`BINDINGS`: root, roots, tracked Python files, tests dir) over the `corpus = true` consumers (mlframe, pyutilz, llm_bench), writes per-finder counts, errors and timings to a JSON snapshot, and `compare` fails when a count grows by more than 20% and more than 5 (both configurable), drops to zero from non-zero, or starts to raise. Finders a corpus cannot feed are listed in `NON_CORPUS` with reasons; a test fails on a finder that is neither bindable nor listed, and on a listing that is bindable. `.github/workflows/corpus-drift.yml` runs nightly and on dispatch, finds the last successful run with `gh api` and downloads its snapshot artifact by run id; `accept: true` makes a reviewed drift the next baseline. A local run over pyutilz and llm_bench counted 58 finders each, none errored; regression tests: tests/test_corpus_drift.py::test_thresholds_need_both_the_relative_and_the_absolute_jump, tests/test_corpus_drift.py::test_a_finder_that_starts_to_raise_fails_and_one_that_always_raised_does_not, tests/test_corpus_drift.py::test_every_finder_is_bindable_or_listed_with_a_reason

- **Bug class:** false positives and blind spots that only real repositories trigger
- **Repos:** mlframe, pyutilz, llm_bench

### NEW-33 (Med) -- C901 complexity ratchet

**Disposition:** RESOLVED -- `py_ci_shared.complexity_ratchet.assert_complexity_does_not_grow` (registered gate, canary tests/canary/complexity_ratchet/): ruff's mccabe number computed from the AST (identical to `ruff check --select C901` on all 5061 functions in src/ and tests/), a committed `{"path::Qual.name": complexity}` baseline; a new function over the limit (default 10) or a grown entry fails, a shrunk, fixed or deleted entry fails until a refresh lowers or drops it. Dogfooded in `[tool.py_ci_shared.gates.complexity_ratchet]` over src/ and tests/ with tests/baselines/complexity_ratchet.json (70 entries, the 70 ruff reports); regression tests: tests/test_complexity_ratchet.py::test_parity_with_ruff_on_this_package, tests/test_complexity_ratchet.py::test_a_new_complex_function_fails_and_a_baselined_one_passes, tests/test_self_gates.py::test_gate_passes_on_this_repo

- **Bug class:** ruff C901 findings (70 in src and tests) that nothing gated: the rule was ignored wholesale, so any new function could join the backlog
- **Repos:** py-ci-shared, every consumer that ignores C901

### NEW-34 (High) -- Shrink-only baseline refresh

**Disposition:** RESOLVED -- `_core.baseline.write_ratchet` / `shrink_only`: a refresh drops entries that no longer fire and lowers counts and ceilings; an addition, a raised count or seeding a missing baseline with findings writes only the removals and fails naming each refused entry and the opt-in (`--py-ci-refresh-grow`, `PY_CI_SHARED_REFRESH_ALLOW_GROW=1`, `py-ci-shared refresh --grow`, `grow=True`). Routed through `_core.Baseline.regenerate/enforce` and every gate that writes its own file (function_length, loc_budget with its growth slack, complexity_ratchet, fail_open_handlers, deferred_drift, code_audit_meta, import_side_effects, ignore_ratchet, audit_wave_filenames, baseline_ratchet.Baseline.regenerate, source_text_claims); exempt with reason: mutation_teeth (every new key carries NEEDS-JUSTIFICATION, which fails the next run) and content_hash_version_bump_gate (a version-to-hash pin, nothing to grow); regression tests: tests/test_core_baseline.py::TestShrinkOnlyRefresh, tests/test_complexity_ratchet.py::test_refresh_is_shrink_only_by_default, tests/test_complexity_ratchet.py::test_seeding_a_missing_baseline_needs_the_opt_in

- **Bug class:** "refresh" doubling as "accept everything": a refresh rewrote the baseline to whatever the scan found, silently accepting new real violations
- **Repos:** every consumer with a baselined gate

### NEW-35 (Med) -- source_text_claims: arbitrary-file reads

**Disposition:** RESOLVED -- `source_text_claims` now judges text read from a path it cannot place (`p.read_text()`, `read_bytes()`, `open(p).read()`): a claim when the assertion takes a substring's position in it (`find`/`index`/`rfind`/`rindex`, inline or via a bound name) or searches it with a literal (`in`, `==`, `count`, `startswith`) for a code-like string; data suffixes, deserialisers, regex extraction and pytest `tmp_path`/`tmpdir` outputs stay out. Also new: a `return` of a content check over source, and an assert over a name bound to one (`found = "x" in src; assert found`). Parity with llm_bench's local tests/test_meta/test_no_source_text_proxy_assertions.py: on llm_bench's 69 real test files 0 local / 0 central hits; on the 19 synthetic shapes of its own regression class 19/19 identical; regression tests: tests/test_source_text_claims.py::TestArbitraryFileReads

- **Bug class:** source-text proxy assertions through an arbitrary path (`Path(x).read_text().find(...)`) that the central gate missed, so llm_bench kept a local copy
- **Repos:** llm_bench (local copy can now retire), every consumer of source_text_claims

### NEW-39 (Med) -- epsilon_padded_denominators: shapes its pyutilz counterpart catches

**Disposition:** NOT A DEFECT -- the proposal asked for a pad on a non-power denominator (`d + eps`, not
`d**k + eps`) to be flagged too. The gate's own docstring already measures and rejects this exact widening:
on a ~3500-module repository the general additive-pad rule matched 109 sites, "nearly all of them legitimate
relative-error denominators," against 2 true bugs for the power-only rule. `pyutilz.additive_epsilon_denominator`
is the wider, unconditional rule and stays for exactly this reason (its own docstring: "This scanner is the
wider rule and reports every additive pad, so it stays"). No change.

- **Bug class:** additive-epsilon pad on a denominator that is not a power
- **Repos:** none (won't-fix, pre-existing evidence in the gate's own docstring)

### NEW-40 (Med) -- swallowed_exceptions: shapes its pyutilz counterpart catches

**Disposition:** NOT A DEFECT -- both proposed shapes (a bare/`BaseException` handler in `scripts/`; a broad
handler that only logs below WARNING) are existing, documented opt-in knobs, not gaps: `exclude_parts=()`
already includes `scripts/` in scope, and `quiet_log_is_silent=True` already treats a DEBUG/INFO-only log
call as silent. Verified: `find_swallowed_exceptions(root, exclude_parts=(), quiet_log_is_silent=False)`
misses a `scripts/backfill.py`-shaped `except: logging.debug(...)` fixture; `quiet_log_is_silent=True` finds
it immediately. No change.

- **Bug class:** silent exception handler outside the gate's default-on scope
- **Repos:** none (won't-fix, existing opt-in parameters already cover it)

### NEW-41 (Med) -- sentinel_or_fallback: shapes its pyutilz counterpart catches

**Disposition:** NOT A DEFECT -- `find_sentinel_or_fallback`/`assert_no_sentinel_or_fallback` already take a
`names` parameter for exactly the proposed generalisation past the fixed settings list. Verified:
`group_id or DEFAULT_GROUP_ID` is not reported with the default `names`, but is reported immediately with
`names=DEFAULT_SENTINEL_NAMES | {"group_id"}`. No change.

- **Bug class:** `value or fallback` on a setting outside the built-in name list
- **Repos:** none (won't-fix, existing `names` parameter already covers it)

### NEW-42 (Med) -- phantom_code_references: shapes its pyutilz counterpart catches

**Disposition:** RESOLVED -- new `find_stale_absolute_line_citations`/`assert_no_stale_absolute_line_citations`:
flags a comment/docstring citing an absolute line of ITS OWN file with the explicit "line N of this file" /
"line N in this file" phrasing, where N is past the file's actual line count. Self-verifying, no baseline.
Narrowed from an initial bare `line \d+` form after the real-tree run found two false-positive classes: a
pasted traceback frame (`File "...", line 212 in __call__`, a citation of a DIFFERENT library's line) and
prose naming a different file a few words earlier in the same sentence; neither uses "of/in this file"
phrasing, so requiring it removes both. Left out (WON'T FIX, different philosophy): an unconditional ban on
citing any line number at all, which is what `pyutilz.dev.code_audit.comment_names_missing_symbol`'s
`scan_comment_cites_absolute_line` already does and is named in this gate's own docstring as staying for
that reason -- this module's other checks are all self-verifying (only report what is ALREADY wrong).
real-corpus run (this new check only): pyutilz/src 0, mlframe/src 0, after narrowing (first pass before
narrowing: pyutilz 1, mlframe 19, two of which were the false positives above). regression tests:
tests/test_phantom_code_references.py

- **Bug class:** a comment citing an absolute line number of its own file instead of a name
- **Method:** regex over comment/docstring text for the explicit "line N of/in this file" phrasing, compared
  against the file's own line count
- **False-positive risk:** a bare "line N" (traceback frames, prose naming another file) was excluded by
  requiring the explicit self-reference phrase; verified 0 findings, true or false, on two real trees after
- **Repos:** all
- **Evidence:** adopt24_upstream.md proposal 4; pyutilz's `comment_names_missing_symbol.py` docstring names
  the same recurrence ("one 92 lines out of date... a RECURRENCE of an identical finding closed in an
  earlier round")

### NEW-43 (Med) -- config_getattr_default_parity: shapes its pyutilz counterpart catches

**Disposition:** RESOLVED -- new `dataclass_field_names()` helper and `dataclass_classes` parameter on
`find_getattr_default_mismatches`/`assert_getattr_defaults_match_schema`: a `getattr(cfg, "field", default)`
naming a field none of the supplied plain `@dataclass`es declare (and no pydantic schema declares either) is
reported via a new `UNDECLARED` sentinel, alongside the existing pydantic default-mismatch check. Omitting
`dataclass_classes` reproduces the old pydantic-only behaviour exactly (verified by test). Counterpart named
in the module docstring: `pyutilz.dev.code_audit.getattr_literal_on_known_dataclass` reports a dataclass-typo
site directly; this module folds the same case in via an opt-in parameter. Not corpus-scanning (the gate
takes an explicit file + schema list per caller), so no consumer's result changes without opting in.
regression tests: tests/test_config_getattr_default_parity.py

- **Bug class:** `getattr` fallback naming a field a plain dataclass config does not declare at all (a typo)
- **Method:** AST: union of `dataclasses.fields()` names across the caller's dataclasses; a `getattr` field
  in neither that set nor the pydantic schema defaults is reported by name
- **False-positive risk:** opt-in parameter, defaults to off; no change to existing behaviour when omitted
- **Repos:** all
- **Evidence:** adopt24_upstream.md proposal 5

### NEW-44 (Med) -- pickle_state_completeness: shapes its pyutilz counterpart catches

**Disposition:** NOT A DEFECT -- the gate's own docstring already names this exact split: the
`unpicklable-without-getstate` rule is scoped to a lock/handle/etc. assigned OUTSIDE `__init__`, and states
"Inside `__init__` this is pyutilz's `unpicklable_resource_state` scanner; the lazily created one is what it
misses." The proposal's example (`self._lock = threading.Lock()` inside `__init__`) is precisely the half
already assigned to the pyutilz scanner. No change.

- **Bug class:** a lock/handle assigned in `__init__` on a class with no `__getstate__`/`__setstate__`
- **Repos:** none (won't-fix, pre-existing documented split; pyutilz's `unpicklable_resource_state` covers it)

### NEW-45 (Med) -- stale_source_citations: shapes its pyutilz counterpart catches

**Disposition:** RESOLVED (for the past-end/stale-line half); NOT A DEFECT for the vanished-file half. Added
an `extra_globs` parameter (e.g. `("*.md", "*.sql", "*.toml", "*.yaml")`): `line-past-end` and
`symbol-not-at-line` now also scan matching non-Python files for a stale `.py` citation, reading every line
as plain text since neither Python's tokenizer nor a docstring boundary applies outside `.py`; `self-citation`
stays Python-only (it is specifically about a string a logger emits from the file it names). The vanished-file
half was already explicitly out of scope by the gate's own pre-existing docstring ("a citation whose path
matches no file or several files is not judged (pyutilz's `stale_source_citation` reports vanished files)"),
confirmed from the pyutilz side too (its docstring: "It leaves a cited file that no longer exists to this
scanner... so it stays (opt-in)"). real-corpus run (pyutilz repo root, `*.md`/`*.sql`/`*.toml`/`*.yaml`/`*.yml`):
before (parameter omitted) 0 new findings (unchanged); after (parameter passed, no `exclude_parts`) 115
findings, ~110 of them inside dated, closed `audits/2026-07-21_*` and `audits/implemented/*` reports whose
prose correctly cited the line a finding sat on AT THE TIME the report was written -- expected staleness in a
historical record, not a live-doc defect; re-run with the PRE-EXISTING `exclude_parts=("audits",)` drops this
to 0. Documented in the module docstring as the scoping a consumer must apply, the same way `tests/`/
`benchmarks/` are already scoped away for the Python-only case. regression tests:
tests/test_stale_source_citations.py::TestExtraGlobs

- **Bug class:** a non-Python doc/config file citing a stale line of a `.py` source in the same corpus
- **Method:** plain-text line scan of caller-chosen globs, resolved against the same Python-corpus index the
  gate already builds
- **False-positive risk:** high if pointed at a dated audit-report archive (its prose is EXPECTED to go stale
  as code moves); mitigated by the existing `exclude_parts` parameter, not a new one -- documented, not coded
  around, since scoping the corpus is already this gate's (and the whole package's) existing idiom
- **Repos:** all
- **Evidence:** adopt24_upstream.md proposal 7; pyutilz's `stale_source_citations.py` docstring confirms the
  same split from its side

### NEW-46 (Med) -- clock_day_boundary: shapes its pyutilz counterpart catches

**Disposition:** NOT A DEFECT -- the gate's own docstring already names this exact split: "The single-shot
timing half of the original proposal (`assert elapsed < 5.0`) is pyutilz's `wall_clock_assertion` scanner,
run by `code_audit_meta`." An exact-equality assertion against a freshly-read, un-shifted `time.time()` (no
day-boundary shift at all) is that same disjoint shape. No change.

- **Bug class:** a test asserting on a bare, unshifted wall-clock read
- **Repos:** none (won't-fix, pre-existing documented split, already wired via `code_audit_meta`)

### NEW-47 (Med) -- vacuous_loop_assertions: shapes its pyutilz counterpart catches

**Disposition:** WON'T FIX -- wrong bug class, not this gate's scope. Verified empirically:
`find_floorless_loops` returns `[]` for a loop whose body is `expected = compute_from(row); assert
row.value == expected`. This is correct per the gate's own definition: the body is NOT "assert-only" (it
also assigns), so it is out of scope by design -- the gate's job is "did the loop run at all," not "is the
per-iteration assertion tautological." The proposal's actual defect (the floor is derived from the same row
it is compared against, so the assertion can never fail) is a DIFFERENT bug class -- a self-referential /
tautological assertion, which is `nondiscriminating_shapes`' territory (see NEW-48), not
`vacuous_loop_assertions`'. Widening this gate to also judge assertion tautology would conflate two
independently-tracked bug classes. No change here; redirected to NEW-48's gate instead.

- **Bug class:** (misfiled) tautological per-iteration assertion, not a zero-iteration loop
- **Repos:** none (won't-fix as filed; the underlying concern is addressed by NEW-48 instead)

### NEW-48 (Med) -- nondiscriminating_shapes: shapes its pyutilz counterpart catches

**Disposition:** RESOLVED (one of two proposed shapes); WON'T FIX for the other. Added a
`nonempty-only-assert` shape: a test function's SOLE assertion is `len(x) > 0` / `len(x) >= 1` (either
operand order) -- true for a single bad element exactly as for a correct collection, so it cannot fail on a
wrong-but-nonempty result. Scoped to a SOLE assertion so `assert len(x) > 0; assert x == [1, 2]` (a real
floor alongside it) stays clean. Left out (WON'T FIX, false-positive risk): "an assertion whose both sides
come from the same call" -- distinguishing "the identical call, so always true" from "two calls that render
identically but are not the same invocation" needs heuristics beyond a scoped AST shape (purity, intervening
mutable state); `pyutilz.dev.code_audit.nondiscriminating_test` already carries that heuristic weight and
stays the documented home for it. real-corpus run (`nonempty-only-assert` only, existing shapes unchanged):
pyutilz 15 new findings, mlframe 90 new findings; reviewed all 15 pyutilz hits and a sample of the mlframe
ones -- every one is a test whose only assertion is a bare non-emptiness/length check on a result the same
test just computed, matching the shape as defined; no false positives found. This is an opt-in addition to
the list `shape_reasons()` returns (a consumer's own meta-test decides scope, per the module's existing
design), so no consumer's enforced result changes without adding the new slug. regression tests:
tests/test_nondiscriminating_shapes.py::TestNonemptyOnlyAssert

- **Bug class:** a test's only assertion checks container non-emptiness after a mutation the test itself performed
- **Method:** AST: the function has exactly one `ast.Assert`, and its test is `len(x) > 0`/`>= 1` in either operand order
- **False-positive risk:** none found in a real-tree sample of 15 (pyutilz) plus a spot-check of 90 (mlframe); scoped to a SOLE assertion to avoid flagging a non-emptiness check alongside a real value assertion
- **Repos:** all
- **Evidence:** adopt24_upstream.md proposal 10; pyutilz's `nondiscriminating_test.py` registry entry

### NEW-49 (Med) -- inert_patch_targets: shapes its pyutilz counterpart catches

**Disposition:** WON'T FIX -- documented, measured false-positive rate, by design. The gate's own docstring
is an extended argument for why exactly this shape (`mock.patch("pkg.mod.NAME")` where `NAME` is a
re-export) was implemented and then REMOVED: "That rule was removed after it reported 337 findings on its
first real tree, and the reason is not that it needed tuning. A patch is very often placed precisely so that
something is NOT called, and 'nothing reads this name' is then the intended state rather than a defect. The
two cases are indistinguishable from the source." Re-adding it would reproduce the same 337-finding
false-positive rate already measured and rejected. `pyutilz.dev.code_audit.reexport_patch_target` /
`patch_target_is_a_reexport` are named in the proposal itself as already covering it (decision b: pyutilz
keeps every scanner regardless of gate overlap). No change.

- **Bug class:** `mock.patch` of a re-exported name rather than its defining module
- **Repos:** none (won't-fix, 337-finding false-positive rate measured and documented in the gate's own history)

### INFRA-1 (High) -- pytest11 plugin py_ci_shared.pytest_plugin

**Disposition:** RESOLVED -- `[project.entry-points.pytest11] py_ci_shared = "py_ci_shared.pytest_plugin"`: inert without `[tool.py_ci_shared]`; with it, a bare `pytest` (or `--py-ci-gates=on`) gets one `pyproject.toml::<gate>` item per enabled gate that calls the entry with the table's kwargs from the repo root; `--py-ci-refresh` is wired to `_core.refresh` through the env var; a malformed table is a usage error; regression test: tests/test_pytest_plugin.py::test_a_repo_without_the_table_sees_no_gate_items, tests/test_pytest_plugin.py::test_a_bare_run_adds_one_item_per_gate_and_reports_the_gates_text, tests/test_pytest_plugin.py::test_selecting_paths_leaves_gates_out_unless_forced, tests/test_pytest_plugin.py::test_a_malformed_table_is_a_usage_error

- **Priority:** P0
- **Proposal:** No `[project.entry-points.pytest11]` exists today, so every

### INFRA-2 (High) -- Gate-teeth self-check

**Disposition:** RESOLVED -- tests/test_gate_teeth.py runs 53 corpus-scanning gates through their public `assert_*` entries over the canary corpus: the violation must fail naming the seed, the clean control must pass, the BOM copy must be reported exactly as the plain violation, an unparsable file must fail the gate (by name unless it replaces the single file passed), and an empty corpus must fail (floor). A static teeth-check per gate asserts that violation != clean, that bom == BOM + violation byte for byte, that violation and clean parse, and that the unparsable file really does not parse. A meta-test requires every registered gate/library module, and every unregistered public module, to have a canary or a reasoned EXEMPT entry. Confirmed gate defects are strict xfails (listed below); regression test: tests/test_gate_teeth.py

- **Priority:** P0
- **Proposal:** Every gate module ships `SEEDED_VIOLATIONS` (snippet + expected finding) and a clean

### INFRA-3 (High) -- Canary corpus

**Disposition:** RESOLVED -- tests/canary/<gate>/{violation,clean,bom,unparsable,support}/ holds a few-line mini-repo per gate (53 gates). Files carry a `.canary` suffix, so pytest and this repo's own gates neither collect nor scan them. The test copies each variant into tmp_path and strips the suffix, so the repo is never written. Non-Python scanners (workflow YAML, pyproject TOML, SQL, Markdown) are included where the gate takes a path.

- **Priority:** P1
- **Proposal:** `tests/canary/` holds one mini-repo per bug class; CI runs every gate over it, so a

### INFRA-4 (High) -- Per-gate runtime budget

**Disposition:** RESOLVED -- each gate carries `budget_s` in the registry (30 s default, 60 to 900 s for subprocess-heavy ones); the runner times every call; the pytest item warns (PytestWarning) past the budget and fails under `budget = "fail"`, `run-all` prints a BUDGET line and exits 1 in fail mode; a table's `budget_s` overrides; regression test: tests/test_pytest_plugin.py::test_a_gate_over_budget_warns_and_fails_in_fail_mode, tests/test_cli.py::TestCommandLine::test_budget_fail_mode_fails_a_passing_gate_that_ran_too_long

- **Priority:** P1
- **Proposal:** The plugin times each gate and fails past a `budget_s` from the registry, keeping

### INFRA-5 (Med) -- Registry + README parity

**Disposition:** RESOLVED -- `py_ci_shared.registry` (data in registry.toml: name, kind, entries, since from the first tag containing the module, budget_s, cli, summary, tests) with `unregistered_modules()` discovery and `render_catalogue()`; README parity, test-file parity and entry parity are tested; regression test: tests/test_package_inventory.py::test_each_spec_matches_its_module, tests/test_package_inventory.py::test_the_readme_catalogue_is_the_rendered_registry

- **Priority:** P2
- **Proposal:** A single `GATES` registry (name, kind, since, budget, teeth fixture) wired to

### INFRA-6 (Med) -- Consumer adoption matrix

**Disposition:** RESOLVED -- `src/py_ci_shared/adoption_matrix.py` (registered as a `cli` module, `py-ci-shared tool adoption_matrix`) reads consumer repo roots (args or `--repos-file` TOML) read-only and reports pins per location (pyproject dependency tables, `[tool.uv.sources]`, ruff `extend`, requirements, `uv.lock`, pre-commit `rev`, workflow `uses:` refs, `py-ci-shared-ref` inputs, pip installs, `git clone`, `actions/checkout`), pin agreement (`--resolve-in` resolves tags and short SHAs through a py-ci-shared checkout), moving pins, module usage (imports, `python -m`, `py-ci-shared run`, `[tool.py_ci_shared]` tables), silent skips (importorskip, collect_ignore, exit 0, pytest.skip, availability flag nobody checks loudly, swallowed ImportError) and local copies via `local_copy_report.find_local_copies`. The output is a markdown summary, pin table, modules x repos matrix, workflow matrix and findings list. Exit 1 on disagreeing pins, moving pins or silent skips; `--advisory` exits 0. Real run: `audits/2026-09-24/adoption_matrix_2026-09-24.md`; regression test: tests/test_adoption_matrix.py

- **Priority:** P2
- **Proposal:** Extend `config_drift_check` to report which consumers run which gates and which
