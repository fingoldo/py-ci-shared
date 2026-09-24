# Audit: scanner correctness, modules m..z and the mutation harness

Merged from four sub-audits (m-p, r-s, t-z, mutation_teeth group). No source edits were made.
Repro scripts live in the session scratchpad (`repro.py`, `rs/`, `tz/`, `r1..r6.py`).

## Cross-cutting

- **BOM files silently skipped** (MP-1, RS-1, TZ-4, MT-3): reading with `utf-8` keeps U+FEFF, `ast.parse` raises, the `except SyntaxError` drops the file. Fix once in a shared reader (`utf-8-sig`).
- **Parse failures fail open** (MP-2, RS-2, TZ-4): unparsable files are skipped silently; `min_*` floors count files passed in, not files parsed.
- **Missing baseline seeds and skips** (TZ-17, RS-4): deleting the baseline makes CI permanently green.
- **Refresh flag read from `sys.argv`** (RS-6): ignored under xdist or `pytest.main`.
- **Tests lock in gaps**: `test_naive_utcnow.py:78` (MP-2), `test_marker_runner_coverage.py:135-141` (MP-10), `test_pytest_markers.py:92-94` (MP-38), `test_sql_verify.py:101` (RS-45), readme baseline test line 228 (RS-5).


`_toml_compat.py`: no findings.

Test gaps: `test_mutation_teeth.py` covers none of MT-1, BOM, zero mutants, generator/empty `lines`, `sweep_files(jobs>1)`, twin fan-out, `wider_net_note` replay, fd-1 worker writes. `_mutation_worker` has no test file; `test_teeth_sweep.py` covers only `_apply`.

### Proposed split of mutation_teeth.py (2099 LOC → 5 modules, re-exported)

1. `_mutation_model.py` (~230): HARNESS_VERSION, _UNJUSTIFIED, REFRESH_FLAG, _COPY_IGNORE, _CONTAINER_SAMPLE, MutationHarnessError, Mutant, MutationRun, (de)serialisers.
2. `_mutation_operators.py` (~720): operator tables, range/span helpers, candidate generators, `generate_mutants`.
3. `_mutation_fingerprint.py` (~180): `_first_party_imports`, `fingerprint`, plugin cache.
4. `_mutation_runner.py` (~360): `_pytest_env`, crash classification, `_run_pytest`, `_WarmRunner`, `_classify*`.
5. `mutation_teeth.py` (~600): sweep, `find_surviving_mutants` (with extracted `_scope_key`, `_load_cached_run`, `_store_cached_run`), refresh option, `sweep_files`, public asserts, `__all__`.

Caveats: look up `_run_pytest`/`_WarmRunner`/`_classify*` through the `mutation_teeth` namespace so existing monkeypatches keep working; add the new files to `tests/test_mutation_harness_version_is_bumped.py:27`; the move re-keys the code_audit baseline.

## Findings

### MP-1 (High) -- BOM files are dropped: ast.parse rejects U+FEFF and the SyntaxError is swallowed

**Disposition:** OPEN

- **Where:** marker_runner_coverage.py:80-86, meta_private_imports.py:57, module_reload_safety.py:109-117, naive_utcnow.py:65, optional_truthiness.py:86, private_imports.py:72, phantom_code_references.py:95, pytest_markers.py:109, prompt_field_parity.py:93
- **Finding:** BOM files are dropped: `ast.parse` rejects U+FEFF and the SyntaxError is swallowed
- **Failing input:** BOM file with `datetime.utcnow()` → `[]`
- **Proposed fix:** read `utf-8-sig`; unparsable files are findings
- **Verified:** yes-repro

### MP-2 (High) -- any SyntaxError/decode error is a silent skip; newer syntax than the interpreter passes

**Disposition:** OPEN

- **Where:** same sites
- **Finding:** any SyntaxError/decode error is a silent skip; newer syntax than the interpreter passes
- **Failing input:** `def (:` + real offender
- **Proposed fix:** report or fail on unparsable files
- **Verified:** yes-read

### MP-3 (Med) -- only .utcnow() calls matched; default_factory=datetime.utcnow and utcfromtimestamp missed

**Disposition:** OPEN

- **Where:** naive_utcnow.py:44-51
- **Finding:** only `.utcnow()` calls matched; `default_factory=datetime.utcnow` and `utcfromtimestamp` missed
- **Failing input:** `Field(default_factory=datetime.utcnow)` → `[]`
- **Proposed fix:** match the Attribute, called or not
- **Verified:** yes-repro

### MP-4 (Med) -- no file-count floor; missing root passes

**Disposition:** OPEN

- **Where:** naive_utcnow.py:61, module_reload_safety.py:129, private_imports.py:68
- **Finding:** no file-count floor; missing root passes
- **Failing input:** `assert_no_unpaired_reloads(Path("nonexistent"))` passes
- **Proposed fix:** `min_files`
- **Verified:** yes-repro

### MP-5 (Med) -- class-level markers, AnnAssign/class pytestmark, from pytest import mark missed

**Disposition:** OPEN

- **Where:** marker_runner_coverage.py:95-100
- **Finding:** class-level markers, AnnAssign/class `pytestmark`, `from pytest import mark` missed
- **Failing input:** repro: only test_a found
- **Proposed fix:** walk ClassDef decorators, AnnAssign, aliases
- **Verified:** yes-repro

### MP-6 (High) -- positional tests (no /, no .py) not seen as path → "reaches everything"

**Disposition:** OPEN

- **Where:** marker_runner_coverage.py:117
- **Finding:** positional `tests` (no `/`, no `.py`) not seen as path → "reaches everything"
- **Failing input:** `pytest tests -m 'not integration'` → `((), ...)`
- **Proposed fix:** any non-option positional is a path
- **Verified:** yes-repro

### MP-7 (Med) -- ./tests not normalised; cd pkg && pytest tests/ never matches

**Disposition:** OPEN

- **Where:** marker_runner_coverage.py:118,167
- **Finding:** `./tests` not normalised; `cd pkg && pytest tests/` never matches
- **Failing input:** `Runner(paths=("./tests",))` → False
- **Proposed fix:** normpath; per-runner cwd
- **Verified:** yes-repro

### MP-8 (Med) -- first -m used, docstring says last wins; -m=/-mexpr not parsed

**Disposition:** OPEN

- **Where:** marker_runner_coverage.py:119
- **Finding:** first `-m` used, docstring says last wins; `-m=`/`-mexpr` not parsed
- **Failing input:** `-m integration -m 'not integration'`
- **Proposed fix:** `findall()[-1]`
- **Verified:** yes-repro

### MP-9 (Med) -- node-id runner reaches whole file; -k ignored

**Disposition:** OPEN

- **Where:** marker_runner_coverage.py:167
- **Finding:** node-id runner reaches whole file; `-k` ignored
- **Failing input:** `tests/test_a.py::test_x` → True
- **Proposed fix:** compare node id to `test.key`
- **Verified:** yes-repro

### MP-10 (Med) -- ratchet key is the file: new tests in a known file excused; node-id known reported stale

**Disposition:** OPEN

- **Where:** marker_runner_coverage.py:211-212
- **Finding:** ratchet key is the file: new tests in a known file excused; node-id `known` reported stale
- **Failing input:** two unselected tests, `known=["tests/test_a.py"]` passes
- **Proposed fix:** key by `file::name`, migrate
- **Verified:** yes-repro

### MP-11 (Low) -- eval of marker expr; exception = "selects" (fail-open)

**Disposition:** OPEN

- **Where:** marker_runner_coverage.py:159-161
- **Finding:** `eval` of marker expr; exception = "selects" (fail-open)
- **Failing input:** `-m "slow andd integration"` → True
- **Proposed fix:** small parser; report unparseable
- **Verified:** yes-read

### MP-12 (Med) -- aliased reload, importlib as il, sys as _sys missed; substring prefilter drops files

**Disposition:** OPEN

- **Where:** module_reload_safety.py:49-58, 31
- **Finding:** aliased `reload`, `importlib as il`, `sys as _sys` missed; substring prefilter drops files
- **Failing input:** `from importlib import reload; reload(sys)`
- **Proposed fix:** resolve aliases, widen prefilter
- **Verified:** yes-repro

### MP-13 (High) -- any sys.modules[...] = x counts as restore, including installing a fake

**Disposition:** OPEN

- **Where:** module_reload_safety.py:64
- **Finding:** any `sys.modules[...] = x` counts as restore, including installing a fake
- **Failing input:** `sys.modules["foo"]=object(); importlib.reload(sys)`
- **Proposed fix:** only snapshot-sourced or finally/finalizer restores
- **Verified:** yes-repro

### MP-14 (Med) -- innermost-scope only (FP); conftest/usefixtures fixtures unseen (FP); one autouse resto...

**Disposition:** OPEN

- **Where:** module_reload_safety.py:98-104, 150
- **Finding:** innermost-scope only (FP); conftest/usefixtures fixtures unseen (FP); one autouse restore clears whole file (FN)
- **Failing input:** nested inner() reload with outer finally → flagged
- **Proposed fix:** walk enclosing scopes, include conftest, scope autouse
- **Verified:** yes-repro/yes-read

### MP-15 (Low) -- any __dict__.update, addfinalizer, subprocess.run counts as restore

**Disposition:** OPEN

- **Where:** module_reload_safety.py:70-73
- **Finding:** any `__dict__.update`, `addfinalizer`, `subprocess.run` counts as restore
- **Failing input:** `request.addfinalizer(close_db)`
- **Proposed fix:** finalizer must reference module/snapshot
- **Verified:** yes-read

### MP-16 (Low) -- allowlist keyed on (path, line): drift, no stale check, missing roots skipped

**Disposition:** OPEN

- **Where:** module_reload_safety.py:156-174
- **Finding:** allowlist keyed on (path, line): drift, no stale check, missing roots skipped
- **Failing input:** new reload lands on old line
- **Proposed fix:** key by statement text; fail stale/missing
- **Verified:** yes-read

### MP-17 (Med) -- success line + nonzero exit reported clean

**Disposition:** OPEN

- **Where:** mypy_gate.py:52-73
- **Finding:** success line + nonzero exit reported clean
- **Failing input:** `check_mypy_output("Success: ...", 2)` → None
- **Proposed fix:** require returncode 0
- **Verified:** yes-repro

### MP-18 (Low) -- --min-files w/o value IndexError; --min-files=200 passed to mypy; locale decoding on Wi...

**Disposition:** OPEN

- **Where:** mypy_gate.py:80-85
- **Finding:** `--min-files` w/o value IndexError; `--min-files=200` passed to mypy; locale decoding on Windows
- **Failing input:** `main(["--min-files"])`
- **Proposed fix:** argparse; utf-8 errors=replace
- **Verified:** yes-read

### MP-19 (Med) -- _ENV_PROBE substring (os in loss); any enclosing Try exempts

**Disposition:** OPEN

- **Where:** nondiscriminating_shapes.py:34,114-123
- **Finding:** `_ENV_PROBE` substring (`os` in `loss`); any enclosing Try exempts
- **Failing input:** `loss=...; if loss is None: pytest.skip()` → `[]`
- **Proposed fix:** `\b` identifiers; only ExceptHandler bodies
- **Verified:** yes-repro

### MP-20 (Low) -- reversed ranges, AnnAssign/walrus/with, from pytest import skip missed

**Disposition:** OPEN

- **Where:** nondiscriminating_shapes.py:50,97,91
- **Finding:** reversed ranges, AnnAssign/walrus/with, `from pytest import skip` missed
- **Failing input:** `assert 100 > rmse > 0` → `[]`
- **Proposed fix:** handle Gt/GtE, AnnAssign, aliases
- **Verified:** yes-repro

### MP-21 (High) -- _unimportable discarded; walk_packages(onerror=lambda _: None) hides subpackages

**Disposition:** OPEN

- **Where:** package_doctests.py:91
- **Finding:** `_unimportable` discarded; `walk_packages(onerror=lambda _: None)` hides subpackages
- **Failing input:** module with `>>>` raising ImportError → green
- **Proposed fix:** fail on unimportable with tolerate list
- **Verified:** yes-read

### MP-22 (Low) -- plain prefix: pkg.io skips pkg.iostats

**Disposition:** OPEN

- **Where:** package_doctests.py:66
- **Finding:** plain prefix: `pkg.io` skips `pkg.iostats`
- **Failing input:** skip_prefixes=("pkg.io",)
- **Proposed fix:** `== p or startswith(p+".")`
- **Verified:** yes-read

### MP-23 (Med) -- if not x, IfExp, while, assert, comprehension ifs missed

**Disposition:** OPEN

- **Where:** optional_truthiness.py:97-108
- **Finding:** `if not x`, IfExp, while, assert, comprehension ifs missed
- **Failing input:** repro m.py lines 3/5/7
- **Proposed fix:** unwrap Not; include those tests
- **Verified:** yes-repro

### MP-24 (Med) -- key uses path.name; no stale check; line-number keys

**Disposition:** OPEN

- **Where:** optional_truthiness.py:104,127-132
- **Finding:** key uses `path.name`; no stale check; line-number keys
- **Failing input:** two `utils.py`, one baselined
- **Proposed fix:** repo-relative key, stale check
- **Verified:** yes-read

### MP-25 (Low) -- walk descends into nested defs; Annotated not unwrapped

**Disposition:** OPEN

- **Where:** optional_truthiness.py:97
- **Finding:** walk descends into nested defs; `Annotated` not unwrapped
- **Failing input:** nested `def g(limit: list)`
- **Proposed fix:** stop at nested defs
- **Verified:** yes-read

### MP-26 (Med) -- from pkg.a import _impl and relative imports missed

**Disposition:** OPEN

- **Where:** private_imports.py:42-48,77-80
- **Finding:** `from pkg.a import _impl` and relative imports missed
- **Failing input:** repro: 2 of 3 missed
- **Proposed fix:** judge `module.alias`; resolve relatives
- **Verified:** yes-repro

### MP-27 (Low) -- relative_to ValueError when src outside root

**Disposition:** OPEN

- **Where:** private_imports.py:81
- **Finding:** `relative_to` ValueError when src outside root
- **Failing input:** src outside repo
- **Proposed fix:** fallback
- **Verified:** yes-read

### MP-28 (Low) -- non-recursive glob; parse errors silent; import_module("pkg._x") missed

**Disposition:** OPEN

- **Where:** meta_private_imports.py:84,57-59
- **Finding:** non-recursive glob; parse errors silent; `import_module("pkg._x")` missed
- **Failing input:** `meta/sub/test_x.py`
- **Proposed fix:** rglob, report parse failures
- **Verified:** yes-read

### MP-29 (Med) -- # inside a string treated as comment (FP)

**Disposition:** OPEN

- **Where:** phantom_code_references.py:181-182
- **Finding:** `#` inside a string treated as comment (FP)
- **Failing input:** `"http://a#`Ghost()`"`
- **Proposed fix:** tokenize COMMENT tokens
- **Verified:** yes-repro

### MP-30 (Med) -- docstring parity flips on SQL = """ literals

**Disposition:** OPEN

- **Where:** phantom_code_references.py:166-180
- **Finding:** docstring parity flips on `SQL = """` literals
- **Failing input:** SQL block in repro
- **Proposed fix:** tokenize/AST spans
- **Verified:** yes-read

### MP-31 (Med) -- declared head → Class.member never checked; a.b.c skipped

**Disposition:** OPEN

- **Where:** phantom_code_references.py:226-231
- **Finding:** declared head → `Class.member` never checked; `a.b.c` skipped
- **Failing input:** `` `Foo.renamed_method()` ``
- **Proposed fix:** check members of repo classes
- **Verified:** yes-repro

### MP-32 (Low) -- test files matched by basename over rglob incl .venv

**Disposition:** OPEN

- **Where:** phantom_code_references.py:205,217
- **Finding:** test files matched by basename over rglob incl .venv
- **Failing input:** `tests/unit/test_foo.py` claim
- **Proposed fix:** full path, tracked files
- **Verified:** yes-read

### MP-33 (Med) -- also resolves vs repo root (FN); /x.md joined to drive root (FP)

**Disposition:** OPEN

- **Where:** phantom_markdown_links.py:80
- **Finding:** also resolves vs repo root (FN); `/x.md` joined to drive root (FP)
- **Failing input:** `docs/guide.md` → `[a](README.md)` passes
- **Proposed fix:** resolve relative to file; `/` = repo root
- **Verified:** yes-repro

### MP-34 (Low) -- anchors, queries, <..>, reference links, other extensions skipped; fences scanned

**Disposition:** OPEN

- **Where:** phantom_markdown_links.py:39
- **Finding:** anchors, queries, `<..>`, reference links, other extensions skipped; fences scanned
- **Failing input:** `[c](missing.md#sec)`
- **Proposed fix:** strip fragment, widen, skip fences
- **Verified:** yes-repro

### MP-35 (High) -- required non-str fields get string sentinels → ValidationError read as "enforced"; stil...

**Disposition:** OPEN

- **Where:** pydantic_field_bounds.py:43-44,67-77,93
- **Finding:** required non-str fields get string sentinels → ValidationError read as "enforced"; still counted as audited
- **Failing input:** `Unenf` with required `n: int` → `(1, [])`
- **Proposed fix:** probe valid kwargs first; inconclusive otherwise; check error `loc`
- **Verified:** yes-repro

### MP-36 (Med) -- only first bound probed; conint/Interval skipped

**Disposition:** OPEN

- **Where:** pydantic_field_bounds.py:90
- **Finding:** only first bound probed; `conint`/Interval skipped
- **Failing input:** `ge=0, le=1` never tests le
- **Proposed fix:** probe all bounds, unpack Interval
- **Verified:** yes-repro

### MP-37 (Low) -- fractional bound on int field rejected by int parsing

**Disposition:** OPEN

- **Where:** pydantic_field_bounds.py:57-58
- **Finding:** fractional bound on int field rejected by int parsing
- **Failing input:** `n: int = Field(0, lt=0.5)`
- **Proposed fix:** typed violating value
- **Verified:** yes-read

### MP-38 (Med) -- [tool.pytest], pytest.toml, root conftest, non-literal addinivalue_line missed → false...

**Disposition:** OPEN

- **Where:** pytest_markers.py:62-69,73,105
- **Finding:** `[tool.pytest]`, `pytest.toml`, root conftest, non-literal `addinivalue_line` missed → false "unregistered"
- **Failing input:** repro r9
- **Proposed fix:** read those sources
- **Verified:** yes-repro

### MP-39 (Low) -- configparser error swallowed

**Disposition:** OPEN

- **Where:** pytest_markers.py:90-93
- **Finding:** configparser error swallowed
- **Failing input:** tox.ini duplicate `markers`
- **Proposed fix:** strict=False or surface
- **Verified:** yes-read

### MP-40 (Low) -- pin regex needs "ruff==x"; pre-commit regex order/quote sensitive

**Disposition:** OPEN

- **Where:** pinned_tool_versions.py:37,55
- **Finding:** pin regex needs `"ruff==x"`; pre-commit regex order/quote sensitive
- **Failing input:** `['ruff==0.6.0']` → None
- **Proposed fix:** tomllib + packaging; yaml
- **Verified:** yes-repro

### MP-41 (Med) -- $defs names reported as fields

**Disposition:** OPEN

- **Where:** prompt_field_parity.py:130-132
- **Finding:** `$defs` names reported as fields
- **Failing input:** `$defs: {Inner: ...}` → Inner
- **Proposed fix:** recurse into values only
- **Verified:** yes-repro

### MP-42 (Med) -- DDL types lack INT/BOOL/FLOAT/CHAR/quoted names

**Disposition:** OPEN

- **Where:** prompt_field_parity.py:233
- **Finding:** DDL types lack INT/BOOL/FLOAT/CHAR/quoted names
- **Failing input:** `score INT` → set()
- **Proposed fix:** generic `\w+ type` or sqlglot
- **Verified:** yes-repro

### MP-43 (Med) -- unparsable prompt module contributes nothing (fail-open); unguarded non-UTF8 reads crash

**Disposition:** OPEN

- **Where:** prompt_field_parity.py:93-95,153
- **Finding:** unparsable prompt module contributes nothing (fail-open); unguarded non-UTF8 reads crash
- **Failing input:** 3.13 syntax on 3.11 CI
- **Proposed fix:** raise/report
- **Verified:** yes-read

### MP-44 (Low) -- Sequence[, Mapping[, frozenset[, Optional[list] treated as scalar

**Disposition:** OPEN

- **Where:** prompt_field_parity.py:278
- **Finding:** `Sequence[`, `Mapping[`, `frozenset[`, `Optional[list]` treated as scalar
- **Failing input:** `tags: Sequence[str]`
- **Proposed fix:** parse annotation origin
- **Verified:** yes-read

### MP-45 (Low) -- float(group(1)) crashes on groupless pattern, None group, 1.2k

**Disposition:** OPEN

- **Where:** prose_numeric_claims.py:99
- **Finding:** `float(group(1))` crashes on groupless pattern, None group, `1.2k`
- **Failing input:** `r"\d+ tests"`
- **Proposed fix:** validate `groups==1`; report bad parse
- **Verified:** yes-read

### RS-1 (High) -- BOM files dropped; sql_verifier_coverage.py:63 crashes

**Disposition:** OPEN

- **Where:** readme_env_var_parity.py:131, resource_release_paths.py:71,90, runtime_registry_mutation.py:114, source_text_claims.py:272-283, spec_bound_doubles.py:70, sql_verifier_coverage.py:42, statement_compilation.py:103
- **Finding:** BOM files dropped; sql_verifier_coverage.py:63 crashes
- **Failing input:** BOM + `os.getenv('BOMVAR')` → set()
- **Proposed fix:** `utf-8-sig`
- **Verified:** yes-repro

### RS-2 (High) -- parse errors fail open; floors count inputs not parsed files

**Disposition:** OPEN

- **Where:** same + runtime_registry_mutation.py:139, source_text_claims.py:317
- **Finding:** parse errors fail open; floors count inputs not parsed files
- **Failing input:** syntax-error file passes `min_files=1`
- **Proposed fix:** fail on unparsable; floor on parsed
- **Verified:** yes-repro

### RS-3 (Med) -- from os import environ/getenv, os as _os, os.environ["X"], getenv(key=) missed

**Disposition:** OPEN

- **Where:** readme_env_var_parity.py:36-37
- **Finding:** `from os import environ/getenv`, `os as _os`, `os.environ["X"]`, `getenv(key=)` missed
- **Failing input:** 5 forms → set()
- **Proposed fix:** resolve aliases, Subscript, kwargs
- **Verified:** yes-repro

### RS-4 (Med) -- missing baseline seeds and skips

**Disposition:** OPEN

- **Where:** readme_env_var_parity.py:224, source_text_claims.py:363
- **Finding:** missing baseline seeds and skips
- **Failing input:** delete baseline in CI
- **Proposed fix:** seed only on refresh; fail when absent
- **Verified:** yes-read

### RS-5 (Low) -- baseline never tightens (test pins it)

**Disposition:** OPEN

- **Where:** readme_env_var_parity.py:232
- **Finding:** baseline never tightens (test pins it)
- **Failing input:** documented X stays excused
- **Proposed fix:** fail on stale entries
- **Verified:** yes-read

### RS-6 (Low) -- refresh from sys.argv misses xdist/pytest.main

**Disposition:** OPEN

- **Where:** readme_env_var_parity.py:224
- **Finding:** refresh from `sys.argv` misses xdist/`pytest.main`
- **Failing input:** `pytest.main([...])`
- **Proposed fix:** `config.getoption`
- **Verified:** yes-read

### RS-7 (Med) -- .coverage substring flags .coveragerc; /build/ flags packages named build

**Disposition:** OPEN

- **Where:** repo_hygiene.py:81,123
- **Finding:** `.coverage` substring flags `.coveragerc`; `/build/` flags packages named build
- **Failing input:** tracked `.coveragerc`
- **Proposed fix:** anchor on components/basename
- **Verified:** yes-repro

### RS-8 (Med) -- ls-files without -z: non-ASCII paths quoted, rules miss them

**Disposition:** OPEN

- **Where:** repo_hygiene.py:102,262
- **Finding:** `ls-files` without `-z`: non-ASCII paths quoted, rules miss them
- **Failing input:** `audits/проба.py`
- **Proposed fix:** `-z` + surrogateescape
- **Verified:** yes-repro

### RS-9 (Med) -- ${COV:-} accepted as guard

**Disposition:** OPEN

- **Where:** repo_hygiene.py:95
- **Finding:** `${COV:-}` accepted as guard
- **Failing input:** `X="${COV:-}"` then compare
- **Proposed fix:** only `:?` or non-empty default before compare
- **Verified:** yes-repro

### RS-10 (Low) -- test -n "$COV" not a guard (FP)

**Disposition:** OPEN

- **Where:** repo_hygiene.py:91-97
- **Finding:** `test -n "$COV"` not a guard (FP)
- **Failing input:** `test -n "$COV" \|\| exit 1`
- **Proposed fix:** add to template
- **Verified:** yes-repro

### RS-11 (Low) -- per-line simple $X compares only

**Disposition:** OPEN

- **Where:** repo_hygiene.py:86-90
- **Finding:** per-line simple `$X` compares only
- **Failing input:** `echo "${COV%\%} < 80" \| bc`
- **Proposed fix:** widen regex
- **Verified:** yes-read

### RS-12 (Med) -- only arg-less .dispose()

**Disposition:** OPEN

- **Where:** resource_release_paths.py:79
- **Finding:** only arg-less `.dispose()`
- **Failing input:** `await e.dispose(close=False)`
- **Proposed fix:** match `func.attr`
- **Verified:** yes-repro

### RS-13 (Low) -- substring protection; redispose() exempts module

**Disposition:** OPEN

- **Where:** resource_release_paths.py:60,63
- **Finding:** substring protection; `redispose()` exempts module
- **Failing input:** `with open(): fh.redispose()`
- **Proposed fix:** Call node match, same receiver
- **Verified:** yes-repro

### RS-14 (Low) -- constructor alias missed

**Disposition:** OPEN

- **Where:** resource_release_paths.py:48
- **Finding:** constructor alias missed
- **Failing input:** `create_async_engine as cae`
- **Proposed fix:** resolve asnames
- **Verified:** yes-read

### RS-15 (Med) -- decorator-factory inner deco flagged (FP)

**Disposition:** OPEN

- **Where:** runtime_registry_mutation.py:127
- **Finding:** decorator-factory inner `deco` flagged (FP)
- **Failing input:** `def register(n): def deco(fn): _REGISTRY[n]=fn`
- **Proposed fix:** inherit exemption for nested
- **Verified:** yes-repro

### RS-16 (High) -- any name called at module scope (incl

**Disposition:** OPEN

- **Where:** runtime_registry_mutation.py:84-88,127
- **Finding:** any name called at module scope (incl. `__main__` guard) exempts that name everywhere
- **Failing input:** `def main(): _REGISTRY[...]=1`
- **Proposed fix:** exclude `__main__`; per-module match
- **Verified:** yes-repro

### RS-17 (Med) -- collections.defaultdict/OrderedDict not recognised

**Disposition:** OPEN

- **Where:** runtime_registry_mutation.py:61
- **Finding:** `collections.defaultdict/OrderedDict` not recognised
- **Failing input:** `collections.defaultdict(list)`
- **Proposed fix:** `_callee_name`
- **Verified:** yes-repro

### RS-18 (Low) -- registries under try/if; mod._REGISTRY[k]=; aliases missed

**Disposition:** OPEN

- **Where:** runtime_registry_mutation.py:53,101-104
- **Finding:** registries under try/if; `mod._REGISTRY[k]=`; aliases missed
- **Failing input:** `reg._REGISTRY[k]=v`
- **Proposed fix:** walk compound stmts; Attribute targets
- **Verified:** yes-read

### RS-19 (Low) -- relative_to crash outside root

**Disposition:** OPEN

- **Where:** runtime_registry_mutation.py:117
- **Finding:** `relative_to` crash outside root
- **Failing input:** `/tmp/x.py`
- **Proposed fix:** fallback
- **Verified:** yes-read

### RS-20 (Med) -- patch applied although target check found missing attrs

**Disposition:** OPEN

- **Where:** safe_precommit.py:150
- **Finding:** patch applied although target check found missing attrs
- **Failing input:** pre-commit without `_CHECKOUT_CMD`
- **Proposed fix:** return False when missing
- **Verified:** yes-read

### RS-21 (Low) -- failed restore leaves concurrent edits only in patch file

**Disposition:** OPEN

- **Where:** safe_precommit.py:101-115
- **Finding:** failed restore leaves concurrent edits only in patch file
- **Failing input:** concurrent edit during hooks
- **Proposed fix:** louder signal
- **Verified:** yes-read

### RS-22 (Med) -- rf"/F", .extend, +=, dynamic f-strings missed; comments matched

**Disposition:** OPEN

- **Where:** save_failure_markers.py:31-32
- **Finding:** `rf"`/`F"`, `.extend`, `+=`, dynamic f-strings missed; comments matched
- **Failing input:** repro M1
- **Proposed fix:** AST or tolerant regex; skip comments
- **Verified:** yes-repro

### RS-23 (Med) -- missing root → {} passes

**Disposition:** OPEN

- **Where:** save_failure_markers.py:40,65
- **Finding:** missing root → `{}` passes
- **Failing input:** `Path("nope")`
- **Proposed fix:** `min_markers`, fail on missing root
- **Verified:** yes-repro

### RS-24 (Low) -- strict utf-8 read crashes scan

**Disposition:** OPEN

- **Where:** save_failure_markers.py:43
- **Finding:** strict utf-8 read crashes scan
- **Failing input:** cp1251 file
- **Proposed fix:** errors=replace or report
- **Verified:** yes-read

### RS-25 (Low) -- re-run after moving clone keeps stale export; no tests

**Disposition:** OPEN

- **Where:** setup_env.py:106
- **Finding:** re-run after moving clone keeps stale export; no tests
- **Failing input:** move clone, re-run
- **Proposed fix:** replace tagged line
- **Verified:** yes-read

### RS-26 (Low) -- value not XML-escaped / shell-quoted; decode error uncaught

**Disposition:** OPEN

- **Where:** setup_env.py:70,109
- **Finding:** value not XML-escaped / shell-quoted; decode error uncaught
- **Failing input:** path with `&`/`"`/`$`
- **Proposed fix:** escape, shlex.quote
- **Verified:** yes-read

### RS-27 (High) -- SECURITY DEFINER after $$ body never seen

**Disposition:** OPEN

- **Where:** sql_function_privileges.py:83-88
- **Finding:** `SECURITY DEFINER` after `$$` body never seen
- **Failing input:** `... $$ LANGUAGE plpgsql SECURITY DEFINER;`
- **Proposed fix:** scan options after body
- **Verified:** yes-repro

### RS-28 (High) -- quoted identifiers (pg_dump) / multi-line headers invisible

**Disposition:** OPEN

- **Where:** sql_function_privileges.py:48
- **Finding:** quoted identifiers (pg_dump) / multi-line headers invisible
- **Failing input:** `"public"."quoted"()`
- **Proposed fix:** accept quotes, multi-line
- **Verified:** yes-repro

### RS-29 (High) -- commented-out REVOKE counts

**Disposition:** OPEN

- **Where:** sql_function_privileges.py:96,129
- **Finding:** commented-out REVOKE counts
- **Failing input:** `-- REVOKE ... FROM PUBLIC;`
- **Proposed fix:** strip comments
- **Verified:** yes-repro

### RS-30 (High) -- schema ignored; DROP+CREATE ordering ignored

**Disposition:** OPEN

- **Where:** sql_function_privileges.py:56,99
- **Finding:** schema ignored; DROP+CREATE ordering ignored
- **Failing input:** `other.shadow` REVOKE covers `private.shadow`
- **Proposed fix:** key (schema,name); ordered replay
- **Verified:** yes-repro/yes-read

### RS-31 (Med) -- search_path checked on every definition, not last (FP); O(n·m) re-reads

**Disposition:** OPEN

- **Where:** sql_function_privileges.py:166-179
- **Finding:** search_path checked on every definition, not last (FP); O(n·m) re-reads
- **Failing input:** fixed in 002, still flagged from 001
- **Proposed fix:** last header per name
- **Verified:** yes-repro

### RS-32 (Low) -- REVOKE w/o parens, ON ALL FUNCTIONS IN SCHEMA, default privileges missed (FP)

**Disposition:** OPEN

- **Where:** sql_function_privileges.py:56
- **Finding:** REVOKE w/o parens, `ON ALL FUNCTIONS IN SCHEMA`, default privileges missed (FP)
- **Failing input:** `REVOKE EXECUTE ON ALL FUNCTIONS ...`
- **Proposed fix:** extend regexes
- **Verified:** yes-read

### RS-33 (Med) -- quote-led lines skipped incl

**Disposition:** OPEN

- **Where:** source_text_ban.py:105
- **Finding:** quote-led lines skipped incl. assert continuations
- **Failing input:** multi-line `assert (...)`
- **Proposed fix:** skip only docstring ranges
- **Verified:** yes-repro

### RS-34 (Med) -- non-Python literal on line exempts; getsource as gs, open(__file__).read() missed

**Disposition:** OPEN

- **Where:** source_text_ban.py:110
- **Finding:** non-Python literal on line exempts; `getsource as gs`, `open(__file__).read()` missed
- **Failing input:** `"x.json" in Path(__file__).read_text()`
- **Proposed fix:** scope NON_PY to path arg; consider deprecating vs source_text_claims
- **Verified:** yes-repro

### RS-35 (High) -- fixtures, getsource as gs, dis.get_instructions, closures missed

**Disposition:** OPEN

- **Where:** source_text_claims.py:144,146,196
- **Finding:** fixtures, `getsource as gs`, `dis.get_instructions`, closures missed
- **Failing input:** repro C1: 4 of 5 missed
- **Proposed fix:** alias resolution, fixture taint, closure taint
- **Verified:** yes-repro

### RS-36 (Med) -- key rel::func::kind: more claims of same kind never new

**Disposition:** OPEN

- **Where:** source_text_claims.py:73-75
- **Finding:** key `rel::func::kind`: more claims of same kind never new
- **Failing input:** 1 baselined → 6 pass
- **Proposed fix:** count per key
- **Verified:** yes-read

### RS-37 (Med) -- m = AsyncMock(); f(m), tuple unpack, patch() as s, spec=None missed

**Disposition:** OPEN

- **Where:** spec_bound_doubles.py:81-89
- **Finding:** `m = AsyncMock(); f(m)`, tuple unpack, `patch() as s`, `spec=None` missed
- **Failing input:** repro S1
- **Proposed fix:** track bare-mock names
- **Verified:** yes-repro

### RS-38 (Med) -- startswith w/o boundary (prose FPs); comment-led SQL missed; chained targets

**Disposition:** OPEN

- **Where:** sql_verifier_coverage.py:50
- **Finding:** `startswith` w/o boundary (prose FPs); comment-led SQL missed; chained targets
- **Failing input:** `MSG="Withdrawal failed"`
- **Proposed fix:** strip comments, `\b` regex
- **Verified:** yes-repro

### RS-39 (Low) -- pkg.__init__.X key; no dedicated test file

**Disposition:** OPEN

- **Where:** sql_verifier_coverage.py:45
- **Finding:** `pkg.__init__.X` key; no dedicated test file
- **Failing input:** `pkg/__init__.py: Q=...`
- **Proposed fix:** strip `.__init__`
- **Verified:** yes-read

### RS-40 (High) -- check() commits every statement: writes persist

**Disposition:** OPEN

- **Where:** sql_verify.py:95
- **Finding:** `check()` commits every statement: writes persist
- **Failing input:** `UPDATE t SET a=1 RETURNING id`
- **Proposed fix:** always rollback
- **Verified:** yes-read

### RS-41 (Med) -- fetchall() on no-result statement → FAIL

**Disposition:** OPEN

- **Where:** sql_verify.py:94
- **Finding:** `fetchall()` on no-result statement → FAIL
- **Failing input:** `INSERT INTO t VALUES (1)`
- **Proposed fix:** fetch only if `cur.description`
- **Verified:** yes-read

### RS-42 (Med) -- psycopg2 with connect() does not close; no connect_timeout

**Disposition:** OPEN

- **Where:** sql_verify.py:176
- **Finding:** psycopg2 `with connect()` does not close; no connect_timeout
- **Failing input:** any run
- **Proposed fix:** `contextlib.closing`, timeout
- **Verified:** yes-read

### RS-43 (Med) -- every connect error → SKIPPED, exit 0 with --skip-without-db; +driver DSN not normalised

**Disposition:** OPEN

- **Where:** sql_verify.py:167-172
- **Finding:** every connect error → SKIPPED, exit 0 with `--skip-without-db`; `+driver` DSN not normalised
- **Failing input:** `postgresql+psycopg2://...`
- **Proposed fix:** skip only network errors
- **Verified:** yes-read

### RS-44 (Low) -- psycopg2 imported before DSN check

**Disposition:** OPEN

- **Where:** sql_verify.py:160
- **Finding:** psycopg2 imported before DSN check
- **Failing input:** env without psycopg2
- **Proposed fix:** import later
- **Verified:** yes-read

### RS-45 (Low) -- params={} always passed; % literal behaviour undetermined

**Disposition:** OPEN

- **Where:** sql_verify.py:93
- **Finding:** `params={}` always passed; `%` literal behaviour undetermined
- **Failing input:** `SELECT 'a%'`
- **Proposed fix:** pass None
- **Verified:** no

### RS-46 (Low) -- Python slices flagged; quoted casts missed; comments/docstrings scanned

**Disposition:** OPEN

- **Where:** sqlalchemy_text_binds.py:29,36
- **Finding:** Python slices flagged; quoted casts missed; comments/docstrings scanned
- **Failing input:** `xs[:n::step]`
- **Proposed fix:** scan string literals only
- **Verified:** yes-repro

### RS-47 (High) -- blame failure → {} → all skipped; shallow clone makes gate a no-op

**Disposition:** OPEN

- **Where:** stale_comment_age.py:107,162
- **Finding:** blame failure → `{}` → all skipped; shallow clone makes gate a no-op
- **Failing input:** `fetch-depth: 1`
- **Proposed fix:** fail on blame error; detect shallow
- **Verified:** yes-read

### RS-48 (High) -- trailing # TODO never checked

**Disposition:** OPEN

- **Where:** stale_comment_age.py:38
- **Finding:** trailing `# TODO` never checked
- **Failing input:** `x = 1  # TODO fix`
- **Proposed fix:** tokenize comments
- **Verified:** yes-repro

### RS-49 (High) -- issue-ref regex on whole line exempts foo(x), UTF-8

**Disposition:** OPEN

- **Where:** stale_comment_age.py:41,152
- **Finding:** issue-ref regex on whole line exempts `foo(x)`, `UTF-8`
- **Failing input:** `# TODO: call foo(x) later`
- **Proposed fix:** ref only right after TODO
- **Verified:** yes-repro

### RS-50 (Med) -- commented-out code needs ;/,: Python dead calls missed

**Disposition:** OPEN

- **Where:** stale_comment_age.py:40
- **Finding:** commented-out code needs `;`/`,`: Python dead calls missed
- **Failing input:** `# foo(bar)`
- **Proposed fix:** no terminator for `#` langs
- **Verified:** yes-repro

### RS-51 (Low) -- SHA-256 repos (64 hex) rejected

**Disposition:** OPEN

- **Where:** stale_comment_age.py:113
- **Finding:** SHA-256 repos (64 hex) rejected
- **Failing input:** sha256 repo
- **Proposed fix:** `{7,64}`
- **Verified:** yes-repro

### RS-52 (Low) -- one -L per candidate (cmdline limit); locale decoding

**Disposition:** OPEN

- **Where:** stale_comment_age.py:103,106
- **Finding:** one `-L` per candidate (cmdline limit); locale decoding
- **Failing input:** 2k TODOs
- **Proposed fix:** batch; utf-8
- **Verified:** yes-read

### RS-53 (High) -- mock.patch and aliases not in _PATCH_NAMES

**Disposition:** OPEN

- **Where:** statement_compilation.py:33,109
- **Finding:** `mock.patch` and aliases not in `_PATCH_NAMES`
- **Failing input:** `with mock.patch("pkg.mod.insert")`
- **Proposed fix:** suffix match, aliases
- **Verified:** yes-repro

### RS-54 (High) -- routing exemption starts at def line, so stacked @patch flagged

**Disposition:** OPEN

- **Where:** statement_compilation.py:89
- **Finding:** routing exemption starts at def line, so stacked `@patch` flagged
- **Failing input:** `@patch` over `@pytest.mark.routing`
- **Proposed fix:** start at min decorator lineno
- **Verified:** yes-repro

### RS-55 (Med) -- new_callable and autospec=False count as autospecced

**Disposition:** OPEN

- **Where:** statement_compilation.py:34,65
- **Finding:** `new_callable` and `autospec=False` count as autospecced
- **Failing input:** `new_callable=MagicMock`
- **Proposed fix:** require truthy value
- **Verified:** yes-repro

### RS-56 (Low) -- last-name match flags HTTP mocks; module pytestmark ignored

**Disposition:** OPEN

- **Where:** statement_compilation.py:31,54
- **Finding:** last-name match flags HTTP mocks; module `pytestmark` ignored
- **Failing input:** `patch("httpx.AsyncClient.delete")`
- **Proposed fix:** configurable module prefix
- **Verified:** yes-repro

### RS-57 (Low) -- no floor; relative_to crash

**Disposition:** OPEN

- **Where:** statement_compilation.py:101-105,107
- **Finding:** no floor; `relative_to` crash
- **Failing input:** `test_files=[]`
- **Proposed fix:** floor, fallback
- **Verified:** yes-read

### RS-58 (Low) -- no tests for BOM, aliases, decorator factories, post-body DEFINER, quoted ids, trailing...

**Disposition:** OPEN

- **Where:** tests
- **Finding:** no tests for BOM, aliases, decorator factories, post-body DEFINER, quoted ids, trailing TODOs, `mock.patch`; `setup_env` untested; some tests pin questionable behaviour
- **Failing input:** —
- **Proposed fix:** one regression test per repro
- **Verified:** yes-read

### TZ-1 (High) -- cat-file -e = object exists, not reachable: staged-only work counts as committed → REMO...

**Disposition:** OPEN

- **Where:** worktree_hygiene.py:117-119, 141
- **Finding:** `cat-file -e` = object exists, not reachable: staged-only work counts as committed → REMOVABLE
- **Failing input:** `git add new.py` in wt
- **Proposed fix:** reachability check
- **Verified:** yes-repro

### TZ-2 (High) -- ignored files (.env, data) never unsaved

**Disposition:** OPEN

- **Where:** worktree_hygiene.py:130
- **Finding:** ignored files (`.env`, data) never unsaved
- **Failing input:** ignored `.env`
- **Proposed fix:** `--ignored=matching` + filter
- **Verified:** yes-repro

### TZ-3 (High) -- failed git cherry → every branch "spare"

**Disposition:** OPEN

- **Where:** worktree_hygiene.py:214, 219
- **Finding:** failed `git cherry` → every branch "spare"
- **Failing input:** `ref="origin/nope"` → `['master']`
- **Proposed fix:** `rev-parse --verify` first
- **Verified:** yes-repro

### TZ-4 (High) -- parse/decode failures dropped; BOM; uncalled_functions loses call sites → FPs

**Disposition:** OPEN

- **Where:** uncalled_functions.py:80-84; unread_init_params.py:101-104; unresolved_imports.py:49-53, 220-223; vacuous_loop_assertions.py:155-162; value_bearing_asserts.py:67-70
- **Finding:** parse/decode failures dropped; BOM; uncalled_functions loses call sites → FPs
- **Failing input:** BOM assert file → `([], 0)`
- **Proposed fix:** utf-8-sig, fail on unparsed
- **Verified:** yes-repro

### TZ-5 (Med) -- __getattr__ substring anywhere marks module dynamic

**Disposition:** OPEN

- **Where:** unresolved_imports.py:33, 54
- **Finding:** `__getattr__` substring anywhere marks module dynamic
- **Failing input:** class method `__getattr__`
- **Proposed fix:** AST module-level only
- **Verified:** yes-repro

### TZ-6 (Med) -- pkg prefix matches pkg_other

**Disposition:** OPEN

- **Where:** unresolved_imports.py:187
- **Finding:** `pkg` prefix matches `pkg_other`
- **Failing input:** `from pkg_other import X`
- **Proposed fix:** `== p or startswith(p+".")`
- **Verified:** yes-repro

### TZ-7 (Med) -- type X=, with/for/walrus/match/global bindings missed (FP); walk over-binds nested loca...

**Disposition:** OPEN

- **Where:** unresolved_imports.py:145-153
- **Finding:** `type X=`, with/for/walrus/match/global bindings missed (FP); walk over-binds nested locals (FN)
- **Failing input:** `type Alias = int`
- **Proposed fix:** handle nodes; stop at defs
- **Verified:** yes-repro

### TZ-8 (Low) -- only missing[0] reported

**Disposition:** OPEN

- **Where:** unresolved_imports.py:201
- **Finding:** only `missing[0]` reported
- **Failing input:** `from pkg.a import A, B`
- **Proposed fix:** report all
- **Verified:** yes-read

### TZ-9 (Low) -- suppress(ImportError), tuple raises, BaseException guards unseen (FP)

**Disposition:** OPEN

- **Where:** unresolved_imports.py:244, 272
- **Finding:** `suppress(ImportError)`, tuple raises, BaseException guards unseen (FP)
- **Failing input:** `with suppress(ImportError): ...`
- **Proposed fix:** accept them
- **Verified:** yes-read

### TZ-10 (Med) -- floor satisfied by assert in another floorless loop with same var

**Disposition:** OPEN

- **Where:** vacuous_loop_assertions.py:112, 124
- **Finding:** floor satisfied by assert in another floorless loop with same var
- **Failing input:** two `for x` loops
- **Proposed fix:** exclude asserts in other loops
- **Verified:** yes-repro

### TZ-11 (Med) -- nested function loops reported twice; duplicate keys

**Disposition:** OPEN

- **Where:** vacuous_loop_assertions.py:165-168
- **Finding:** nested function loops reported twice; duplicate keys
- **Failing input:** outer/inner
- **Proposed fix:** don't descend into nested defs
- **Verified:** yes-repro

### TZ-12 (Med) -- continue/pass/raise/pytest.fail/with subtests bodies not assert-only

**Disposition:** OPEN

- **Where:** vacuous_loop_assertions.py:89-101
- **Finding:** `continue`/`pass`/`raise`/`pytest.fail`/`with subtests` bodies not assert-only
- **Failing input:** `if not i: continue; assert i>0`
- **Proposed fix:** treat as assert-only
- **Verified:** yes-repro

### TZ-13 (Low) -- [*m] treated non-empty

**Disposition:** OPEN

- **Where:** vacuous_loop_assertions.py:146
- **Finding:** `[*m]` treated non-empty
- **Failing input:** `for q in [*m]`
- **Proposed fix:** reject Starred
- **Verified:** yes-repro

### TZ-14 (Low) -- cwd-relative keys; crash outside root

**Disposition:** OPEN

- **Where:** vacuous_loop_assertions.py:163
- **Finding:** cwd-relative keys; crash outside root
- **Failing input:** relative files from other cwd
- **Proposed fix:** resolve + fallback
- **Verified:** yes-read

### TZ-15 (Med) -- identical asserts collapse into one key; 90-char truncation collisions

**Disposition:** OPEN

- **Where:** value_bearing_asserts.py:80-83, 104
- **Finding:** identical asserts collapse into one key; 90-char truncation collisions
- **Failing input:** two `assert n > 0`
- **Proposed fix:** multiset ratchet, full key
- **Verified:** yes-repro

### TZ-16 (Low) -- bare Name/Attribute/Subscript always narrowing

**Disposition:** OPEN

- **Where:** value_bearing_asserts.py:43
- **Finding:** bare Name/Attribute/Subscript always narrowing
- **Failing input:** `assert self.enabled`
- **Proposed fix:** optional strict mode
- **Verified:** yes-read

### TZ-17 (Med) -- missing baseline seeds and skips

**Disposition:** OPEN

- **Where:** uncalled_functions.py:192; value_bearing_asserts.py:105
- **Finding:** missing baseline seeds and skips
- **Failing input:** delete baseline
- **Proposed fix:** seed on refresh only
- **Verified:** yes-read

### TZ-18 (Med) -- self-recursion / same-name locals count as calls

**Disposition:** OPEN

- **Where:** uncalled_functions.py:122-124
- **Finding:** self-recursion / same-name locals count as calls
- **Failing input:** `def f(n): return f(n-1)`
- **Proposed fix:** exclude own body
- **Verified:** yes-repro

### TZ-19 (Low) -- defs under module if/try not judged; no file floor

**Disposition:** OPEN

- **Where:** uncalled_functions.py:98, 150
- **Finding:** defs under module if/try not judged; no file floor
- **Failing input:** try/except-defined f
- **Proposed fix:** recurse; `min_files`
- **Verified:** yes-read

### TZ-20 (Med) -- self.x: T = p and chained assigns treated as used

**Disposition:** OPEN

- **Where:** unread_init_params.py:70-74
- **Finding:** `self.x: T = p` and chained assigns treated as used
- **Failing input:** `self.p: int = p`
- **Proposed fix:** handle AnnAssign, multi-target
- **Verified:** yes-repro

### TZ-21 (Low) -- any string constant counts as read (__slots__)

**Disposition:** OPEN

- **Where:** unread_init_params.py:92
- **Finding:** any string constant counts as read (`__slots__`)
- **Failing input:** `__slots__ = ("alpha",)`
- **Proposed fix:** exclude slots/docstrings
- **Verified:** yes-read

### TZ-22 (Low) -- relative_to crash

**Disposition:** OPEN

- **Where:** unread_init_params.py:105
- **Finding:** `relative_to` crash
- **Failing input:** site-packages path
- **Proposed fix:** fallback
- **Verified:** yes-read

### TZ-23 (Med) -- test.describe.skip, test.fixme, xit, xdescribe missed

**Disposition:** OPEN

- **Where:** test_partition_reachability.py:56
- **Finding:** `test.describe.skip`, `test.fixme`, `xit`, `xdescribe` missed
- **Failing input:** `test.describe.skip(...)`
- **Proposed fix:** widen regex
- **Verified:** yes-repro

### TZ-24 (Med) -- --project="Mobile Chrome" → "Mobile"

**Disposition:** OPEN

- **Where:** test_partition_reachability.py:50
- **Finding:** `--project="Mobile Chrome"` → "Mobile"
- **Failing input:** quoted project
- **Proposed fix:** quoted capture
- **Verified:** yes-repro

### TZ-25 (Med) -- any playwright test line without --project disables check

**Disposition:** OPEN

- **Where:** test_partition_reachability.py:52
- **Finding:** any `playwright test` line without `--project` disables check
- **Failing input:** `\` continuation
- **Proposed fix:** join continuations, strip comments
- **Verified:** yes-read

### TZ-26 (Med) -- absolute parts: any ancestor tests skips all

**Disposition:** OPEN

- **Where:** test_partition_reachability.py:130
- **Finding:** absolute `parts`: any ancestor `tests` skips all
- **Failing input:** `REPO/"tests"/"e2e"` → `[]`
- **Proposed fix:** relative parts
- **Verified:** yes-repro

### TZ-27 (Med) -- missing runner/tag/config/spec paths → clean

**Disposition:** OPEN

- **Where:** test_partition_reachability.py:60-71, 77, 105
- **Finding:** missing runner/tag/config/spec paths → clean
- **Failing input:** typo'd tag file
- **Proposed fix:** fail on missing paths
- **Verified:** yes-read

### TZ-28 (Low) -- quoted/short tag flags, 4-space tags unparsed (fail-open)

**Disposition:** OPEN

- **Where:** test_partition_reachability.py:48-49, 43
- **Finding:** quoted/short tag flags, 4-space tags unparsed (fail-open)
- **Failing input:** `--exclude-tags "benchmark"`
- **Proposed fix:** widen
- **Verified:** yes-read

### TZ-29 (Low) -- substring script match

**Disposition:** OPEN

- **Where:** test_partition_reachability.py:135
- **Finding:** substring script match
- **Failing input:** `prerun.py` covers `run.py`
- **Proposed fix:** word boundary
- **Verified:** yes-read

### TZ-30 (Low) -- empty/stale allowlist reasons accepted

**Disposition:** OPEN

- **Where:** test_partition_reachability.py:169-189
- **Finding:** empty/stale allowlist reasons accepted
- **Failing input:** `{"bench": ""}`
- **Proposed fix:** reject
- **Verified:** yes-read

### TZ-31 (Med) -- guard counts headers in fences; rejects **Findings**

**Disposition:** OPEN

- **Where:** tracker_summary_parity.py:162
- **Finding:** guard counts headers in fences; rejects `**Findings**`
- **Failing input:** fenced example header
- **Proposed fix:** count after stripping fences
- **Verified:** yes-read

### TZ-32 (Low) -- non-backticked rows skipped; empty cells crash

**Disposition:** OPEN

- **Where:** tracker_summary_parity.py:100-102, 92
- **Finding:** non-backticked rows skipped; empty cells crash
- **Failing input:** `\| round1.md \| 5 \|`
- **Proposed fix:** flag; guard index
- **Verified:** yes-read

### TZ-33 (Low) -- status column assumed first; other headers counted as findings

**Disposition:** OPEN

- **Where:** tracker_summary_parity.py:50
- **Finding:** status column assumed first; other headers counted as findings
- **Failing input:** `\| State \| ID \|`
- **Proposed fix:** positional header skip
- **Verified:** yes-read

### TZ-34 (Med) -- ./scripts vs scripts string compare; ruff.toml not read (fail-open)

**Disposition:** OPEN

- **Where:** timezone_honest.py:136
- **Finding:** `./scripts` vs `scripts` string compare; ruff.toml not read (fail-open)
- **Failing input:** `scan_paths=("./scripts",)`
- **Proposed fix:** normalise; read ruff.toml
- **Verified:** yes-read

### TZ-35 (Low) -- only first component vs test dirs

**Disposition:** OPEN

- **Where:** timezone_honest.py:116
- **Finding:** only first component vs test dirs
- **Failing input:** `src/pkg/tests/test_a.py`
- **Proposed fix:** any component
- **Verified:** yes-read

### TZ-36 (Low) -- locale decoding; syntax-error files silently skipped (hypothesis)

**Disposition:** OPEN

- **Where:** timezone_honest.py:93-98
- **Finding:** locale decoding; syntax-error files silently skipped (hypothesis)
- **Failing input:** Cyrillic filename
- **Proposed fix:** utf-8; fail on non-DTZ lines
- **Verified:** no

### TZ-37 (Low) -- missing/unmatched source dropped silently

**Disposition:** OPEN

- **Where:** version_consistency.py:44
- **Finding:** missing/unmatched source dropped silently
- **Failing input:** typo file + pyproject
- **Proposed fix:** fail per requested source
- **Verified:** yes-read

### TZ-38 (Med) -- wasted subprocess; any nonzero = "not ancestor"; missing git crash

**Disposition:** OPEN

- **Where:** version_tag_currency.py:90-95
- **Finding:** wasted subprocess; any nonzero = "not ancestor"; missing git crash
- **Failing input:** shallow clone
- **Proposed fix:** distinguish rc 1; "cannot determine"
- **Verified:** yes-read

### TZ-39 (Med) -- unanchored DOTALL regex: core: inside app_core:, path deps steal refs, quoted refs skipped

**Disposition:** OPEN

- **Where:** version_tag_currency.py:113
- **Finding:** unanchored DOTALL regex: `core:` inside `app_core:`, path deps steal refs, quoted refs skipped
- **Failing input:** pubspec repro
- **Proposed fix:** parse YAML
- **Verified:** yes-repro

### TZ-40 (Low) -- first version = anywhere; suffixes truncated

**Disposition:** OPEN

- **Where:** version_tag_currency.py:36-37
- **Finding:** first `version =` anywhere; suffixes truncated
- **Failing input:** `[tool.foo] version` first
- **Proposed fix:** tomllib
- **Verified:** yes-read

### TZ-41 (Med) -- trailing --src-path IndexError blocks commit; = form unparsed

**Disposition:** OPEN

- **Where:** vulture_warn.py:32, 43
- **Finding:** trailing `--src-path` IndexError blocks commit; `=` form unparsed
- **Failing input:** `--src-path` last
- **Proposed fix:** argparse
- **Verified:** yes-read

### TZ-42 (Med) -- env path \ not normalised; substring match

**Disposition:** OPEN

- **Where:** vulture_warn.py:48
- **Finding:** env path `\` not normalised; substring match
- **Failing input:** `src\mlframe`
- **Proposed fix:** normalise, path prefix
- **Verified:** yes-read

### TZ-43 (Low) -- any nonzero = findings (missing vulture)

**Disposition:** OPEN

- **Where:** vulture_warn.py:56
- **Finding:** any nonzero = findings (missing vulture)
- **Failing input:** vulture absent
- **Proposed fix:** rc 3 only
- **Verified:** yes-read

### TZ-44 (Low) -- ~4 processes per file

**Disposition:** OPEN

- **Where:** worktree_hygiene.py:140, 162
- **Finding:** ~4 processes per file
- **Failing input:** 10k-file orphan
- **Proposed fix:** `--stdin-paths`, `--batch-check`
- **Verified:** yes-read

### TZ-45 (Low) -- rename's old path token cut by [3:]

**Disposition:** OPEN

- **Where:** worktree_hygiene.py:133
- **Finding:** rename's old path token cut by `[3:]`
- **Failing input:** `git mv a.py bb.py`
- **Proposed fix:** skip token after R/C
- **Verified:** yes-read

### TZ-46 (Low) -- no tests for vulture_warn/tool_versions; no BOM/non-UTF8/missing-baseline cases

**Disposition:** OPEN

- **Where:** tests
- **Finding:** no tests for vulture_warn/tool_versions; no BOM/non-UTF8/missing-baseline cases
- **Failing input:** —
- **Proposed fix:** regression test per finding
- **Verified:** yes-read

### MT-1 (High) -- NEEDS-JUSTIFICATION: never rejected on the next run

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1993-2035; baseline_ratchet.py:98-127
- **Finding:** `NEEDS-JUSTIFICATION:` never rejected on the next run
- **Failing input:** refresh then run → passes
- **Proposed fix:** fail on `_UNJUSTIFIED` notes (ideally in `Baseline.enforce`)
- **Verified:** yes-repro

### MT-2 (High) -- zero mutants (unparsable file, empty scope) passes

**Disposition:** OPEN

- **Where:** mutation_teeth.py:2006-2035
- **Finding:** zero mutants (unparsable file, empty scope) passes
- **Failing input:** `def g(:` → 0 mutants, green
- **Proposed fix:** raise unless `allow_empty`
- **Verified:** yes-repro

### MT-3 (High) -- BOM: AST operators return []

**Disposition:** OPEN

- **Where:** mutation_teeth.py:848 (1513, 2067)
- **Finding:** BOM: AST operators return `[]`
- **Failing input:** 4 mutants vs 1 with BOM
- **Proposed fix:** utf-8-sig, re-add BOM on write
- **Verified:** yes-repro

### MT-4 (Med) -- generator lines consumed by fingerprint; whole file swept, cached under narrow key

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1670 → 1760, 863
- **Finding:** generator `lines` consumed by fingerprint; whole file swept, cached under narrow key
- **Failing input:** generator range → 3 mutants not 1
- **Proposed fix:** `list(lines)` once
- **Verified:** yes-repro

### MT-5 (Med) -- lines=[] = whole file, same cache key as full run

**Disposition:** OPEN

- **Where:** mutation_teeth.py:860-863, 879; 1670
- **Finding:** `lines=[]` = whole file, same cache key as full run
- **Failing input:** `lines=[]`
- **Proposed fix:** None vs empty distinct
- **Verified:** yes-repro

### MT-6 (Med) -- twin verdicts not fanned out; accepted twins read stale

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1767-1771, 1815-1830
- **Finding:** twin verdicts not fanned out; accepted twins read stale
- **Failing input:** byte-identical mutants
- **Proposed fix:** copy verdict to all twins
- **Verified:** yes-read

### MT-7 (High) -- worker reply desync after stray fd-1 output

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1356-1361; _mutation_worker.py:136
- **Finding:** worker reply desync after stray fd-1 output
- **Failing input:** `addopts=-s` + `os.write(1, ...)`
- **Proposed fix:** request ids; dup2 protocol fd
- **Verified:** yes-repro

### MT-8 (High) -- sweep_files(jobs>1) extra sandboxes not removed → FileExistsError on 2nd file

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1781-1784
- **Finding:** `sweep_files(jobs>1)` extra sandboxes not removed → FileExistsError on 2nd file
- **Failing input:** 2 targets, jobs=2
- **Proposed fix:** per-call mkdtemp, cleanup
- **Verified:** yes-repro

### MT-9 (Low) -- extra sandboxes copied from live repo, no cold baseline

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1727, 1783
- **Finding:** extra sandboxes copied from live repo, no cold baseline
- **Failing input:** repo edited mid-sweep
- **Proposed fix:** copy from verified sandbox
- **Verified:** yes-read

### MT-10 (Med) -- warm-path crash kills undercounted

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1579-1584
- **Finding:** warm-path crash kills undercounted
- **Failing input:** TypeError kill
- **Proposed fix:** worker returns exception type
- **Verified:** yes-read

### MT-11 (Low) -- dead AssertionError entry

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1170 vs 1195
- **Finding:** dead `AssertionError` entry
- **Failing input:** n/a
- **Proposed fix:** remove
- **Verified:** yes-read

### MT-12 (Med) -- worker not restarted after timeout; timeout paid twice

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1350-1355, 1340
- **Finding:** worker not restarted after timeout; timeout paid twice
- **Failing input:** early infinite loop
- **Proposed fix:** restart; warm timeout = inconclusive
- **Verified:** yes-read

### MT-13 (Med) -- wider-net timeout recorded as survivor

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1607-1609
- **Finding:** wider-net timeout recorded as survivor
- **Failing input:** hang
- **Proposed fix:** inconclusive
- **Verified:** yes-read

### MT-14 (Med) -- cache drops wider_net_note

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1871-1896 vs 1682-1705
- **Finding:** cache drops `wider_net_note`
- **Failing input:** fallback + replay
- **Proposed fix:** store/replay note
- **Verified:** yes-read

### MT-15 (Med) -- unlocked RMW cache with fixed .tmp name

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1867-1902
- **Finding:** unlocked RMW cache with fixed `.tmp` name
- **Failing input:** 2 xdist workers
- **Proposed fix:** mkstemp + file lock
- **Verified:** yes-read

### MT-16 (Med) -- node-id test paths hashed as placeholder → stale replay

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1011-1020, 1046-1051
- **Finding:** node-id test paths hashed as placeholder → stale replay
- **Failing input:** `tests/x.py::test_f`
- **Proposed fix:** split `::`
- **Verified:** yes-repro

### MT-17 (Low) -- only test_*.py in fingerprint

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1017
- **Finding:** only `test_*.py` in fingerprint
- **Failing input:** `foo_test.py`
- **Proposed fix:** honour python_files
- **Verified:** yes-read

### MT-18 (Low) -- string >= instead of ancestry

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1022
- **Finding:** string `>=` instead of ancestry
- **Failing input:** `../other/test_x.py`
- **Proposed fix:** `is_relative_to`
- **Verified:** yes-read

### MT-19 (Med) -- "dropped a not" eats a char: unparsable mutant counted as kill

**Disposition:** OPEN

- **Where:** mutation_teeth.py:749, 335
- **Finding:** "dropped a not" eats a char: unparsable mutant counted as kill
- **Failing input:** `return not(x)` → `return x)`
- **Proposed fix:** token-precise removal; syntax-risky
- **Verified:** yes-repro

### MT-20 (Med) -- emptying string: bytes type change / no-op on empty literals

**Disposition:** OPEN

- **Where:** mutation_teeth.py:765-774
- **Finding:** emptying string: bytes type change / no-op on empty literals
- **Failing input:** `b'ab'`, `r''`
- **Proposed fix:** keep prefix; skip empty
- **Verified:** yes-repro

### MT-21 (Low) -- min description says "max becomes min" (re-keys baseline)

**Disposition:** OPEN

- **Where:** mutation_teeth.py:303
- **Finding:** `min` description says "max becomes min" (re-keys baseline)
- **Failing input:** `min(a,b)`
- **Proposed fix:** fix + bump HARNESS_VERSION
- **Verified:** yes-repro

### MT-22 (Low) -- sampled_containers wrong counts; AST operators skip sampling

**Disposition:** OPEN

- **Where:** mutation_teeth.py:921, 450-453
- **Finding:** `sampled_containers` wrong counts; AST operators skip sampling
- **Failing input:** 6-entry dict "3 of 12"
- **Proposed fix:** count rows; apply to all sources
- **Verified:** yes-repro

### MT-23 (Low) -- non-UTF8 → UnicodeDecodeError not MutationHarnessError

**Disposition:** OPEN

- **Where:** mutation_teeth.py:848, 1513
- **Finding:** non-UTF8 → UnicodeDecodeError not MutationHarnessError
- **Failing input:** latin-1 file
- **Proposed fix:** wrap
- **Verified:** yes-repro

### MT-24 (Low) -- unclosed open() handles (ResourceWarning; Windows locks)

**Disposition:** OPEN

- **Where:** mutation_teeth.py:848, 932, 1513, ... 2086
- **Finding:** unclosed `open()` handles (ResourceWarning; Windows locks)
- **Failing input:** r2.py
- **Proposed fix:** Path read/write or `with`
- **Verified:** yes-repro

### MT-25 (Med) -- no check the mutated module is imported from sandbox (editable install → all survive)

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1134-1136; _mutation_worker.py:95
- **Finding:** no check the mutated module is imported from sandbox (editable install → all survive)
- **Failing input:** src layout w/o pythonpath
- **Proposed fix:** worker reports `__file__`; raise
- **Verified:** no

### MT-26 (Med) -- timeout kills only direct child; Windows grandchildren leak; rmtree errors ignored

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1274-1283, 1847, 2099
- **Finding:** timeout kills only direct child; Windows grandchildren leak; rmtree errors ignored
- **Failing input:** test spawns server
- **Proposed fix:** process-group tree kill; log
- **Verified:** yes-read

### MT-27 (Low) -- last_timings/last_failed not reset on early return

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1340-1361, 1562-1569
- **Finding:** `last_timings`/`last_failed` not reset on early return
- **Failing input:** worker dies
- **Proposed fix:** reset at top
- **Verified:** yes-read

### MT-28 (Low) -- 6-7 ast.parse per target; O(tokens×ranges)

**Disposition:** OPEN

- **Where:** mutation_teeth.py:381, 433, 506, 552, 604, 810, 1215, 697, 716
- **Finding:** 6-7 `ast.parse` per target; O(tokens×ranges)
- **Failing input:** large modules
- **Proposed fix:** parse once; bisect
- **Verified:** yes-read

### MT-29 (Low) -- non-range lines entries dropped from key but crash later

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1670 vs 881
- **Finding:** non-range `lines` entries dropped from key but crash later
- **Failing input:** `lines=[(1,5)]`
- **Proposed fix:** validate up front
- **Verified:** yes-read

### MT-30 (Low) -- parent __init__.py not in import closure

**Disposition:** OPEN

- **Where:** mutation_teeth.py:941-975
- **Finding:** parent `__init__.py` not in import closure
- **Failing input:** edit `pkg/__init__.py`
- **Proposed fix:** add parents
- **Verified:** yes-read

### MT-31 (Med) -- refresh writes baseline before inconclusive/gap/truncation checks

**Disposition:** OPEN

- **Where:** mutation_teeth.py:1994-1999 vs 2006-2015
- **Finding:** refresh writes baseline before inconclusive/gap/truncation checks
- **Failing input:** refresh with `limit=5`
- **Proposed fix:** refuse partial refresh
- **Verified:** yes-read

### MT-32 (Low) -- truncated run passes with warning (documented)

**Disposition:** OPEN

- **Where:** mutation_teeth.py:2006-2011
- **Finding:** truncated run passes with warning (documented)
- **Failing input:** `limit=10`
- **Proposed fix:** optional strict
- **Verified:** yes-read

### W-1 (Med) -- cwd/env/sys.path/argv not restored between runs → false kills

**Disposition:** OPEN

- **Where:** _mutation_worker.py:111-160
- **Finding:** cwd/env/sys.path/argv not restored between runs → false kills
- **Failing input:** `os.chdir` in test
- **Proposed fix:** snapshot/restore; cold re-check sample
- **Verified:** yes-read

### W-2 (Low) -- namespace paths re-resolved per mutant

**Disposition:** OPEN

- **Where:** _mutation_worker.py:83-91
- **Finding:** namespace paths re-resolved per mutant
- **Failing input:** many namespace pkgs
- **Proposed fix:** cache
- **Verified:** yes-read

### TS-1 (High) -- pytest exit code/stderr ignored: usage/conftest errors → every case "NO TEETH"

**Disposition:** OPEN

- **Where:** teeth_sweep.py:291-304, 254-281
- **Finding:** pytest exit code/stderr ignored: usage/conftest errors → every case "NO TEETH"
- **Failing input:** repo without xdist
- **Proposed fix:** use returncode; add flags only if plugin installed
- **Verified:** yes-read

### TS-2 (Med) -- timeout leaves xdist workers alive on Windows

**Disposition:** OPEN

- **Where:** teeth_sweep.py:291-303
- **Finding:** timeout leaves xdist workers alive on Windows
- **Failing input:** hanging mutation
- **Proposed fix:** tree kill
- **Verified:** yes-read

### TS-3 (Low) -- one bad case aborts whole sweep

**Disposition:** OPEN

- **Where:** teeth_sweep.py:242, 344-347
- **Finding:** one bad case aborts whole sweep
- **Failing input:** latin-1 target
- **Proposed fix:** per-case catch
- **Verified:** yes-read

### TS-4 (Low) -- read-back check vacuous for empty/duplicated repl

**Disposition:** OPEN

- **Where:** teeth_sweep.py:249
- **Finding:** read-back check vacuous for empty/duplicated repl
- **Failing input:** `new=""`
- **Proposed fix:** compare exact bytes
- **Verified:** yes-read

### TS-5 (Low) -- CRLF old becomes \r\r\n

**Disposition:** OPEN

- **Where:** teeth_sweep.py:244
- **Finding:** CRLF `old` becomes `\r\r\n`
- **Failing input:** CRLF case JSON
- **Proposed fix:** normalise first
- **Verified:** yes-read
