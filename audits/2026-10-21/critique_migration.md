# Critique: code_audit consolidation plan, migration and consumer risk

Scope: read-only. Plan read: `audits/2026-10-21/code_audit_consolidation_plan.md`. Search excluded `.claude` and build dirs.

## 1. Consumers found: 4 repositories, 5 files

No file imports `pyutilz.dev.code_audit` directly except py-ci-shared. Every consumer goes through `py_ci_shared.code_audit_meta`, which imports pyutilz at call time (`src/py_ci_shared/code_audit_meta.py:172`, `from pyutilz.dev.code_audit import registry`). The plan's phase 4 ("switch imports from `pyutilz.dev.code_audit` to py-ci-shared") is therefore mostly a no-op for consumers. The real dependency is the `code_audit_meta` API and the pyutilz version each consumer installs. mlframe has no hit. VERIFIED by grep.

| # | Repo | File:line | Import | Pin |
|---|---|---|---|---|
| 1 | algopacksimple | `test_code_audit_baseline.py:28` | `from py_ci_shared.code_audit_meta import assert_no_new_code_audit_findings` | py-ci-shared: **floating** `git+...py-ci-shared.git` (`ci.yml`, install step, no SHA). pyutilz: `PYUTILZ_SHA: 1910c98...` (`ci.yml:70-71`) |
| 2 | algopacksimple | `conftest.py:34` | `from py_ci_shared.code_audit_meta import register_refresh_option` | same as 1 |
| 3 | dash_app_core | `tests/test_meta/test_code_audit_baseline.py:17` | `from py_ci_shared.code_audit_meta import assert_no_new_code_audit_findings` | `pyproject.toml:71` py-ci-shared `@fd2f017` (v1.22.13); `:72` pyutilz `@8a40c46`. No `.github/workflows` directory in this repo |
| 4 | dash_app_core | `tests/conftest.py:17` | `from py_ci_shared.code_audit_meta import register_refresh_option` | same as 3 |
| 5 | glossum_backend_scripts | `tests/test_meta/test_code_audit_baseline.py:94` | `from py_ci_shared.code_audit_meta import assert_no_new_code_audit_findings` | py-ci-shared `@fd2f017` (`pyproject.toml:211`); pyutilz unpinned in pyproject (`:143`), resolved via `uv.lock` (UNVERIFIED: lock contents not read) |
| 6 | glossum_wip | same file as 5 | same | same as 5 (sibling copy of the same repo) |

All import lines are VERIFIED by Grep and Read. The pins are VERIFIED.

Other facts, VERIFIED:
- py-ci-shared's own `pyproject.toml:54` pins pyutilz `@5372fee`, and `.github/actions/install-pyutilz/action.yml:21` defaults to `5372fee`. These are consistent with each other.
- pyutilz `1910c98` (algopacksimple's CI pin) has no `code_audit/registry.py`. `git grep` finds `run_all_checked` only in `__init__.py` and `dead_wiring.py` at 1910c98 and 8a40c46, and in `registry.py` at 5372fee. The `code_audit_meta.py:181-186` fallback handles this: it emits a warning and runs `registry.run_all`, so a scanner that raises is silently counted as clean.
- algopacksimple CI runs `test_code_audit_baseline.py` by explicit name (`ci.yml:104`), so the meta-test does run there.
- dash_app_core has no CI workflow in this checkout. Its gate runs only where its tests are run locally or by a pre-commit hook.

## 2. Phase-by-phase effect on each consumer

- **Phase 2 (port into py-ci-shared).** No consumer changes. But the new `py_ci_shared/audit/` package must keep the public names `assert_no_new_code_audit_findings` and `register_refresh_option` importable from `code_audit_meta`. The plan says "the wrapper becomes a call into the new package". That is correct, but the plan must state that the old names are a frozen API.
- **Phase 3 (pyutilz shim).** This is where consumers break. Under the plan, pyutilz `dev/code_audit` re-exports names that now live in py-ci-shared, and pyutilz depends on py-ci-shared. This creates a **cycle**: py-ci-shared's `code_audit_meta` imports pyutilz, and pyutilz's shim imports py-ci-shared. Consumers that pin pyutilz at a new SHA will pull py-ci-shared at the SHA pyutilz names, which can differ from the one the consumer pins. The plan does not address this. The pip/uv resolution order is unspecified.
  - Consumers on old pyutilz SHAs (1910c98, 8a40c46) keep their old checks. Their `code_audit_meta` still works. OK.
  - Consumers that bump pyutilz to a shim SHA get a DeprecationWarning. Dash and glossum run with warnings as errors? (UNVERIFIED: pytest `filterwarnings` not checked for these repos.)
- **Phase 4 (consumer switch).** The plan says "switch imports to py-ci-shared" but the consumers import py-ci-shared already. What actually changes is the py-ci-shared pin. Each switch is a pin bump in 3 repos, and the plan's "one at a time, CI green" rule is hard to apply to dash_app_core, which has no CI.
- **Case: consumer CI cannot reach GitHub.** algopacksimple clones pyutilz and mlframe from GitHub with `git clone` (`ci.yml:70-74`) and installs py-ci-shared from GitHub without a SHA. A GitHub outage fails the job before any test runs. This is the same as today. But after the move, the check set itself lives in py-ci-shared, so a consumer on a cached or old pyutilz SHA can no longer be checked for the new checks at all. The plan's "old SHAs keep working" is only true if the old SHA still contains the old check code, which it does by definition, so this is acceptable, but it must be stated as "stale, not failing".

## 3. Risks the plan misses or understates

1. **Floating py-ci-shared in algopacksimple (HIGH).** `ci.yml` installs `py-ci-shared` from the default branch with no SHA. A moved check that is stricter, or a rename, turns algopacksimple red without any change in that repo. Change: pin it to a SHA, and add the consumer to the consumer-pins workflow the plan relies on.
2. **Silent verdict change through the fallback (HIGH).** A pyutilz older than 5372fee (algopacksimple, 1910c98) runs `registry.run_all`, not `run_all_checked`. A scanner that crashes is reported as clean. The plan's "old and new must match" check does not cover this, because it runs both implementations on this repo, not on the consumer's pinned pyutilz. Change: a test that runs the moved check across each consumer's pinned pyutilz, or fails the consumer when the `UserWarning` fires.
3. **Two versions of one library in one consumer (MEDIUM).** The plan's shim has pyutilz depend on py-ci-shared, while consumers pin both. A consumer with py-ci-shared at fd2f017 and pyutilz at a shim SHA that pins py-ci-shared at a newer SHA gets an unresolved or split environment. Change: the shim must not import py-ci-shared at module level, and the consumer test must run `pip check` or `uv pip check` after the bump.
4. **The plan's risk section does not list the release order (MEDIUM).** Required order: (a) py-ci-shared release with the new `py_ci_shared/audit/` package, shipped with the old pyutilz still working (because py-ci-shared must not require the shim); (b) pyutilz shim release pinned to (a); (c) consumer pin bumps. If (b) is released before (a), the shim imports a missing module, and every consumer on the new pyutilz breaks at import. If (c) is done before (a), `code_audit_meta` cannot find the names. Change: write this order into the plan, with a test in py-ci-shared that imports the shim names from a pinned pyutilz.
5. **`@v1` promise (MEDIUM).** `CLAUDE.md` says a change that "can turn a green consumer red" needs a CHANGELOG entry beginning **Behaviour change**. Moving a check into py-ci-shared is additive only if the gate set and finding ids are identical. The plan says "keep the same finding ids" but does not require a canary per moved check. Change: each moved check must pass the canary suite and the old-vs-new corpus diff before the release.
6. **Baseline keys (MEDIUM).** `code_audit_meta._key` and `_legacy_key` (`code_audit_meta.py:90-141`) fingerprint findings. Moving a check can change the fingerprint if the rule text, the file path, or the snippet changes. Every consumer's baseline (`_code_audit_baseline.json`) would then show new findings and old ones "fixed". The plan says markers stay, but does not say the baseline keys stay. Change: a golden-file test that checks the key of every existing baseline entry is unchanged after the move.
7. **Markers and baselines are two systems (MEDIUM).** Consumers keep `# audit-exempt:` markers and baselines separately. The plan's mitigation ("the migration maps them, it does not convert them") has no test. Change: a test that loads each consumer's baseline and runs the moved check over it.
8. **Phase 0 inventory is missing the consumers (LOW).** The plan's open question 3 says the consumer list "is not in this repository". This critique found it: 4 repositories, 5 files. Change: add this table to the plan.
9. **Consumer tests on `glossum_wip` and `glossum_backend_scripts` are two copies of one repo (LOW).** A fix to one is not in the other. Change: note that migration must be done in both.

## 4. Verdict-change detection (question 4)

A moved check can change verdicts silently. The plan's corpus diff (section 6) catches this only on the corpora it names. A test that would catch a verdict change: a golden corpus of violation and clean files per check (the existing `tests/canary/<name>/` layout in py-ci-shared), run through the old pyutilz implementation and the new one in CI, with the expected finding list committed. Any difference fails the build. The plan does not require this; it only requires a one-time comparison.

## 5. Release sequence (question 5)

Order: py-ci-shared (new package, old names kept, pyutilz unchanged) → pyutilz shim (pinned to that py-ci-shared SHA) → consumer pin bumps, each with CI green. Wrong order breaks as in risk 4. The plan's open question 5 asks whether `@v1` is affected; the answer is "yes, if any finding id or key changes" (VERIFIED against `CLAUDE.md` Versions and releases).

## Summary

- Consumers: 4 repositories (algopacksimple, dash_app_core, glossum_backend_scripts, glossum_wip), 5 importing files, all through `py_ci_shared.code_audit_meta`.
- Biggest risks: floating py-ci-shared in algopacksimple, the silent fallback on old pyutilz, the release-order trap, and baseline key stability.
