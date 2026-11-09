# Dimension 40: gate false negatives (round 2026-11-10)

Read-only audit of `src/py_ci_shared` (gates and `_core`) and the existing tests. No file under `src/` or `tests/` was edited.

Already known and not re-reported: every 40-F* row of `audits/2026-11-07/40_gate_false_negatives.md` (F1 to F13: entry-name substring, swallowed-exception tuples, seed keywords, return-exempts-handler, sys import aliases, rollback receivers, check-then-act scope, unsafe deserialization variants, fail-open handlers, env-flag parsing, reload detection, tmp path exemption, utcnow getattr). Also already closed in `audits/TRACKER.md`: 20-F*, 49-F7 (skip default is an owner decision, `skip = "ok"`), 42-F1 to F11 (baseline and corpus semantics), 35-F3 (byte cache). Checked and not findings: `_core/runner.py` catch-all (`runner.py:184`) reports ERROR, not PASS; `optional_truthiness.py:497` drops an unparsable file only because `find_truthiness_tests` (called at `optional_truthiness.py:533`) reports it; `value_bearing_asserts` covers the `python -O` class; the asserts in `deletion_gates.py` and `conceded_defect_pins.py` are docstring examples, not gate code.

Four findings. Two are P2 and two are P3. Items 40-F3 and 40-F4 are documented best-effort choices, so they are false negatives by design, not by accident.

## 40-F1 (P2) The gate cache reads only the config's kwargs for the network flag, so a network gate's PASS is cached and replayed

- **File:line:** `src/py_ci_shared/_core/gate_cache.py:105-106` (`_kwargs_ask_for_network`) and `:124` (`key_for`); caller `_core/runner.py:` `_cache_key(config, run, kwargs, budget)`, which passes the pre-resolution config kwargs. The gate's default is at `src/py_ci_shared/sibling_floor_skew.py:363-364`: `network: bool = True`.
- **Evidence:** `_kwargs_ask_for_network` looks only at keys present in the config table (`"network"`, `"online"`, `"fetch_remote"`). A parameter whose default is truthy is invisible to it. Reproduction: `key_for(Path('.'), 'py_ci_shared.sibling_floor_skew', 'assert_sibling_floor_skew', {'repo_root': '.'}, 0.0, 'T')` returns a non-None key (cacheable). The same call with `{'network': True}` returns None. `sibling_floor_skew.version_at` (line 278) fetches the sibling's pyproject from the network at a given `ref`.
- **Failure scenario:** a consumer's gate table omits `network`, so the run is cacheable. The first run PASSes and is recorded. A sibling install ref is a branch, and the sibling's pyproject version on that branch drops below the consumer's floor. The tree is unchanged, so the key is unchanged, and the cached PASS is returned without a network call (`runner.py` cache lookup). The skew goes undetected until the local tree changes.
- **Disposition: RESOLVED-by-fix.** In `runner.py`, compute the key from the signature-resolved kwargs (defaults included), or have `_kwargs_ask_for_network` consult `inspect.signature(func).parameters[name].default`. Add a regression test in `tests/test_core_gate_cache.py` that a network-defaulting gate with no config kwarg is not cached.

## 40-F2 (P2) The cache key has no interpreter or installed-tool identity, so a verdict from one Python or tool version replays under another

- **File:line:** `src/py_ci_shared/_core/gate_cache.py:126-137` (the payload: `module`, `entry`, `kwargs`, `budget`, `package` version, `tree`, `root`). No `sys.version`, `sys.executable`, or tool version is in it.
- **Evidence:** the docstring (lines 1-10) says a re-run on an unchanged tree gives the same verdict. Several gates do not satisfy that: `api_floor.py:65` runs vermin through the active interpreter, and `api_floor.py:237` reads its exit code. `45-F1` and `45-F3` (round 2026-11-07) already record that `ast.unparse` output and token-ratio thresholds depend on the interpreter, so a gate's verdict differs between 3.9 and 3.11+. The key is identical for both, so a PASS recorded under one replays under the other. I checked this by reading the payload and did not run it under a second interpreter.
- **Failure scenario:** a developer runs the gates under Python 3.13 and records PASS. The same repository is then checked under Python 3.9 (a tox or matrix leg on the same machine, a persisted `~/.cache`), and the 3.9 verdict is never computed. The cache sits under `Path.home()/.cache/py_ci_shared/gate_cache` (`gate_cache.py:_store_dir`), which survives across legs and runs. A version-dependent failure on 3.9 passes silently.
- **Disposition: RESOLVED-by-fix.** Add `sys.version_info[:3]`, `sys.implementation.name` and a digest of the interpreter path to the payload in `key_for`. For tool-backed gates (vermin, mypy, ruff), also add the tool's version, or mark those gates uncacheable. Add a test that two different interpreter identities produce different keys.

## 40-F3 (P3) Several gates skip a file that cannot be parsed or decoded, so its findings are never produced

- **File:line:**
  - `src/py_ci_shared/code_audit/source_text_assertions.py:51-52` (test file, `SourceError` then `continue`; the docstring at line 39 says so).
  - `src/py_ci_shared/tool_config_env_vars_set_in_every_tool_job.py:126-128` (a test file read error becomes `continue`).
  - `src/py_ci_shared/phantom_code_references.py:393-395`, `:428-430`, `:823-825` (a file that fails `read_source` or `parse_source` is dropped as a source of names, constants or line counts).
  - `src/py_ci_shared/nginx_location_headers.py:228-230` (one of several `include` targets that fails to parse is dropped; the others still splice).
  - `src/py_ci_shared/ci_install_covers_conftest.py:679-681` (a `conftest`/`pytest.ini` that fails to read is treated as having no `testpaths`).
- **Evidence:** each handler catches `SourceError` (or `NginxParseError`, `configparser.Error`) and continues without recording a finding. Contrast `optional_truthiness.py:248-250`, which reports `unparsable:` as a finding. `_core/errors.py` defines `SourceError` as a typed error, so the pattern is deliberate, not a missed case. `tests/test_gate_floors_and_parse_errors.py` covers the unparsable path only for the gates that report it.
- **Failure scenario:** a test file with a syntax error is skipped by `source_text_assertions`. Pytest fails on that file at collection, so CI goes red regardless. The impact is real only where pytest does not import the file: a non-test `.py` under `tests/` is not collected, or `phantom_code_references` sees a symbol defined only in a broken file and reports the wrong thing. For `ci_install_covers_conftest`, a broken `pytest.ini` makes the gate evaluate a different install set than the one pytest will use.
- **Disposition: RESOLVED-by-fix** for `ci_install_covers_conftest` and `tool_config_env_vars` (report the unreadable file as a finding, as `optional_truthiness` does). For `source_text_assertions`, `phantom_code_references` and `nginx_location_headers`, **WON'T FIX** on the grounds that the skip is a documented pyutilz convention and the file is already reported by a parse gate; this needs owner confirmation before it is closed.

## 40-F4 (P3) stale_source_citations drops every extra-glob file it cannot read, so stale citations in that file pass

- **File:line:** `src/py_ci_shared/stale_source_citations.py:271-273` (`except SourceError: continue`), inside `_extra_findings`; the docstring at `:256-260` says "best-effort: a file that cannot be read is skipped rather than raised".
- **Evidence:** I wrote a `.md` file containing the byte `\xff` (invalid UTF-8) and ran `read_source` on it. It raises `SourceReadError`, a subclass of `SourceError` (checked: `issubclass` is True). The handler at `:271` therefore drops the file. The file is never scanned for `line-past-end` or `symbol-not-at-line`.
- **Failure scenario:** `extra_globs = ["*.md"]` is configured. A README in Latin-1 (or with one stray byte) cites `src/foo.py:912` after the file was shortened. The citation is not checked, and the gate reports clean. The author sees no message that a file was skipped.
- **Disposition: RESOLVED-by-fix.** Emit a finding `unreadable: <path>` (a `RULE_UNREADABLE` or reuse of the existing rule that names the file) instead of `continue`. Add a regression test that a non-UTF-8 extra-glob file produces a finding.

## Summary

Four new findings in this round, all in the cache and in the handling of files that fail to read or parse. The most consequential is 40-F1: a network-dependent gate that does not set `network` in config is cached, so its PASS is replayed without the network check. 40-F2 is the same class across interpreters and tool versions, and is established from the code, not from a second interpreter run. The two P3 findings are documented best-effort skips; they become findings to fix only if the owner rejects the convention. The gate runner's catch-all, the `value_bearing_asserts` guard and the optional-truthiness parse path were checked and are not false negatives.

Counts: P0: 0. P1: 0. P2: 2 (40-F1, 40-F2). P3: 2 (40-F3, 40-F4).
