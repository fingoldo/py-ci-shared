# 45_cross_platform_version

Round 2026-11-11, dimension 45. Read-only audit of `src/`. Every new finding is OPEN until its Disposition line says otherwise.

Scope and method: greps over `src/py_ci_shared` for Python 3.10+ syntax and stdlib APIs (match statements, `zip(strict=)`, `datetime.UTC`, `StrEnum`, `asyncio.timeout`, `TaskGroup`, `inspect.get_annotations`, `types.NoneType`, `entry_points(group=)`, `contextlib.chdir`, `hashlib.file_digest`, `logging.getLevelNamesMapping`, `itertools.pairwise`, `functools.cached_property`, PEP 604 unions at runtime); text I/O without `encoding`; `subprocess` text-mode calls; POSIX-only calls (`os.killpg`, `os.setsid`, `os.getuid`, `signal.SIG*`, `fcntl`/`pwd`/`grp`/`termios`); hardcoded POSIX paths and commands; path-separator string handling; undeclared third-party imports against `pyproject.toml` (`dependencies` = tomli, PyYAML; `dev`/`api` extras for the rest). Behaviour checked on CPython 3.14.3 (Windows 11) with a snippet on `tomllib`. Prior findings 45-F1 to 45-F10 were checked for their current state and are not re-reported.

Not run: Python 3.9 interpreter (the prior round ran it), Linux and macOS, and the full test suite (not permitted for this pass).

Negative results (checked, no defect found):
- No 3.10+ syntax or API in `src/` outside string literals and comments. The `datetime.UTC`, `removeprefix`, `StrEnum` and `TypeGuard` hits are inside regex/string constants that are source-analysis rules, not runtime calls. `functools.cached_property` (`_import_graph.py:167`) is 3.8+.
- Every `read_text`/`write_text`/`open(..., mode)` in text mode passes `encoding=`; `_scaffold._write` writes bytes explicitly. `subprocess` text-mode calls all pass `encoding="utf-8", errors="replace"` (`api_floor.py:231`, `guard_population.py:223`, `script_entry_points.py:406`, `timezone_honest.py:120`, `_mutation_runner.py:391,524`).
- POSIX-only calls are guarded: `os.getuid` by `hasattr` (`embedded_postgres.py:134`), `os.chmod` is portable, `os.killpg` / `os.setsid` are behind `_new_group_kwargs`, `_mutation_runner.py` imports `signal` (portable module) and uses no `SIGKILL`/`SIGHUP`, `fcntl`/`msvcrt` are lazy per OS (`_mutation_worker.py`).
- Windows `bash` resolution is handled: `guard_population.py:170-181` prefers Git's `bin/bash.exe` over the WSL `System32\bash.exe` launcher.
- Optional third-party imports (`psutil`, `psycopg2`, `numpy`, `vermin`, `pydantic`, `sqlglot`, `numba`) are all inside functions with an `ImportError` path or a `# optional-import-ok` marker. Top-level `import pytest` occurs only in pytest-plugin modules carrying that marker. `yaml` is at top level in four modules and is a declared runtime dependency. `tomllib` goes through `_toml_compat`.
- `entry_points(group=)` 3.10 API is guarded (`_mutation_runner.py:199`, `randomly_seed_guard.py:41`); `packages_distributions` is fetched with `getattr` (`_ci_install_parts.py:717`); `os.chdir` restore paths are fine.
- `ZoneInfo` / `zoneinfo` are not used; `fromisoformat` with `Z` is handled at `unexecuted_function_bodies.py:182` (see 45-F10 below).
- Path handling: `closed_audit_rounds._under` and `ci_test_dir_reachability._covers` compare git-diff or `replace("\\", "/")`-normalised strings, so backslashes do not reach the prefix check.

## Already tracked

- **45-F1 to 45-F8**: already tracked (2026-11-07 round; all RESOLVED in TRACKER.md).
- **45-F9**: already tracked: `_split_command` quote handling on Windows. Re-checked: `black_filtered_apply.py:84-93` now strips the quote pair (RESOLVED, test named in TRACKER.md).
- **45-F10**: already tracked: `fromisoformat` with trailing `Z`. Re-checked: `unexecuted_function_bodies.py:182` reads `Z` as `+00:00` (RESOLVED; TRACKER.md notes the gate path is not exercised by the test, a follow-up).

## 45-S11 (P3) `_scaffold._read` keeps a UTF-8 BOM, so `tomllib` rejects a BOM-prefixed pyproject.toml in the scaffold

- File: `src/py_ci_shared/_scaffold.py:50-51` (`_read`), used at `:217` (`_version` on `pyproject.toml`), `:240`, `:246`, `:253`, `:278`, `:280`.
- Evidence: `_read` returns `path.read_bytes().decode("utf-8")`, so a leading `U+FEFF` survives. `_version` passes that string to `tomllib.loads`. Run on CPython 3.14.3 (Windows 11): `tomllib.loads('﻿[tool.x]\n')` raises `TOMLDecodeError: Invalid statement (at line 1, column 1)`. The same file read with `utf-8-sig` parses.
- Platform where it breaks: any platform, but the input is typical of a file saved by a Windows editor (Notepad before 2019 and some Visual Studio templates write a BOM). The failure is loud (an uncaught `TOMLDecodeError` out of the scaffold), not silent.
- Scope: the BOM-preserving behaviour is correct for `registry.toml` and the test file (lines 278, 280 write the text back), so the fix should not strip it for writes.
- Proposed disposition: decode with `utf-8-sig` in `_version` (or in a reader used only for the TOML parse), keep `_read` BOM-preserving for write-back, and add a test that calls `_version` on a BOM-prefixed pyproject. Disposition: OPEN (proposed).
- AST-checkable: True (a `tomllib.loads(_read(...))` call with a `_read` that uses plain `"utf-8"`). FP risk: low.

## 45-S12 (P3) `_core/git.py:32` mutates the process environment at import time (Windows-only effect, inherited by every child)

- File: `src/py_ci_shared/_core/git.py:32-33`.
- Evidence: `os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")` runs when the module is imported, not when a git command is issued. On Windows this switches off the current-directory-first lookup for every child process the importing host later spawns, including children that have nothing to do with git. On POSIX the variable has no effect, so the Linux and macOS legs cannot observe the difference. The comment states the intent ("covers every child"), so the global scope is deliberate, but it is set as an import side effect of a helper module.
- Platform where it breaks: Windows only, for a host that imports `py_ci_shared._core` and then spawns a program it expects to resolve from the current directory. Not reproduced (no Windows-side test exists for this variable).
- Proposed disposition: move the variable into the environment built by `git_env` (and any other spawn site that resolves a bare name), so the mutation is scoped to the subprocesses this package runs; add a test that `git_env()` carries the variable on Windows and that importing the module leaves `os.environ` unchanged. If the global scope is kept on purpose, document the import side effect in the module docstring. Disposition: OPEN (proposed).
- AST-checkable: True (an `os.environ` assignment at module top level). FP risk: low.

## Summary

| Id | Sev | Finding | Disposition |
|---|---|---|---|
| 45-S11 | P3 | `_scaffold._read` keeps a UTF-8 BOM, so `tomllib` rejects a BOM-prefixed pyproject.toml in the scaffold | RESOLVED (proposed) |
| 45-S12 | P3 | `_core/git.py` sets `NoDefaultCurrentDirectoryInExePath` in `os.environ` at import; Windows-only, inherited by every child | DEFERRED (proposed) |

Counts: 2 new findings (0 P0, 0 P1, 0 P2, 2 P3). Already tracked: 10 (45-F1 to 45-F10).

Verified by running: 45-F11 (tomllib BOM parse on CPython 3.14.3). 45-F12 is verified by reading the code only. Neither P0 nor P1 was found, so no P0/P1 verification was needed.

Not covered: Python 3.9 execution of the new code paths, Linux and macOS execution, and the Windows runtime behaviour of the `NoDefaultCurrentDirectoryInExePath` variable.
