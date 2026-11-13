# Changelog

Milestones only; the commit log has the detail. Versions are the release tags (`vX.Y.Z`).

## Unreleased (next: 1.23.10)

- New `cuda_offline_report` (additive library): the offline NVRTC -> ptxas engine a consumer's CUDA meta-test builds on, moved here from a consumer's `tests/`: `build_ptx_and_report` (PTX plus a report that starts with the NVRTC log of the successful build, lines prefixed `nvrtc: `, then `ptxas -v`; a variant that names its own `-std=` is built with that one only), `entry_reports` (registers, shared memory, stack and spill bytes joined with the f64/f32 opcode counts by entry name), `launch_bounds_violations`, `occupancy` / `blocks_per_sm` over the `ARCH_LIMITS` table, `required_arch`, `find_ptxas`, `local_device_limits` and a markdown report with a per-architecture failure table. The kernel sources and their placeholder recipes stay the consumer's.
- New `cuda_kernel_names` (additive, `find_cuda_kernel_names` / `assert_cuda_kernel_names`): every `__global__` kernel name written in a file must appear in a kernel source the consumer's offline compile check built for that file (`compiled`: file path relative to the root -> source texts; `exclude` names out-of-scope path parts). A template spelling such as `bin_codes_TYPENAME` matches a compiled name with its fixed prefix. Rule `cuda-kernel-not-compiled`; it finds what a file-level coverage check misses when only one kernel of a file was extracted.
- New `cuda_kernel_factories` (additive, `find_cuda_kernel_factories` / `assert_cuda_kernel_factories`): a module-level `kernel` / `kernel_*(cp, real, ...)` factory that the consumer's register-pressure registry does not list (the consumer passes `registered`, `(dotted module, function name)` pairs, matched by the file's dotted path or its tail) is a kernel whose spills nobody checks on a device. Rule `cuda-kernel-factory-unregistered`; methods, nested functions and other signatures are not factories.
- **Behaviour change** (`cuda_launch_geometry`): the gate now also reports a launch with a literal block size when its grid or arguments are not tuple literals (`kern(grid, (256,), (x,))`, `kern((n,), (256,), args)`, `kern(grid, (256,), args)`), when the block is arithmetic over literals (`(2 * 128,)`) or `int(256)`, and for a numba launch `kern[blocks, 256](x)` (block of at least 32 threads for these loose shapes, since a tuple in the second slot of a call is often a shape; calls on `np`/`cp`/`itertools` are never launches). The strict shape and its rules are unchanged; found in a consumer where two literal-256 histogram launches had the grid in a variable. What to do: run the gate; for each new finding take the block from the device or mark the launch `# launch-ok: <reason>`.
- New in-place exemption for `patch_target_read_at_call` (additive): a `monkeypatch` line carrying `# audit-exempt: patch_target_read_at_call -- <reason of at least 20 characters>` is no longer reported, for a reader that resolves the patched name through an accessor the static scan cannot follow (a parent-module bridge such as `_get_live("NAME")`). The check id uses underscores because the shared marker regex allows no hyphens; a marker with no reason, or naming another check, still leaves the finding.
- `complexity_ratchet` accepts a second baseline entry form, `{"count": ceiling, "note": reason}`, next to the bare ceiling. The gate reads the ceiling from either form; `load_complexity_notes` returns the reasons; a refresh keeps the reason of every entry it keeps. What to do: nothing for a consumer whose baseline holds bare ceilings; a consumer that wants a reason next to an entry writes the record form.
- **New** `mutation_rerun` (CLI): the per-module mutation drivers that lived in a session scratchpad, now shared. `python -m py_ci_shared.mutation_rerun --repo . sweep <module> <tests> <checkpoint>` runs a whole module with a resumable checkpoint; `… lines <module> <tests> <survivor-log> <notes.json> [min max]` re-checks only the survivor lines and records a survivor in the journal only when the notes explain it. Paths are relative to `--repo`, the journal path is `--survivors` (default `tests/test_meta/mutation_survivors.json`). What to do: nothing for a consumer that does not run mutation sweeps; a consumer that did may delete its own copies of these scripts.
- **Behaviour change** (`ci_health`): a workflow counts toward a consumer's red days only when its file has a live trigger (`push`, `pull_request`, `pull_request_target` or `schedule`); a `workflow_dispatch`-only workflow no longer keeps a red last scheduled run in the report. Private consumers are left out of the public report unless `--include-private` is passed. What to do: nothing for a public consumer whose workflows all run on push or schedule. A consumer that wants its private repos in the report runs `ci_health --include-private` (the scheduled workflow must pass it to keep them).
- Self-hygiene (audit 52-F6, no behaviour change): every public module defines `__all__`, the 61 public functions without a docstring have one, the 20 unannotated ones are annotated, and library functions no longer call `print` (diagnostics go to stderr; the verifier report of `sql_verify` stays on stdout). `mutation_teeth.HARNESS_VERSION` is now 37 because the tracked sources changed: a consumer's mutation cache is recomputed once, verdicts are unchanged.
- `uncalled_functions` takes `private_only=True` (additive, default off; also a `[tool.py_ci_shared.gates.uncalled_functions]` key): only functions whose own name starts with an underscore (not a dunder) are judged, so a library whose public functions are called only by consumers can still find dead private code. The findings are exactly the private subset of the default ones. This repo enables it over `src` (audit 52-F4): it found 6 private functions with no production caller; 2 were deleted (`possibly_unbound_local._deletes`, `_mutation_operators._container_members` with its unused re-export in `mutation_teeth`) and 4 are allowed by name with a reason in `pyproject.toml`.
- Internal self-gate change (this repository only): `loc_budget` now also covers `tests/**/*.py` (empty baseline). `tests/test_mutation_teeth.py` and `tests/test_gate_teeth.py` were split into parts; `py-ci-shared new-gate` now inserts the `CANARIES` entry into `tests/_gate_teeth_canaries.py` instead of `tests/test_gate_teeth.py` (a developer-facing change for contributors of this repo, not for consumers).
- `local_copy_report`: the content signature of `nondiscriminating_shapes` now names the shapes that gate checks (wide literal range, envelope assert, median round-trip, late skip, non-empty-only) instead of a helper called `_*tautolog*`, which matched glossum's tautology scanner (a different rule) and kept `adoption_matrix` red (fewer findings).
- `adoption_matrix` no longer reports a dunder attribute of the package (`py_ci_shared.__file__`, `__version__`) as an `unknown-module` (fewer findings; pyutilz's pin-coverage test reads `py_ci_shared.__file__`).
- **Behaviour change** (`adoption_matrix`, audit 47-F8): a run without `--resolve-in` now exits 1 unless `--advisory` is given, because the stale-pin, unresolvable-pin, pin-comment and waiver checks did not run and an exit 0 claimed they had passed. What to do: give `--resolve-in <a py-ci-shared checkout with its release tags>`, or pass `--advisory` to keep reporting without failing.
- New `raw_write_row_count` (audit 50-F3, additive): a raw UPDATE or DELETE sent through `execute` in a function that never reads `rowcount` is a finding. Migrations and `batch_*` scripts are not judged. `assert_raw_write_row_count(root, report_only=True)` prints the findings and never fails: the first step before the gate blocks.
- **Behaviour change** (`fail_open_handlers`, audit 37-F35 item 3): a handler that returns a reason code (three or more lowercase words joined by underscores, such as `modifier_refused_by_the_graph`) now counts as reporting the failure in-band, so it is no longer a silent default. What to do: nothing, unless your gate relied on that finding; the gate now finds fewer handlers.

- New `adoption_sha_ref` (CLI, audit 47-F10 and 43-F9, additive, report only): flags a consumer workflow that pins a between-release SHA of the reusable workflows without `py-ci-shared-ref`, which runs an older configuration than its `# vX.Y.Z` comment suggests. It does not fail a consumer until the consumers pass the input. What to do: nothing to do; run `python -m py_ci_shared.adoption_sha_ref --repos-file repos.toml --resolve-in ../py-ci-shared` to see which consumers still need the input.

### Strict private consumers, public names, registry summaries (audits 46-F8, 47-F5, 43-F6, 43-F8)

- **Behaviour change (ci-health, corpus-drift):** a private consumer skipped for want of `CONSUMER_READ_TOKEN` is now a RED row (`ci_health --fail-on-skipped-private`: a table row `NOT CHECKED`, an `::error::` annotation, exit 1) and `corpus-drift.yml` passes `--fail-on-skipped-private` to its checkout, exactly as `consumer-pins.yml` already did. The scheduled `ci-health.yml` and `corpus-drift.yml` pass the flag unless a manual run sets the new `allow-skipped-private` input. While the secret is missing or expired both scheduled jobs are red; `consumer-pins` was already red for the same reason. The flag is off by default on the command line (`ci_health` and `_consumers checkout` keep their warning). What to do: keep the fine-grained secret `CONSUMER_READ_TOKEN` (actions:read, contents:read, metadata:read on the private consumers) valid; it was created on 2026-11-07 and `consumer-pins` then cloned all 13 consumers. If it expires, renew it, or run the jobs by hand with `allow-skipped-private`. With the private repos now actually read, what shows red is real consumer state (a run found mlframe with silent importorskip skips and social with unpinned workflows), not these changes. With the secret absent, public consumers are still checked out and scanned; the strict flag only decides the exit code.
- **Behaviour change (corpus-drift):** `configs/consumers.toml` marks `glossum_backend_scripts` and `algopacksimple` (both private) `corpus = true`, so the nightly snapshot scans them and the first night after the token exists adds their finders to the baseline's comparison (a count that is new in a repo is not a jump, but any gate that raises on them fails the night). Every consumer outside the corpus now says why in `consumers.toml`. What to do: run `corpus-drift` by hand with `accept: true` once if the first night reports drift for the two new repos.
- Public names for what consumers imported by their underscore spelling (additive, the old names stay as aliases): `py_ci_shared.code_audit.is_test_file` and `is_locals_or_globals_call` (were reachable only through `code_audit._base`), `code_audit.registry.physical_cpu_count` and `MIN_SCANNERS_PER_WORKER`, `sql_verify.LITERAL_PERCENT`, and the module `py_ci_shared.toml_compat` (`_toml_compat` re-exports it). Registered in `registry.toml` (`toml_compat`) and `INTERNAL_MODULES`; `tests/test_promoted_public_names.py` pins the aliases.
- New `consumer_import_census --private-imports report|fail [--only-private]` and `find_consumer_private_imports` / `assert_no_consumer_private_imports` (additive, rule `consumer-private-import`): an import of a module path with an underscore component, or of an underscore name, from the package. `consumer-pins.yml` runs it over every consumer in `configs/consumers.toml` in `report` mode (no new red). Consumers would change: pyutilz (`tests/conftest.py`, `tests/test_architecture_audit_20260903.py` and other files) `from py_ci_shared.code_audit._base import is_test_file, is_locals_or_globals_call` becomes `from py_ci_shared.code_audit import ...`; `from py_ci_shared.code_audit.registry import _physical_cpu_count, _MIN_SCANNERS_PER_WORKER` becomes `physical_cpu_count, MIN_SCANNERS_PER_WORKER`; the two `sql_verify` consumers `_LITERAL_PERCENT` becomes `LITERAL_PERCENT`; the two files that use `from py_ci_shared._toml_compat import tomllib` use `from py_ci_shared.toml_compat import tomllib`. Nothing breaks if they do not: the old spellings are kept for the whole v1 line.
- Registry summaries (audit 43-F8): `tests/test_registry_summaries.py` holds every `summary` to balanced code spans, a capital first letter, no trailing period or dangling word, no entry-function prefix, and at least half of the words of the module docstring's first paragraph. Twenty-one summaries were rewritten from their docstrings (among them `ci_workflow_gate`, which was cut off inside a code span and printed `Every ``continue-on-error` in the README, and `setup_env`, cut off mid-quote); the README catalogue was regenerated from the registry. Documentation only.

### Strict baselines, opt-in stubs, fail-closed predicates 2026-11-07 (audits 42-F4, 42-F5, 42-F9, 41-F7)

- **Behaviour change (stale baseline entries fail):** `baseline_ratchet.Baseline.enforce` returns 1, and `_core.BaselineOutcome.ok` is False, when the baseline accepts a key the scan no longer finds or a count above what is still found (a fixed violation left a free slot that a later regression of the same key took for nothing). New `BaselineOutcome.blocking` keeps the old meaning (new, unjustified or missing); `raise_for_pytest` already failed on stale entries unless `fail_on_stale=False`. Callers that tested only `.ok` (`mutation_teeth`, `unexecuted_function_bodies`) are strict through the property; `enforce(..., fail_on_empty_scan=False)` now also fails on the stale entries until the baseline is refreshed. What to do: run the gate's refresh command (`py-ci-shared refresh <gate>` or the `pytest --refresh-...` flag it names); the refresh only shrinks the baseline, nothing is lost.
- New `py-ci-shared tool stale_baseline_sweep REPO...` (additive, read-only: `git archive HEAD` into a temporary directory, never writes a consumer) counts, per consumer, the baselines that would newly go red. Measured on the consumer checkouts at their HEADs 2026-11-07 (pytest files importing `py_ci_shared`, mutation tests excluded): pyutilz, llm_bench, algopacksimple, dash_app_core, flutter_uptime_monitor, claude-usage-notifier 0 stale entries; mlframe, glossum_backend_scripts and autopsia (pytest timed out at 1500 s, no stale entry seen before it), social (19 collection errors) NOT measured; noema_app, polyvocab_app and flutter_app_core have no Python gate tests. So no measured consumer goes red on the bump; the mutation baselines of pyutilz and mlframe (`mutation_teeth`) need their multi-hour run and are not covered.
- `_core.iter_files` and `scan_python` take `include_stubs=False` (default unchanged): `True` also matches `.pyi`, `.pyw` and any-case `.py`. **Behaviour change (more files scanned):** `unsafe_deserialization`, `predictable_tmp_path`, `systemexit_in_library`, `swallowed_exceptions` and `naive_utcnow` opt in (runtime-hazard gates, where an `X.PY` or `.pyw` script is real code and a stub has no body to flag). No consumer checkout holds such a file today, so no finding changes there. What to do: nothing, unless a finding appears in a `.pyw`/`.PY` file; fix it or baseline it.
- **Behaviour change (`fail_open_handlers`, fewer findings):** a `silent_default_return` handler that returns `False`, `None`, `0`, `''` or a bare `return` in a predicate-named function (`is_*`, `has_*`, `can_*`, `check`/`check_*`, `verify*`, `valid*`, `*_ok`, `*_trusted`, `*_valid`, `*_verified`, `*_usable`, `*_available`) is exempt, and so is any handler marked `# fail-closed: <reason>` on the `except` line or the line above. `return True` is still flagged, as is a non-predicate returning `False`; the `token`/`digest` cache-name exemption is unchanged. On autopsia's baseline this clears `_is_trusted`, `load_valid`, `load_verified`, `is_minted_case_id` and `cupy_device_usable`; `_secret`, `load_verified_bytes`, `trailer_mac`, `signed_pickle` and `load_record` stay flagged until marked. What to do: refresh the baseline to drop the cleared keys (stale entries fail), and mark a fail-closed helper with a non-predicate name `# fail-closed: <reason>`.

### New gates from audit 50 (2026-11-07)

- New gate `possibly_unbound_local` (additive): a definite-assignment pass that reports a read of a name bound on only some paths (`possibly-unbound-after-if`, `-after-try`, `-after-match`), the mlframe 169f17000 Muon dispatcher shape (`device_name` bound in the `verdict is None` branch, read by a log line after it: `UnboundLocalError` on every cached call). A consumer would see findings where two tests agree (`if flag: a = 1` ... `if flag: use(a)`); a path ending in `raise`/`return`/`continue`/`pytest.fail` binds nothing, loop-bound names count as bound. Marker `# unbound-ok: <reason>` on the read; baseline supported. Measured at introduction: pyutilz/src 22, glossum_backend_scripts 44, algopacksimple 25, mlframe/src 281, llm_bench/src 0; this repo's own src needed one marker.
- New gate `uv_lock_parity` (additive): `uv.lock` against the `pyproject.toml` beside it, offline: `uv-lock-missing-requirement`, `uv-lock-specifier-differs`, `uv-lock-stale-requirement`, `uv-lock-requires-python-differs`, `uv-lock-no-root-package` (mlframe b5931c12b: the lock drifted and the lint job's `uv lock --check` failed on every run). A consumer without a `uv.lock` has nothing to compare (`min_files=1` fails an empty scan, so do not enable it there). Measured: mlframe 0, glossum_backend_scripts 0; pyutilz, llm_bench and algopacksimple have no `uv.lock`.
- New gate `nested_numba_parallel_guard` (additive): a thread pool (`ThreadPoolExecutor`/`ThreadPool`, `threading.Thread`, joblib threading backend) with an unguarded call path (up to 6 hops, by function name, through dispatch tables) to a `parallel=True` numba kernel (`nested-parallel-via-executor`, `-via-thread`, `-via-joblib`); two threads in one prange region abort the process on macOS. `guard_name` (default `parallel_kernel_entry`) names the context manager; marker `# parallel-guarded: <reason>` on the pool line or its `def`; baseline supported. Measured: mlframe/src 8, pyutilz/src 0, glossum_backend_scripts 0, algopacksimple 0, llm_bench/src 0.
- New gate `nan_admitted_by_reject_guard` (additive): `if loss >= worst: continue` (or `break`/`return`) on a score-like name against a bound-like name in a function with float evidence admits NaN (`nan-admitted-by-loop-reject`, `-by-return-reject`; mlframe 584c9d880). Marker `# nan-ok: <reason>`; baseline supported. Measured: mlframe/src 1, pyutilz/src 0, glossum_backend_scripts 0, algopacksimple 0, llm_bench/src 0.
- New gate `instance_clear_leaves_module_cache` (additive): a `clear*`/`reset*`/`invalidate*` (`instance-clear-leaves-module-cache`) or `release*`/`free*`/`dispose*` (`instance-release-leaves-module-cache`) method that references `self` but reaches none of the cache-named module-level containers its sibling methods use (mlframe 29f1e5c8d: `clear_cache()` left the module LRU holding the C++ matrix). Marker `# cache-scope-ok: <reason>` on the `def`; baseline supported. Measured: 0 in pyutilz, mlframe, glossum_backend_scripts, algopacksimple, llm_bench (the one hand-found pyutilz case is a warn-once set, not named like a cache, and is not matched).
- New gate `tool_config_env_vars_set_in_every_tool_job` (additive): a `$VAR` a `[tool.<name>]` table of `pyproject.toml` reads (llm_bench d5990de: `[tool.ruff] extend = "$PY_CI_SHARED_DIR/..."`) must be defined in every workflow job that runs the tool (`tool-env-var-unset-in-job`) or runs pytest over a test that shells out to it (`tool-env-var-unset-in-test-job`). `allow_jobs=["file.yml::job"]` skips a job; baseline supported. Measured: glossum_backend_scripts 2 (`ci.yml` test, `nightly-thorough.yml` property-tests), pyutilz 2, algopacksimple 0, llm_bench 0.

- New gate `njit_twin_untested` (additive): the inventory of where a pure-Python twin of a numba kernel is owed a parity test, scoped to code that says a second implementation exists, not to every `@njit` (mlframe has 1065 kernel names and 5 test files that touch `py_func`). Rules: `njit-flag-fallback-untested` (a plain function branching on `_HAS_NUMBA`/`_NUMBA_AVAILABLE`/`numba_available()` that also calls an njit kernel: the mlframe 1b56cfb90 frac-diff shape), `njit-twin-name-untested` (`foo`/`foo_py`, `foo_numpy`, `foo_reference`..., or `foo_njit`/`foo`), `njit-redefined-twin-untested` (one name defined with and without `@njit`), `njit-parity-marker-untested` (`# parity-test:` on an njit def is the author's own obligation). Evidence is a test file that reaches the kernel through `py_func`/`assert_njit_twin_parity`, toggles the flag by name, or names both twins. `tests_root=` points at a separate tests directory; `# parity-ok: <reason>` waives; baseline supported. Measured (obligations / unmet): mlframe 82 / 55 (43 flag, 4 name, 8 redefined; 70 flag obligations before tests, so 27 are already covered), pyutilz 1 / 0, algopacksimple 0 / 0, llm_bench 0 / 0 (no njit).
- New library helper `njit_twin_parity.assert_njit_twin_parity(kernel, inputs_factory, ...)` (additive): runs `kernel` and `kernel.py_func` (or `twin=`) on seeded inputs, each with its own deep copy, and compares the returns (nested tuples, lists, dicts, arrays, scalars) and every array argument after the call, bitwise by default (NaN equals NaN, `-0.0` differs from `0.0`), with `rtol`/`atol` where a different summation order is documented. The failure names the case and seed, the path of the differing element, how many differ, both values with their bit patterns and the largest gap. Skips (never silently passes) when numba is absent or the kernel has no `py_func`; `kernel_contexts`/`twin_contexts` toggle a flag around a dispatcher compared with itself. numpy is imported lazily. Its tests run with fake dispatchers everywhere and with real numba where `numba` is installed (`numba` joins the `dev` extra).
- New library helper `launch_dtype_audit` (additive): the launch-time CUDA argument dtype audit promoted from mlframe (`kernel_arg_dtype_audit`, 6e2cc9c97 row_argmax). `kernel_parameters(source, name, defines={"REAL": "float"})` parses a (templated) `__global__` signature, `assert_launch_dtypes(source, name, args, defines)` checks an argument list with no kernel at all, `audited_kernel(kernel, source, name, defines)` refuses a mismatched launch before it happens (or records it), `audit_cupy()` swaps `cupy.RawKernel`/`RawModule` for auditing twins, and `python -m py_ci_shared.launch_dtype_audit script.py` runs a script under it (exit 1 on a mismatch). Arguments are duck-typed, cupy is imported only inside `audit_cupy`, numpy never: the tests use fake kernels and arrays and need no GPU runner.

### Deferred audit findings worked 2026-11-07 (42-F11, 41-F12, 41-F13, 48-F3)

- **Behaviour change (corpus, audit 42-F11):** a work tree that git refuses as "dubious ownership" (a runner whose checkout belongs to another user, `safe.directory` unset) raises `CorpusError` naming the fix instead of silently becoming a directory walk that read ignored files and nested clones. What to do: run `git config --global --add safe.directory <path>` on the runner (actions/checkout does it for its own workspace), or pass `use_git=False` to walk on purpose. Measured on pyutilz, mlframe, llm_bench, glossum_backend_scripts, algopacksimple and py-ci-shared: no corpus changed.
- **Behaviour change (corpus walk, audit 42-F11):** the walk (no git, `use_git=False`, `iter_files_pruned`) also prunes a directory that holds a `.git` DIRECTORY (a nested clone or vendored repository), as it already pruned a `.git` FILE (submodule); git mode never listed their content. Nothing to do on the six consumer trees above: none holds a nested `.git` directory.
- **Behaviour change (`skip_as_pass`, audit 41-F12, fewer findings only):** shell is read by a block parser (`_shell_blocks`), so each `run:` step of a workflow is its own script, a `SKIPPED` print is paired only with an exit in its own `if`/`case`/group/function body, and a top-level unconditional `echo "SKIPPED <optional step>"` in a script that already ran real commands is a progress message, not a guard. What to do: a baseline entry that no longer reproduces fails as stale; delete it. The one consumer baseline (flutter_app_core) is empty.
- `tracker_summary_parity.disposition_state_problems` (audit 41-F13, additive; `assert_tracker_summaries_agree(..., check_states=True)` adds it, default off): one finding must carry the same state word in its tracker row, its round-file table row and its `Disposition:` line, and a RESOLVED cell must not say the finding was not reproduced. This repository's own `audits/` now pass it (tests/test_tracker_summary_parity.py).
- `_core.relative_posix` slices the string when the path is the root plus plain segments instead of calling `Path.relative_to` per file (audit 48-F3): a warm `scan_python` is faster, results identical.

### Release and CI surface 2026-11-07 (audits 43, 46, 47)

- **Behaviour change (reusable workflows `docs.yml`, `mypy-full.yml`, `lint-blocking.yml`):** `install-command` and `deptry-install-command` reach the step through the environment and run as `bash -eo pipefail -c "$VAR"` instead of being expanded into the script text. A multi-line command still runs line by line and stops at its first failure. What to do: nothing, unless the command relied on the caller's `${{ }}` expansion inside the string (it is now passed as written; move such a value into your own `env:`).
- The four reusable workflows' `py-ci-shared-ref` default is the last PUBLISHED tag between releases; `release_guard.py prepare` moves all four (it listed three in prose, four in code). Additive. A caller on master no longer gets a ref that does not exist yet.
- `release_guard.py`: `ci-green` refuses a SHA that is not 40 lowercase hex characters (exit 2), looks only at runs on master, and says how to start a fresh run when only cancelled runs exist; it prints failed jobs of a green run; `rollback --repo` requires a GitHub release for the target; `prepare --check` runs in the Release `verify` job, which now waits up to 100 minutes for self-ci. Additive.
- `self-ci.yml`: push runs are grouped per commit (a newer push no longer cancels a queued run of the previous commit), `workflow_dispatch` is allowed, and a `preview-report` job lists failed `continue-on-error` legs. Additive.
- `ci_health`: a scheduled workflow whose last three scheduled runs were all cancelled is red. New `configs/self_watch.toml` makes `ci-health.yml` read this repository's own schedules. **Behaviour change** for any consumer whose nightly workflow is cancelled every night: it now fails the report. What to do: fix the workflow's timeout, or accept the red.
- **Behaviour change (`adoption_matrix`, new finding `ci-pin-comment-not-release`):** a `Pin` now carries its trailing `# ...` comment, and with `--resolve-in` a fixed pin followed by `# vX.Y.Z` is compared with the commit of that tag (`git rev-parse vX.Y.Z^{commit}`); a different commit (or a tag that does not exist) is reported. It is report-only: the exit status changes only with the new `--strict-pin-comments`, so `consumer-pins.yml` stays green. Without `--resolve-in` the comments are not checked and a note says so. Run against the sibling checkouts on 2026-11-07 it flags mlframe (adba0d7, 24 pins), pyutilz (64e63f5, 12), social (e13229e, 16 pins, and 177acd3 labelled v1.23.3 in `upwork/new_scraper/production_scrapers/requirements.txt`), dash_app_core (e13229e, 1), llm_bench (e13229e, 5) and algopacksimple (e13229e, 8), all labelled `# v1.23.8` while that tag is 630d5b7 (audits 46-F10, 47-F2). What to do: in each consumer pin the tag commit (630d5b7) or change the comment to `# master <sha7>`; pass `--strict-pin-comments` once they are clean.
- `adoption_matrix --waivers FILE` and `configs/pin_waivers.toml`: dated per-consumer lag waivers, so accepted lag is reported as a note and an expired waiver fails again. Additive.
- `registry.toml` `since` of eleven modules corrected to the first live tag that contains them (the README catalogue follows); v1.22.0 and v1.22.2 are dead tags. `tool_versions.MYPY_VERSION` added. Additive.

### Interpreter-independent finding text 2026-11-07

- **Behaviour change (baseline keys, Python 3.9 and 3.10 only):** a finding message that quotes a node is a baseline key, and `ast.unparse` wrote `lambda : 1`, `for (a, b) in c:`, `[x for (a, b) in c]` and `(a, b) = c` before 3.11 where 3.11+ writes `lambda: 1`, `for a, b in c:`, `[x for a, b in c]` and `a, b = c`. A baseline refreshed on 3.11 therefore looked stale on the 3.9 and 3.10 legs, and the same findings looked new. New `py_ci_shared._core.unparse.unparse_stable(node)` renders the 3.11+ shape on every interpreter, and all 53 modules that put a node in a message (alembic_concurrently, naive_utcnow, hash_key_determinism, atomic_write_staging and the rest) use it; `tests/test_unparse_stable.py` bans a bare `ast.unparse` (or `getattr(ast, "unparse", ...)`) in `src`. What to do: nothing on 3.11+. A baseline refreshed on 3.9 or 3.10 whose keys contain `lambda :` or a parenthesised tuple target fails once on those legs; refresh it on any interpreter.
- **Behaviour change (Python 3.9 only):** `resource_leak_checks._imported_library` took `getattr(sys, "stdlib_module_names", frozenset())`, an attribute that exists from 3.10, so on 3.9 every lazily imported standard-library module counted as a library and exempted the test from the stream, `sys.path` and warning-filter leak checks. It now asks `_ci_install_parts.is_stdlib`, whose 3.9 path also recognises the compiled stdlib modules in `<prefix>\DLLs` (`_zoneinfo`, `_ssl`). `code_audit.undeclared_imports` and `phantom_code_references` use the same predicate instead of an empty fallback, and a meta test bans `getattr(sys, "stdlib_module_names", <empty>)` in `src`. What to do: on 3.9 a test that leaks a stream, a `sys.path` entry or a warning filter after a lazy stdlib import is now reported, as it already was on 3.10+.
- **Behaviour change (duplicate function bodies):** `duplicate_function_body` and `near_duplicate_function_body` fingerprint a body with `drifted_duplicate_functions._canonical` instead of `ast.dump`. The token count of one function was 652 on 3.9 and 3.11 and 597 on 3.13 and 3.14, so a pair a hair under the 0.99 / 0.98 similarity threshold flipped between interpreters. What to do: a baseline of either gate may differ by a few borderline pairs; refresh it.
- **Behaviour change (Windows file locks, audit 45-F6):** `_core.atomic_write_text` (baseline refreshes), `install_safe_hook`, the code-audit scan cache and the vermin result cache replace their staged file through the new `_core.replace_file`, which retries a Windows `PermissionError` three times (0.05 s, 0.1 s) before raising. What to do: nothing; a refresh or hook install that used to fail once while an editor or indexer held the target now succeeds, and a lock held longer still raises as before.
- New `tests/test_cross_interpreter_keys.py` runs one corpus through unparse-keyed and fingerprint-keyed gates on every other installed Python (uv's directory, or `PY_CI_SHARED_OTHER_PYTHONS`) and fails when baseline keys differ. Test only. The stale "supports 3.8" comments above removed fallbacks are gone. Additive.
- `self-ci.yml` gains a non-blocking `ubuntu-24.04` / Python 3.14 leg (`preview: true`). Promote it to blocking after one green run.

### Runner and CLI outcomes 2026-11-07 (audit 49 F3-F5/F7-F9/F11, audit 40-F9)

- **Behaviour change (`py-ci-shared refresh` output):** a gate that rewrote its baseline is reported as `REFRESH <gate>: +N -M entries (file)` and counted as `N refreshed` in the summary line, no longer as `SKIP` / `1 skipped` (the exit code is unchanged, 0). Under pytest nothing changes: the gate still skips. What to do: a script that greps `skipped` in `py-ci-shared refresh` output to detect a refresh should grep `refreshed`; nothing to do otherwise.
- **Behaviour change (`py-ci-shared run`/`run-all` output):** the `BUDGET` row is printed on stdout under its gate (it went to stderr), a skipped gate prints `skipped: <reason>`, a PASS replayed from the gate cache adds `(N cached)` to the summary line, and the summary gains `, N refreshed` only when a gate refreshed. Exit codes are unchanged. What to do: a CI step that reads the BUDGET line from stderr must read stdout.
- **Behaviour change (error text):** a gate whose `[tool.py_ci_shared.gates.X]` table omits a required argument is an ERROR (exit 2, as before) that names the table and the missing parameters instead of a bare `TypeError`; a gate crash ends with `(at file:line; PY_CI_SHARED_TRACEBACK=1 prints the traceback)`; the missing-baseline and stale-entry messages name `py-ci-shared refresh <gate>` under the CLI (the pytest flag under pytest). What to do: nothing, unless a script matches the old message text.
- New `skip = "fail"` in `[tool.py_ci_shared]` (default `"ok"`, so green consumers stay green): a gate that skips because it cannot judge its input (a CHANGELOG without the section `changelog_promise_parity` scopes to, `import_side_effects` with no matching files, `sibling_floor_skew`, `tracked_secret_shapes` without its table) fails the run, exit 1. Under GitHub Actions a skipped gate also emits a `::warning::`. Additive.
- A gate may attach a note to its PASS (`_core.outcome.add_note`); `mutation_teeth` does so for a truncated or cached run, so the CLI shows it where `-W ignore` hid the `warnings.warn`. Additive.
- Config-error handlers in `cli.py` no longer log a `WARNING: substituted a fallback` line before their own message (nothing was substituted), and no log message hard-codes `file.py line N` any more.
- **Behaviour change (`fail_open_handlers`, new rule `gate_swallows_error`):** in a function whose name matches `gate_name_re` (`gate|filter|check|_ok$` by default), an `except` for `Exception`, `OSError`, `SyntaxError`, `ValueError` or a bare `except` whose whole body is `pass`, `continue`, `sys.exit(0)`, `pytest.skip(...)` or `print(...)` is a finding keyed `path::function::gate_swallows_error`. 0 findings on this repository, pyutilz and llm_bench. What to do: log at WARNING or above, raise, or mark the `except` line with `# best-effort: <reason>`; or accept the key with `refresh --grow`.

### Entry-point exit codes 2026-11-07 (audit 49)

- **Behaviour change:** `python -m py_ci_shared.import_cycles SRC_ROOT [PACKAGE]` now exits 1 on a cycle or an unparsed file (it exited 0 on every cycle), 2 with a one-line message on a missing root or no arguments (it was a traceback, exit 1), and prints paths prefixed with SRC_ROOT. `python -m py_ci_shared.teeth_sweep` exits 2 (was a traceback, exit 1, the "toothless" code) for an unreadable or malformed `--cases` file and for an empty case list. `python -m py_ci_shared.workflow_runner_labels` exits 2 (was 1 for a config error and 0 silently) on a repo with no workflow files, and prints a problem count; pass `--allow-empty` for the old pass. What to do: a script that relied on exit 0 from import_cycles must fix the cycles (or baseline them via the gate); a release script calling workflow_runner_labels in a workflow-less repo adds `--allow-empty`.
- `bandit_warn`, `advisory_warn` and `format_warn` (still always exit 0) print `skipped: <tool> is not installed (pip install <tool>)` instead of the "findings above are WARNINGS ONLY" banner when the tool is missing. The `function_length` floor message and `EmptyScanError` now name the config keys that fix them.

### Core and gate semantics 2026-11-07 (audits 41 and 42)

- **Behaviour change (more files scanned):** `_core.corpus.iter_files` in git mode keeps a TRACKED file under a directory named `build`, `dist`, `venv`, `node_modules` or `site-packages` (`src/pkg/build/m.py` was dropped from every gate's corpus without an unparsed finding). An untracked one, any file under a dot-directory and `__pycache__` stay excluded, and the walk fallback is unchanged. What to do: a repo with such a tracked package may see new findings from files that were never scanned; fix them or baseline them.
- **Behaviour change (more files scanned):** `_core.scan.scan_roots_excluding` (drifted_duplicate_functions, epsilon_padded_denominators, hash_fed_by_array_copy, latched_availability_flags, standard_stream_restore) matches its `exclude` fragments against the root-relative path. It matched the absolute path, so a checkout under a directory such as `my_tests_dir` parsed zero files and the gates passed. What to do: a checkout that lives under a directory containing an exclude fragment may now report findings it never reported.
- **Behaviour change (baseline files):** a baseline JSON with a duplicated key (`{"k": ..., "k": ...}`, a bad merge) now raises `BaselineError` (it was last-wins, silently changing a count), and a list baseline holding both the pair `["a", "b"]` and the string `"a|b"` raises (the two merged into one count). What to do: remove the duplicate entry.
- `line_has_marker` (the `# <gate>-ok:` opt-out of every gate that uses it) no longer takes text inside a multi-line string literal for a comment, so a docstring or fixture string that mentions the marker cannot suppress a finding. The parse and bytes caches add `st_ctime_ns` to the stat signature (a same-size rewrite with a restored mtime was served stale on POSIX; on Windows ctime is the creation time, so the gap remains there).
- `shared_mutable_placeholder`: new marker `# shared-mutable-placeholder-ok: <reason>` on the assignment or the `return` (or the comment line above), and a call of a class whose name ends in `Executor`, `Pool`, `Client`, `Session`, `Engine`, `Queue`, `Logger`, `ContextVar`, `Local` or `Manager` is no longer a placeholder. Findings only fall. `pickle_state_completeness`: new marker `# pickle-ok: <reason>` for a class that is never pickled. `drifted_duplicate_literals`: `# literal-set-ok:` now also exempts a `named-constant` and a `threshold` site, and integer type bounds (127, 32767, 65535, 2147483647) and significance levels (0.01, 0.025, 0.05, 0.1, 0.9, 0.975) join the ignored numbers. `no_source_text_tests`: a membership test whose right operand is a collection (set/list/tuple/dict display or comprehension, `.keys()`, a name ending in `s`, anything read from `.headers`) is not a probe. `hash_key_determinism`: a dict display with string-constant keys and `dataclasses.asdict(obj)` are fixed-order payloads. `secret_assertion_operands`: `key = "OMP_NUM_THREADS"` / `for name in ("A", "B")` followed by `os.environ.get(key)` is judged by the bound names. `env_flag_parsing`: a variable the same file also passes to `int()`, `float()`, `Path()`, `os.path.*`, `.split` or arithmetic is not a switch. `fail_open_handlers`: `silent_default_return` accepts `KeyError`, `IndexError`, `StopIteration`, `queue.Empty`, `ProcessLookupError`, `ZeroDivisionError` and `CancelledError` around a single statement. All of these only reduce findings; a baseline entry that no longer matches is reported stale, refresh it.
- The `Gate false positives 2026-11-06` entry on single-value parse guards was wrong (audit 41-F3): the code and `tests/test_fail_open_handlers.py` flag them on purpose since commit 70f2152; the entry is corrected in place.

## 1.23.9

### Interpreter-independent drift grouping 2026-11-07

- **Behaviour change:** `drifted_duplicate_functions` similarity no longer depends on the interpreter. It was computed on `ast.dump`, which Python 3.11 writes with empty `args=[]`/`keywords=[]` fields that 3.14 omits, so the same bodies scored 0.93 on 3.14 and 0.44 on 3.11. The bodies are now rendered by a canonical serializer that skips empty, unset and implicit-`Load` fields. On 3.11 the glossum `key_of` (sppas_it/phonitalia_it), `_phones` (duden_de/raevskaya_fr) and `_digest` (batching/prompt_clarity_a3) groups are now reported, as they already were on 3.14. What to do: a 3.11 consumer that was green may go red on those copies; consolidate them or add the name to `allow` with a reason.

### Gate false positives 2026-11-07

- **Behaviour change:** `source_text_assertion` no longer treats a call's result as source text when the text argument is a parameter that the same file's function only hands to `ast.parse`/`ast.walk`/`iter_child_nodes` (`read |= orm_reads(src, table_of)` with `orm_reads` parsing `source`). A test asserting on the table names found that way is not a source-text claim. A text argument that is returned, sliced or read in the function still taints the result, and a real file read through such a helper is still reported. What to do: nothing, unless a claim you had accepted is now absent from the baseline, which the refresh shrinks.
- Windows: `black_filtered_apply --check` passes Black an absolute stdin filename (a relative, missing path was dropped by Black on the Windows 3.9 leg, so `force-exclude` and `.pyi` mode silently did not apply), and the cached resolve anchors a relative path to the working directory before resolving it.

## 1.23.8

- New module `py_ci_shared.meta_test_utils` (additive): the meta-test building blocks (`consumer_corpus`, `public_top_level_symbols`, `capture_signature`, `capture_module_surface`, `findings_ratchet`, `unbacked_audit_dispositions`, `assert_fields_roundtrip`, `optional_scalar_fields`, `enumerate_test_files`, `scan_todo_markers`, `count_user_deferred_entries` and the rest of its `__all__`), moved here verbatim from `pyutilz.dev.meta_test_utils`, which no longer exists (no re-export in pyutilz). Registered as an internal module. It walks through `_core.corpus.iter_files_pruned` (no `walk-ok` marker, no baseline) and reads its ratchet baselines with stdlib `json`, so py-ci-shared still imports nothing from pyutilz. **A consumer must pin a py-ci-shared version that contains it** (autopsia `test`/`dev` extras and workflows, mlframe and glossum through `PY_CI_SHARED_DIR`). Its tests moved with it (`tests/test_meta_test_utils*.py`). See `audits/2026-11-05_meta_test_utils_move.md`.
- Tree walks no longer enter `.claude` (Claude Code's agent worktrees, each a COMPLETE copy of the repository; one consumer audit test took 19 to 30 minutes with about 20 of them). New `_core.corpus.iter_files_pruned(root, suffixes, exclude_dirs)` is the pruned walk; `llm_output_caps.numeric_output_caps`, `destructive_tests_throwaway_only` (conftest guards), `_mutation_runner` and `_mutation_speed` (test discovery), `contract_ledger.snapshot_files`, `ci_install_covers_entry_imports` (pytest paths), `dart_scanners.dart_files_under`, `deferred_entries`, `code_audit.column_no_write_path`, `code_audit.redundant_test_fit`, `code_audit.source_text_assertions` and `code_audit._todo_markers` use it (or `_iter_py_files`) whatever root they receive. **Behaviour change (fewer findings, only for a root that contains `.claude`):** a worktree copy is no longer read. `dart_files_under` lists in relative-path order and `snapshot_files` also leaves out `.claude`. `tests/test_no_unpruned_tree_walk.py` fails any new `rglob`, `glob("**")` or `os.walk` under `src/py_ci_shared` that is not in a pruning helper and carries no `# walk-ok: <reason>`. What to do: nothing; refresh a baseline that listed a file under `.claude`.
- `cuda_launch_geometry`: a call whose grid or block tuple holds a string or float constant (`itertools.product(("a", "b"), (3, 5), ...)`) is no longer taken for a kernel launch.
- New gate `cuda_launch_geometry` (additive): a kernel launch `f((grid,), (block,), (args,))` whose block tuple holds an integer literal other than 1, a module constant bound to one, or a local name bound once to one (`cuda-literal-block-size`). The best block size depends on the card (SM count, registers and shared memory per SM) and on the kernel's own registers and shared memory, so a literal tuned on one card is wrong on the others; take it from the device (occupancy over `num_regs` / `shared_size_bytes`, e.g. `pyutilz.performance.gpu_hw_launch.block_size_for`). Marker `# launch-ok: <reason>` on the first or last line of the call for a geometry the kernel fixes (a warp per item, a shared array sized by the block); baseline supported.
- New command `py-ci-shared tool pytest_run OUTFILE <paths and pytest options>` (additive): pytest with the flags a developer machine needs (`PYTHONUNBUFFERED=1`, `-s`, `--durations=200`, `--no-cov` and `-n 0` when the plugins are installed, `-p no:cacheprovider`), the whole output in OUTFILE ending with `exit=<code>`, `-q` refused. README "Running pytest on a developer machine" and CLAUDE.md "Running tests" say why each flag is there.

### Gate exemptions 2026-11-06

- `ddl_lock_safety` takes `applied_files` (additive, default empty): a `{repo-relative path: reason}` map for migrations already applied whose history must not change. Every finding of a listed file is dropped; a listed file that no longer exists under `sql_dirs` or has no finding is reported as `ddl-applied-exemption-stale`; an empty reason is a `ValueError`. Consumers that do not pass it see no change.

### Gate false positives 2026-11-06

- **Behaviour change:** `fail_open_handlers` `silent_default_return` no longer flags an `except` whose only body is a return, in a function whose name is a key, encode or freeze builder (`_key`, `_encode`, `_freeze_object`), when the handler catches no I/O error (a bare `except`, `OSError`, `subprocess`, `sqlite3.Error` or `Exception` still flags). Consumers whose baseline lists such a scope must refresh it (it only shrinks). What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- **Behaviour change:** `no_xfail_to_defer` `untracked-reason` no longer judges a `pytest.mark.skip`/`xfail` call bound to a name (`skip_fuzz = pytest.mark.skip(reason=...)`) in a scope that also has an `if` on a runtime condition (`config.getoption`, `os.environ`, `getenv`, xdist, fast mode): the marker is attached only while that condition holds. A marker with no such condition in its scope is still judged, and `xfail-not-strict` and `no-reason` still apply. Consumers: a baseline entry for such a marker no longer matches, so refresh the baseline (it only shrinks) to clear it. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- **Behaviour change:** `drifted_duplicate_literals` `literal-set` no longer counts a literal set whose line (or the line above) carries `# literal-set-ok: <reason>`: a row-count sweep grid measured for one kernel is a separate measurement, not a copy. Only the marked site leaves the group. Consumers: a baseline group that loses its last unmarked copy no longer matches, so the gate reports it as stale; refresh the baseline (it only shrinks) to clear it. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- **Behaviour change:** `fail_open_handlers` `admit_on_error` no longer flags a handler that logs at WARNING or above (a logger call or `log_throttle(..., logging.WARNING, ...)`) and stores a constant under the loop key (`out[i] = 0.0`): the failure is reported and a constant is recorded, not the element. A loud handler that keeps the element (`survivors.append(spec)`) stays flagged. Consumers: the baseline entries for such handlers no longer match, so the gate reports them as stale; refresh the baseline (it only shrinks) to clear them. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- **Behaviour change:** `default_via_or` no longer flags `x / (total or 1)`: a zero denominator replaced by the unit default is a deliberate guard, so the ratio is zero rather than an error. Only a unit default directly on the right of a division is exempt; `n / (d or 5)` and `n * (d or 1)` stay findings. The non-direct guard (`tot = s or 1`, then `/ tot`) stays flagged because it needs data flow the scanner does not track. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- **Behaviour change:** `fail_open_handlers` `silent_default_return` and `quiet_substitution` no longer flag four reasoned fallbacks: an `except ImportError`/`ModuleNotFoundError` handler (an optional dependency), an `except FileNotFoundError` handler (absence is a state), a handler whose returned value carries a message (`DatasetSummary(..., "not installed in this checkout")`, `f"unreadable:{...}"`), a handler in a function named for a cache, stamp, token, digest or signature (a miss the caller recomputes), and a handler that hands the caught exception to the caller (`error = e`). A handler catching `(ImportError, OSError)`, a bare `return None` on `OSError` in any other function, or a quiet fallback value stays flagged. On `autopsia` (the 62 files of audit `2026-11-05/gate_false_positives.md`) the findings fall from 95 to 50. Consumers: the shrink-only baseline now holds entries that no longer match, so the gate reports them as stale; refresh the baseline (it only shrinks) to clear them. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- **Behaviour change:** `drifted_duplicate_literals` takes a role word (`floor`, `ceiling`, `cap`, `tol`, `tolerance`, `mean`, `seed`, `ttl`, `window`, `prior`, `timeout`, `weight`) as generic, as it already does for `max`, `min`, `limit` and the unit words: `_PROBE_FLOOR` and `_WEIGHT_FLOOR` say what kind of number they are, not what they are about, so they no longer form a `named-constant` group. A subject word (`STALE`, `CAVEAT`) still groups. A tuple of calendar years (1900-2100) is a date, not a threshold set, like the dates the rule already ignores, in code and in text. On `autopsia/` the groups fall from 39 to 18 (per-package scan of the same tree). Consumers: refresh the duplicate-literals baseline (it only shrinks) to clear the stale groups. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- **Behaviour change:** `fail_open_handlers` `silent_default_return` no longer flags a reasoned fallback: a handler that catches only `sqlite3.OperationalError` around a read through `execute`/`fetchone`/`fetchall` (a local table or install that is not there). A broader `sqlite3.Error` and a try that makes no SQLite read stay flagged. (Correction 2026-11-07, audit 41-F3: this entry first also listed a single-value parse guard, `try: return int(x) except ValueError: return default`. Commit 70f2152 re-flagged those on purpose, as the 2026-11-04 per-key decision says, and `tests/test_fail_open_handlers.py::test_an_unparseable_value_that_returns_none_is_flagged` pins it: a parse guard is still reported.) On `autopsia` the sqlite exemption clears 8 baseline keys (scopes), not 13; the 5 value-parse keys stay. Consumers: those baseline entries no longer match and the gate reports them as stale; refresh the baseline (it only shrinks) to clear them. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.

### Gate false positives 2026-11-05

- `default_via_or`: a `getattr(obj, name, False)` flag is a boolean operand; a fallback that re-reads another field of the same receiver (`row.get("parsed") or safe_json_load(row.get("response"))`) is an alternative read; a string-valued getattr default and a literal-prefixed string concatenation are text fallbacks. **Behaviour change:** the glossum findings at `example_validation.py:518`, `translation_validation_log.py:153`, `_task_scorers.py:59,177 (_validator.py)`, `db_retry.py:243`, `_consolidate.py:168` disappear. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- `async_primitive_reinit_per_call`: a local bound to an entry of a module-level cache (`locks = _LOOP_LOCKS.setdefault(loop, {})`) is module-backed, so storing a freshly built lock into it persists the lock. **Behaviour change:** `glossum/core/loop_locks.py:36` disappears. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- `possibly_dead_import`: a name spelled as a string anywhere in the corpus counts as consumed, so a facade export listed in a contract checked with hasattr() is not dead. **Behaviour change:** `glossum/storage/orm_models/__init__.py:32` (PG_UUID) disappears. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- `broad_except_swallow`: a swallow inside a logging handler's `emit(self, record)` is the logging.Handler contract (emit must not raise), so it is not reported. **Behaviour change:** `glossum/llm/benchmark_mode.py:175` disappears. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- `resource_handle_safety`: a handle assigned to a name that the module closes with `atexit.register(name.close)` is not reported, as the process owns it until exit. **Behaviour change:** `glossum/scripts/_full_phase1_all_rounds.py:123` disappears. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- `resource_handle_safety`: an `async with aiofiles.open(...)` block counts as a context manager (`ast.AsyncWith`). **Behaviour change:** the 3 aiofiles findings in glossum (`frequency_importer.py:294,452`, `kaikki_importer/__init__.py:355`) disappear. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- New gate `cuda_kernel_pointer_types` (additive): a `__global__` function with a `double*` parameter and no `{REAL}` placeholder among its parameters (hard, `cuda-bare-double-pointer`), the same for `float*` (advisory, `include_advisory`), and a `"float" if X.dtype == ... else "double"` kernel-type probe whose `X` is never a launch argument by name (hard, `cuda-type-probe-not-launched`). A float32 buffer bound to a `double*` is read past its end; the context is poisoned and a later thrust call aborts the interpreter. Marker `// dtype-ok: <reason>` in the kernel string (or `# dtype-ok:` on the probe line); baseline supported. Found in mlframe: the `row_argmax` kernel read its float32 operands as double.
- Gate defects from audit 2026-11-04 step 8 (`audits/2026-11-04/37_step8_gate_defects.md`):
  **Behaviour change:** `assert_installed_matches_ci_pin` takes `pin_repo` instead of `repo`. The config layer filled any parameter named `repo` with the repo root, so the gate crashed (`'WindowsPath' object has no attribute 'rsplit'`). A `[tool.py_ci_shared.gates.ci_pin_version_skew] repo = ...` entry must be renamed to `pin_repo`.
  `env_flag_parsing` judges only boolean flags: a path, number, URL, host, credential or secret read is not reported, and a comparison counts only against a spelling of on/off.
  `secret_assertion_operands` treats `os.environ.get("NAME")` with a non-secret literal name as harmless, like the subscript and `getenv` forms.
  `validation_errors_echo_input` does not judge validators of a `BaseSettings` class (not a request path).
  `assert_modules_resolve_to_checkout` puts the checkout's own package first on `sys.path` (a worktree under an editable install of the main checkout no longer fails). What to do: rename a `repo = ...` key under `gates.ci_pin_version_skew` to `pin_repo`; for the gates named on the following lines, which report fewer findings, refresh their baselines.
- Gate defects of audit 2026-11-04, round 37, group 3 (concurrency and SQL), 37-F15..F18 and 37-F33:
  **Behaviour change (fewer findings):** `ddl_lock_safety` reads a `--   ALTER ...` paste-ready command only in the file's leading comment header; a commented-out statement below the first SQL statement is comment text and is no longer a finding.
  **Behaviour change (fewer findings):** `lock_held_across_io` no longer counts a cross-process file lock (`FileLock`, `portalocker`, `fcntl`, `claim_lock`, `try_claim_lock`, `*_file_lock`) as a thread lock; I/O under it is not a stall of other threads.
  **Behaviour change (more findings):** `check_then_act_outside_lock` treats a `with self._guard():` as holding a lock when `_guard` is a module function that takes a lock, so a test and a write under it are judged as one lock.
  `hash_key_determinism` does not report a `json.dumps` whose text is sent on the wire (a send, write or request call, or a `data`/`content`/`body` keyword): its digest names the exact bytes archived.
  `verified_then_reopened` does not treat a size or length check as verification, and accepts a reopen marked `# reopen-ok: <reason>`. What to do: for the fewer-findings gates above refresh their baselines; for `check_then_act_outside_lock` (more findings) fix each new finding or baseline it.
- Leftovers of audit 2026-11-04 (30, 33, 34, 35):
  `tests/test_mutation_speed.py` runs on a stand-in for pytest and the warm worker (17 tests, about 30 s instead of about 12 minutes); two real sweeps stay as `slow` tests that run on CI (`CI` is set) or with `PY_CI_RUN_SLOW=1`.
  `near_duplicate_function_body` generates candidate pairs from a prefix of each function's rarest shingles; the pairs, and so the findings, are identical (compared on py-ci-shared and pyutilz source).
  New internal `_core.open_audit` (`count_opens`, `python -m py_ci_shared._core.open_audit`) counts file opens per gate; `tests/test_open_audit.py` caps opens per file for the shared readers.
  **Behaviour change (fewer findings):** `unread_function_params` no longer reports a knob in a no-op override (`return self`/`return`/`return None` only) of a class that has a base class.
  `polars_null_equality` (advisory) also reports `pl.lit(None)` comparisons, `is_in([None, ...])` and `frame['a'] == frame['b']` on a visibly polars frame. Additive.
  New reusable workflow `gates.yml` (`py-ci-shared run-all` for the repo's enabled gates), off unless the caller passes `enabled: true`. Additive.
  `Canary.tokens` for 25 more gates (multi-shape teeth) and eleven more test files banned from `assert any(substring)`. Test-only. What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.
- `find_surviving_mutants` pays less fixed cost per sweep (audit 2026-11-04, 30-F1..F9). Default: the unmutated primary tests run once, in the warm worker, instead of cold and then warm (and once per extra `jobs` partition); the wider net's baseline runs only when a survivor needs it (`single_baseline=False` restores the old order); the checkpoint stores each mutant's killer file and line, and a resumed or partitioned sweep leads with the killers already learned. Opt-in: `timeout_factor`, `confirm_in_worker` / `cold_confirm_every`, `coverage_prune` (line map from one coverage run; mutants on unexecuted statements skip the primary run), `copy_ignore_patterns`, `ignore_addopts`. Additive; verdicts are unchanged. `HARNESS_VERSION` moves to 33.

### New gates from audit 32, 2026-11-04

- `njit_fastmath_nan` (opt-in gate): an `@njit`/`@jit`/`@vectorize` kernel compiled with NaN/inf-assuming `fastmath` (`True`, or a flag set with `nnan`/`ninf`/`fast`, literal or module constant) whose own body tests or produces NaN/inf. Marker `# fastmath-nan-ok: <reason>`. Additive.
- `ordinal_rank_scatter` (opt-in gate): `ranks[argsort(x)] = arange(n)` rank construction (tied values split by input order, NaN ranked highest) in a function with no tie vocabulary. Marker `# ordinal-rank-ok: <reason>`. Additive.
- `group_blind_split` (opt-in gate): a function with a groups-like parameter that builds KFold/ShuffleSplit/train_test_split without it (the `if groups is None` fallback branch is exempt). Marker `# group-blind-ok: <reason>`, config `allowed={path::function: reason}`. Additive.
- `oof_buffer_zero_init` (opt-in gate): an `oof`/`holdout` buffer built with `np.zeros`/`np.empty`/`np.ones` and filled per fold from a caller-supplied or non-partition splitter (locally built KFold/StratifiedKFold loops and coverage-checked buffers are exempt). Marker `# oof-zero-ok: <reason>`. Additive.
- `ml_api_contracts` (opt-in gate, three rules): `best_iteration` used as a tree count without `+ 1`, `argmax(...) == y_true` with no class mapping, and an sklearn estimator `__init__` assigning `self.<param>` something other than the bare parameter. Marker `# ml-api-ok: <reason>`. Additive.
- `njit_unsupported_calls` (opt-in gate): `typing.cast`, `np.median`/`np.nanmedian`, `warnings.warn` and logger calls inside `@njit`/`@jit`/`@vectorize`/`torch.jit.script` bodies; table replaceable (`unsupported=`) and extensible (`extra=`). Marker `# njit-ok: <reason>`. Additive.
- `truncating_discretisation` (opt-in gate): `if <distinct count of y> <= k: y = y.astype(int)` without an integrality check (round/floor/issubdtype), which collapses a fractional target to one class. Marker `# truncating-ok: <reason>`. Additive.
- `unseeded_rng_fallback` (opt-in gate, intended blocking): a function calling `np.random.default_rng()`, `RandomState()`, `random.Random()` or `np.random.seed()`/`random.seed()` with no seed (or `None`), i.e. a `rng=None` fallback to OS entropy. Marker `# rng-entropy-ok: <reason>`, config `allowed={path::function: reason}`, `allowed_functions` patterns, `exclude` path fragments. Additive.
- `caller_frame_mutation` (opt-in gate, ADVISORY: warns with `CallerFrameMutationWarning` by default, `advisory=False` fails or ratchets against a baseline): a function that mutates a frame-named parameter (`X`, `df`, `data`, `frame`, `X_*`, `*_df`) in place (`X[col] = ...`, `.loc[...] =`, `del X[col]`, `X.columns =`, `inplace=True`) without rebinding it to a copy. Exempt by function name (`inplace`, `in_place`, `mutate`) or docstring (`in place`, `mutates`, `modifies`). Marker `# frame-mutation-ok: <reason>`, config `allowed`, `frame_params`, `exclude`. Additive.
- `loop_invariant_cast` gains an opt-in second rule set (`heavy=True`, rule `loop-invariant-heavy-call`): `np.argsort/sort/unique/lexsort/quantile/percentile/median/bincount/cumsum/corrcoef` and `scipy.stats.rankdata` (plus the `x.argsort()`-style methods) inside a loop body whose every operand, positional and keyword, is loop-invariant (no call in an operand, no operand name rebound or mutated in the loop, not under an `if`/`elif`/ternary that reads a loop-variant name). Marker `# loop-invariant-ok`; `heavy_funcs=` replaces the table. The default (`heavy=False`) is unchanged. Additive.
- `numeric_edge_matrix` (library): `assert_edge_contract(fn, expect=..., twin=...)` runs a metric or kernel over empty, one-row, constant, single-class, all-NaN, some-NaN, inf, ties, float32-extreme, subnormal and zero-weight inputs, forbids ZeroDivisionError/IndexError/RuntimeWarning, requires NaN or ValueError on empty and all-NaN input, and compares a CPU/GPU twin; `assert_population_covered` fails when a public function is neither covered nor skipped with a reason. numpy is imported lazily. Additive.
- `target_leak_canary` (library): `assert_no_train_target_leak(make_transformer)` fails when a feature builder correlates with an independent noise target on its own training rows; `assert_oof_scores_at_chance` is the stacker variant (training-row AUC of out-of-fold scores on noise labels). Additive.
- `cache_key_sensitivity` (library): `assert_key_sensitive(key_fn, base, sensitive, insensitive)` requires the key to move with every result-changing argument, stay put for the insensitive ones, be deterministic, and every parameter of the key function to be declared one way or the other. Additive.
- `estimator_invariants` (library): `assert_column_order_invariant`, `assert_n_jobs_invariant` and `assert_frame_type_parity` fit estimators from a factory and compare outputs across column order, worker count and frame type. Additive.
- `find_surviving_mutants(..., checkpoint_path=...)` records every judged mutant to a JSON-lines file as its verdict lands, keyed by the sweep's fingerprint. A sweep killed midway resumes from it: the mutants already judged are not run again, and their survivors, coverage gaps and budget are replayed onto the current objects. Additive: without `checkpoint_path` nothing changes. `HARNESS_VERSION` moves to 32.
- `mutation_teeth` logs each surviving mutant at WARNING as its verdict lands, not only when the sweep ends, so a sweep that runs for hours can be read while it runs.
  Additive: verdicts, the returned `MutationRun` and the cache key are unchanged, and the sweep prints nothing new on a clean run. `HARNESS_VERSION` moves to 31, so a cached
  verdict computed by version 30 is recomputed once.

### Gate defects 2026-11-04

Step 8 consumer reports (`audits/2026-11-04/37_step8_gate_defects.md`). Each entry is a false positive or a missing marker; the gate now honours the marker the consumer already wrote.
- **Behaviour change:** the text corpora behind `skip_as_pass` and `comment_repo_paths` (and every gate built on `read_text_corpus`) skip a binary file (a NUL in its first 8000 bytes: an image, an archive, a profile) instead of reporting it as unreadable text. Undecodable text is still an unreadable finding. New helper `_core.read_text_if_not_binary`. What to do: nothing; refresh a baseline that listed a binary file as unreadable.
- `skip_as_pass` flags a `SKIPPED` marker only when the printed message begins with that word; a warning that merely contains "skipped" and exits 0 is not a finding.
- `machine_specific_paths` no longer reads a sed expression such as `s://.*::` as a `db-url`: the user part of a DSN excludes `*`, `\`, `^`, `$`, brackets and braces, and the password part excludes `*` and `\`. A real DSN with an inline password is still a finding.
- `fail_open_handlers` `silent_default_return` does not fire when the handler returns the bound exception or a part of it (`return exc.sqlstate`): that hands back the failure's own data.
- `sentinel_or_fallback` honours `# falsy-ok: <reason>` on a conditional expression (`x.seed if x.seed else DEFAULT`) as its docstring promises, not only on an `or` chain.
- **Behaviour change:** `import_cycles` exempts a cycle when a member module's first line carries `# audit-exempt: import_cycle -- <reason>` (the reason is required, as for every `audit-exempt` marker). What to do: refresh the gate's baseline with its refresh command so the entries that no longer fire are pruned (a stale entry fails by default); nothing else.

### Gate quality 2026-11-04

Audit 33 (`audits/2026-11-04/33_existing_gate_quality.md`). Every change makes a gate stricter or removes a false positive; the entries that can turn a green consumer red are marked.
- `ImportAliases.from_tree(tree, scoped=True)` is an opt-in scope-aware mode: an import binds only the scope it is written in, a parameter or non-import rebind shadows it (the name resolves to `None`), and `p = pickle` / `L = pickle.loads` resolve one hop. The default is unchanged for the other ~57 users. `unsafe_deserialization`, `naive_utcnow`, `stdlib_json_ban`, `hardcoded_seed_in_library`, `swallowed_exceptions` and `module_reload_safety` use it, and `naive_utcnow` exempts `arrow`/`pendulum` only when really imported.
  **Behaviour change:** those six gates now report a use that a stray function-local or later import of the same short name used to hide; fix the site or add the gate's marker. What to do: fix the site or add the gate's marker, as stated.
- **Behaviour change:** the text-ratchet gates (`skip_as_pass`, `wallclock_test_waits`, `git_child_env`, `guard_without_fixture`) report a file that is not valid UTF-8 as `<rel>: unreadable` instead of skipping it, and number identical lines in one file (`key`, `key #2`, `key #3`) instead of collapsing them to one key. Refresh the baselines once so accepted duplicates are recorded. What to do: refresh the four gates' baselines once.
- `phantom_markdown_links`, `comment_repo_paths` and `docs_inventory_parity` compare path case exactly (`_core.exists_exact_case`): a wrong-case link that passed on Windows/macOS now fails everywhere. **Behaviour change** for Windows/macOS consumers only. What to do: rename a link or path whose case differs from the file on disk (Windows/macOS consumers only; everyone else already failed).
- **Behaviour change:** `swallowed_exceptions` judges `with contextlib.suppress(Exception|OSError|...)` like the equivalent silent handler, and no longer exempts every handler in a `finally`, `close`, `__exit__` or `__aexit__` (only release-type calls, `__del__` and `emit` stay exempt). What to do: fix each new finding, or record it in the baseline with the gate's refresh command.
- **Behaviour change:** `unsafe_deserialization` requires the earlier hash/verify call to concern the loaded value (an unrelated `sha256` no longer counts), reports `pandas.read_pickle`, `pickle.Unpickler(fh).load()`, `marshal`, `shelve.open`, `jsonpickle.decode`, ruamel `typ='unsafe'`, keras `safe_mode=False`, a non-literal `torch.load(weights_only=...)`, and loads in class bodies and lambdas. What to do: fix each new finding, or record it in the baseline with the gate's refresh command.
- **Behaviour change:** `rollback_then_continue` treats only a constant / empty literal / no-argument constructor assignment as an accumulator reset (`msg = str(e)` and `n = n + 1` no longer exempt) and sees `*rollback*` helper calls. `ci_workflow_timeout_gate` rejects `timeout-minutes` of 0, a negative, a non-integer, 360 or more, or more than `max_minutes` (default 120). What to do: fix each new finding, or record it in the baseline with the gate's refresh command.
- **Behaviour change:** `fail_open_handlers` (`extend/add/insert/+=/setitem/yield` admissions, flag-then-return), `hardcoded_seed_in_library` (module constants, folded expressions, `rs`/`rng_seed` parameters, `**{"random_state": 7}`, lambdas; the fall-through after `if seed is not None: return` is the default), `wallclock_test_waits` (ast mode resolves imports, `asyncio`/`trio`/`anyio` sleeps, Dart without `const`), `machine_specific_paths` (DSNs of any scheme, folded concatenation, bytes, `.env`/`.json`/`.sh`/`.ps1` files), `latched_availability_flags` (`HAVE`/`USE`/`OK`/`GPU`-style names, cached falsy probes), `sentinel_or_fallback` and `hardcoded_token_ceilings` (assignments, once-bound constants, `2**12`, camelCase, `max_new_tokens`/`num_predict`), `discarded_model_copy` (dropped result, tuple targets, `dataclasses.replace`, module scope), `env_flag_parsing` (two-step reads, walrus, `bool()`, aliases), `predictable_tmp_path` (variables, f-strings, `/tmp` literals), `ddl_lock_safety` (`ADD PRIMARY KEY/UNIQUE`, `SET NOT NULL`, `SET LOCAL` ends at COMMIT), `skip_as_pass` (bare `exit`, `sys.exit()`, `SystemExit(0)`, `tools/`, `.github/`), `lock_held_across_io` (cursor round trips, `write_text`, `asyncio.sleep`, mutex names), `no_xfail_to_defer` (an unconditional skip needs a tracked reason), `epsilon_padded_denominators`, `hash_fed_by_array_copy` and `gpu_timing_sync` all report shapes they missed; refresh the affected baselines. What to do: fix each new finding, or record it in the baseline with the gate's refresh command.
- Fewer false positives: `naive_utcnow` (a parameter named `arrow`), `identity_comparisons` (a name rebound to a non-string), `workflow_runner_labels` (a label in `run:`/`name:` text), `env_flag_parsing` (backend selectors and `or "default"`), `gpu_timing_sync` (`.cpu()`/`.item()` end a timed region), `line_has_marker` (the opt-out must be in the real comment, not a string literal; **Behaviour change**: a line that relied on a `# marker` inside a string now reports). What to do: refresh the affected baselines; move a gate marker out of a string literal into a real comment.
- `vacuous_loop_assertions` counts unittest `self.assert*`/`self.fail` as checks and floors. Canary teeth: `Canary(tokens=(...))` requires EVERY seeded shape to be named; `swallowed_exceptions` and `unsafe_deserialization` canaries now seed one site per shape.

### Speed and self meta-tests, 2026-11-04

- Scan speed, output unchanged: the code-audit scanners walk through one memoised walk (`_core.node_index.walk` now caches any subtree, `code_audit._base.walk_nodes` / `child_nodes`) and one parse cache (`_safe_parse` is `_core.source.parse_file`); a pyutilz/src scan takes about 35 percent less wall time and found the same findings. `_core.source.read_bytes_cached` serves repeated reads of an unchanged file from memory (5.8x fewer opens in a gate-suite pattern). A shrink-only test keeps raw `ast.walk` out of `code_audit/`.
- **Behaviour change (more files parsed):** the code-audit scanners now decode source as the interpreter does (BOM, PEP 263 cookie), so a BOM-prefixed file that they used to skip as unparsable is scanned. A consumer may see new findings in such files; fix them or baseline them. What to do: fix or baseline the new findings.
- `budget = "warn"` now also prints a BUDGET summary line, a GitHub warning annotation per gate over budget and a job-summary list; the exit code is unchanged.
- `py-ci-shared tool <module>`: a `main` returning anything but `None` or an int exits 2 with a message instead of 0; a `KeyError` raised inside a gate keeps its traceback instead of exiting 2.
- `config_drift_check` covers every consumer of `configs/consumers.toml` (private ones with `CONSUMER_READ_TOKEN`), and a consumer without `[tool.ruff]` / `[tool.mypy]` is no longer reported as diverging on it.
- `_consumers checkout --fail-on-skipped-private`; the scheduled consumer-pins job uses it (manual runs can set `allow-skipped-private`).
- `adoption_matrix` counts pre-commit hook ids and gate names listed in `.py` files, and prints ranked gaps, unadopted modules, Python consumers without a gate runner and reusable workflows a consumer does not call (`ranked_gaps`, `is_python_repo`, `has_gate_runner`).
- `lint-blocking.yml` reports tools its inputs skip (interrogate, deptry, zizmor); `self-ci.yml` has non-blocking Windows legs for Python 3.9 and 3.13.
- New self meta-tests: `tests/test_self_api_hygiene.py` (annotations, docstrings, `__all__`, library `print`, import-time calls, shrink-only baselines) and an exemption-validity check in `tests/test_gate_teeth.py`.

### New gates from audit 31, 2026-11-04

- `duplicate_test_body` (`assert_no_duplicate_test_bodies`): two `test*` functions in one file/class with identical body, decorators and signature (opt-in, default off).
- `definition_after_entry_guard` (`assert_definition_after_entry_guard`): a module-level def/class below a `__main__` entry guard whose body calls a module name (NameError only when run as a script).
- `verified_then_reopened` (`assert_verified_then_reopened`): a function verifies a file by path (sidecar/hash) and then opens the same path again to load it (TOCTOU).
- `state_before_work` (`assert_state_before_work`): a schedule stamp (`last_/next_ full|sweep|run|scan|cycle|refresh` = clock) or progress counter (`processed|written|...` `+=`) written in the statement just before the call it gates (configurable regexes, restore-in-except exemption).
- `systemexit_in_library` (`assert_no_systemexit_in_library`): `sys.exit`/`os._exit`/`raise SystemExit` inside a function of a module with no `__main__` guard, or of a function imported by name elsewhere; entry names, opt-in `exit*` params, `# exit-ok: reason` and test files are exempt.
- `optional_collaborator_wired` (`assert_optional_collaborator_wired`): a `*_memo`/`*_cache`/`*_fn` parameter defaulting to `None` that every non-test call site omits or passes as literal `None` (positional and keyword binding, `self` skipped, ambiguous names and `*args` calls treated as wired).
- `cli_flag_spelling` (`assert_cli_flag_spelling`): one flag stem registered as `--a-b` in some `add_argument` calls and `--a_b` in others; a call listing both spellings is the accepted alias.

## 1.23.7

- `release_guard` and `code_audit`: `find_surviving_mutants` / `assert_revert_fails_tests` refuse an absolute path; the `code_audit` gate honours in-place `audit-exempt` markers before the baseline comparison. No consumer action.

## 1.23.6

- Dash plugin entry points are read on Python 3.9; several audit scanners are faster with the same findings. No consumer action.

## 1.23.5

- Behaviour change: an `audit-exempt` marker needs a reason of at least 20 characters, and the source-text gate honours a justified per-line marker. What to do: lengthen any shorter reason, or nothing.

## 1.23.4

- `redundant_test_fit` reads a test file's text outside the loop; no finding changes. No consumer action.

## 1.23.3

### New gates from audit 32, 2026-11-04

- `contract_ledger` gained five helpers for contract ratchets: `assert_documents_identical` / `document_diff` / `canonical_json` (a committed JSON document, for example an OpenAPI fixture, must equal the live one in full; the failure lists each added, removed and changed path, so a new property on an existing schema is named instead of passing), `promise_message_ids` (catalogue messages containing a promise word as a whole word), `missing_test_references` (ledger rows that name a test that does not exist, resolved by parsing the file), and `value_mismatches` (a ledger's expected values against what the code emitted). Additive.
- **Behaviour change (moved, no shim):** the code-audit CLI (`python -m py_ci_shared.code_audit`, module `py_ci_shared.code_audit.cli`) and the field/text agreement record checker (`py_ci_shared.code_audit.field_text_agreement`) moved here from pyutilz, which no longer re-exports them. Import them from their new paths. The CLI's `--format json` output now comes from the standard library `json` with 2-space indent, sorted keys and unescaped non-ASCII, so its bytes can differ from the former `pyutilz.core.jsonlib` output while carrying the same data. What to do: import from the new paths; compare `--format json` output by its data, not its bytes.
- **Behaviour change (fewer findings):** `source_text_claims` stopped flagging four shapes that are not claims about source text. (1) A name the assertion
  itself uses as a mapping (`x["key"]`, `x.get(...)`, `.items()`, `.keys()`, `.values()`): a string has none of those, so it holds parsed data. (2) A file whose path
  literal ends in a data suffix, now including `.xml`, `.tsv`, `.xsd`, `.svg`, `.obo`, `.owl`, `.rdf`, `.ttl`, `.fasta`, `.pdf`, `.gz`, `.parquet`, `.pkl`, however the path
  is built (`Path(__file__).parent / "fixtures" / "x.xml"`). (3) `loads` / `load` / `safe_load` from a module named for a data format, so a project's own
  `from pkg import _json as json` counts as a deserialiser. (4) `f(...) == expected`, where `expected` is read text and the call neither reads nor takes it: a golden-file test
  measures the function. Substring, regex and integer-index checks on source text, a reader on the call side and a text method of the value are flagged as before.
  A consumer's `source_text_assertion` baseline may now list entries that no longer fire: refresh it (it only shrinks). What to do: refresh the `source_text_assertion` baseline.
- The `additive_epsilon_denominator` scanner honours `# audit-exempt: additive_epsilon_denominator -- <reason>` on the division's line, like the
  other scanners. Before, the marker was silently ignored for this check, so a pad that cannot matter could only be hidden by rewriting the
  expression. Additive: a line without the marker (and a reason) is flagged as before.
- **Behaviour change:** the code-audit detectors and their registry now live in `py_ci_shared.code_audit`. The
  `code_audit_meta` gate runs them from here, so a consumer's gate results follow the py-ci-shared pin instead of the
  pyutilz pin. The rules are the same code (copied byte for byte, then modernised to `collections.abc` and built-in
  generics). pyutilz keeps `pyutilz.dev.code_audit.*` as aliases, so existing imports still work. To keep the old
  behaviour while you move, pin py-ci-shared to the release that carries this and rebuild the code-audit baselines once. What to do: after bumping the pin, rebuild the code-audit baselines once.
- `stale_comment_age.assert_no_stale_todos` takes `allow_unparsed` (default `True`: an unreadable file is still scanned with a lossy decode,
  as before; `False` reports it as a problem and checks nothing in it) and `min_files` (default `0`: a scan that read fewer files than the floor
  reports it). `test_partition_reachability.assert_partitions_reachable` takes `allow_unparsed` (default `False`: an unreadable runner or spec
  file fails with its name, as it always did; `True` skips it) and `min_files` (default `0`). Defaults keep every existing call's behaviour.
- `repo_hygiene.assert_repo_hygiene` takes `min_files` (default `0`): a pass that read fewer text files than the floor reports it. Pass it to opt in.
- `gate_contract.NEVER_PARSES` and `NO_CORPUS` name the corpus entries that cannot take `allow_unparsed` or a floor, each with its reason.

## 1.23.2

- New library `contract_ledger` (`src/py_ci_shared/contract_ledger.py`): the shared mechanism behind the 2026-10-21 contract ratchets in
  autopsia. A committed JSON ledger lists every value a contract surface can emit (a response literal, an HTTP status and its
  Retry-After behaviour, a file that stores user text); `ledger_gaps` and `ledger_entry_problems` report the values with no entry or a
  malformed one; `assert_fixture_fresh` fails a stale committed fixture; `store_writers` runs a probe and lists every file it created or grew.
  Additive: no existing gate changes.

## 1.23.1

- Behaviour change: `.github/actions/install-pyutilz` without a `pyutilz-ref` input installs the pyutilz commit the calling repository's own `pyproject.toml` pins, not py-ci-shared's pin. A repository with no pin, or with two different pins, now fails with a message; set the input to the commit you mean. What to do: set `pyutilz-ref` when the repository has no pin or two pins.
- Behaviour change: the eight lint tools `lint-blocking.yml` runs (actionlint-py, zizmor, codespell, yamllint, bandit, vulture, interrogate, deptry) now default to the exact versions in `tool_versions.py` when a consumer leaves its `*-version` input empty, instead of the newest PyPI release at run time. A consumer that wants a different version sets the input. Each version is at least two weeks old at the time it is chosen. What to do: set the tool's `*-version` input when you need another version.
- Behaviour change: `phantom_code_references` no longer reports nine classes of false positive (Python protocol names, standard vocabulary
  codes, errno constants, external data file names, one-character symbols, names the repo spells out as strings, bare `self.x` attributes,
  `table.column` pairs spelled in SQL). On the autopsia tree 901 findings become 494. A consumer baseline recorded against the old scanner
  now has entries that no longer fire: rebuild it from the new scanner (`PYTHONPATH=. python tests/test_meta/<test>.py`), do not hand-edit it. What to do: rebuild the baseline with the new scanner, as stated.

## 1.23.0

- Security audit of 2026-10-20 (`audits/2026-10-20/`, 39 findings, all dispositioned in `audits/TRACKER.md`). The consumer-visible changes:
  - **Behaviour change:** a `[tool.py_ci_shared]` corpus entry that resolves outside the repository root (an absolute path, or `..`) is now a `ConfigError`.
    Move the file inside the repository or drop the entry; a gate used to read it silently.
  - **Behaviour change:** `iter_files` skips symlinks. A tracked link to a file outside the checkout used to be read as a corpus file; a gate that counted
    such links will see fewer files.
  - **Behaviour change:** `load_consumers` rejects a `name` or `repo` that holds a separator, a `.`/`..` component, `?` or `#`.
  - **Behaviour change:** `mypy_gate` fails when mypy runs longer than 30 minutes, `black_filtered_apply` raises after 10 minutes, and the warn-only runners
    (`advisory_warn`, `bandit_warn`, `format_warn`, `vulture_warn`) report `skipped` after 10 minutes (15 for pip-audit), instead of hanging the hook.
  - **Behaviour change:** ruff, bandit, vulture, pip-audit and black run as `python -I -m <tool>`, so `PYTHONPATH`, the user site and the working directory are
    not on their `sys.path`. mypy is left as it was: a repository-local mypy plugin must be importable from the working directory.
  - `env_example_round_trip` and `corpus_drift` no longer print a committed value or the text of an exception; they name the field and the exception type.
  - `release.yml` keeps no credential in `.git/config` (the token reaches only the tag push), and the rollback job runs only from `master`.
  - The gate cache moved from the shared temp directory to `~/.cache/py_ci_shared/gate_cache`; `api_floor` keeps vermin's output per file digest in
    `~/.cache/py_ci_shared/vermin` (a warm run on this repository: 0.8 s, was 17 s).
  - `env_write_restore` accepts `# env-write-ok: <reason>` on a deliberate write.
  - Four regexes that backtracked on crafted input (the Dart empty-catch body was exponential) are linear. What to do: move or drop an out-of-root corpus entry; fix a malformed `name` or `repo` in consumers.toml; expect `skipped` after the new timeouts; a repository-local plugin of ruff, black, bandit or vulture must be a regular install (they no longer see `PYTHONPATH`).
- The test suite runs on the machine's spare cores by default: `tests/conftest.py` passes `worker_budget.available_workers()` to pytest-xdist when it is installed
  (`pytest-xdist` is now in the `dev` extra). `PY_CI_WORKERS=1` gives one worker. The CPU share (rounded half up) is then capped so each worker has 4 GiB of
  free memory, never below one: a worker that starts without it pages, and the next worker's DLL loads fail with an error that names no memory.
  The memory per worker is set per repository: 4 GiB by default, 2 GiB for this suite (`available_workers(ram_per_worker_bytes=...)`).
- The tests that start a Postgres server (`xdist_group("postgres")`) run on one worker under xdist, so the session has one server, not one per worker. The
  mode is added to the arguments the workers start from (see the `pytest_cmdline_main` note in `tests/conftest.py`). Tests that need an isolated schema take
  a `fresh_database()` on the session server (`py_ci_shared.embedded_postgres`). The lifecycle test (`test_a_real_server_starts...`) keeps its own start.
- Fixed: `tests/conftest.py` called `bound_randomly_reseeders()` without the session config, so the skip for a session without pytest-randomly never ran
  there and every pytest start imported thinc, torch and cupy (about 7 s). Every nested pytest run of `test_scaffold` paid that three times: 137 s to 51 s.
- New: `tests/test_pytest_startup_is_light.py` starts a real pytest without pytest-randomly and fails if thinc, torch or cupy get imported (the bug the next entry fixes).
- `test_run_hands_the_command_its_dsn` checks the handoff with a stand-in DSN instead of starting a server (26 s to under 1 s).
- Fixed: a Postgres server whose test process was killed (a crashed xdist worker, a timeout) outlived it. `embedded_postgres` now stops and deletes the
  data directories of dead owners at the next start (`owner.pid`); live owners are left alone. `psutil` is in the dev extra.
- Fixed: the mutation worker turned a KeyboardInterrupt in a request into an error reply and went on serving; an interrupt now ends the worker.
- `mutation_teeth` is under the 1000-line budget: the per-mutant verdict (`_judge`, `_primary_verdict`, `_check_unmutated`, `_group_twins`, `_wider_net`, `_module_names`,
  `PathArg`) moved to `_mutation_sweep.py`; `mutation_teeth` re-exports what tests and callers use. Harness version 17.
- `exc.line or 1` in 25 modules now maps only a missing line to 1 (`exc.line if exc.line is not None else 1`); a line 0 is no longer rewritten.
- Refactor: the mutation sweep's per-mutant verdict is split into helpers (`_judge`, `_primary_verdict`, `_check_unmutated`, `_group_twins`, `_sweep_in_sandbox`),
  clearing two C901 findings. Decisions and budget accounting are unchanged; the harness version is 16 because its tracked files changed.
- `embedded_postgres` logs a warning when psutil is missing, so the reaper's no-op is visible.
- `stale_comment_age`: `warn_days=0` is a zero-day window, no longer rewritten to 7 (a sentinel `or` that the self-audit reports as P1).
- Handlers that substitute a fallback value log it at WARNING (27 self-audit findings): `_mutation_worker`, `cli`, `closed_audit_rounds`, `commit_metadata`, `consumer_import_census`, `dart_scanners`, `epsilon_padded_denominators`, `hash_fed_by_array_copy`, `mutation_teeth`, `required_check_contexts`, `workflow_runner_labels`, `_ci_install_parts`, `adoption_matrix`, `corpus_drift`, `git_dependency_pins`, `offline_suite_without_credentials`, `phantom_code_references`, `resource_leak_checks`, `schema_snapshot_parity`, `sibling_floor_skew`, `baseline_trend`, `required_check_contexts`. Unreadable-file fallbacks that are deliberately best-effort carry a `# best-effort:` reason.
- Registry: `_mutation_sweep` is declared internal to mutation_teeth.
- Gate results are cached on disk: a PASSED result is not re-run while nothing in the repository changed (the key is the gate, its arguments, the package version and a digest of the tree; FAILED, refresh and network gates always run; `PY_CI_SHARED_NO_CACHE=1` turns it off). The repeat of the package's own gates: 33 s to 12 s.
- `stale_source_citations` on the mlframe tree: 192 s to 57 s with the same findings. The token list of a file is computed once (three passes read it), files
  without a `.py:` text are not tokenized (no citation pattern can match them), statement spans, docstring lines and split source lines are computed once per tree.
- Self gate: `comment_repo_paths` runs on this repository (roots `tests/`, `.github/`; code and config files only). Its roots now accept the path form that config tables produce. About 50 illustrative or stale path references in docstrings and comments were reworded.
- Fixed: `test_cwd_sys_path_and_warnings` depended on `warnings.simplefilter("ignore")` changing the filters, which it does not when the session already ignores
  warnings (`pytest -W ignore`); the probe now installs a filter no caller has.

## 1.22.13

- New opt-in gate `import_time_global_side_effects`: a module-level statement (outside `if __name__ == "__main__":` and any function) that changes
  process-global state for everything imported after it: `logging.disable`, `basicConfig(force=True)`, root-logger handler/level surgery, a blanket
  `warnings` ignore, assignment to `sys.stdout`/`stderr`/`stdin`, `os.chdir`, `locale.setlocale`, `signal.signal`. `imported_by="tests"` limits the
  check to modules a test imports (the precise form for a scripts directory). Found when a test importing a CLI script dropped every INFO/WARNING
  record in all xdist workers (78 failures in the Glossum backend).
- Fixed: `guard_population` on Windows under Python 3.9 sliced `Path.parents` while finding Git's bash, which 3.9 cannot, so every guard
  raised `TypeError`. Found by running 3.9 on Windows locally (self-ci runs 3.9 on Linux only).

## 1.22.12

`lock_held_across_io` honours a reviewed marker: a call that must stay under a cross-process file lock carries
`# lock-io-ok: <reason>` on its own line, with a non-empty reason. A marker without a reason does not suppress the finding.

Four new gates for concurrency and test-quality defects, and one new rule in `fail_open_handlers`: `lock_held_across_io`,
`check_then_act_outside_lock`, `no_source_text_tests` and `shared_mutable_placeholder`.

Behaviour change: `fail_open_handlers` has a fifth rule, `silent_default_return`, an `except` handler whose whole body is one
`return` and that logs nothing. A gate already on `@v1` that scans a file with such a handler now reports it. What to do: log
the failure at WARNING (or raise), or mark the `except` line `# best-effort: <reason>` when the fallback cannot change a result,
then refresh the baseline if the handler is accepted.

Behaviour change: `code_audit_meta.assert_no_new_code_audit_findings` fails when a pyutilz code-audit scanner raises, instead
of counting the crashed scanner as "no findings" (it calls `pyutilz.dev.code_audit.registry.run_all_checked`). What to do: fix
or exclude the scanner the failure names. With a pyutilz older than `run_all_checked` the gate warns and keeps the old
behaviour; upgrade pyutilz (the `[dev]` extra and `install-pyutilz` now pin `ec8f2bd`) to get the check.

- New gate `comment_repo_paths`: a comment (`#` in shell/YAML/TOML/INI/Dockerfile/`#!` scripts, `//` in Dart/JS/TS,
  Python comments and docstrings) that names a path under `tool/`, `tools/`, `scripts/`, `.githooks/` or
  `.github/scripts/` must name a file or directory that exists under the repo root. Glob and template tokens and
  past-tense lines ("renamed", "removed", "no longer"...) are skipped; an unparsable `.py` is reported. Opt-in.
- New gate `nginx_location_headers`: a `server`/`location`/`if` block with its own `add_header` must repeat every header
  an enclosing block sets, since nginx then drops the inherited set; an `include` where headers matter is reported
  unless `resolve_includes=True` reads it; `required=` names headers every `server`/`location` must end up with.
  Unbalanced braces are an unparsed file, not a pass; `.conf.template` files with `${VAR}` parse. Opt-in.
  `exempt={selector: reason}` exempts a `location` (selector exactly as written) from the dropped-header and `required`
  checks; it must then carry `required_in_exempt`. An empty reason or a selector matching no location is a finding.
- New gate `validation_errors_echo_input`: a pydantic validator (`field_validator`/`validator`/`model_validator`/
  `root_validator`), or a helper it passes the value to, that raises `ValueError`/`TypeError`/`AssertionError`/
  `PydanticCustomError` (or asserts) with a message reaching the validated value (`len(value)` and the field name are
  fine), and a `RequestValidationError` handler that returns `exc.errors()` without dropping `input` for every error. Both
  put the rejected value into the 422 detail that clients log (autopsia, 2026-10-15). Keys are `path::function::shape`;
  shrink-only baseline. Opt-in.
- `phantom_code_references.count_claim_mismatches` also counts a bullet list naming one thing per item
  (`#   NAME - text`, `# - NAME: text`, `// * NAME - text`) under a claim line ending in `:`, and accepts any
  "<number> <word(s)>:" claim ("Two substitutions:") for such a list. Numbered lists are counted as before.

**Behaviour change**: `phantom_code_references` (`find_phantom_code_references`, `count_claim_mismatches`) now reads `#`
lines of `.sh`/`.bash`/`.zsh`/`.yaml`/`.yml`/`.toml`/`.cfg`/`.ini`/`.conf`/`.template` files, `Dockerfile*` and
extensionless `#!` scripts as comments (before, only `//`/`*` lines of non-Python files were read), and a bullet list
under a count claim can now disagree with it. A consumer that passes such files can see new findings. What to do: fix
the comment (a backticked name that does not exist, or a count that does not match its list), or keep those files out
of the `files` you pass.

`dart_scanners.scan_tappable_semantics` can tell two real tap-target exemptions from a missing `MinTapTargetBox`. Both are
checked, so neither lets an ordinary button opt out.

- `target_wrappers=[...]` (keyword argument): project widgets or `Widget name(...)` helpers that guarantee a compact WCAG 2.5.8
  AA target. A tappable inside one counts as wrapped only when the wrapper's own definition, found in the scanned files, sets
  both `minWidth` and `minHeight` to at least 24 (or to a `minTapTarget` constant). A named wrapper that is missing, or that
  does not constrain both axes, is a finding of its own.
- `// tap-target: inline-text - <reason>` on the comment lines directly above a tappable: WCAG 2.5.8's inline-target exemption.
  It holds only when the tappable's `child:` holds text alone (`Text`, `RichText`, `TextSpan`, semantics and text styling). A
  marker with no reason, or on a tappable whose child holds any other widget (`Icon`, `Padding`, `Container`...), is reported
  as an invalid exemption. The marker does not touch the role rule.

Nothing changes for a consumer that passes no `target_wrappers` and writes no marker. The source-text helpers moved to the
private `_dart_source` module and are re-imported by `dart_scanners`.

## 1.22.10

Regression fix in the opt-in `wallclock_test_waits` gate. 1.22.9 scanned a file under `test`/`tests` only when its name was
`test_*.py`, `*_test.py` or `*_test.dart`, so helpers there (`tests/conftest.py`, `tests/support/*.py`, `test/support/*.dart`,
`test/helpers.dart`) were skipped and a real `time.sleep(` or `Future.delayed(const Duration(milliseconds` in them was missed.
Now every `.py` and `.dart` file inside a `test`/`tests` directory is scanned. Outside one nothing is scanned, so exploratory
scripts stay out. The comment-line and docstring exclusions are unchanged.

Behaviour change (opt-in gate only): a wall-clock wait in a test helper is now a finding. What to do: if the gate is enabled and
it reports a helper's wait, remove the wait or record it in `tool/meta/baselines/wallclock_test_waits.json` with a note.

## 1.22.9

Two false-positive fixes in opt-in ratchet gates. Both gates are opt-in (`[tool.py_ci_shared] enable = [...]`), so a consumer
that has not enabled them sees no change.

- `guard_without_fixture`: a `tool/check*` guard named by a CI workflow (`.github/**/*.yml`, `*.yaml`) or a git hook (`.githooks/*`)
  is covered, not reported. Before, only `test/` and `tests/` counted, so a guard run only by CI was flagged. A guard named nowhere
  is still reported.
- `wallclock_test_waits`: only a `test_*.py`, `*_test.py` or `*_test.dart` file inside a `test`/`tests` directory is scanned, so a
  `test_*.py` exploratory script outside one is no longer a finding. A line whose stripped text starts with `#` is a comment, not a
  wait, and a line inside a Python module, class or function docstring is not a wait either. Dart files get no docstring check.

Behaviour change (opt-in gates only): both gates report fewer findings. An accepted baseline entry that no longer fires is reported as
stale and does not fail; prune it with the gate's refresh command. What to do: nothing, unless you enabled one of these gates and want
the stale entries removed.

- **Behaviour change** `git_child_env` (Dart): the call body was read from the closing quote of `'git'`, not from the call's `(`, so it
  ended at the first nested call's `)` or, with none, held no arguments at all; a scrubbed `environment:` map literal was reported as "passes no
  environment:". The body is now the whole argument list. A scrubbed `environment:` now also needs `includeParentEnvironment: false`: Dart
  merges `environment:` into the parent's environment otherwise, so the hook's `GIT_DIR` reaches the child anyway. Over noema_app,
  polyvocab_app and flutter_app_core this adds no finding. What to do: pass `includeParentEnvironment: false` next to the scrubbed map.
- **Behaviour change** `dart_scanners.scan_tappable_semantics` finds a spinner's labelling `Semantics(label:)`, a GestureDetector's
  announcing `Semantics(button: true / checked: / ...)` and a tappable's `MinTapTargetBox` by walking out through the widget's ENCLOSING calls
  (bracket nesting over string-masked source) instead of a fixed 400/600-character window. A label far above on the widget's own ancestor now
  counts (noema_app leaf_controls, polyvocab_app landing_navbar); a `Semantics` that is a closed SIBLING above the widget no longer does.
  `scan_hardcoded_ui_strings` skips a `label:` literal whose innermost enclosing call is in `non_ui_label_calls` (default `("retryTransient",)`,
  a retry log label that is never rendered). Over noema_app, polyvocab_app and flutter_app_core this removes three false positives and adds
  none. What to do: regenerate the `tappable-semantics` and `hardcoded-ui-strings` baselines so the entries that no longer fire drop out.
- `uncalled_functions` gains named exemption categories, declared once for every consumer: `CATEGORIES` (PUBLIC_API, FRAMEWORK_HOOK,
  PLUGIN_HOOK, SUPERSEDED, RETAINED_KERNEL, OPT_IN_SLOW, OFFLINE_HELPER, TEST_UTILITY, each with its meaning), an `Exemption(category, reason)`
  per name passed as `exempt=`, and `reexported_public_api(files, root)`, which lists what a package `__init__` re-exports (`from x import f` and
  `__all__`). `assert_no_new_uncalled_function(..., public_api_from_exports=True)` exempts those names as PUBLIC_API without a hand list.
  An exemption with an unknown category or an empty reason fails the gate. `ignore` keeps working for bare names. Additive: no consumer's
  green gate turns red.

## 1.22.8

- Five new **opt-in** ratchet gates, none in the default set: a repo enables each by name in `[tool.py_ci_shared] enable`, and
  only NEW violations fail; existing ones are recorded in the repo's `tool/meta/baselines/<gate>.json` with a note each.
  `skip_as_pass` (a tool/scripts/.githooks script that prints SKIPPED and exits 0), `wallclock_test_waits` (`Future.delayed` of a
  `Duration`, `time.sleep` inside a test), `guard_without_fixture` (a `tool/check*` guard no test names), `git_child_env` (a test's git
  spawn that does not scrub `GIT_*` from its environment) and `dup_baseline_percent_key` (a duplicate-body pair listed twice because only
  its similarity percentage moved). Enabling them in a consumer that already has violations needs its baseline first.

## 1.22.7

- New gate `emitted_vocabulary_literals` (`assert_emitted_vocabulary_literals`, `EmitSite`): every value a backend passes to a closed-vocabulary
  field at its emit sites (a callee name plus the keyword and/or positional slots) is a member of the vocabulary it publishes, and every published
  member is emitted somewhere (a class default of that field counts). Arguments resolve statically: literals, both arms of a conditional, module- and
  class-level string constants (also imported, absolute or relative), locals and tuples unpacked from a same-module function's returns. An argument
  it cannot resolve is a finding unless its source text is listed in `pass_through`. Generalized from Autopsia's `test_emitted_vocabulary_literals`
  (a `rebased` kind reached a client's screen untranslated); over Autopsia's tree with that test's two configurations it reports nothing, and one
  planted literal at a `ModeratorDisclosureOut(kind=...)` site is reported along with the published member it displaced.
- `version_tag_currency.find_lock_pin_problems` / `assert_lock_agrees_with_pin`: a Flutter app's `pubspec.yaml` git pin of a package, its
  `pubspec.lock` and the package's remote name one commit (the lock's `ref` is the pinned ref; its `resolved-ref` is what that ref names on the
  remote today, via `git ls-remote`, with an annotated tag peeled). The build honours the lock, so a ref moved without `flutter pub get`, or a tag
  re-pointed after it, ships the old code under a green check. An unreachable remote raises `VersionCheckError` rather than passing;
  `check_remote=False` checks the first half offline. Generalized from noema_app's `tool/check-core-pin-agrees.sh`.

## 1.22.6

No notes recorded for this release.

## 1.22.5

- `corpus-drift.yml` installs the `sql` extra, so `statement_columns_exist_in_ddl` runs in the nightly corpus scan instead of raising ImportError for sqlglot.

## 1.22.4

- `lint-advisory.yml` gains `pip-audit-ignore-vulns`: advisory ids pip-audit skips (`--ignore-vuln` each), for risks a repo has accepted and written
  down. The list is the baseline, so a vulnerability that is not in it still appears in the report instead of being buried among the known ones.
- New gate `free_text_in_get_route` (`assert_free_text_in_get_route`): a FastAPI/Starlette `@x.get("/path")` route must not take free text in its path or its query string.
  A parameter counts as free text when it is a `str` named like one (`term`, `query`, `q`, `text`, `search`, `condition`, `complaint`, `chief_complaint`, `symptoms`,
  `description`, `note`, `comment`, `message`, `prompt`, `question`; configurable) or carries `Query`/`Path(max_length>=100)` unless its name says it is an identifier.
  A URL is written to every access log, proxy and browser history, so text a clinician or customer typed belongs in a POST body. Run over seven codebases it reported
  nothing in five and seven routes in one backend (three clinical lookups, three vocabulary typeaheads and a reviewer name); a route that must stay GET goes in the baseline.

## 1.22.3

- `.github/scripts/release_guard.py prepare X.Y.Z [--write | --check]` brings every place that names a release (pyproject, `__version__`, the README install tag and gate catalogue, the reusable workflows' default ref, the `since` of modules not yet published) to one version before the tag is pushed; two tags (v1.22.0, v1.22.2) failed the Release run's `verify` job for want of it.

## 1.22.2

- `lint-advisory.yml` gains `pip-audit-uv-lock` (and `pip-audit-skip-packages`): it exports the calling repo's `uv.lock` and audits the exact locked versions
  with `--no-deps --disable-pip`. Without it the job resolved the project itself and failed whenever a dependency was git-sourced (mlframe's `pyutilz`), so the
  scan ran on nothing; against mlframe's lock it audited 366 dependencies and reported 31 advisories in 10 packages.
- **Behaviour change**: `module_reload_safety.find_unpaired_reloads` no longer counts a second `importlib.reload` as a restore, whether in an
  `addfinalizer` callable or in a `finally`. Reloading again mints a third set of class objects instead of putting the originals back, so a
  model pickled afterwards carries them by value (dill `_create_type`) and a restricted loader refuses it; mlframe's `lgb_shim` test did exactly
  this and failed two unrelated round-trip tests in a shared worker. What to do: take `saved = dict(module.__dict__)` before the reload and write
  it back with `module.__dict__.update(saved)` (clear first if names were added), or restore through a fixture that does.
- Thirteen new gates from the mlframe audit of 2026-10-04, each with canaries and a suppression comment that needs a reason:
  `cancellation_prone_moments` (variance, skewness and kurtosis from raw power sums, which cancels on large-offset data),
  `unsafe_deserialization` (a `find_class` that allows whole modules or gadget modules, `torch.load` without `weights_only`, `np.load(allow_pickle=True)`,
  unverified pickle and joblib loads, unsafe `yaml.load`), `unread_function_params` (`sample_weight`, `seed`, `n_jobs` and similar accepted and never read),
  `hardcoded_seed_in_library` (an int literal seed in a function that has its own `random_state` or `seed`), `constant_fallback_cache_key` (a digest or key
  builder that returns a constant from an `except`, so distinct inputs collide), `ci_default_branch_never_cancelled` (`cancel-in-progress` that can be true on a
  push to the default branch), `ci_install_covers_entry_imports` (a workflow that installs fewer extras than its entry module imports; plus
  `assert_entry_imports_without_extras`), `optional_imports_guarded` (a module-level import of a distribution that only an extra declares),
  `single_shot_timing_assertion` (a speedup or ceiling asserted from one measurement per side, or a skip placed after the timing), `cuda_kernel_integer_width`
  (`long` in a CUDA kernel source, 32 bits under Windows NVRTC), `id_keyed_cache_validates_identity` (a cache keyed by `id(obj)` that never checks identity),
  `persisted_negative_probe` (a failed hardware probe written to disk as a negative verdict). New library module `pytest_known_gap` (`known_gap(reason,
  gap_closed)`: xfail while a gap is open, fail when it closes).
- **Behaviour change**: `no_xfail_to_defer` follows `known_gap(...)` calls (from `py_ci_shared.pytest_known_gap` or the modules named in
  `known_gap_modules`, default `tests._known_gap`): the reason is judged like a `pytest.xfail` reason, and an unguarded call whose `gap_closed` is a falsy
  literal (a gap that can never close, so can never fail) is a finding. What to do: give each `known_gap` reason a tracked issue or an external component, and
  make `gap_closed` a measured condition.
- New opt-in gate `env_write_restore` (`assert_env_write_restore(root, baseline_path=...)`): a test function that writes `os.environ` (assignment, `del`, `update`/`setdefault`/`pop`, `os.putenv`) and never restores it leaks the setting into every later test in the process, so the victim is a different innocent test each run and passes alone (mlframe: three `MLFRAME_*` strict-GPU flags set directly made a bit-identity test fail 4 runs in 5). The import-time half stays in `import_side_effects`. A write is accepted inside `patch.dict` / `monkeypatch.context`, in a function with a `try` whose `finally` writes the environment, in a yield-fixture that writes again after the yield, in a `setUp` whose class tears it down, with `addfinalizer`/`addCleanup`, or in a function named as a restorer. Findings are keyed `rule::path::function: statement` (no line numbers) and ride the ratchet `Baseline`; seed a first baseline with `grow=True`.

## 1.22.1

No notes recorded for this release.

## 1.22.0

- **Behaviour change**: `sql_verifier_coverage` now recognises a statement written `(` + a `--` comment + the keyword (a parenthesised subquery constant with a leading comment); it was invisible to the gate, so a verifier list could omit it unnoticed. What to do: list each newly found name in `STATEMENTS`, or exclude it with a reason.
- New opt-in gate `psycopg2_param_arity` (`assert_psycopg2_param_arity(root)`): a `cur.execute(sql, params)` whose statement resolves to text (a literal, a `+` chain, a module-level constant) and whose parameters are a literal tuple, list or dict must give one value per `%s` and a key for every `%(name)s`. `?`/`$1` statements (DuckDB, sqlite3) are skipped.
- New opt-in gate `module_state_test_reset` (`assert_module_state_test_reset(root, tests_root)`): a module-level dict/list/set that a function mutates and that no test file mentions has no test-side reset, so one test's entries leak into the next.
- `sql_verify` gains the helpers every project's verifier used to copy: `normalise_psycopg2_idioms` (`VALUES %s`, `IN %s`, `{name}` templates, a leading `SET`, `REFRESH`), `to_positional` and `without_session_prefix`.
- **Behaviour change** (audit 2026-10-03): `sql_verify` no longer reads the `%s` inside a doubled `%%` as a placeholder (`ILIKE '%%suspended%%'` was prepared with an invented parameter and failed with `could not determine data type of parameter $2`). What to do: nothing; a statement that failed only because of this now passes.
- New opt-in gate `script_entry_points` (`assert_script_entry_points(root, script_dirs, probe_dirs=...)`) for packages of flat top-level scripts:
  an entry point (`if __name__ == "__main__":`) that a sibling imports by bare name, at any depth, must register itself with
  `sys.modules.setdefault("<name>", sys.modules["__main__"])` (otherwise the import executes the file a second time with its own globals);
  scripts and probes must not put the working directory on `sys.path`; two script directories must not define the same module name.
  `double_execution_findings(...)` is the opt-in behavioural half: one subprocess per exposed entry point loads it as `__main__` without its work
  block and asserts `import_module(name)` returns the same object (timeout, and a skip table in which every skip carries a reason).
- New opt-in gate `ci_pin_version_skew` (`assert_installed_matches_ci_pin`, `find_pin_skew_findings`): compares the `# vX.Y.Z` comment on the py-ci-shared SHA
  pins in a consumer's workflows with the installed `py_ci_shared.__version__`. Installed older than a pin is a finding; installed newer is one unless
  `tolerate_ahead=True`; a SHA pin without a version comment, mixed pins across workflows and (with `known_releases`) a pin more than `max_lag` releases behind are
  reported too. Offline: it cannot check that a SHA is the commit its comment names.
- New opt-in gate `statement_real_engine_coverage`: every SQL statement constant a package defines (found by `sql_verifier_coverage.sql_constants`) must be referenced by name from
  a test of the real-engine tier, which the caller defines with `engine_test_dirs` and/or `engine_markers`; one level of `import ... as` alias is followed. A statement without
  one is a finding, ratcheted by a shrink-only baseline of `module.NAME -> reason`. `sql_constants` gained an optional `use_git`.
- New gate `ddl_lock_safety` (`assert_ddl_files_lock_safe`, `find_ddl_lock_findings`): hand-applied `.sql` files that change a live table must bound the lock wait (`SET [LOCAL] lock_timeout` before the first `ALTER TABLE`; `0` and `DEFAULT` do not count), add `CHECK`/`FOREIGN KEY` constraints `NOT VALID`, build indexes `CONCURRENTLY` (and never inside an explicit `BEGIN` block), and not rewrite the table (volatile `ADD COLUMN ... DEFAULT`, `serial`, `GENERATED ... STORED`, `ALTER COLUMN ... TYPE`) unless the file says `-- rewrite-ok: <reason>`. A paste-ready `--   command` comment header counts as a command. Small tables are allowed through a caller-supplied `tiny_tables` mapping with a reason each. Opt-in: nothing calls it until a consumer's test does, so it cannot turn a green consumer red; a baseline lets a repo adopt it over the files it already has.
- New opt-in gate `statement_columns_exist_in_ddl` (`assert_statement_columns_exist_in_ddl`, `find_unknown_statement_columns`, `statement_column_report`;
  needs the `sql` extra): every column a module-level SQL constant names (`INSERT INTO t (cols)`, `ON CONFLICT (cols)` and `DO UPDATE SET`,
  `UPDATE t SET c`, `EXCLUDED.c`, `alias.c` bound through FROM/JOIN to a table) must exist in the tables the project's DDL files define
  (`CREATE TABLE`, `ADD`/`DROP`/`RENAME COLUMN`, `DROP`/`RENAME TABLE`), read offline. It is the offline half of the 2026-10-04 CORR-26 incident, a column
  named in code before its additive migration was applied. A statement the gate cannot read (f-string field, `{name}` field, unparsable) or that
  touches no table the DDL defines is NOT CHECKED and counted in the report, never clean; baseline-able and shrink-only because a project is
  legitimately ahead of its DDL for a window. `sql_verifier_coverage` gained `statement_constants` (the constants with their text); its own behaviour
  is unchanged.
- **Behaviour change** (audit 2026-10-03, SQL-26): `sql_verifier_coverage` reads more of what a package sends. A constant built with `+`
  (`_CTE + "SELECT ..."`, resolving module-level names bound to strings; an operand the module does not bind leaves the statement seen by
  its literal), one that starts `REFRESH`, and one led by a session setting (`SET LOCAL TimeZone = 'UTC'; INSERT ...`) are now SQL
  constants, so a verifier whose `STATEMENTS` list omits them fails the "not checked" direction, and a list that names them no longer fails the
  "constant that no longer exists" direction. What to do: list each newly found name in `STATEMENTS`, or exclude it with a reason, and make the
  verifier PREPARE it (a `REFRESH` through a read of the view, a `SET` prefix stripped first). On the audit's two consumers this found
  seven statements in `production_scrapers` and four in `realtime_applications` that no list had ever carried.
- The pytest plugin accepts the `[tool.py_ci_shared.secret_shapes]` table that `tracked_secret_shapes` documents as its config; it used to reject it as
  an unknown key and fail the collection of every test in a repo that followed the documentation.
- `resource_leak_guard` no longer reports a connection the test already closed: a socket in FIN_WAIT1/2, TIME_WAIT, CLOSING or LAST_ACK stays
  listed until the peer finishes the teardown (seconds, against a remote database), so every test that closed its connection properly failed at teardown.
- New gate `schema_snapshot_parity`: a `schema.sql` that promises to provision a database from scratch is applied twice on a private
  throwaway Postgres (`embedded_postgres`) and its catalogue compared with a committed, versioned production snapshot: tables and
  columns missing, types (timezone-ness included), nullability, generated/identity, extras (allowances need a reason, stale ones are
  findings), optionally index names. No server means NOT CHECKED, loudly, never a pass. `refresh` writes the snapshot through a
  read-only DSN named by an environment variable.

## 1.21.1

New gate `connection_liveness_kwargs` (`assert_every_connection_has_liveness_kwargs`): every call that opens a network database
connection (`psycopg2.connect`, the psycopg2 pools, `psycopg`, `psycopg_pool`) must set the four TCP keepalive keywords, literally, through a
shared `**KEEPALIVES` mapping the scan can read, or in a DSN literal; a tunnel that drops silently otherwise leaves the client waiting for the
two-hour OS timer. The mapping is also exported as `connection_liveness_kwargs.KEEPALIVES`.
New gate `tracked_secret_shapes`: tracked (index) files holding a string of a secret shape the consumer lists in
`[tool.py_ci_shared.secret_shapes]`, with a pre-commit entry point (`python -m py_ci_shared.tracked_secret_shapes`); the matched
text is never printed and no baseline is accepted.

Git hooks for this repository: a `.pre-commit-config.yaml` (ruff, filtered black, actionlint, secret scan, mypy and the self-gate
tests on push) so a push can no longer fail the self-gates that CI enforces. `tests/test_release_version.py` scratch
repositories no longer inherit the hook's `GIT_*` variables.

New gate `destructive_tests_throwaway_only` (`assert_destructive_tests_are_throwaway_only`): a test file that writes SQL
(INSERT/UPDATE/DELETE/TRUNCATE/DROP/ALTER/CREATE in a string literal) and reaches a database (a connect call, a `real_database` or
`integration` mark, or a DSN read) must take its DSN from a throwaway accessor, never `live_dsn` or a `DATABASE_*` variable, and every
pre-commit hook or workflow step naming it must start `python -m py_ci_shared.embedded_postgres run`. Test roots may be several packages
with the configs at the repository root; a reasoned per-file allowlist covers rollback-only tests. `embedded_postgres` gains
`require_loopback_dsn` and `throwaway_dsn_from_env`, which refuse a non-loopback, hostless or unparseable DSN with
`NotAThrowawayServerError` without echoing it.
New gate `unresolved_module_attributes` (`assert_no_unresolved_module_attributes`): an attribute read on an imported first-party module
(`import m; m.NAME`, `import a.b as x`, `from pkg import submodule`) must name something the module defines. The sibling of
`unresolved_imports`, which sees only `from X import Y`. Targets are parsed, never imported; `resolve_roots` lists the directories
where bare module names are found. Modules with a `__getattr__`, a `__class__` swap, `globals()[...] =` or `exec` are skipped and reported.
New gate `cross_package_private_names`: a package must not import or reach an underscore-private NAME inside another package's
module when the modules are imported by bare name (`from show_top_jobs import _get_dsn`, `import top_jobs_query as tq; tq._X`).
It follows aliases and `from pkg import module as x`, skips `if TYPE_CHECKING:` blocks, names the public alias when one exists,
lists a module name found in several packages as skipped (never as a pass), and takes an `allowed` map with mandatory reasons
that may only shrink. `private_imports` only sees underscore MODULES.
New gate `unexecuted_function_bodies`: reads the coverage.py JSON report of the UNIT run (`pytest --cov=pkg --cov-report=json`) and
reports every function or method whose body statements have no executed line (a function every test stubs, as `outcome_column_exists`
was), as a shrink-only ratchet; a missing, empty, stale or mismatching report is an error. CLI: `python -m py_ci_shared.unexecuted_function_bodies`.

## 1.21.0

Eight checks from the 2026-10-03 Upwork dashboard audit, each proven on the real defect it is named for:

- New gates `stub_signature_parity` (a monkeypatched or `mock.patch`ed stub must accept every parameter of the callable it
  replaces; static scan, plus the opt-in run-time plugin `stub_signature_guard`) and `wrapper_protocol_parity` (a wrapper that
  copies some of a protocol's attributes, e.g. `cache_clear`, from its inner callable must set all the inner one has, e.g.
  `refresh`; plus the run-time `protocol_attributes.assert_satisfies_protocol`).
- New gate `secret_assertion_operands`, and the opt-in pytest plugins `secret_safe_test_output` (credentials redacted from
  every report, `os.environ` restored) and `offline_suite_without_credentials` (credential variables and `.env` loading
  blanked for every test without the `real_database` marker). Plugins are switched on in `[tool.py_ci_shared]`
  (`PLUGIN_KEYS`: `resource_leak_guard`, `stub_signature_guard`, `secret_safe_test_output`, `offline_suite_without_credentials`)
  or by `-p py_ci_shared.<name>`. Three of this repo's own tests that printed the whole environment on failure fold to a bool.
- New gate `constant_relations`: a TOML registry of declared relations between numeric constants and config defaults across
  packages, evaluated by a walked `ast` grammar (no eval); an unresolvable reference fails.
- `sql_verify` is now a gate: `FragmentMatrix` / `assert_fragment_matrix` build every fragment of a dict into every template and
  fail on a statement that does not parse, keeps an unfilled `{slot}`, or names a table alias out of scope where it lands.
  New extras `sql` (sqlglot) and `pglast`.
- New gates `connect_error_echo` (an `except` around a database connect or DSN resolve must not format the exception into
  output: libpq echoes the connection string) and `drifted_duplicate_literals` (one set of numbers restated in several
  modules of a package; baseline entries need a reason).

## 1.20.0

- `import_side_effects`: an import-time `os.environ` write is reported by key and value length, never by value. A
  module that loads a `.env` at import made a failing gate print every DSN and API key in full.
- **Behaviour change** (audit 2026-10-03 remainders, N-19, WF-11): a baseline that exists but cannot be read is
  reported as "baseline X is unreadable (...); fix or delete it" by every refresh, never as "does not exist" (the core
  `Baseline` refresh, `mutation_teeth`, `function_length`, `ignore_ratchet`, `import_side_effects`, `audit_wave_filenames`,
  `fail_open_handlers`, `loc_budget`, `phantom_code_references`, `vacuous_loop_assertions`); the file is left as it is.
  Floors where a gate passed on nothing: `phantom_code_references.assert_no_phantom_code_references`,
  `doc_identifier_parity.assert_doc_identifiers_exist` and `timezone_honest.assert_timezone_honest` take `min_files=1`;
  `test_partition_reachability` fails a runner path that holds no runner text and names an unparsable tags file.
  `effect_assertion_parity` reports an unparsable `test_*.py` that `build_import_map` had dropped. `gate_integrity` names
  an unparsable pre-commit or workflow file (it raised a bare yaml error), and `pytest_addopts_path_runs` no longer
  reports the clean files' runs when another test file is unparsable.
- `local_copy_report` also judges every `conftest.py` by content (`CONFTEST_SIGNATURES`: a hand-rolled autouse stream
  guard is a copy of `resource_leak_checks`); `include_conftest=False` turns it off (audit 2026-10-03 G-7).
- `stale_comment_age`: a code-shaped comment in an AST analyser that names a call shape the file matches by a string
  literal (`# self.stats.setdefault("k", 0)` above `attr == "setdefault"`) is a label, not commented-out code; and
  `python -m py_ci_shared.stale_comment_age --stale-warning-summary REPO ...` prints per-repo counts of comments going
  stale plus a `stale-comment early warnings: N` total for a job that checks consumers out (audit 2026-10-03 G-12).
- **Behaviour change** (audit 2026-10-03, G-3): `optional_truthiness` also follows optional numbers carried on `self`
  (`self.x`, `getattr(self, "x")`, a local bound from either) and one hop of argument forwarding, for budget-like
  names (`BOUND_NAMES`). On by default (`follow_attributes=True`); mlframe master gets 7 new findings (three zero-budget
  bugs plus the sites 1256fff4d fixes). Fix them with `is not None`, or pass `follow_attributes=False` while you do.
- New gates (audit 2026-10-03 G-1, G-2, G-4, G-5): `hook_attestation` (a `commit-msg` trailer and a pushed-range check
  for commits that skipped the hooks), `sibling_floor_skew` (a sibling floor in `pyproject.toml` above the revision CI
  installs), `api_floor` (vermin against `requires-python`, guard-aware; `pip install py-ci-shared[api]`) and
  `committed_line_endings` (bare CR, mixed and CRLF line ends in the committed blobs).
- **Behaviour change** (audit 2026-10-03, new code): gates added since v1.17.0 got stricter where they failed open.
  `function_complexity` fails on an unparsable file and refuses a refresh while one exists (`allow_unparsed=True` to
  tolerate it; its entries are then kept), as `complexity_ratchet` did; both name an unreadable baseline. `adoption_matrix`
  with `--resolve-in` fails a pin to a tag or SHA that does not exist (`unresolvable-pin`). `corpus_drift compare` fails
  when a repo of last night's snapshot is missing tonight (`repo-gone`) and when a finder errors from its first night.
  `standard_stream_restore` now judges only RESTORES of a saved stream (through `import sys as x` and `setattr` too),
  guarded only by an identity check that encloses or precedes the restore: CLI-wide `sys.stdout = TextIOWrapper(...)`
  is no longer reported, and `allow` accepts `path::function`. `nondiscriminating_shapes` raises on an unknown
  `extra_shapes` slug and no longer lets `exists`/`supported` on a computed name or a trivial companion assert hide a
  late skip or a median round-trip. `config_getattr_default_parity` raises `TypeError` for a non-dataclass in
  `dataclass_classes`. `ci_install_covers_conftest` reads setuptools dynamic dependencies, requires `pytest_plugins`
  and `-p` plugins, reports a tox/nox/`make test` job as unevaluated, and no longer lets the interpreter running it
  choose the rule (an import with no table mapping is `ci-install-unmapped` everywhere). Re-run the gates after
  upgrading and fix, allow or acknowledge what they name.
- **Behaviour change** (audit 2026-10-03, core): `[tool.py_ci_shared]` is validated more strictly. A string `enable`,
  a gate enabled twice (dashes and underscores count alike), a non-numeric or non-positive `budget_s`, a non-string
  `entry`/`module` and a glob that matches no file are configuration errors (exit 2) where they used to run wrongly or
  crash. Fix the table the message names.
- **Behaviour change**: inside a git work tree, a failed or timed-out `git ls-files` now fails the gate (`CorpusError`)
  instead of silently walking the directory, ignored files and submodules included. `PY_CI_SHARED_GIT_TIMEOUT_S`
  raises the 120 s git timeout. Every git call goes through one runner (`_core.git`).
- **Behaviour change**: the pytest plugin stops with a usage error when the nearest `pyproject.toml` has a
  `[tool.py_ci_shared]` table but pytest's rootdir is another directory (the gates used to vanish from the run), and
  when `--py-ci-gates=on` finds no table. Pass `--rootdir`.
- **Behaviour change**: a baseline count that is not a whole number >= 1 is a `BaselineError` naming the file.
- `resource_leak_guard` now catches env leaks in the first test of a session without psutil and, on Python 3.9, in
  tests that import a stdlib module; the header says when psutil is missing. `version_tag_currency` no longer takes
  tag `10.2.3` for version `0.2.3`, and counts only releases of the pin's own spelling as "behind". The CLI escapes
  characters the console cannot print instead of crashing, and `run naive-utcnow` finds `naive_utcnow`.
- **Behaviour change** (audit 2026-10-03, gaps G-6..G-14): `resource_leak_guard` also checks `streams`, `cwd`,
  `sys_path` and `warnings` (module `resource_leak_checks`); a test that leaves `sys.stdout` swapped, the working
  directory changed, `sys.path` edited or a warning filter added now errors at teardown. Restore it in the test, or
  allow it (`stream:<name>`, `cwd`, `sys_path:<glob>`, `warnings`), or narrow `leak_guard_checks`. New gates
  `vendored_internal_imports`, `consumer_import_census`, `external_fact_tables`, `commit_metadata` (forbidden trailers
  configurable, none by default), `closed_audit_rounds`, `workflow_runner_labels` (`--fix`), and the
  `required_check_contexts` CLI. `assert_no_stale_todos` warns by default 7 days before a comment goes stale
  (`warn_days=0` restores the old silence; the advisory never fails a run).

- New gate `ci_install_covers_conftest`: a workflow job that runs pytest must install every third-party package its
  `conftest.py` files import at collection (module level and session hooks), read statically from the job's pip/uv
  installs, `-r` files, `pyproject.toml` extras, `uv.lock`, local composite actions and shell scripts, with
  `sys.version_info` guards and `python_version` markers evaluated per matrix Python. It flags pyutilz's
  `numba-coverage.yml` before e6db9af (no py-ci-shared for `tests/conftest.py`), the class behind three collection
  failures in four days.
- New CLI `ci_health` and daily workflow `ci-health.yml`: for every repo in `configs/consumers.toml`, how many days each
  GitHub Actions workflow on its CI branch (and its scheduled runs) has been continuously red, counted from the first
  failure after the last success; cancelled and skipped runs do not count. A workflow red for more than
  `--max-red-days` (default 2) fails the job. A run GitHub refused to start because the account's payment failed or
  its spending limit was hit is reported as `billing`, apart from code failures, and never fails it. Private repos
  need `CONSUMER_READ_TOKEN`, else they are skipped with a warning, as in `consumer-pins.yml`.
- `consumer-pins.yml` runs `adoption_matrix --allow-behind 2`: a consumer's fixed pin may lag the latest release by up
  to 2 tags before the daily job fails (with the default 0, every release turned every consumer red until it bumped).
- `stale_comment_age`: opt-in early warning. `assert_no_stale_todos(..., warn_days=7)` emits one `UserWarning` per TODO
  or commented-out call that crosses `max_age_days` within the next 7 days, naming `file:line` and the date it goes
  stale, and returns those lines; it never fails on them. `find_comments_going_stale` returns the same list.
  `warn_days=0` (the default) changes nothing for existing callers. Both functions and `find_stale_comments` take
  `now=` to freeze the clock.

- **Behaviour change** (reusable workflows): `ruff-blocking.yml`, `lint-advisory.yml` and `black-filtered.yml` no
  longer fall back to master when `py-ci-shared-ref` cannot be fetched; they retry three times and fail. Pass a ref
  that exists. New input `force-remote-fetch` (self-ci only).
- **Behaviour change** (`lint-advisory.yml`): pip-audit audits the calling project (`.`), or the files in the new
  `pip-audit-requirements` input; before, it audited its own tool environment and never the project.
- **Behaviour change** (`ci_health`): a billing-refused run no longer relabels an older code-failure streak as
  `billing`; such workflows are now red. `--only` with an unknown name exits 2. Per-request timeouts, `--deadline`
  (600 s), `--jobs` (8) and per-consumer progress on stderr.
- `release.yml`: refuses a tag that is not the newest release of its major, requires self-ci green for the tagged
  commit, runs the whole suite, is re-runnable, and has a `rollback-to` dispatch (CLAUDE.md "Rolling back a release").

## 1.19.0

- `unresolved_imports` resolves `from X import *` exactly instead of treating the star-importing module as unknowable: the exported set is X's literal `__all__` (else its public names), followed through chains. A name a facade built on `from .core import *` does not carry (mlframe: `from mlframe.metrics import show_plots_unless_agg`, which raised ImportError at run time) is now reported. A star from outside the parsed roots, or from a module with `__getattr__`/`globals()` tricks, stays unjudged.
- `nondiscriminating_shapes`: two false positives removed. `late-skip` no longer flags the write-the-baseline-on-first-run skip (`if not BASELINE.exists(): write(); pytest.skip(...)`, used by dozens of meta-gates): an `.exists()` check is a filesystem-state probe, not the data deciding. `median-roundtrip` now fires only when the median is the test's SOLE verdict; a median canary next to `assert_allclose` or another assert is a precondition, not the pass/fail criterion.
- `nondiscriminating_shapes`: the environment-probe vocabulary of `late-skip` also recognises `supported` (`not callbacks_supported()`), `vram` and `major`/`minor` (a compute-capability or version tuple). Whole identifier parts only, so `majority_share` and `n_supporters` stay data and are still flagged.
- New gate `standard_stream_restore`: code that swaps `sys.stdout`/`sys.stderr` restores them only while its own stream is
  still installed, so a nested swap cannot put back a stream somebody else has already replaced.
- `corpus_drift.finders()` imports every gate module; a gate whose third-party dependency is missing now raises
  `MissingDependencyError` naming each module and package (`snapshot` exits 2 and writes nothing) instead of dying with a bare
  `ModuleNotFoundError`. The nightly workflow installs pytest, `config-drift-check.yml` installs the package (it printed
  `No module named 'py_ci_shared'` on every run while showing green, because the pipe through `tee` hid the exit code and
  the step had no pipefail), and a workflow test requires every job that runs `python -m py_ci_shared.*` to install it first.
- `unresolved_imports.ModuleIndex` indexes one file per helper call (the complexity ratchet had flagged `__init__`).

## 1.18.0

- `uncalled_functions` follows import aliases across files (a re-export module `from ._impl import _h as h`, loaded by a
  consumer as `from pkg.shared import h as _probe`), and a name a function imports locally stays the imported function
  even when an `except ImportError: f = None` fallback also assigns it. Both were reported as dead code.
- `content_hash_version_bump_gate` keys its history by `str(version)`: an int version constant crashed the re-pin in
  `json.dump(sort_keys=True)` and never matched the rule that refuses reusing an old version for new content.
- **Gates walk each tree once.** `_core.nodes_of(tree, *types)` returns a tree's nodes of those types in `ast.walk` order from one
  cached walk, and `_core.tree_memo` keeps any per-tree derived value; both live as long as the parse cache. `ImportAliases.from_tree`,
  `unresolved_imports` and `marker_runner_coverage` use them, and every gate walks with `_core.walk` (the `ast.walk` order, about 1.5x
  faster). On mlframe's meta suite: the marker check 70 s -> 29 s for the first marker and 5.5 s for each further one, the import check
  143 s -> 34 s. Mutation harness version 14.
- `effect_assertion_parity`, `vacuous_loop_assertions`, `inert_patch_targets` and `unread_init_params` read their node types
  through `nodes_of`; `vacuous_loop_assertions` builds its per-function maps only for functions with an assert-only loop, and
  `inert_patch_targets` skips the per-function `global` walk in modules that declare none. On mlframe: 50 -> 20 s, 31 -> 15 s,
  21 -> 14 s, 17 -> 13 s; findings unchanged.
- **A refresh only shrinks a baseline.** It drops entries that no longer fire and lowers counts; adding an entry,
  raising a count or seeding a missing baseline with findings fails and names them unless growth is opted into
  (`--py-ci-refresh-grow`, `PY_CI_SHARED_REFRESH_ALLOW_GROW=1`, `py-ci-shared refresh --grow`, `grow=True`).
- **New gate `complexity_ratchet`.** No new function over ruff's C901 limit, and the ones over it may not grow; the
  number is ruff's mccabe count computed from the AST (identical to ruff on every function in this repo).
- **`source_text_claims` judges arbitrary file reads.** Text read from a path it cannot place is a claim when an
  assertion takes a substring's position in it or searches it for a code-like literal; a `return` of a content
  check over source, and an `assert` over a name bound to one, are claims too.
- `adoption_matrix --resolve-in` counts how many releases each fixed pin is behind and fails a stale one (`--allow-behind N`).
- `py-ci-shared new-gate <name>` scaffolds a gate: module on `_core`, failing test skeleton, canary, registry entry, README row.
- `corpus_drift` counts every corpus-bindable finder over real consumer repos and fails a night whose counts jump, drop to zero or start to error.
- Scheduled workflows: `consumer-pins.yml` (daily adoption matrix over the consumers in `configs/consumers.toml`) and `corpus-drift.yml` (nightly).
- `function_complexity` keeps its API and baseline format but measures through `complexity_ratchet` and refreshes shrink-only.
- This repo's full-suite CI run enforces coverage `fail_under = 89`.

## 1.17.0

Stricter by default. Every item below can turn a green consumer run red on upgrade, each for a defect that was
being passed silently. The [README](README.md#baselines-refresh-and-what-now-fails) lists the affected gates.

- **A missing baseline fails.** A baselined gate no longer seeds its baseline on first run and passes; it fails
  and names its refresh command. Refresh once with `pytest --py-ci-refresh=<gate>`,
  `PY_CI_SHARED_REFRESH=<gate>` or `py-ci-shared refresh <gate>`. A refresh never writes after a failed floor or an
  unparsable file, and a `NEEDS-JUSTIFICATION` note fails until someone writes the reason.
- **An unparsable or unreadable file is a finding.** AST gates read sources the way the interpreter does (BOM,
  PEP 263 cookie) and report a file they cannot parse instead of skipping it. `allow_unparsed=True` exists where
  that is expected.
- **An empty scan fails.** File floors count parsed files; a missing root raises. Gates that had no floor now take
  `min_files=1` (`module_reload_safety`, `vacuous_loop_assertions`, `config_call_site_parity`, `unresolved_imports`,
  `phantom_markdown_links`, `pytest_markers`, `env_flag_parsing`).
- `git_dependency_pins` parses the TOML and checks every dependency string (single-line arrays, extras, groups,
  build requires); invalid TOML fails. A requirements file is read as pip reads it (comments, continuations,
  `-r`/`-c` includes, `-e` and bare `git+` lines). `prompt_field_parity.string_literals` is strict by default, and the
  `llm_call_archive_gate` finders raise on unparsable files.
- The package ships `py.typed`, and `tool_versions.BLACK_VERSION` names the black `black-filtered.yml` runs.
  `lint-blocking.yml` takes `working-directory`, `codespell-toml`, `bandit-config` and `bandit-exclude`, so it can
  lint a monorepo subproject.
- **orjson is no longer a dependency**: baselines go through the stdlib `json` module (`_core.load_json` /
  `dump_json`), with the on-disk format unchanged.
- **Refresh works under pytest-xdist.** Refresh requests travel through the `PY_CI_SHARED_REFRESH` environment
  variable, which workers inherit, instead of `sys.argv`, which they do not.
- **Some baseline and allowlist keys changed** (`alembic_concurrently`, `dart_scanners`, `marker_runner_coverage`,
  `value_bearing_asserts`, `db_transaction_completeness`, `fail_open_handlers`, `discarded_model_copy`,
  `gate_integrity`, `machine_specific_paths`, whose keys now carry a digest of the flagged path instead of the path):
  refresh or rewrite those once.
- `mutation_teeth` fails on a scope with zero mutants unless `allow_empty=True` (`HARNESS_VERSION` 11);
  `teeth_sweep` reports unrunnable cases as `ERRORED`; `worktree_hygiene` never calls staged or ignored work
  `REMOVABLE`.

New:

- `[tool.py_ci_shared]` in a consumer's `pyproject.toml` enables gates with their arguments. The package now
  ships a pytest plugin (`pytest11` entry point `py_ci_shared`) that turns each into a test item, times it
  against its runtime budget and adds `--py-ci-refresh`. A repo without the table sees no change.
- The `py-ci-shared` command: `run`, `run-all`, `refresh`, `list`, `config-path`, `tool`.
- `py_ci_shared.registry` lists every module; the README gate catalogue is generated from it.
- The ruff configs ship as package data (`py-ci-shared config-path ruff-base`).
- `adoption_matrix` reports, across consumer repos, where each pins py-ci-shared (and whether the pins agree or
  move), which modules each runs, and where a gate skips itself when the package is missing.
- Pre-commit hooks for `run-all`, `pinned_tool_versions`, `mypy_gate` and `worktree_hygiene`.
- New gates: `atomic_write_staging`, `clock_day_boundary`, `coverage_config_parity`, `hash_key_determinism`,
  `import_cycles`, `local_copy_report`, `no_xfail_to_defer`, `numba_seed_range`, `pickle_state_completeness`
  (static, plus the runtime `assert_pickle_round_trips`), `plotly_annotation_loop`, `polars_null_equality` (advisory),
  `pytest_addopts_path_runs`, `reiterated_iterable_params`, `rollback_then_continue`, `sentinel_or_fallback`,
  `stale_source_citations`, `stdlib_json_ban` (opt-in), `swallowed_exceptions`, and the opt-in pytest plugin
  `resource_leak_guard` (`resource_leak_guard = true` in `[tool.py_ci_shared]`).
- `dart_scanners` takes over the eight Dart scans the Flutter repos copied (file size, empty catch, source-text tests, import/export cycles, assertion-free tests, timed dismissal, double error reports, unused test seams) plus `dart_files_under`/`dart_reader`/`package_name`; `arb_checks.find_dead_keys` also counts `l10n .key` split across lines and `?.key` calls.

Release and CI:

- The package version is the release version (it was `0.1.0` through `v1.16.1`). `release.yml` checks the tag
  against it, then moves `v1` to the new tag.
- The reusable workflows fetch configs, `RUFF_VERSION` and the package at their own release
  (`py-ci-shared-ref` input), not at master. They declare `permissions: contents: read`, and lint-advisory's
  tools are installed at exact versions.
- `install-pyutilz` defaults to a pinned pyutilz commit instead of the branch tip.
- This repo's CI tests Python 3.9 to 3.13, installs the dev extra without a fallback, measures coverage and runs
  its own gates on itself with `py-ci-shared run-all`.

### Gate false negatives 2026-11-07 (audit 40)

- **Behaviour change:** `systemexit_in_library` `entry_names` default is anchored. It was an unanchored substring search, so `get_domain_dsn`, `client_dsn`, `remaining_budget` and `decline_request` were exempt as entries. Now only `main`, `cli`, `run_cli`, `parse_args`, `entry`, `entrypoint` (leading underscores allowed) and `main_*`/`cli_*` are exempt. What to do: a real entry point with another name goes in `entry_names=` or gets `# exit-ok: <reason>`.
- **Behaviour change:** `swallowed_exceptions` also judges the `OSError` subclasses (`ConnectionError` family, `TimeoutError`, `BrokenPipeError`, `BlockingIOError`, `InterruptedError`, `socket.error`/`timeout`/`gaierror`, `os.error`, `select.error`), a module-level tuple constant (`ERRS = (OSError, ...)`; `except ERRS`), and `suppress(*errs)` (an unresolvable starred argument counts as broad). What to do: handle the failure, or mark `# swallow-ok: <reason>`; the baseline refresh accepts existing sites.
- **Behaviour change:** `hardcoded_seed_in_library` also sees the keywords `random_seed`, `rng`, `rand_state`, `seed_value`, a local name assigned once to an int (`s = 42; default_rng(s)`), and the seeders `SeedSequence`, `PCG64`/`MT19937`/`Philox`/`SFC64`, `.manual_seed()`, `tf.random.set_seed`, `keras.utils.set_random_seed`, `transformers.set_seed`, `seed_everything`. What to do: forward the function's own seed, or mark `# seed-ok: <reason>`.
- **Behaviour change:** `swallowed_exceptions`: a `try` body is a fallback (exempt) only when no call follows its first `return`; `if hit: return x` then `p.write_text(...)` is now reported. `check_then_act_outside_lock` also treats `del d[k]`, `d.pop/popitem/update(...)` and `cls._d` as acts (pyutilz: 1 -> 17 findings). `unsafe_deserialization` reports an `Unpickler` subclass without `find_class`, a verify call that does not run on every path to the load, and `np.load(allow_pickle=<variable>)`. `predictable_tmp_path` exempts only when a per-user value flows into the path (an unused `os.getuid()` no longer exempts), and reports `.format()` paths and `tempfile.mktemp()`. `rollback_then_continue` sees writes through the rolled-back object (`s.add(i)`, `repo.save(i)`, `insert_row(conn, i)`, `cur.execute(sql, params)`). `systemexit_in_library` resolves `import sys as _sys` and `import mod; mod.fn()`. `env_flag_parsing` adds `bool(int(env))`, `"X" in os.environ` and f-string keys; `module_reload_safety` adds `sys.modules.clear()`, `sys.modules[k] = None`, `getattr(importlib, "reload")`; `naive_utcnow` sees `getattr(x, "utcnow")`. What to do: fix the site, mark the gate's `-ok` marker with a reason, or refresh the baseline.
- `check_then_act_outside_lock` (fewer findings, follow-up to the entry above; pyutilz src 8 -> 0): `d.pop(k, default)` is idempotent and no longer an act; a removal under an `if` inside a lock block whose earlier check was also under a lock is not paired with it (the lock-table refcount pattern); a helper named `*_unlocked`/`*_locked` or documented "caller holds the lock" is not judged. An unlocked test followed by a locked removal is still reported.
