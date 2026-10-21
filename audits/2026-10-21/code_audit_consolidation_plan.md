# Consolidating the code-audit checks: problems and migration plan (version 2)

Status: proposal, version 2, 2026-10-21. Revised after three read-only critiques
(`critique_against.md`, `critique_migration.md`, `critique_inventory.md`, in this folder). No code is changed by this document.

## 0. What changed from version 1

- Option B is no longer the recommendation on its own. The critique found that pyutilz's detectors are not a leaf:
  `pyutilz/src/pyutilz/dev/field_text_agreement.py` imports `.code_audit._base` at runtime, so "move the checks and keep a shim"
  leaves a pyutilz library module depending on a package that now depends on pyutilz. Moving them creates a dependency cycle.
- The duplicate inventory was partly wrong. Version 2 uses the corrected pairs (section 3).
- Consumers were found: four repositories import the audit through `py_ci_shared.code_audit_meta` (section 2, problem 10).
- The order changes. Phase 0 (inventory and a golden corpus) and the single-pin fix come first and need no decision. The
  choice between B and C-plus is made on the inventory's numbers, not on the opinion in version 1.

## 1. Facts

- pyutilz `src/pyutilz/dev/code_audit`: 102 detector modules, 110 check ids (`check="..."`). Some detectors are library
  code: `field_text_agreement.py` (lines 24 and 190) imports `.code_audit._base.Finding` at runtime.
- py-ci-shared: 236 modules. Gates are registered in `registry.toml` (with `since`) and `[tool.py_ci_shared.gates.*]`.
- py-ci-shared's `pyproject.toml` `[dev]` pins pyutilz by commit (`5372fee4baf0d8edaa7bb41102142b80ebb97a0f`), and the composite
  action `.github/actions/install-pyutilz/action.yml` must carry the same SHA (a test checks it).
- `code_audit_meta.py` lazily imports `pyutilz.dev.code_audit` (lines 55, 172, 187) and still handles pyutilz versions without
  `run_all_checked` (lines 174-186) with a warning.
- The `@v1` rule (`py-ci-shared/README.md` lines 53-62): a gate "may get STRICTER" within v1. So a change to a moved check is a
  consumer-visible change, even when the consumer changed nothing.

## 2. Problems we have now

1. **One fix, two repositories, two releases.** S-5, S-6, S-7 were detector bugs in pyutilz: pyutilz commit `5372fee`, then a pin
   bump in py-ci-shared, then the composite action. Each step had its own pre-push run.
2. **The same SHA lives in two files.** Pin in `pyproject.toml` and default ref in the action must agree. A pre-push test caught
   a mismatch only after a push was refused (`ec8f2bd` left in the action).
3. **Duplicate implementations with different rules.** `default_via_or` (pyutilz) reports 182 hits on py-ci-shared's own source;
   `sentinel_or_fallback` (py-ci-shared, a narrower rule on named settings) reports 0. Different questions, but no single answer
   for a consumer.
4. **Two places to read what is enforced.** "Is `bare_except` enforced?" is answered in pyutilz; "is `swallowed_exceptions`?" in
   py-ci-shared. The two docs do not reference each other.
5. **Two baseline systems.** py-ci-shared: shrink-only ratchets (`tests/baselines/*.json`, `PY_CI_SHARED_REFRESH`) and the tracker.
   pyutilz: `# audit-exempt:` markers and no shared ratchet. An accepted finding in one is open in the other.
6. **Consumers install pyutilz from git in CI.** Not on PyPI. A consumer's CI depends on GitHub and on the SHA being reachable.
7. **Test ownership is split.** Detector tests are in pyutilz (`tests/code_audit`), gate tests in py-ci-shared. A green run in one
   proves nothing about the other.
8. **Different release cadences.** py-ci-shared: tags, `@v1` for consumers, `since` per gate. pyutilz: a general library, not
   versioned against consumers' gates.
9. **Hygiene drift.** A detector can exist with no gate (and the reverse) and nothing reports it. The audit round format applies
   to py-ci-shared's reports only.
10. **Consumers that pin loosely.** Found by the migration critic (VERIFIED by grep and read):
    - `algopacksimple`: `test_code_audit_baseline.py:28` and `conftest.py:34`; CI installs py-ci-shared from the default branch with
      no SHA; pyutilz pinned to `1910c98`. A change to a moved check can turn it red with no change in it.
    - `dash_app_core`: `tests/test_meta/test_code_audit_baseline.py:17`, `tests/conftest.py:17`; py-ci-shared `fd2f017`, pyutilz
      `8a40c46`.
    - `glossum_backend_scripts`: `tests/test_meta/test_code_audit_baseline.py:94`; py-ci-shared `fd2f017`; pyutilz resolved through
      `uv.lock` (not read).
    - `glossum_wip`: a sibling copy of the glossum test (not separately audited).
    - Four repositories found; the code_audit_meta docstring says seven, which is not confirmed by a grep and must be checked.

## 3. Duplicate inventory (corrected by the inventory critique)

SAME rule (the shared gate is a superset):
- `import_cycle` (pyutilz) / `import_cycles` (py-ci-shared). Py-ci-shared adds package-facade cycles and order-dependent from-imports.
- `source_text_assertion` (pyutilz) / `source_text_claims` (py-ci-shared). pyutilz's own docstring says the shared gate is a superset.
- `reexport_patch_target` (pyutilz) / `patch_target_read_at_call` (py-ci-shared).

OVERLAPPING (different cases, same family):
- `vacuous_loop_assertion` (pyutilz) / `vacuous_loop_assertions` (py-ci-shared): the two disagree on what counts as a floor (per pyutilz's docstring).
- `swallowed_exceptions` (py-ci-shared) / `bare_except`, `raising_stub_swallowed` (pyutilz): production code only vs tests and scripts too.
- `sentinel_or_fallback` (py-ci-shared) / `default_via_or` (pyutilz): named settings vs general shape. Not the same rule.

UNRELATED (the first version was wrong to pair them):
- `sentinel_cached_as_answer`, `sentinel_guard_mismatch` (pyutilz) are not `sentinel_or_fallback`.
- `default_via_or` is not `config_getattr_default_parity`.
- No SAME pair among the resource-release rows.
- `undeclared_import` is declared the counterpart of `unresolved_imports`, but the rules are disjoint.

Corrections to names: `stale_source_citation` is `stale_source_citations.py` in py-ci-shared.

pyutilz-only (no counterpart in py-ci-shared; would need a port): `retry_loops` (no retry gate exists), `tautological_guard`,
`vacuous_assertions`, `vacuous_matching`, `unraised_exceptions`, `credential_logging`, `duplicate_credential_regex`, the `sql_lint`
shapes. About 20 more pyutilz modules were not read; some may have a counterpart under another name.

Self-declared pyutilz counterparts missing from version 1: `additive_epsilon_denominator`, `comment_names_missing_symbol`,
`duplicate_function_body`, `nondiscriminating_test`, and five others listed in `critique_inventory.md`.

## 4. Options

- **A. Keep both, write one owner per check id.** Smallest. Does not remove duplicates or the release cost.
- **B. Move the checks into py-ci-shared; pyutilz re-exports.** Single owner, removes the cross-repo fix cycle. Problems: the
  dependency cycle (pyutilz library code needs the detector base), a heavier runtime for py-ci-shared (its pyproject says it is
  dependency-free beyond `tomli` and `PyYAML`), and every moved fix becomes a consumer-visible `@v1` change.
- **C. Checks stay in pyutilz; py-ci-shared holds only the gate wrappers, ratchets and tracker.** pyutilz owns the logic and its
  own library code keeps working. Problems: every check fix still crosses repositories (S-5..S-7 again).
- **C-plus. C, plus the cheap fixes.** Single pin file read by both sides (problem 2). One ratchet per check id, held by py-ci-shared,
  reading pyutilz's findings (problems 5 and 9). A documented owner per pair in the inventory (problem 4). No code moves.
  Duplicates that are SAME rule are removed from py-ci-shared (pointing at pyutilz), since they cannot disagree then.
- **D. A third package for the detector base** that both depend on, with no cycle. Clean, one more release to manage; worth it if
  more repositories need the base. Decide after Phase 0.

**Recommendation now:** do Phase 0 and Phase 6 (single pin file). Both need no decision about the architecture. Then choose between
C-plus and B, or D, on the inventory's numbers. My current preference is C-plus, because it removes the two costs that hurt most today
(problem 2 and the duplicate rules) without moving code that pyutilz's own library needs, and it avoids a consumer-visible change
across `@v1`. B stays on the table if the inventory shows most pyutilz detectors have no consumer outside py-ci-shared.

## 5. Phases

Phase 0. Inventory and golden corpus (no code change).
- Complete the pair table (section 3) with rule, scope, markers, and canary status for every check id on both sides.
- Consumer grep: every repository on this machine that imports `pyutilz.dev.code_audit` or `code_audit_meta`, with how it pins.
  The docstring claim of seven consumers is checked here.
- Golden corpus: for every check that might move, the findings on this repository, pyutilz, and at least one consumer, recorded as
  a file. Any later change must reproduce the file or explain each difference.
- Record which pyutilz SHAs must keep working with the wrapper (the fallback at `code_audit_meta.py:174-186` is retired on purpose).

Phase 1. Single pin (problem 2). One file holds the pyutilz SHA; `pyproject.toml` and the composite action read it, or a test fails
on any difference. Release: one py-ci-shared patch.

Phase 2. Duplicates. For each SAME pair, remove the py-ci-shared copy and point its gate at pyutilz (C-plus) or move the single
implementation (B). Release: one py-ci-shared minor, with a `Behaviour change` entry if any finding id or verdict moves.

Phase 3. Ratchets. One ratchet per check id in py-ci-shared over pyutilz's findings, shrink-only. Release: one py-ci-shared minor.

Phase 4. Consumers. Pin the loose consumers (algopacksimple first: it floats py-ci-shared) to a SHA; then move them one at a time,
each with its own CI green. Release count: none in py-ci-shared; the consumers' own pins.

Phase 5 (B only, if chosen). Port the OVERLAPPING and pyutilz-only checks, with the dependency cycle broken first (phase D). Each port is
a gate entry with `since`, a canary, `min_files`/`allow_unparsed` and a `budget_s`, and counts as a stricter gate under `@v1`.

Phase 6. Remove shims and duplicates that no consumer imports, in the next major.

## 6. Risks

- **Silent verdict changes.** Mitigation: golden corpus (phase 0) and the old/new comparison in CI, not a one-time check.
- **Baseline key stability.** Moving a check can change finding keys; every consumer baseline would show spurious new and fixed findings.
  Mitigation: keys are frozen by the golden corpus; a key change needs a `Behaviour change` entry.
- **`@v1` strictness.** A pyutilz fix to a check that is a py-ci-shared gate turns every `@v1` consumer red. Mitigation: decide per check
  whether it is a gate (strict) or a diagnostic (advisory) before moving it.
- **Release order.** The shim or wrapper must not import the other side at module level; py-ci-shared ships before the consumer pins
  move. Getting this wrong breaks imports.
- **Dependency weight.** Moving about 100 AST scanners into py-ci-shared adds weight to a package that is dependency-light by design.
- **Scope creep.** Mitigation: one check per change.

## 7. Open decisions (for the owner)

1. Accept the recommendation (Phase 0 and 1 now; C-plus unless the inventory argues for B or D)?
2. Is `algopacksimple` allowed to change its pin (it floats py-ci-shared today)? Its CI will change.
3. Which consumers are in scope? (Four are found; the docstring claims seven. The list must be confirmed.)
4. The `@v1` question: is a stricter gate acceptable within v1, or do strict ports wait for v2?
5. Shim lifetime, if B is chosen: one minor cycle, or until the next major.

## 8. Not covered

- The mutation harness and its content hash stay in py-ci-shared.
- The `code_audit_meta` wrapper becomes a thin call on whichever side is chosen.
- Scanner performance is measured after the decision, on one tree.

## 9. Decision (2026-10-21)

**C-plus.** Checks stay in pyutilz with the logic their library code needs; py-ci-shared holds the gate wrappers, the
shrink-only ratchets and the tracker; the rules that exist in both repositories are removed from py-ci-shared and
delegate to pyutilz or to the one py-ci-shared implementation named in the registry counterpart table.

Why, against the open decisions in section 7:

- Option B needs most pyutilz detectors to have no consumer outside py-ci-shared. The inventory shows the opposite:
  at least six repositories run `code_audit` (mlframe, autopsia, glossum_backend_scripts, llm_bench, dash_app_core,
  algopacksimple), so moving the logic would turn every fix into a consumer-visible `@v1` change.
- Option D is worth a third package only once more repositories need the base. None beyond these needs it yet.

Consequences recorded here so they are not forgotten:

- `source_text`: the ban rule and the claims rule are two implementations of one practice, measured in
  `audits/2026-10-21/source_text_consolidation.md`. Consolidation onto the claims detector with a generated-file exemption
  is the next change; it changes a blocking gate, so it lands with a CHANGELOG entry and the before/after counts.
- Consumer pins: the three repositories that gained `code_audit` today are pinned at pyutilz 7f4c767. The others keep
  their pins until each one's own rebuild, so one repository's baseline is never refreshed against another's pin.
- Section 7 question 1 is answered by this decision. Questions 2 to 5 stay open for the owner.
