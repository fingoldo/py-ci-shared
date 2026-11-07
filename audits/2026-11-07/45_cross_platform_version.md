# 45_cross_platform_version

Round 2026-11-07, dimension 45. Every finding is OPEN until its Disposition line says otherwise.

Interpreters run: CPython 3.9.25, 3.11.15, 3.13.14 (uv-managed, `py -0p`) and 3.14 (default), all on Windows 11. Not run: Linux, macOS (no access), 3.10 and 3.12 (not installed), the glossum locked venvs (not needed once 3.11 was available). Scratch scripts: `C:\Users\Admin\AppData\Local\Temp\claude\C--Users-Admin\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\audit45\` (u.py, d.py, rl.py, tk.py, tk2.py, crlf.py, imp.py).

## 45-F1 (P0) Finding messages built from ast.unparse are baseline keys, and ast.unparse differs between 3.9 and 3.11+

- Evidence: `Finding.key` defaults to `rule::path::message` (_core/findings.py:24-25); alembic_concurrently.py:129 and about 59 modules put `ast.unparse(node)` into the message. Same migration `op.execute(lambda: "CREATE INDEX CONCURRENTLY ...")` through `collect_unguarded_concurrently`: 3.9.25 key `alembic-concurrently::001_x.py::op.execute(lambda : 'CREATE INDEX ...')`; 3.11.15 and 3.14 key `...op.execute(lambda: 'CREATE INDEX ...')`. u.py unparse probes (29 shapes): 3.9 differs from 3.11/3.13/3.14 on `lambda : (yield)` vs `lambda: (yield)` and `for (a, b) in c:` vs `for a, b in c:`; 3.11, 3.13, 3.14 agree on all 29.
- Failure scenario: a baseline refreshed on the 3.11 leg holds `lambda: ...`; the 3.9/3.10 legs compute `lambda : ...`, so an accepted finding looks new (red shard) and the entry looks stale. The failure is not explainable by any code change.
- Proposed fix: a `_core.unparse_stable(node)` that normalises the 3.9 differences (or builds messages from the source segment); a meta test banning raw `ast.unparse` inside `Finding(...)` message or key; a lambda/for-target canary.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED in 10a487c (see CHANGELOG 'Interpreter-independent finding text 2026-11-07'; tests/test_unparse_stable.py)

## 45-F2 (P0) resource_leak_checks._imported_library: empty `stdlib_module_names` fallback on 3.9 suppresses leak reports

- Evidence: resource_leak_checks.py:92 `getattr(sys, "stdlib_module_names", frozenset())` (attribute is 3.10+). rl.py (snapshot, import only colorsys and zoneinfo, call `_imported_library`): 3.9.25 True; 3.11.15 False; 3.14 False. The same bug was fixed in `resource_leak_guard._is_stdlib` (K-5) and `_core.is_stdlib`; this copy was missed. The flag gates the stream, sys.path and warnings.filters exemptions at :160-169.
- Failure scenario: a test leaking warning filters or a wrapped stream after lazily importing a stdlib module is a finding on 3.10-3.14 and passes on 3.9.
- Proposed fix: use the shared stdlib predicate; meta test banning `getattr(sys, "stdlib_module_names", <empty>)` (also at undeclared_imports.py:107 and phantom_code_references.py:668, harmless there today).
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED in 10a487c (see CHANGELOG 'Interpreter-independent finding text 2026-11-07'; tests/test_unparse_stable.py)

## 45-F3 (P1) near_duplicate_function_body ratio over ast.dump tokens against fixed 0.99/0.98 thresholds depends on the interpreter

- Evidence: near_duplicate_function_body.py:27-28, 78-90; duplicate_function_body.py:44. tk.py token count of one 5-statement function: 3.9 652, 3.11 652, 3.13 597, 3.14 597. tk2.py, two 11-statement bodies differing in one statement: ratio 3.11 0.9904 (reported) vs 3.14 0.9895 (not reported); at 12 statements 0.9912 vs 0.9904 (both). A full-scanner run on a synthetic corpus returned 0 findings everywhere, so the flip is demonstrated at ratio level only.
- Failure scenario: pairs within about 0.001 of the threshold flip between legs; baselines accepted on one leg are stale on another.
- Proposed fix: reuse `drifted_duplicate_functions._canonical` for the dump; pin a token-list golden test.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED in 10a487c (see CHANGELOG 'Interpreter-independent finding text 2026-11-07'; tests/test_unparse_stable.py)

## 45-F4 (P1) No 3.14 leg; Windows 3.9 and 3.13 non-blocking; macOS and Windows 3.11 only

- Evidence: .github/workflows/self-ci.yml:37-61. The owner's default interpreter is 3.14, and e873e5d was found locally.
- Failure scenario: 3.14-only or Windows-3.9-only regressions ship green.
- Proposed fix: add 3.14 (preview first), promote Windows 3.9 to blocking after one green run.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED in 10a487c (see CHANGELOG 'Interpreter-independent finding text 2026-11-07'; tests/test_unparse_stable.py)

## 45-F5 (P2) No cross-version equivalence test

- Evidence: `ls tests | grep -i 'platform|windows|crlf|version'` lists only tool-version and pin tests. F1-F3 were found in an hour by running the same code on four interpreters.
- Failure scenario: the next dump/unparse change (3.15) is found by a consumer's red shard.
- Proposed fix: a test that runs a golden corpus through the unparse- and dump-keyed gates under each available interpreter (subprocess, `uv python find`) and asserts equal key sets, plus a golden unparse test for lambda, for-target and starred subscript.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED in aec2d6a (tests/test_cross_interpreter_keys.py runs one corpus through unparse- and fingerprint-keyed gates on each other installed Python and compares keys; the golden unparse list already existed in tests/test_unparse_stable.py; wiring a CI leg that installs a second interpreter stays with the matrix, the test uses PY_CI_SHARED_OTHER_PYTHONS or uv pythons and skips when none)

## 45-F6 (P3) os.replace without a Windows retry in four writers

- Evidence: _core/baseline.py:69, install_safe_hook.py:96, code_audit/_scan_cache.py:98, api_floor.py:212 replace once; _mutation_runner.py:437-450 retries 3 times. Not reproduced.
- Failure scenario: an indexer holding the target makes a refresh or hook install raise PermissionError once.
- Proposed fix: short Windows-only retry inside `atomic_write_text`; the others call it.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED in aec2d6a (_core.replace_file retries a Windows PermissionError 3 times; atomic_write_text, install_safe_hook, _scan_cache and api_floor use it; tests/test_replace_file_retry.py)

## 45-F7 (P3) Stale "supports 3.8" comments and dead unparse/dump fallbacks

- Evidence: pyproject.toml:13 `>=3.9`; meta_test_utils.py:733; code_audit/default_via_or.py:206-225 and assert_in_loop.py:100-104 keep `getattr(ast, 'unparse', ast.dump)` fallbacks that cannot fire; timezone_honest.py:142 uses removeprefix freely.
- Failure scenario: cosmetic; the fallbacks are an untested second key format.
- Proposed fix: delete them.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED in aec2d6a (the 3.8 comments and the hand-rolled removesuffix are gone; the getattr(ast, 'unparse') fallbacks were already removed in 10a487c; test in tests/test_unparse_stable.py)

## 45-F8 (P3) Checked and robust (do not re-audit)

- Evidence: crlf.py: skip_as_pass and wallclock_test_waits give identical findings for LF and CRLF on 3.9 and 3.14. `guard_population._bash()` avoids the WSL bash. `_toml_compat` falls back to tomli. All 385 modules import on 3.9/3.11/3.13/3.14 (imp.py; only third-party ImportErrors on bare interpreters). `_core/git.py:32` handles the Windows cwd-before-PATH trap. committed_line_endings reads index blobs. No `open()`/`read_text()` without encoding in src. `.gitattributes` forces eol=lf.
- Failure scenario: none.
- Proposed fix: none.
- AST-checkable: False; FP risk: low
- Disposition: WON'T FIX (no defect: finding records robust areas to prevent re-audit)
