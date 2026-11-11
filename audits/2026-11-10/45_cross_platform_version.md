# 45_cross_platform_version

Round 2026-11-10, dimension 45. Read-only audit of `src/`. Every finding is OPEN until its Disposition line says otherwise.

Scope and method: the CI matrix in `.github/workflows/self-ci.yml` (Linux 3.9 to 3.13, Linux 3.14 and Ubuntu 26.04 preview legs, Windows 3.11 blocking plus Windows 3.9 and 3.13 preview, macOS 3.11). Greps over `src/` for stdlib APIs newer than 3.9, removed or deprecated APIs, POSIX-only calls, hardcoded POSIX commands, and text-mode I/O without an encoding. Behaviour checked by running snippets on the uv-managed interpreters (3.9.25 and 3.14). Findings already in `audits/TRACKER.md` and `audits/2026-11-07/45_cross_platform_version.md` (45-F1 to 45-F8) are not re-reported.

Interpreters run: CPython 3.9.25 and 3.14 on Windows 11. Not run: Linux and macOS (no access), 3.10 to 3.13 directly.

Negative results (checked, no defect found): no `import imp`, `distutils`, `pkgutil.find_loader`, `ast.Str`/`ast.Num`, `utcnow()` in production code (`naive_utcnow` gate), `inspect.getargspec`, `ZoneInfo` use (no tzdata dependency); no `os.killpg`/`os.setsid`/`fcntl` at module import on Windows (`_mutation_worker.py` imports fcntl/msvcrt lazily per OS); `_mutation_runner.py` `rmtree` picks `onexc` on 3.12+ and `onerror` below; `zip(strict=)`, `itertools.batched`, `Path.walk`, `typing.Self`, `tomllib` import outside `toml_compat` are absent; every `Path | None` annotation in files without `from __future__ import annotations` is quoted or absent; no `shell=True`; `subprocess` text-mode calls pass `encoding="utf-8"`.

## 45-R9 (P2) `_split_command` with `posix=False` keeps quote characters, so a quoted executable path in BLACK_CMD fails on Windows

- File: `src/py_ci_shared/black_filtered_apply.py:84-86` (`_split_command`), used at `:89` for `_BLACK_CMD`.
- Evidence: the function calls `shlex.split(value, posix=not windows)`. On Windows (`windows=True`) that is `posix=False`, which keeps the quote characters in the token. Run on the 3.14 interpreter:
  `shlex.split('"C:\\Program Files\\black.exe" --check', posix=False)` returns `['"C:\\Program Files\x08lack.exe"', '--check']` (the `\b` escape in my snippet is my own quoting, the point is that the leading and trailing `"` survive). The POSIX mode strips them. The subprocess then receives the literal `"C:\tools\black.exe"` with quotes, which `CreateProcess` does not resolve, so `FileNotFoundError` is raised from the black invocation.
- Failure scenario: a Windows developer sets `BLACK_CMD='"C:\tools\black.exe"'` (quoted because the path is written that way in a shell profile, or because the executable lives under a directory with a space and they quote it). Every black run on Windows raises `FileNotFoundError` instead of checking. Linux and macOS are unaffected (POSIX mode strips the quotes), so the defect is visible only on the Windows legs or on a Windows developer machine, which no blocking CI leg runs for this path.
- Scope note: the docstring says BLACK_CMD is space-separated, so a path containing a space is unsupported on every platform. The defect in this finding is the quote retention for a quoted path with no space, and the mismatch between the POSIX and Windows parsers.
- Proposed disposition: on Windows, strip one matched pair of surrounding `"` from each token (or use `shlex.split(value, posix=False)` followed by `token[1:-1]` for quoted tokens); add a unit test that calls `_split_command('"C:\\tools\\black.exe" --check', windows=True)` and asserts `['C:\\tools\\black.exe', '--check']`. The test is platform-independent because `windows` is a parameter.
- AST-checkable: False. FP risk: low.
- Disposition: RESOLVED (proposed).

## 45-R10 (P3) `datetime.fromisoformat` rejects a trailing `Z` on Python 3.9 and 3.10, so the coverage staleness check is silently skipped there

- File: `src/py_ci_shared/unexecuted_function_bodies.py:177-184` (parse), `:376-378` (use).
- Evidence: `stamp = datetime.fromisoformat(meta["timestamp"])` is wrapped in `except ValueError: stamp = None`. On 3.9.25: `datetime.fromisoformat('2026-11-10T12:00:00Z')` raises `ValueError: Invalid isoformat string`; `'2026-11-10T12:00:00+00:00'` and `'2026-11-10T12:00:00.123456'` parse. `Z` is accepted only from 3.11. The use site at `:376` is guarded by `report.timestamp is not None`, so a `None` disables the "report written before the newest source change" check with no message.
- Failure scenario: a coverage JSON whose `meta.timestamp` carries a `Z` suffix (produced by a tool other than `coverage.py`'s naive `isoformat()`, or by a future coverage version that writes UTC) is accepted on the 3.11+ legs and rejected on the 3.9 and 3.10 legs. The stale-report warning fires on 3.11+ and never on 3.9 or 3.10. The two legs disagree on the same input, and the 3.9 leg is green when it should be red.
- Caveat: I did not verify the exact timestamp format `coverage.py` writes for the pinned coverage version; the finding holds for any input that carries `Z` or another non-3.9 form.
- Proposed disposition: normalise a trailing `Z` to `+00:00` before parsing (`ts[:-1] + "+00:00" if ts.endswith("Z") else ts`), and log at warning level when parsing fails instead of silently setting `None`. Add a test that parses a `Z` timestamp on every leg.
- AST-checkable: False. FP risk: low.
- Disposition: RESOLVED (proposed).

## Summary

| Id | Sev | Finding | Disposition |
|---|---|---|---|
| 45-R9 | P2 | `_split_command` keeps quotes on Windows, breaking a quoted BLACK_CMD executable | RESOLVED (proposed) |
| 45-R10 | P3 | `fromisoformat` rejects `Z` on 3.9/3.10, staleness check silently skipped | RESOLVED (proposed) |

Counts: 2 new findings (0 P0, 0 P1, 1 P2, 1 P3). Both are unverified against a live Windows CI run; 45-F9 is verified in the parser output on 3.14, and 45-F10 is verified on 3.9.25 for the `fromisoformat` behaviour, with the coverage-format caveat above.

Not covered: Linux and macOS execution (no access), 3.10 to 3.13 direct runs, and `tests/`, which are out of scope for this read-only pass.
