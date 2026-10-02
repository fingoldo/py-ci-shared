# Audit: core infrastructure (_core, CLI, pytest plugin, config, registry, version/git helpers)

Read-only audit of `e53587c` (master). Scope: `src/py_ci_shared/_core/**`, `cli.py`, `pytest_plugin.py`,
`registry.py`/`registry.toml`, `_consumers.py`, `version_consistency.py`, `version_tag_currency.py`,
`audit_round_format.py`, `worktree_hygiene.py`, `_scaffold.py`, git/subprocess helpers. Earlier rounds
(`audits/implemented/2026-09-24`, `audits/2026-09-04`) were read and are not re-reported.

Every finding was reproduced on Windows 11 (Python 3.14.3 global env; 3.9.25, 3.11.15, 3.13.14 through
`uv run --no-project --with tomli --with pyyaml --with pytest`). Reproducer scripts live in the session scratchpad
(`k20/e1.py` .. `e4.py`), never in the worktree. Commands: `PYTHONPATH=<worktree>/src python <script>`.

No module is over the 1000-line limit (largest: `mutation_teeth.py` 970, `adoption_matrix.py` 890).
Core tests on 3.9 (`test_core_*`, `test_cli.py`, `test_pytest_plugin.py`): 146 passed, 1 failed, 1 skipped; the one
failure is K-5.

Severity counts: High 0, Med 5, Low 12.

### K-1 (Med) -- `py-ci-shared run` crashes on a cp1251 console and loses the finding text

**Evidence.** `cli.py:44-46` writes the gate message straight to `sys.stdout`. A repo whose `src/数据.py` calls
`datetime.datetime.utcnow()`, run with `PYTHONUTF8=0` (the default cp1251 locale here) and stdout redirected:

```
PYTHONUTF8=0 python -m py_ci_shared.cli run naive_utcnow --repo r8 > o8.txt 2>&1   -> exit=1
  File ".../cli.py", line 63, in _report
  File ".../cli.py", line 46, in _print
UnicodeEncodeError: 'charmap' codec can't encode characters in position 455-456
FAIL  naive_utcnow (1.33s)
```

With `PYTHONUTF8=1`, the same command prints `src/数据.py:2: datetime.datetime.utcnow()`.

**Impact.** On any Windows machine without UTF-8 mode, a finding whose path or message contains a character the
console codepage lacks kills the run with a traceback. The finding line, the remaining gates and the summary line
are all lost. The exit code is 1 only because the traceback happens to give 1. An ERROR gate (contract: exit 2)
also leaves with 1.

**Proposed fix.** In `main`, reconfigure both streams once: `stream.reconfigure(errors="backslashreplace")`, or
encode with `errors="backslashreplace"` inside `_print`. Add a test that runs the CLI in a subprocess with
`PYTHONIOENCODING=cp1251` and a non-cp1251 path.

### K-2 (Med) -- The plugin reads `[tool.py_ci_shared]` from pytest's rootdir, the CLI from the nearest pyproject; gates vanish silently

**Evidence.** `pytest_plugin.py:95` calls `load_config(config.rootpath)`. `config.py:86-92` (CLI) uses the nearest
`pyproject.toml`. In pytest, a `pyproject.toml` with no `[tool.pytest.ini_options]` is not a config file, so
rootdir moves up to whichever ancestor has one. Repro: `rp/pyproject.toml` holds only `[tool.pytest.ini_options]`,
and `rp/repo/pyproject.toml` holds `enable = ["naive_utcnow"]` plus a violating `src/m.py`. Running in `rp/repo`:

```
pytest -p py_ci_shared.pytest_plugin --py-ci-gates=on
rootdir: ...\k20\rp
1 passed            <- the naive_utcnow item is absent, even with --py-ci-gates=on
```

`py-ci-shared run-all --repo rp/repo` on the same tree finds the violation.

**Impact.** In a consumer that keeps its pytest options in `setup.cfg`/`pytest.ini` under a subdirectory, or that
sits inside a monorepo or workspace whose parent has pytest config, every enabled gate drops out of the pytest run.
No warning is printed, and an explicit `--py-ci-gates=on` does not help.

**Proposed fix.** Look the table up the way the CLI does: walk from `config.invocation_params.dir` (and from
`rootpath`) to the nearest `pyproject.toml` that has `[tool.py_ci_shared]`. When `--py-ci-gates=on` and no table is
found, raise `UsageError` instead of passing quietly.

### K-3 (Med) -- Glob kwargs return nothing when the repo path contains `[`, `]`, `*` or `?`

**Evidence.** `config.py:197-200` globs `str(repo_root / item)` without escaping the root:

```
resolve_kwargs(gate, {"files": ["src/*.py"]}, Path(".../proj[1]"))  -> {'files': []}
resolve_kwargs(gate, {"files": ["src/*.py"]}, Path(".../proj1"))    -> {'files': [.../proj1/src/a.py]}
```

**Impact.** A checkout in a directory like `work[old]` or `C:\proj[1]` gives every `files = ["src/**/*.py"]` gate an
empty list. At best the floor error says "only 0 file(s) parsed" and blames the config. A gate without a floor
passes with nothing checked.

**Proposed fix.** Use `_glob.glob(item, root_dir=repo_root, recursive=True)` (3.10+), or on 3.9
`glob.escape(str(repo_root)) + "/" + item`. Separately, fail (ConfigError) when a glob a user wrote matches nothing.

### K-4 (Med) -- `iter_files` silently changes corpus definition when `git ls-files` fails or times out

**Evidence.** `corpus.py:58-62` turns any `SubprocessError`, including the 120 s `TimeoutExpired`, into `None`.
`corpus.py:123-128` then falls back to the filesystem walk, which ignores `.gitignore` and enters submodules.
Repro `e2.py` (main repo with a `.gitignore`d `generated/` and a submodule `vendor/sub`):

```
git mode : ['a.py']
walk mode: ['a.py', 'generated/junk.py', 'vendor/sub/inner.py']
ls-files timed out (use_git=None): ['a.py', 'generated/junk.py', 'vendor/sub/inner.py']
```

**Impact.** On a very large repo or a slow filesystem (Windows Defender scanning a checkout), a timed-out listing
does not fail. It quietly widens the corpus to ignored build output and vendored submodules, so new findings appear
and baselines disagree with CI. The docstring (`corpus.py:10-13`) promises "what git would commit". Submodule
content is also treated inconsistently between the two modes: invisible under git, scanned by the walk.

**Proposed fix.** Return `None` only for "not a work tree" or "root ignored". When `rev-parse` says it IS a work tree
but `ls-files` fails or times out, raise `CorpusError`. Make the timeout configurable, and document submodules as
excluded in both modes (have the walk prune directories that contain a `.git` file).

### K-5 (Med) -- `resource_leak_guard` misses env leaks: the first test of every session without psutil, and on 3.9 any test that imports a stdlib module

**Evidence.** `resource_leak_guard.py:165-178` skips every env var added while a "library" module was first imported.
Without psutil, `_processes()` (`:75-80`) imports `multiprocessing`, which registers `__mp_main__`. That happens
inside the test window, and `_is_library("__mp_main__")` is True. On 3.9, `sys.stdlib_module_names` does not exist
(`:178`), so every stdlib module counts as a library. Repros (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, table
`resource_leak_guard = true`):

```
3.13, no psutil:   test_leaks_env alone                       -> 1 passed          (leak missed)
3.13, with psutil: same                                       -> 1 passed, 1 error (caught)
3.13, no psutil:   test_a + test_leaks_env                    -> 2 passed, 1 error (only the 2nd test is guarded)
3.9 + psutil:      test that does `import colorsys` then leaks -> 2 passed          (missed)
3.13 + psutil:     same                                       -> 2 passed, 1 error
```

psutil is neither a dependency nor a dev extra (`pyproject.toml:32,35-66`). In a clean `pip install .[dev]` env,
`tests/test_pytest_plugin.py::test_the_table_loads_the_resource_leak_guard_only_when_asked` fails. It does so on
3.9/3.11/3.13 under uv, and passes only because the global 3.14 env here has psutil 7.2.2.

**Impact.** A leak guard that is blind for whole classes of tests, which differ by Python version and installed
packages. The repo's own test of this depends on an undeclared package.

**Proposed fix.** Import `multiprocessing` at module import, not inside the test window. Exclude `__mp_main__`/`__main__`.
On <3.10, use a fallback stdlib list (`sysconfig.get_paths()["stdlib"]` prefix check on `module.__file__`). Either
declare psutil in `[dev]` or importorskip it in the test, and add a no-psutil variant.

### K-6 (Low) -- `version_tag_currency` accepts tag `10.2.3` as the tag for declared version `0.2.3`

**Evidence.** `version_tag_currency.py:124`: `matching = [t for t in tags if t[1:] == version or t == version]`
strips the first character whether or not it is a `v`. Repro `e3.py` (pyproject `version = "0.2.3"`, only tag
`10.2.3`):

```
tags: ['10.2.3']
problems for declared 0.2.3 with only tag 10.2.3: []
```

**Impact.** `assert_version_is_tagged` passes for a version that was never tagged. A release workflow keyed on it
would ship untagged.

**Proposed fix.** `t == version or (t.startswith("v") and t[1:] == version)`, plus a regression test with
`10.2.3` vs `0.2.3`.

### K-7 (Low) -- `[tool.py_ci_shared]` shapes are under-validated: a string `enable`, duplicate names and a non-numeric `budget_s`

**Evidence.** `config.py:131` iterates `table.get("enable", [])` without a type check, `config.py:137` checks only
enable-vs-table duplicates, and `config.py:146` calls `float(budget_s)` unguarded:

```
enable = "naive_utcnow"                    -> gates ['n','a','i','v','e','_','u','t','c','n','o','w']
enable = ["naive_utcnow","naive_utcnow"]   -> gates ['naive_utcnow','naive_utcnow']  (runs twice)
budget_s = "fast"                          -> UNCAUGHT ValueError: could not convert string to float: 'fast'
```

`cli.py:187` catches only `ConfigError`/`KeyError`, so the third case is a traceback rather than exit 2. In the plugin
it escapes `pytest_configure` as an internal error. `entry = 5` and `module = ["x"]` are not type-checked either.

**Proposed fix.** Validate `enable` as a list of unique str, `entry` as str, and `budget_s` as a positive number,
all raising `ConfigError` that names the key. Add tests for each.

### K-8 (Low) -- `scan_python` scans one file twice when it is reached through a relative and an absolute spelling

**Evidence.** `scan.py:128-142` dedupes on the `Path` as given, without resolving it:

```
scan_python(["e5", Path(".../e5/m.py")], use_git=False)
-> rels ['C:/.../e5/m.py', 'm.py'], parsed_count 2
```

**Impact.** Every finding in that file is reported twice under two different keys. Baselines are multisets, so the
double counts land in them. The floor is also met with half the files.

**Proposed fix.** Dedupe on `path.resolve()` (keep the first spelling for `rel`).

### K-9 (Low) -- `node_index` pins every superseded tree; the "bounded by corpus size" promise does not hold

**Evidence.** `source.py:33-35` says the parse cache keeps one entry per path. `node_index.py:58-72,99-110` keys on
`id(tree)` and holds a strong reference, so a replaced tree stays alive in `_INDEX`/`_MEMO` indefinitely. Repro
(`e1.py` E6, rewriting one file five times with a `nodes_of` call each time):

```
parse cache entries 2  node_index entries 5
```

Trees from a plain `ast.parse` (callers outside the cache) are pinned too.

**Impact.** Memory grows without bound in long-lived processes such as watch-mode pytest, `pytest-xdist` workers
across many test files that write and rescan fixtures, and IDE integrations. `test_core_*` alone writes hundreds of
small trees per worker.

**Proposed fix.** Key the index on a `weakref.WeakKeyDictionary` (`ast.AST` supports weakrefs), or drop the
`_INDEX`/`_MEMO` entries for a path's old tree when `parse_source` replaces its cache slot.

### K-10 (Low) -- A malformed baseline count raises `ValueError` instead of `BaselineError`, and negative counts are accepted

**Evidence.** `baseline.py:107-109` returns `int(value.get("count", 1))` unchecked:

```
{"entries": {"k": {"count": "two"}}}  -> ValueError invalid literal for int() with base 10: 'two'
{"entries": {"k": {"count": -3}}}     -> enforce(["k"]): ok=False, new=['k','k','k','k']
```

**Impact.** A hand-edited baseline gives a raw traceback with no file name. A negative count produces nonsensical
"4 new findings" for one occurrence.

**Proposed fix.** Require `int` (not bool) and `>= 1`, otherwise raise `BaselineError(f"{path}: entry {key!r} ...")`.

### K-11 (Low) -- Configuration ERROR results also print a spurious "over its 0s budget" line

**Evidence.** `runner.py:89` starts `budget = 0.0`. A gate failing in `resolve_gate` keeps it, and
`budget_verdict` (`runner.py:117-124`) reports any elapsed time over 0:

```
py-ci-shared run-all --repo r10   (enable = "ab")
BUDGET a took 0.0s, over its 0s budget. Narrow its inputs, raise budget_s in [tool.py_ci_shared.gates.a], ...
ERROR a (0.00s)
```

In the plugin the same text is appended to the configuration-error message (`pytest_plugin.py:142-147`).

**Proposed fix.** `budget_verdict` returns None for `ERROR` results, or the budget defaults to `inf` until it is
resolved.

### K-12 (Low) -- `run`/`refresh` reject dashed gate names that `tool` and the plugin accept

**Evidence.** `registry.by_name` (`registry.py:313-316`) and `_gate_refresh` (`pytest_plugin.py:164`) map `-` to `_`.
`RepoConfig.gate` (`config.py:79-83`) matches exactly:

```
py-ci-shared run naive-utcnow --repo r9  -> "gate 'naive-utcnow' is not enabled", exit 2
```

**Proposed fix.** Normalise dashes in `RepoConfig.gate`, and list the enabled names in the error.

### K-13 (Low) -- `subprocess.run(text=True)` without `encoding` loses or garbles git output on a non-UTF-8 Windows locale

**Evidence.** These calls set no `encoding`: `_consumers.py:95`, `adoption_matrix.py:139`, `corpus_drift.py:210`,
`git_dependency_pins.py:357`, `checkout_resolution.py:100`, `import_side_effects.py:311`, `embedded_postgres.py:122`
and `install_safe_hook.py:41,48`. Repro with `_consumers._git_clone` into an existing `dИr/x` (UTF-8 `И` = `D0 98`,
and 0x98 is undefined in cp1251), `PYTHONUTF8=0`:

```
UnicodeDecodeError: 'charmap' codec can't decode byte 0x98 in position 27   (reader thread)
'git clone exited 128'      <- the real git error text is lost
```

Other non-ASCII output decodes as mojibake rather than failing. `version_tag_currency.py:48`, `stale_comment_age.py:249`
and `corpus.py` already do this correctly (bytes, or `encoding="utf-8", errors="replace"`).

**Proposed fix.** Use one `_core` git runner (see K-14) that always decodes UTF-8 with `errors="replace"`. Add a
meta-test banning `text=True` without `encoding=` in `src/`.

### K-14 (Low) -- Eight private git runners with divergent policies belong in `_core`

**Evidence.** Separate `git` subprocess wrappers: `_core/corpus.py:58` (bytes, timeout 120, GIT_* inherited),
`adoption_matrix.py:135` (text, locale decoding, no timeout), `baseline_trend.py:82,96`, `git_dependency_pins.py:346`
(drops GIT_* env, timeout 60, locale decoding), `stale_comment_age.py:248` (utf-8/replace, no timeout, GIT_*
inherited), `version_tag_currency.py:46,53` (utf-8/replace, no timeout), `worktree_hygiene.py:76,84,94` (bytes, no
timeout), and `git_changed_lines.py:109,190` (bytes, its own `_git_env`).

**Impact.** The GIT_DIR-inside-a-hook defect documented at `git_dependency_pins.py:347-352` is fixed in one runner
only. The other seven still answer for the committing repo when they run inside a pre-commit hook. Timeouts and
encodings also differ per gate (see K-4, K-13).

**Proposed fix.** Add `_core/git.py` exposing `run_git(repo, *args, timeout=..., check=...)`: bytes in, UTF-8 or
surrogateescape out, `GIT_*` scrubbed, `-C repo`, typed error. Migrate the eight callers and add a meta-test against
new `subprocess.run(["git"` calls outside it.

### K-15 (Low) -- `allow_unparsed` and floor naming are inconsistent across gates, against the repo's own rule

**Evidence.** CLAUDE.md ("Adding a gate") requires "a `min_files` floor and an `allow_unparsed` switch". An
`inspect.signature` sweep over every `kind = "gate"` registry entry (`e4.py`) gives:

```
entries: 135, allow_unparsed: 4, refresh: 32, request: 40, grow: 5
floors: min_files 66, then 22 other spellings (min_functions, min_subjects, min_rows, ... min_sources)
entries without any min_* floor: 47
```

56 modules call `scan_python`, but only 16 mention `allow_unparsed` at all.

**Impact.** In `[tool.py_ci_shared.gates.X]` a consumer cannot predict whether `allow_unparsed = true` or
`min_files = 0` will be accepted. `resolve_kwargs` raises `ConfigError` on the unknown key, so the same table works
for one gate and errors for its neighbour.

**Proposed fix.** Add a meta-test: every entry whose module calls `scan_python` accepts `min_files` and
`allow_unparsed`. Retrofit the remaining modules through `_gate_report`/`_gate_run`, and document the canonical
kwargs in README "Configuring gates".

### K-16 (Low) -- `find_stale_pin` counts prereleases and unrelated tags as releases behind

**Evidence.** `version_tag_currency.py:183-186` uses `semver_tags`, which includes `-rc` tags and tags without `v`.
`e3.py` (pin `v1.0.0`, then tags `v1.1.0-rc.1`, `v1.1.0-rc.2`, `10.2.3`):

```
pubspec.yaml pins core v1.0.0, which is 3 release(s) behind 10.2.3.
```

`adoption_matrix.py:147-148` counts only full `vX.Y.Z` releases, so the two modules disagree about the same pin.

**Proposed fix.** Count only non-prerelease tags of the pin's own prefix style. Share the release filter with
`adoption_matrix`.

### K-17 (Low) -- No written compatibility promise for the moving `@v1` tag, although v1.x has changed behaviour

**Evidence.** README.md:48-51 tells consumers to track the moving `v1` tag. README has no statement of what is public
or what may change within v1: a search for "stab|semver|breaking|public api|compat" finds only an unrelated ruff
note at :1085. CHANGELOG.md:57 (1.18.0) changed `refresh` from "write current findings" to shrink-only. A consumer
on `@v1` whose CI seeds baselines with `--refresh-*` got failures with no major bump. `_core.__all__`
(`_core/__init__.py:43-83`) is the de-facto API but is not documented as stable.

**Proposed fix.** Add a "Compatibility" section: the public surface is `_core.__all__`, `assert_*` signatures,
`[tool.py_ci_shared]` keys, CLI subcommands and the baseline format. Within v1, only additive changes plus fixes
that make a gate stricter. Mark behaviour changes in CHANGELOG with a **Behaviour change** label.
