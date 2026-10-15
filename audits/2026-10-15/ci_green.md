# CI green for 1.22.12 (run 38698274157, master 78df5a2)

## Failing tests in the CI log (all jobs)

| Test | Root cause | Fix |
|---|---|---|
| test_code_audit_crashed_scanner.py::test_crashed_scanner_fails_the_gate | d727544 calls `pyutilz.dev.code_audit.registry.run_all_checked`, but the `[dev]` extra and `install-pyutilz` pinned pyutilz 3be55c1, which predates it (AttributeError). Locally an editable newer pyutilz hid this. | Pin bumped to pyutilz master ec8f2bd (has `run_all_checked`) in `pyproject.toml` and `.github/actions/install-pyutilz/action.yml` (test_release_version holds them equal). |
| test_code_audit_meta.py: TestAssertNoNewCodeAuditFindings (8: drained/missing baseline/exclude_dirs/new finding/refresh via request/refresh reseeds/round-trip without orjson/unchanged tree), TestKeysSurviveRelocation (3), TestLegacyBaselineMigration (2) | Same ImportError of `run_all_checked` from the old pinned pyutilz. | Same pin bump. Also, so a `@v1` consumer still on an older pyutilz is not turned red by an ImportError, `code_audit_meta._run_scanners` falls back to `run_all` with a UserWarning that the crash check is off (new test `test_a_pyutilz_without_run_all_checked_falls_back_with_a_warning`). The helper also keeps `assert_no_new_code_audit_findings` at its old C901 complexity. |
| test_v113_rules.py::TestDrainedCodeAuditEntries::test_a_drained_entry_fails_by_default | In CI: same ImportError. Once that was fixed, a second problem: the fixture patched `code_audit.run_all`, which the gate no longer calls. The real scanners ran on tmp_path, so the planted finding never showed up and the "drained" fail did not happen. | The test was stale, so it now follows the new contract: the fixture patches `registry.run_all_checked` and returns `(findings, {})`. There is a comment saying why. The assertions are unchanged. |
| test_mutation_harness_version_is_bumped.py::test_the_harness_version_tracks_the_harness | 641677a ("log every silent default-return handler") changed the tracked mutation harness files. I read the whole diff since the baseline commit 644d9c9: it only adds `logger = logging.getLogger(__name__)` and `logger.debug(...)` lines in except handlers. Return values and verdicts are unchanged, so `HARNESS_VERSION` does not need a bump. | Baseline refreshed: `tests/_mutation_harness_version_baseline.json` content_hash 25bbdc8a6083c8f5 -> a966d3e04c1b2fbe for version 15. Note: the failure message names `--refresh-content-hash-version-baseline`, but this repo's conftest does not register that flag (pytest rejects it). I used `PY_CI_SHARED_REFRESH=content-hash-version`. |

Also: a CHANGELOG 1.22.12 **Behaviour change** entry for d727544. The gate now fails when a scanner raises. It says what to do and gives the pyutilz version needed.

## Checks

- black 26.5.1: clean. mypy src/py_ci_shared: clean. ruff 0.16.1: 64 C901 findings, none in changed code. The complexity rise that the fallback first caused was removed by moving it into a helper.
- Targeted: the code-audit, v113, harness, release_version, pin-agreement and install tests all pass.
- Full suite (`-n 1` from `worker_budget.available_workers()`, 1h41m): **7984 passed, 1 failed**. The failure was `test_mutation_teeth_regressions.py::TestTheImportMustComeFromTheSandbox::test_end_to_end_a_shadowing_copy_is_detected`: "unmutated baseline did not finish within 120s". This was a timeout on a loaded machine. It is not in the CI log, the file is untouched, and the class passes when rerun alone (5 passed).
- `release_guard.py prepare 1.22.12 --check`: "release 1.22.12 (newest published: v1.22.10): nothing differs, ready to tag" (exit 0).
