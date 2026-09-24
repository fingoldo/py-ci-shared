# Audit: architecture, packaging and conventions

Read-only audit. HEAD `39e9b49` (master). 112 modules in `src/py_ci_shared` (21,472 LOC), 108 test files,
9 workflows, 2 composite actions, 2 ruff configs.

## Quantified duplication and inconsistency (grep over `src/py_ci_shared/*.py`)

| Pattern | Files | Occurrences | Note |
|---|---|---|---|
| `ast.parse` | 46 | 75 | no shared parse cache; each gate re-reads and re-parses the same corpus |
| `read_text` | 81 | 161 | 1 file (`install_safe_hook.py`) calls read_text with no `encoding=` |
| `rglob` | 39 | 49 | the dominant corpus enumerator |
| `glob(` | 54 | 81 | |
| `git ls-files` | 4 | (`baseline_trend`, `doc_identifier_parity`, `git_changed_lines`, `repo_hygiene`) | only 4 gates see "what git would commit" |
| `os.walk` | 2 | 2 | `effect_assertion_parity`, `repo_hygiene` |
| local `_SKIP_DIRS`/`_DEFAULT_EXCLUDE` constants | 7 | 7 distinct sets | e.g. `prompt_field_parity`/`save_failure_markers` skip 5 dirs, `db_transaction_completeness` 14, `naive_utcnow` 8, `value_bearing_asserts` also skips `tests`/`scripts` |
| private file-iter helpers (`_iter_py_files`, `_py_files` x2, ...) | 4+ | | re-implemented per module |
| private `_parse(path)` helpers | 3 | | `identity_comparisons`, `module_reload_safety`, `uncalled_functions`, with different return shapes (`Module|None`, `(str, AST)|None`) |
| `except SyntaxError` handlers | 42 files | | follow-ups differ: `continue` (11), `return []` (9), `return set()` (5), `return None`/`False`: unparsable files are silently skipped, not reported |
| `assert_*` public entry functions | 83 modules | | pytest-embedded API; no common signature |
| `def main` CLIs | 13 | | 4 use argparse; 6 take `main(argv)`; others read `sys.argv` directly |
| `register_refresh_option` copies | 6 | | 10 distinct `--refresh-*-baseline` flags |
| baseline writers | ~12 | | json vs orjson, sorted list vs dict vs `{version,content_hash}`, `sort_keys` in only 5 |
| `[tool.py_ci_shared]` config reads | 0 | 0 | no per-repo config; every knob is a Python kwarg in the consumer's test file |
| `lru_cache` / any memoisation | 0 | 0 | |


## Proposed target architecture

```
py_ci_shared/
  _core/
    corpus.py      # RepoContext: repo_root, git-tracked file list (ls-files --cached --others --exclude-standard -z),
                   # one fallback skip set, iter(suffix=..., under=...), memoised per process
    ast_cache.py   # parse(path) -> ParsedFile(text, tree, lines, error); lru_cache on (path, mtime_ns, size)
    baseline.py    # load/save/ratchet with schema + atomic replace + sorted keys; one diff message
    findings.py    # Finding dataclass, text/json/github-annotation renderers
    config.py      # [tool.py_ci_shared] loader (tomllib/tomli), per-gate sections, CLI overrides
    registry.py    # @gate(name, kind="ast"|"text"|"workflow"|"subprocess", baseline=bool)
  gates/<name>.py  # def check(ctx: RepoContext, cfg: Mapping) -> list[Finding]
  assertions.py    # legacy assert_* shims: build ctx, call check, raise AssertionError(render(findings))
  pytest_plugin.py # pytest11 entry: --py-ci-refresh, fixtures ctx/config, one test item per enabled gate
  cli.py           # `py-ci-shared run-all | run <gate> | list | refresh <gate> | config-path <name>`
```

Gate protocol: pure function over a shared `RepoContext`; no gate enumerates files or parses on its own (enforced by a self-gate banning `rglob`/`os.walk`/`ast.parse` outside `_core`). Exit codes 0/1/2. Output formats: text, json, `::error file=...,line=...::` for GitHub.

Per-repo config example:

```toml
[tool.py_ci_shared]
package = "src/mlframe"
tests = "tests"
baseline_dir = "tests/baselines"
enable = ["naive_utcnow", "uncalled_functions", "private_imports", "..."]

[tool.py_ci_shared.gates.function_length]
max_lines = 120
```

Runner: `py-ci-shared run-all` parses the corpus once and runs every enabled gate (thread pool for subprocess gates), so one pre-commit hook and one CI step replace dozens of hand-written meta-tests. The pytest plugin gives the same gates as test items for consumers who prefer pytest.

Release and pinning: version derived from tag (setuptools-scm); tag-push workflow verifies version, moves `v1`, publishes release notes; reusable workflows check out their own `job_workflow_sha` for configs; consumers pin by SHA with an exact-tag comment, bumped by Renovate; `version_tag_currency` verifies the comments.

Dogfooding: self-ci runs `py-ci-shared run-all` on this repo with every gate enabled (repo_hygiene, version_consistency, docs_inventory_parity against a registry-generated README catalogue, fail_message_quality, uncalled_functions), plus a Python matrix covering 3.9 to 3.13.

Migration order: (1) `_core.corpus` + `ast_cache` and move the 39 rglob gates over (fixes B1, B2, B3, B4, A13); (2) `_baseline` + pytest plugin (C3, C4, D3); (3) registry + CLI + config (C1, C2, C5, A9, A10); (4) release automation and workflow ref fix (A1 to A7); (5) self-apply (D1, A11).

## Findings

### ARCH-1 (High) -- Package version is 0.1.0 while releases are tagged up to v1.16.1

**Disposition:** OPEN

- **Original id:** A1
- **Where:** pyproject.toml:7, src/py_ci_shared/__init__.py:10
- **Finding:** Package version is `0.1.0` while releases are tagged up to `v1.16.1`. `pip install ...@v1.16.1` (pyutilz ci.yml:82) installs a distribution that reports 0.1.0, so `importlib.metadata.version`, pip caches and this repo's own `version_consistency`/`version_tag_currency` gates cannot tell releases apart. The repo does not apply its own version gates to itself.
- **Proposed fix:** Set version from the tag (setuptools-scm, or bump in the release commit) and wire `version_consistency` + `version_tag_currency` into self-ci.

### ARCH-2 (High) -- Consumer pinning is inconsistent: README policy is the moving @v1 tag; pyutilz uses @v1...

**Disposition:** OPEN

- **Original id:** A2
- **Where:** README.md:48, pyutilz/.github/workflows/black-filtered.yml:20, mlframe/.github/workflows/*.yml
- **Finding:** Consumer pinning is inconsistent: README policy is the moving `@v1` tag; pyutilz uses `@v1` for workflows but `@v1.16.1` for the package; mlframe pins four different SHAs all commented `# v1` (915217a, 41cbadc, 64e2b6b) plus `# v1.4.0` / `# v1.3.4` for actions. The `# v1` comments are false: `v1` now resolves to 71cf6d0, not any of those.
- **Proposed fix:** Pick one scheme: SHA pin + exact tag comment (`# v1.16.1`), bumped by Dependabot/Renovate, or `@v1` everywhere. Add a gate (version_tag_currency already exists) that verifies SHA-to-tag comments.

### ARCH-3 (High) -- v1 -> 71cf6d0, v1.16.1 -> 797f045; 32 commits on master since v1.16.1 (last tag 2026-09...

**Disposition:** OPEN

- **Original id:** A3
- **Where:** git tags
- **Finding:** `v1` -> 71cf6d0, `v1.16.1` -> 797f045; 32 commits on master since v1.16.1 (last tag 2026-09-12), including several new gates. Consumers on `@v1` do not get them; there is no release automation moving `v1`.
- **Proposed fix:** Release workflow on tag push that moves `v1` and checks pyproject version == tag.

### ARCH-4 (High) -- Reusable workflows git clone --depth 1 py-ci-shared default-branch tip to get configs/r...

**Disposition:** OPEN

- **Original id:** A4
- **Where:** .github/workflows/ruff-blocking.yml:54-60, lint-advisory.yml:66-73
- **Finding:** Reusable workflows `git clone --depth 1` py-ci-shared default-branch tip to get `configs/ruff-base.toml` and `RUFF_VERSION`. A consumer pinning the workflow at a SHA still gets master's ruff config and ruff version: the pin is not reproducible and a master push can break every consumer.
- **Proposed fix:** Clone at `${{ github.job_workflow_sha }}` (or accept a `py-ci-shared-ref` input defaulting to it); or ship the configs as package data and resolve the path via `python -m py_ci_shared.config_path`.

### ARCH-5 (Med) -- 6 of 7 reusable workflows declare no permissions:; they inherit the caller's token scop...

**Disposition:** OPEN

- **Original id:** A5
- **Where:** .github/workflows/black-filtered.yml, lint-advisory.yml, lint-blocking.yml, mypy-beachhead.yml, mypy-full.yml, ruff-blocking.yml
- **Finding:** 6 of 7 reusable workflows declare no `permissions:`; they inherit the caller's token scope (often write-all on push). config-drift-check, docs and self-ci do set it.
- **Proposed fix:** Add `permissions: contents: read` at workflow level in each; zizmor would flag this (the repo runs zizmor only optionally, lint-blocking.yml:138).

### ARCH-6 (Med) -- pip-audit, import-linter, pydoclint, semgrep installed unpinned (uvx pip-audit, uv pip...

**Disposition:** OPEN

- **Original id:** A6
- **Where:** lint-advisory.yml:100,119,126,151
- **Finding:** pip-audit, import-linter, pydoclint, semgrep installed unpinned (`uvx pip-audit`, `uv pip install --system semgrep`), contradicting the pin policy in lint-blocking.yml:66 and tool_versions.py.
- **Proposed fix:** Move versions into tool_versions.py; accept `*-version` inputs like lint-blocking.

### ARCH-7 (Med) -- pyutilz-ref defaults to empty = unpinned pyutilz tip; the description itself documents...

**Disposition:** OPEN

- **Original id:** A7
- **Where:** .github/actions/install-pyutilz/action.yml:15-31
- **Finding:** `pyutilz-ref` defaults to empty = unpinned pyutilz tip; the description itself documents this as a defect kept for compatibility.
- **Proposed fix:** Make it required, or default to a pinned SHA maintained in this repo.

### ARCH-8 (Med) -- requires-python >=3.9 but CI runs only 3.11 (matrix is OS only) and mypy checks as 3.10

**Disposition:** OPEN

- **Original id:** A8
- **Where:** pyproject.toml:13, self-ci.yml:42, pyproject.toml `[tool.mypy] python_version = "3.10"`
- **Finding:** `requires-python >=3.9` but CI runs only 3.11 (matrix is OS only) and mypy checks as 3.10. The 3.9/3.10 tomllib break (see `_toml_compat.py` docstring) was found in the field for exactly this reason.
- **Proposed fix:** Add 3.9 and 3.13 to the self-ci matrix; set mypy python_version = "3.9"; or raise the floor.

### ARCH-9 (Med) -- Manifest exposes one hook (mypy-full-manual, which is just python -m mypy), while 13 mo...

**Disposition:** OPEN

- **Original id:** A9
- **Where:** .pre-commit-hooks.yaml:1-27
- **Finding:** Manifest exposes one hook (`mypy-full-manual`, which is just `python -m mypy`), while 13 modules have CLIs and README (e.g. line 486) tells consumers to wire `python -m py_ci_shared.pinned_tool_versions` as a hand-written local hook. Header example says `rev: v1.0.0`.
- **Proposed fix:** Publish hooks for every CLI (pinned_tool_versions, safe_precommit, worktree_hygiene, mypy_gate, config_drift_check, and the future `run-all`); update the rev example.

### ARCH-10 (Med) -- Only 3 console scripts; the other 10 CLIs are python -m only

**Disposition:** OPEN

- **Original id:** A10
- **Where:** pyproject.toml `[project.scripts]`
- **Finding:** Only 3 console scripts; the other 10 CLIs are `python -m` only. No single entry point.
- **Proposed fix:** One `py-ci-shared` console script with subcommands (see proposal).

### ARCH-11 (Med) -- 67 of 112 modules are not mentioned in README (e.g

**Disposition:** OPEN

- **Original id:** A11
- **Where:** README.md
- **Finding:** 67 of 112 modules are not mentioned in README (e.g. baseline_ratchet, gate_integrity, gate_config_honesty, uncalled_functions, mutation-adjacent teeth_sweep, deletion_gates, function_length, version_consistency). A gate catalogue exists for only part of the package.
- **Proposed fix:** Generate the catalogue from the registry (proposal) and gate it with this repo's own `docs_inventory_parity`.

### ARCH-12 (Low) -- Docstring describes the package as four scripts (black_filtered_apply, format_warn, ban...

**Disposition:** OPEN

- **Original id:** A12
- **Where:** src/py_ci_shared/__init__.py:1-8
- **Finding:** Docstring describes the package as four scripts (black_filtered_apply, format_warn, bandit_warn, vulture_warn); it now holds ~100 gates. No `__all__`, no public API surface, no stability marker. Private modules (`_toml_compat`, `_mutation_worker`) are the only underscore ones; the rest (including helpers such as `git_changed_lines`) are implicitly public.
- **Proposed fix:** Rewrite docstring; declare which modules are public API; move helpers under `py_ci_shared._core`.

### ARCH-13 (Low) -- Stale build/lib (22 modules, vs 112 in src) and build/bdist.win-amd64 exist on disk

**Disposition:** OPEN

- **Original id:** A13
- **Where:** build/
- **Finding:** Stale `build/lib` (22 modules, vs 112 in src) and `build/bdist.win-amd64` exist on disk. Not tracked (`git ls-files build` = 0) and gitignored (.gitignore:7). Harmless for git, but gates that `rglob` from repo root without skipping `build` (e.g. `import_layering.py:111`, `phantom_code_references.py:205`, `stale_comment_age.py:141`, `sqlalchemy_text_binds.py:51`) would scan stale copies when self-applied locally.
- **Proposed fix:** Delete locally; fix enumeration via the shared corpus (B1).

### ARCH-14 (High) -- Corpus enumeration is inconsistent

**Disposition:** OPEN

- **Original id:** B1
- **Where:** 39 modules using `rglob`, 2 `os.walk`, 4 `git ls-files`
- **Finding:** Corpus enumeration is inconsistent. Most gates walk the filesystem, so a local run sees untracked files, venvs, `build/`, worktrees under `.claude/`, while CI sees a clean checkout: gates disagree between local and CI, and between each other. Two modules already document having been bitten (doc_identifier_parity.py:78 861 MB dump; phantom_markdown_links.py:49 venv; effect_assertion_parity.py:454 agent worktrees) and each fixed it locally.
- **Proposed fix:** Single `_corpus.iter_files(repo_root, suffixes, roots)` built on `git ls-files --cached --others --exclude-standard -z` with one fallback skip set; every gate takes files from it.

### ARCH-15 (High) -- Seven divergent skip-dir sets

**Disposition:** OPEN

- **Original id:** B2
- **Where:** 7 modules (db_transaction_completeness.py:84, effect_assertion_parity.py:59, llm_call_archive_gate.py:46, naive_utcnow.py:41, prompt_field_parity.py:76, save_failure_markers.py:34, value_bearing_asserts.py:36)
- **Finding:** Seven divergent skip-dir sets. `prompt_field_parity` and `save_failure_markers` do not skip build/dist/.tox/.claude; `llm_call_archive_gate` does not skip caches; `prompt_field_parity.py:86` matches `_SKIP_DIRS` against absolute `f.parts` so a repo checked out under a dir named `build` or `venv` is skipped entirely.
- **Proposed fix:** Replace with the shared corpus; if a fallback set is kept, one constant matched on repo-relative parts.

### ARCH-16 (Med) -- Every gate re-reads and re-parses the same files; a consumer running 40 gates in one py...

**Disposition:** OPEN

- **Original id:** B3
- **Where:** 46 modules, 75 `ast.parse`
- **Finding:** Every gate re-reads and re-parses the same files; a consumer running 40 gates in one pytest session parses the package ~40 times. No cache.
- **Proposed fix:** `_ast_cache.parse(path) -> ParsedFile(text, tree, lines)` with `functools.lru_cache` keyed on (path, mtime_ns, size).

### ARCH-17 (Med) -- Unparsable files are silently dropped (continue/return []/return set()), so a file with...

**Disposition:** OPEN

- **Original id:** B4
- **Where:** 42 modules with `except SyntaxError`
- **Finding:** Unparsable files are silently dropped (`continue`/`return []`/`return set()`), so a file with a syntax error (or Python-version-specific syntax parsed under an older interpreter) passes every AST gate vacuously.
- **Proposed fix:** Central parse returns an error record; the runner reports unparsable files as findings (or fails once) rather than each gate skipping.

### ARCH-18 (Low) -- read_text() without encoding=: locale-dependent on Windows (cp1251 here).

**Disposition:** OPEN

- **Original id:** B5
- **Where:** install_safe_hook.py
- **Finding:** `read_text()` without `encoding=`: locale-dependent on Windows (cp1251 here).
- **Proposed fix:** `encoding="utf-8"`; add a self-gate banning bare read_text/open.

### ARCH-19 (Med) -- CLI conventions differ: argparse in 4 (baseline_trend, embedded_postgres, pinned_tool_v...

**Disposition:** OPEN

- **Original id:** C1
- **Where:** 13 CLIs (see table above)
- **Finding:** CLI conventions differ: argparse in 4 (baseline_trend, embedded_postgres, pinned_tool_versions, teeth_sweep, worktree_hygiene); `main(argv)` in 6; `advisory_warn.py:20`, `bandit_warn.py:22` read `sys.argv` at import time (module-level side effect); `black_filtered_apply.py:267` raises `SystemExit("<message>")` (exit 1 with text) while others `return 1`; `teeth_sweep` uses exit 2; `format_warn` always `sys.exit(0)`. Some use `sys.exit(main())`, others `raise SystemExit(main())`.
- **Proposed fix:** One convention: `def main(argv: Sequence[str]

### ARCH-20 (Med) -- No common signature

**Disposition:** OPEN

- **Original id:** C2
- **Where:** 83 `assert_*` functions
- **Finding:** No common signature. Arguments for the same concept are spelled `files=`, `root=`, `repo_root=`, `package_root=`, `tests_dir=`, `src_dir=`, `versions_dir=`; allow-lists are `allowed: Mapping`, `exempt_jobs: frozenset`, `reviewed_advisory_steps: set`, baselines are `baseline=`, `baseline_path=`; `min_files` exists on some (population canary) and not others.
- **Proposed fix:** Gate protocol (proposal) with uniform `(ctx: RepoContext, cfg: GateConfig) -> list[Finding]`; keep `assert_*` as thin wrappers.

### ARCH-21 (Med) -- Refresh mechanism duplicated six times, with 10 different flags; detection via REFRESH_...

**Disposition:** OPEN

- **Original id:** C3
- **Where:** 6 `register_refresh_option` copies (code_audit_meta.py:63, content_hash_version_bump_gate.py:58, loc_budget.py:56, mutation_teeth.py:1906, readme_env_var_parity.py:241, uncalled_functions.py:56); code_audit_meta.py:89-101, content_hash_version_bump_gate.py:80
- **Finding:** Refresh mechanism duplicated six times, with 10 different flags; detection via `REFRESH_FLAG in sys.argv` which code_audit_meta documents breaks under xdist workers and so has a second path; other baselined gates (audit_wave_filenames, function_length, ignore_ratchet, import_side_effects, value_bearing_asserts, deferred_drift, source_text_claims) use a `refresh: bool` kwarg or an env var instead.
- **Proposed fix:** One pytest plugin (`py_ci_shared.pytest_plugin`, entry point `pytest11`) with `--py-ci-refresh=<gate>[,<gate>]`/`all` and a `PY_CI_SHARED_REFRESH` env var for xdist.

### ARCH-22 (Med) -- Baseline formats and ratchet semantics differ: json vs orjson (orjson a hard dependency...

**Disposition:** OPEN

- **Original id:** C4
- **Where:** ~12 baseline writers (audit_wave_filenames.py:66, code_audit_meta.py:204, content_hash_version_bump_gate.py:131, deferred_drift.py:61, function_length.py:68, ignore_ratchet.py:102, import_side_effects.py:125, loc_budget.py:103, mutation_teeth.py:1901, readme_env_var_parity.py:226, source_text_claims.py:329, uncalled_functions.py:194, value_bearing_asserts.py:106)
- **Finding:** Baseline formats and ratchet semantics differ: json vs orjson (orjson a hard dependency only because of this), sorted list vs dict vs `{version, content_hash}`, `sort_keys` in 5 of them only (dict baselines without it are not byte-stable), trailing newline sometimes, non-atomic `write_text` everywhere except mutation_teeth (staging file). `baseline_ratchet.py` exists as a generic ratchet but most gates do not use it. No schema/version field, so baseline_hygiene/baseline_trend must special-case shapes.
- **Proposed fix:** One `_baseline` module: `{"schema": 1, "gate": ..., "entries": {key: count

### ARCH-23 (Med) -- No per-repo configuration surface ([tool.py_ci_shared] appears nowhere)

**Disposition:** OPEN

- **Original id:** C5
- **Where:** whole package
- **Finding:** No per-repo configuration surface (`[tool.py_ci_shared]` appears nowhere). Paths, allow-lists, thresholds and baselines live as kwargs scattered across each consumer's meta-test files, so the same knob is re-declared in mlframe, pyutilz, glossum etc. and cannot be read by a CLI or pre-commit hook.
- **Proposed fix:** `[tool.py_ci_shared]` table with `[tool.py_ci_shared.gates.<name>]` sections, loaded once via `_toml_compat`.

### ARCH-24 (Low) -- Message quality varies: some gates print file:line plus fix (pinned_tool_versions, per...

**Disposition:** OPEN

- **Original id:** C6
- **Where:** failure messages
- **Finding:** Message quality varies: some gates print file:line plus fix (pinned_tool_versions, per README:486), others raise bare `AssertionError` with a set repr; `fail_message_quality` exists as a gate for consumers but is not self-applied.
- **Proposed fix:** Common `Finding(path, line, gate, message, fix_hint)` and one renderer; self-apply fail_message_quality.

### ARCH-25 (Med) -- Dogfooding is partial: self-ci runs pytest, ruff, mypy and the reusable workflows, but...

**Disposition:** OPEN

- **Original id:** D1
- **Where:** self-ci.yml
- **Finding:** Dogfooding is partial: self-ci runs pytest, ruff, mypy and the reusable workflows, but not this repo's own content gates (repo_hygiene, version_consistency, version_tag_currency, docs_inventory_parity, uncalled_functions, private_imports, function_length, fail_message_quality, phantom_markdown_links over README/WRITING_TESTS). A1, A11, B5 and C6 would each be caught. `tests/` has only `test_code_audit_meta.py` in the meta category.
- **Proposed fix:** `py-ci-shared run-all` on itself in self-ci with a checked-in `[tool.py_ci_shared]`.

### ARCH-26 (Low) -- T201 ignores are listed per file (16 entries) and must be maintained by hand as CLIs ar...

**Disposition:** OPEN

- **Original id:** D2
- **Where:** pyproject.toml per-file-ignores
- **Finding:** T201 ignores are listed per file (16 entries) and must be maintained by hand as CLIs are added.
- **Proposed fix:** Put CLIs under `py_ci_shared/cli/` and ignore T201 by glob, or route output through one reporter module.

### ARCH-27 (Low) -- orjson is a hard runtime dependency used only for baseline I/O in 5 modules, where stdl...

**Disposition:** OPEN

- **Original id:** D3
- **Where:** pyproject.toml deps
- **Finding:** orjson is a hard runtime dependency used only for baseline I/O in 5 modules, where stdlib json is used in the others.
- **Proposed fix:** Standardise on stdlib json in `_baseline` (baselines are small) and drop orjson, or use orjson everywhere through `_baseline`.

### ARCH-28 (Low) -- ruff-base.toml/ruff-tests.toml are not package data (pyproject comment), so every consu...

**Disposition:** OPEN

- **Original id:** D4
- **Where:** configs/
- **Finding:** `ruff-base.toml`/`ruff-tests.toml` are not package data (pyproject comment), so every consumer needs a second checkout plus the `PY_CI_SHARED_DIR` env var; this is what causes A4.
- **Proposed fix:** Ship as package data and add `py-ci-shared config-path ruff-base` to print the installed path; workflows export `PY_CI_SHARED_DIR` from it.

### ARCH-29 (Info) -- Convention document, not an API reference; claims are measurement-based (realtime_appli...

**Disposition:** OPEN

- **Original id:** D5
- **Where:** WRITING_TESTS.md
- **Finding:** Convention document, not an API reference; claims are measurement-based (realtime_applications sweep). No drift against code found; it is not linked from README's gate list for mutation_teeth/teeth_sweep.
- **Proposed fix:** Link from README.

### ARCH-30 (Info) -- Only module over the 1k LOC limit used elsewhere in these repos.

**Disposition:** OPEN

- **Original id:** D6
- **Where:** mutation_teeth.py (2,099 LOC)
- **Finding:** Only module over the 1k LOC limit used elsewhere in these repos.
- **Proposed fix:** Split (worker protocol, mutant generation, survivor baseline).
