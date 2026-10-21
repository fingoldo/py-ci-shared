# Critique of code_audit_consolidation_plan.md (option B)

Read-only review, 2026-10-21. Claims are marked VERIFIED (read in the code) or UNVERIFIED (inferred, or a grep that did not finish).

## Verdict

Only with changes. B is not wrong, but the plan treats pyutilz's detectors as a leaf that can be moved and re-exported. They are not a leaf, the dependency it proposes creates a cycle, and the release model makes each moved check a consumer-facing event. Recommended: do the single-pin fix (Phase 6) and the pair inventory (Phase 0) now, and decide B vs C after the inventory.

## Objections

1. **pyutilz's library code depends on the detector package at runtime.** The plan calls `dev/code_audit` "a thin shim" (Phase 3). VERIFIED: `pyutilz/src/pyutilz/dev/field_text_agreement.py:24` and `:190` do `from .code_audit._base import Finding` at runtime, and the module docstring (`:9-10`) says `pyutilz.dev.code_audit.field_text_agreement` must keep resolving. `pyutilz/src/pyutilz/dev/code_audit/_base.py:1-555` holds `Finding`, `walk_tree`, `is_line_exempt` and the parse cache that nearly every detector imports (e.g. `bare_except.py:12`). Moving the detectors therefore means moving or copying `_base.py` too, or pyutilz's own library modules gain a py-ci-shared import. Change: Phase 2 must name `_base.py` as a move target, and Phase 3 must say which pyutilz library modules keep working and how.

2. **The plan creates a dependency cycle it does not name.** Today py-ci-shared depends on pyutilz: `py-ci-shared/pyproject.toml` `[dev]` pins `pyutilz @ git+...@5372fee` (VERIFIED), and `tests/test_code_audit_meta.py:18` imports `py_ci_shared.code_audit_meta`, which lazily imports `pyutilz.dev.code_audit` (`code_audit_meta.py:172`, `:187`). Phase 3 adds pyutilz -> py-ci-shared. pyutilz is itself in `configs/consumers.toml` as a corpus consumer (VERIFIED), so pyutilz's CI runs `code_audit_meta` against pyutilz, which needs py-ci-shared installed from git, which needs pyutilz. Change: state the cycle, decide which side installs what in each CI, and add a test that imports both packages in a clean venv.

3. **py-ci-shared's "dependency-free" design is quietly broken by B.** VERIFIED: `pyproject.toml` comments say the package is "deliberately dependency-free" for dev-tool orchestration, with only `tomli` and `PyYAML` as runtime deps. Moving ~100 AST scanners plus `default_via_or.py` (735 lines) and `registry.py` (703 lines) makes the runtime heavier and changes what a consumer installs. Change: the plan's Section 8 says the wrapper "becomes a call into the new package"; it should also say whether the moved code stays stdlib-only.

4. **"pyutilz detectors are only used by py-ci-shared" is not established.** The plan implies the consumers go through py-ci-shared. VERIFIED: the `code_audit_meta.py` docstring (`:4-6`, `:36-40`) says seven consumer repos wire their own test that calls `pyutilz.dev.code_audit.run_all()` directly. That is a claim in a comment, not a current grep. PARTLY VERIFIED by a later grep (background task baf1diimb, sibling repos only, worktrees included): `glossum_backend_scripts/glossum/core/sanitized_fields.py`, `glossum/llm/content_hash.py`, `llm_bench/src/llm_bench/core/redaction.py`, `llm_bench/src/llm_bench/runner/round_runner.py` and `mlframe/src/mlframe/feature_selection/registry.py` reference `pyutilz` code_audit names; the `test_code_audit_baseline.py` files in algopacksimple, dash_app_core, llm_bench and mlframe run the scanner directly. So consumers use the detectors in product code, not only in tests, and not only through py-ci-shared. Also, `pyutilz/build/lib/` holds a stale copy of the package, which a move must not pick up. Change: Phase 0 must record product-code importers, not only meta-tests, before Phase 4 is scoped.

5. **Shim lifetime and `@v1` promise.** VERIFIED: `py-ci-shared/README.md:53-62` says `@v1` moves to the newest v1.x on every release and that a gate "may get STRICTER" within v1. So once a detector is a py-ci-shared gate, every pyutilz fix to it becomes a red CI for every `@v1` consumer with no change on their side. The plan's Risks section (Section 6) does not mention this. Change: decide, per moved check, whether the gate ships at a fixed tag or waits; the plan's Q5 answer ("probably not affected") is wrong for strictness fixes.

6. **Moving a check is more than a copy.** VERIFIED: `README.md:408-414` says gates must take `min_files` and `allow_unparsed`, and older gates are "being moved over one at a time" under `tests/baselines/gate_entry_contract.json`. Each ported check also needs a `registry.toml` entry with `since` (`registry.toml:2`, `:8`), a `[tool.py_ci_shared.gates.*]` entry, a canary, and a `budget_s` (README `:409`). Change: Phase 2 should be sized per check against the entry contract; "move first, improve later" will fail the contract test for each check moved.

7. **Release cost goes up during the migration, not down.** VERIFIED: `.github/workflows/release.yml:73` requires the tag to equal the version in `pyproject.toml` and `__init__.py` (`pyproject.toml:4`, `__init__.py:18`), the commit must be on master and self-ci green, and then `v1` is moved (`release.yml:6-7`). VERIFIED (`py-ci-shared/CLAUDE.md`, "Versions and releases"): a release also needs `release_guard.py prepare X.Y.Z --write`, a `since` on every new module, README and catalogue sync, and a CHANGELOG entry starting "Behaviour change" for any change that can turn a consumer red. Each migration phase is therefore a py-ci-shared release, plus a pyutilz release for the shim, plus a pin bump in each consumer. Change: count the releases per phase, batch to one py-ci-shared tag per phase, and budget the prepare step.

8. **The compatibility dance already exists and will need to survive.** VERIFIED: `code_audit_meta.py:174-186` handles pyutilz versions without `run_all_checked` and without `consumer_roots`, with a warning. Consumers pin old SHAs (the plan's own risk). Change: the plan must say which pyutilz SHAs must still work with the moved wrapper, and that the fallback is retired on purpose, not by accident.

9. **Option C is under-argued.** The plan's reason for B is that "development tooling sits there by accident". That is an opinion. The VERIFIED facts above (objection 1) show pyutilz library modules depend on the detector base, which is a reason to keep it in pyutilz. The real cost in problem 1 is the pin cycle, and Phase 6 (single pin file) fixes problem 2 without moving any code. Change: add a "C plus single pin plus shared drift test" option and compare it on measured cycle count, not on tidiness.

10. **Unverified claims in the inventory.** The table's "182 hits vs 0" claim (Section 2.3) and the duplicate pairs (Section 3) were not checked against code in this review. UNVERIFIED. Also the claim that py-ci-shared "already has every piece the checks need" is UNVERIFIED for the three detectors that need pyutilz's parse cache (`_scan_cache.py`), which has no py-ci-shared counterpart that I found.

## Minimum changes to the plan

- Add `_base.py` and the runtime importers (`field_text_agreement.py`, `meta_test_utils.py`) to the scope.
- Name the pyutilz <-> py-ci-shared cycle and say how CI breaks it.
- Run the consumer grep and record it in Phase 0.
- Answer open question 5 with the strictness rule from `README.md:53-62`.
- Size each Phase 2 port against the gate entry contract, and count the releases per phase.
- Add option C-plus to Section 4 with the same measurements as B.
