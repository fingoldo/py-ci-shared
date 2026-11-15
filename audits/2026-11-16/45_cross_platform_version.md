# 45 cross_platform_version: round 2026-11-16 (T)

Commit audited: `48e49b1` (master). Host: Windows 11, Python 3.14.3 (PATH), uv-managed 3.8/3.9/3.10/3.11/3.12/3.13/3.14/3.14t (free-threaded)/3.15.0b3.
Read first: `audits/2026-11-11/45_cross_platform_version.md`, TRACKER rows 45-F*, 45-R*, 45-S*.

## What I ran

- `ast.parse` + `compile` of all of `src/` on 3.9.25, 3.11.15, 3.13.14, 3.15.0b3: 0 failures.
- Import of every `py_ci_shared.*` module on 3.9, 3.11, 3.13: 0 failures (3.15 venv could not install pyyaml/pytest wheels; its 14 failures are `No module named yaml/pytest`, not package defects).
- `vermin --target=3.9-` over `src/py_ci_shared` (vermin 1.9.2, run from the 3.9 venv): only `shutil.rmtree(onexc)` (`_mutation_runner.py:476`, with an `onerror` branch at :478) and `tomllib` (`toml_compat.py:16`, with the `tomli` fallback :18). Both guarded, no defect.
- `run_all_checked` over all of `src/py_ci_shared` on 3.9 and 3.13: `failed == {}`, 243 findings each, identical key set (same md5 of the sorted `(check, file, line)` tuples).
- `run_all_checked` over a modern-syntax fixture (PEP 695 `type X[T]=`, `class Box[T]`, PEP 701 nested-quote f-string, `except*`, `match`, 3.14 t-string and `except A, B:`) on 3.11, 3.13, 3.14, 3.14t, 3.15: no scanner raised; on 3.11 the PEP 695/701 file is reported `unparsable` (correct, loud), on 3.13+ identical findings.
- Free-threaded 3.14.6t (GIL off): `run_all_checked(parallel=True)` and the fixture run complete with `failed == {}`.
- Tests on 3.9, 3.11, 3.13 (venv with pytest, pyyaml, tomli, vermin, psutil, black; `-o addopts=`): `test_black_filtered_apply, test_toml_compat, test_scaffold_bom_pyproject, test_unparse_stable, test_atomic_write_staging, test_cli, test_cli_tool_exit_codes, test_stdlib_json_ban`: 154 passed on each. On 3.14.3: `test_cross_interpreter_keys, test_toml_compat, test_scaffold_bom_pyproject, test_unparse_stable`: 30 passed (the cross-interpreter test really compared other interpreters here, 26 s).
- cp1252 stdout probes (below).

Not covered: Linux and macOS execution (no host), Windows 3.8-or-older (not supported), the 3.15 leg with a full dev extra (numpy/black would not build for 3.15.0b3 here), whether `actions/setup-python` resolves `"3.15"` without `allow-prereleases` on the runners today (3.15 GA is due this autumn; I could not check the runner manifest offline).

## 45-T1 (P2) Every `python -m py_ci_shared.<gate>` main (47 of 49) writes findings with a raw `sys.stdout.write`: a non-encodable character crashes the gate and loses the findings

- **Evidence:** `cli.py:55-65` has `_print`, which falls back to `backslashreplace` (audit K-1, a cp1251 crash). The same fix was never applied to the stand-alone mains: 49 modules contain `if __name__ == "__main__"`, and 47 of them have no safe writer (`grep -l '__name__ == "__main__"' ... | xargs grep -L "_safe_write\|backslashreplace\|cli import"`). Three of them are pre-commit entries (`.pre-commit-hooks.yaml:31,40,50`: `pinned_tool_versions`, `mypy_gate`, `worktree_hygiene`).
  Demonstration 1 (finding with a Cyrillic path, stdout encoding cp1252, as a pipe on a Western-locale Windows or a CI log capture):
  `PYTHONIOENCODING=cp1252 python -m py_ci_shared.import_cycles <root> пакет` ->
  `File ...import_cycles.py, line 433, in main: sys.stdout.write(shown.render() + "\n")` ... `UnicodeEncodeError: 'charmap' codec can't encode characters in position 121-125`, exit 1, no finding printed. With `PYTHONIOENCODING=utf-8` the same run prints the finding and `1 finding(s), 3 parsed`.
  Demonstration 2 (a hook entry): file `модуль.py` containing `x: int = 'a'`, `PYTHONIOENCODING=cp1252 python -m py_ci_shared.mypy_gate модуль.py` -> traceback `UnicodeEncodeError ... position 0-5` out of `mypy_gate.py:106` `sys.stdout.write(output)`, exit 1; mypy's error text and the gate's own FAIL line are never shown.
- **Impact:** on a non-UTF-8 stdio (Windows pipe or legacy console, a locale-C CI box) any finding whose text holds a non-ASCII character (a non-ASCII path or identifier, which the corpus-wide gates echo back) turns into a traceback. Exit code stays 1 in the demonstrations, but a gate whose contract is another code (the K-1 incident: 2 for ERROR) gets 1, and the user sees no finding. Not silent-pass, it is a lost diagnostic.
- **Proposed fix:** one helper, `_core.console.safe_stdio()`, that calls `sys.stdout.reconfigure(errors="backslashreplace")` and the same for stderr (guarded by `hasattr`), invoked at the top of every `main()` (or once in a shared `run_main(main)` wrapper); a meta-test that every module with `__name__ == "__main__"` references it. ADDITIVE (only changes what happens where the old code crashed).
- **Disposition:** OPEN

## 45-T2 (P2) Nine-plus gates look for `ast.Try` only and so skip `try: ... except* X:` (PEP 654, 3.11+): a swallow in an `except*` handler is not found

- **Evidence:** `ast.TryStar` is a sibling class of `ast.Try`, not a subclass. `grep -rl "ast\.Try\b" src` gives 47 files, of which 23 never mention `TryStar` (`code_audit/broad_except.py`, `skip_masking_except.py`, `silent_escalation.py`, `retry_loops.py`, `raising_stub_swallowed.py`, `bare_except.py`, `asymmetric_except_siblings.py`, `shielded_resource_release.py`, `mutable_defaults.py`, `fail_open_handlers.py`, `state_before_work.py`, `no_xfail_to_defer.py`, `_silent_skip_scan.py`, `_mutation_speed.py` and others; the other 24 handle it by `hasattr(ast, "TryStar")`, e.g. `rollback_then_continue.py:213`, `unresolved_imports.py:457`).
  Demonstration on 3.14.3 (`scratchpad/audit_T/45/ts/run.py`): `a/m.py` has `def f()` with `try/except Exception: pass` (line 4) and `def h()` with `try/except* Exception: pass` (line 10); `tests/test_m.py` has the same pair around `pytest.skip`. Output: `scan_broad_except_swallows -> [4]` and `scan_except_skip_masks_call_under_test -> [4]`. The `except*` twins (lines 10 and 11) are not reported.
- **Impact:** a consumer on 3.11+ that uses exception groups (asyncio TaskGroup code does) gets none of these gates on that code. Low frequency, but the gates are documented as covering `except` handlers, and 24 sibling modules already treat TryStar, so the two groups disagree.
- **Proposed fix:** one constant `TRY_NODES = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())` in `_core` (or `code_audit/_base.py`) and replace the 23 `isinstance(x, ast.Try)` sites; a meta-test (AST over `src/`) that fails on `isinstance(..., ast.Try)` outside that constant, plus fixture tests with `except*`. ADDITIVE for the finders, but it can turn a green consumer red by finding new defects: classify BEHAVIOUR CHANGE (CHANGELOG entry with `What to do:`).
- **Disposition:** OPEN

## 45-T3 (P2) `test_cross_interpreter_keys` can only find other interpreters on this Windows host: on every CI leg it skips, and it also swallows real crashes

- **Evidence:** `tests/test_cross_interpreter_keys.py:48-62` discovers interpreters under `%APPDATA%\uv\python\cpython-3.*` (empty string root on Linux/macOS, so `uv_root.is_dir()` is False) plus `PY_CI_SHARED_OTHER_PYTHONS`. `grep -rn OTHER_PYTHONS .github` finds nothing, and `self-ci.yml` installs exactly one Python per leg (`setup-python` with `matrix.python`). So on CI the test ends in `pytest.skip("no second interpreter ...")` (line 99-100), and the docstring's "CI legs that have one run it" is false for every current leg. 45-F5 is marked RESOLVED on the strength of a test that runs only where the owner develops.
  Second defect, same file `:78-82`: `_probe` returns `None` for ANY non-zero exit ("the interpreter lacks a requirement"), and the loop `continue`s on `None`. A real crash of the gate code on one interpreter (a 3.15-only break, say) is therefore dropped from the comparison instead of failing it. The corpus (`_CORPUS`, lines 24-40) has no 3.12+ construct, so the 3.12/3.14 ast shape changes the brief names are not exercised either.
- **Impact:** the "same keys on every interpreter" promise is verified only when the owner runs the suite locally; a CI-only regression (e.g. 3.15 vs 3.9 ast difference) is caught only by a consumer's red shard, the very pattern 45-F5 was meant to end. Skip-as-pass in the package that ships a skip-as-pass gate.
- **Proposed fix:** (a) in `self-ci.yml` give the ubuntu legs a second interpreter (`uv python install 3.9 3.13` and `PY_CI_SHARED_OTHER_PYTHONS` pointing to them, with `pyyaml` and `tomli` installed into them), or run the probe once per matrix leg and upload the JSON, comparing in the `preview-report` job; (b) in `_probe` distinguish `ModuleNotFoundError` in stderr (skip that interpreter) from any other failure (raise with stderr); (c) add a 3.12+ file (PEP 695 `type X[T]`, `except*`, nested f-string) to the corpus, probed only on interpreters that parse it. ADDITIVE (test and workflow only).
- **Disposition:** OPEN

## 45-T4 (P3) `pyproject.toml` has no Python version classifiers and `requires-python` has no upper bound, while CI exercises 3.9-3.13, 3.14 (non-blocking) and 3.15

- **Evidence:** `pyproject.toml:13` `requires-python = ">=3.9"`; classifiers `:18-23` carry only `Programming Language :: Python :: 3`. `self-ci.yml:44-58` runs 3.9, 3.10, 3.11, 3.12, 3.13, 3.15 blocking, 3.14 preview (`preview: true`, "until it has run green once"), Windows 3.9/3.13 preview, macOS 3.11 only. The README states no Python range (`grep -n "3\.9" README.md` only hits catalogue rows). Nothing wrong is claimed, but nothing is claimed either, so PyPI/pip and tooling cannot tell a consumer which of these were tested. 3.14 being preview while the 3.15 pre-release/GA leg is blocking is inverted relative to the owner's own interpreter (3.14); locally 3.14.3 passes everything I ran (30 + the earlier gate tests), so the promotion rule (10 green master runs) is the only thing keeping it advisory.
- **Impact:** documentation only. A consumer on 3.14 sees no statement of support; the one interpreter the owner develops on is the one whose CI failure cannot block a release.
- **Proposed fix:** add classifiers `Programming Language :: Python :: 3.9` ... `3.13` for the blocking legs (and 3.14 once promoted), add a "Supported Python" line to the README that lists the blocking and the preview legs, and add a test that compares classifiers with the blocking entries of the `self-ci.yml` matrix. ADDITIVE.
- **Disposition:** OPEN

## Summary table

| id | sev | title |
|---|---|---|
| 45-T1 | P2 | 47 of 49 `python -m` mains write findings raw; a non-encodable char crashes the gate and loses the findings |
| 45-T2 | P2 | 23 gate modules match `ast.Try` only, so `except*` handlers are never examined (demonstrated on 2 gates) |
| 45-T3 | P2 | `test_cross_interpreter_keys` skips on every CI leg and swallows real crashes of the other interpreters; corpus has no 3.12+ syntax |
| 45-T4 | P3 | No version classifiers/README range; support statement and CI matrix are not tied together |

Counts: 0 P0, 0 P1, 3 P2, 1 P3.

## Considered and not raised

- **Python 3.9 breakage (match, PEP 604 at runtime, zip(strict=), tomllib, itertools.batched, removeprefix):** compile + import of every module on 3.9.25 clean; vermin flags only the two guarded sites (`onexc`/`tomllib`); 154 targeted tests green on 3.9, 3.11, 3.13.
- **Interpreter-dependent gate keys (ast.unparse / ast.dump / stdlib_module_names):** `run_all_checked` over the whole of `src` gives the same 243 findings and key hash on 3.9 and 3.13; 45-F1 to F3 fixes hold.
- **PEP 695 type params, PEP 701 f-strings, 3.14 t-strings, `except A, B:` (3.14):** none of the ~60 scanners raised on 3.13/3.14/3.15; no `failed` entries. (The only gap found in this family is `except*`, 45-T2.)
- **Free-threaded build:** 3.14.6t (GIL off) runs the parallel registry sweep and the fixture with `failed == {}`; the self-ci comment (no free-threaded leg, orjson) is an honest reason, not a defect.
- **`test_mutation_teeth_regressions.py:840-845` reading `grandchild.pid` after a 5 s deadline** (could race on a loaded macOS runner): NOT REPRODUCED. With the deadline cut to 0.3 s the pid file already existed on this host (`pid.py`), so the interpreter starts far inside 5 s; a 5 s startup would need a pathological runner. No evidence of a flake, not raised.
- **macOS git-scrub timeout:** `_core/git.py:41` default is 120 s and overridable by env (`TIMEOUT_ENV_VAR`); no hard-coded short git timeout in `src/`. The shortest test-side git timeouts are 30-60 s (`tests/conftest.py:99`, `_git_repo_helper.py:18`). I have no macOS host to reproduce the earlier timeout, so no new finding.
- **Console encoding in `cli.py`:** safe (`_print`); the problem is the mains (45-T1).
- **vermin hits `shutil.rmtree(onexc)` and `tomllib`:** both behind version/ImportError guards (`_mutation_runner.py:476-478`, `toml_compat.py:16-18`).
- **`os.rename` over an existing file, shell=True, hardcoded `/tmp`, symlinks:** no `os.rename`/`shell=True` in `src`; `/tmp` appears only as detector literals (`predictable_tmp_path.py:72,180`, marked `tmp-path-ok`).
- **CRLF, case-insensitive paths, bash on Windows, quoting, atomic replace retry, BOM:** already dispositioned (45-F6, F8, R9, S11; `exists_exact_case` in the CHANGELOG); nothing new.
- **Whether `setup-python` finds `"3.15"` on the runners:** unverifiable offline; if 3.15 is still a pre-release there, the leg needs `allow-prereleases: true`. Left to the CI-health dimension.
