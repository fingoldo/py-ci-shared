# py-ci-shared audit 2026-09-24: summary

| report | findings | high |
|---|---|---|
| scanners_a_to_l.md | 211 | 20 |
| scanners_m_to_z.md | 187 (MP 45, RS 58, TZ 46, MT/W/TS 38) | 30 |
| architecture.md | 26 | 7 |
| adoption.md | 30 | see report |
| test_suite.md | 28 | 5 |
| new_meta_tests.md | 24 gates + 6 infra proposals | P0: 3 |

Caveat: some a..l line numbers were corrected by arithmetic, not re-grep; re-check before editing.

## Systemic defects (fix once, centrally)

1. BOM / parse failure = silent skip in ~40 AST gates. Shared `read_source()` (utf-8-sig) + unparsed files are findings.
2. Fail-open on empty corpus: floors count inputs, not parsed files; missing dirs pass.
3. Missing baseline seeds and skips (≥8 gates). Seed only under refresh; fail otherwise.
4. Refresh via `sys.argv` (ignored under xdist). One pytest option.
5. Corpus enumeration differs (rglob / os.walk / git ls-files; 7 skip lists; absolute-path matching skips whole checkouts under `build/`).
6. Alias blindness: `from x import y as z`, `mock.patch`, `import a as b` missed in most gates.
7. Per-line/multiset ratchet keys collapse duplicates (value_bearing, source_text_claims, marker_runner_coverage).

## Release / adoption

- version 0.1.0 in pyproject vs tags v1.16.1; `v1` 20 commits behind master; mixed SHA pins in mlframe.
- Reusable workflows fetch configs from master regardless of pin.
- `importorskip` in ~20 consumer files turns a missing gate into a skip; autopsia CI cannot start; dash_app_core undeclared dep.
- Repo dogfoods ~5 of its own gates; no Python 3.9/3.10 CI despite `>=3.9`.

## Recommended order

1. `_core/`: `read_source`, `iter_corpus` (git ls-files), parse cache, `Baseline` (atomic, sorted, fail-when-missing, reject NEEDS-JUSTIFICATION), `Finding`. Migrate gates onto it: this closes items 1-4 in one sweep.
2. Safety-critical point fixes: sql_verify commit (RS-40), worktree_hygiene REMOVABLE (TZ-1..3), mutation_teeth MT-1/2/7/8, teeth_sweep TS-1.
3. pytest11 plugin + `[tool.py_ci_shared]` config + `py-ci-shared run-all`; gate registry with README/test/entry-point parity.
4. Gate-teeth self-check: every gate ships a seeded violation, a clean control, a BOM variant and an unparsable-file variant; run over a canary corpus.
5. Release workflow: version from tag, auto-move `v1`, configs from own ref; CI matrix 3.9-3.13; dogfood run-all.
6. New gates by priority from new_meta_tests.md (import_cycles, swallowed_exceptions, pickle_state_completeness, test_resource_leaks plugin, atomic_write_staging, hash_key_determinism, sentinel_or_fallback, no_xfail_to_defer, timing_assertions ...), retiring consumers' local copies.
7. Consumers: replace importorskip with hard import, unify pins, wire audit_round_format everywhere.
