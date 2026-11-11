# Dimension 43: registry and documentation parity (round 2026-11-11)

Read-only audit. Nothing under `src/`, `tests/` or the docs was edited. Read first: `audits/README.md`, `audits/TRACKER.md`,
`audits/2026-11-10/43_registry_docs_parity.md`.

Version under audit: pyproject and `__version__` 1.23.10; CHANGELOG `## Unreleased (next: 1.23.10)`; last tag v1.23.9.

## Method

Scratch scripts in `C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\a43\`
(`chk.py`, `gen.md`), run with `python -I`. Note: `-I` ignores `PYTHONPATH`, so the catalogue was regenerated with
`sys.path.insert(0, <checkout>/src)` before calling `registry.render_catalogue()`. A first attempt that imported the installed
package produced a false 14-row mismatch; that was the audit's own error and is not a finding.

## Checked and in parity

- Registry: 256 entries, no duplicates. Public modules on disk and registry entries match exactly (no unregistered public
  module, no registry entry without a module). `INTERNAL_MODULES` (`src/py_ci_shared/registry.py`) lists the underscore modules
  and `cli`, `registry`, `pytest_plugin`, `meta_test_utils`, none of which is registered.
- Kinds: every `kind` is `gate`, `cli` or `library`. No `kind = "cli"` without `cli = true`. Every `cli = true` module defines
  `def main` and a `__main__` guard. Every `gate` has `entries`; no `library` has `entries`.
- Every registered entry function exists in its module. Every registered tests path exists.
- `since`: no value above the package version 1.23.10. The eleven `since` values listed in the 2026-11-10 round are not re-derived.
- README gate catalogue (`README.md:141-402`): regenerated with `registry.render_catalogue()` and compared line by line: 0 diff lines.
- Tags v1.23.0 through v1.23.9 each have a CHANGELOG heading. Headings 1.23.1 and 1.23.10 have no tag, which is expected
  (1.23.1 is a heading without a tag; 1.23.10 is unreleased).
- Every module with `since = "1.23.10"` or `"1.23.9"` is named in the matching CHANGELOG section, except one (43-F9 below).
- Doc path references in README.md, CLAUDE.md, WRITING_TESTS.md, audits/README.md, audits/TRACKER.md, backticked
  `src/`, `tests/`, `configs/`, `audits/` paths: all resolve, apart from the items noted below.
- Bare workflow file names (`ci.yml`, `consumer-pins.yml`, ...) all exist in `.github/workflows/`. `configs/consumers.toml`,
  `DISPOSITION.md`, `conftest.py` and `tests/test_meta/...` in README and CHANGELOG are consumer-repository paths or examples,
  not references to this repository, and are not findings.
- CHANGELOG historical references to `tool/...`, `pubspec.yaml`, `tests/test_architecture_audit_20260903.py`, and
  `2026-11-05/gate_false_positives.md` name files of other repositories or of removed history. The header policy covers this,
  so they are not reported (same reasoning as 2026-11-10 43-F2).
- `py-ci-shared` CLI commands named in README (`list [--markdown]`, `tool <name>`, `run`, `run-all`, `config-path`, `version`,
  `new-gate`) exist in `src/py_ci_shared/cli.py`.
- The registry docstring's `kind` and `cli` definitions match the README sentence at `README.md:135-138`.

## Already tracked

- **already tracked: 43-F1** (2026-11-10, RESOLVED): `closed_audit_rounds`, `commit_metadata`, `consumer_import_census`,
  `launch_dtype_audit`, `workflow_runner_labels` are `kind = "gate"` with `cli = true`; the rule is written down. Still true, no
  new defect. The proposed extra test assertion was not checked.
- **already tracked: 43-F2** (2026-11-10, WON'T FIX): missing 1.22.1 and 1.22.6 headings. The heading check now finds
  headings for all tags from v1.23.0; the earlier-version gap is unchanged. Disposition conflict: see 43-F10.
- **already tracked: 43-F3** (2026-11-10, RESOLVED): CLAUDE.md audit path. Not re-reported.
- **already tracked: 43-F4** (2026-11-10, OPEN): still present. `src/py_ci_shared/code_audit/skip_masking_except.py:55` and
  `src/py_ci_shared/code_audit/uncurated_star_export.py:76` cite `audits/full_audit_2026-07-21/`, and
  `src/py_ci_shared/repo_hygiene.py:40-46` cites `audits/2026-09-01-full-audit/` (not in this repository).

## New findings

## 43-S9 (P2) `adoption_sha_ref` is registered, shipped in a CI job and documented in the README, but has no CHANGELOG entry

- Evidence: `src/py_ci_shared/registry.toml` entry `adoption_sha_ref` (`since = "1.23.10"`, `kind = "cli"`, `cli = true`);
  `README.md:146` catalogue row; `.github/workflows/consumer-pins.yml:69` runs
  `python -m py_ci_shared.adoption_sha_ref --repos-file ... --resolve-in .`. `CHANGELOG.md` `## Unreleased (next: 1.23.10)`
  (lines 5 to about 97) has no mention of `adoption_sha_ref` (grep for the name finds nothing in CHANGELOG.md).
  Every other module with `since = "1.23.10"` is named in the section; this one is not.
- Mismatch: CLAUDE.md requires a CHANGELOG entry for a shipped module, and a new step in a shipped reusable workflow changes
  what consumers' pinned runs do. The module's only description is the README row and its docstring.
- Failure scenario: a consumer reading the release notes for 1.23.10 does not learn that `consumer-pins.yml` now reports
  SHA pins without `py-ci-shared-ref`, or what to do about it.
- Proposed disposition: OPEN. Add an Unreleased CHANGELOG bullet "**New** `adoption_sha_ref` (CLI, report only)" with a
  "What to do:" clause (add `py-ci-shared-ref: <the same SHA>` to a between-release pin, or accept the report). Then the
  registry, README and CHANGELOG agree for 1.23.10.
- AST/grep-checkable: True; FP risk: low. Verified by hand (grep, and the Unreleased section read).

## 43-S10 (P3) The 2026-11-10 round file and TRACKER.md disagree on the disposition of 43-F2

- Evidence: `audits/2026-11-10/43_registry_docs_parity.md`, heading `## 43-F2`, Disposition: "WON'T FIX (the file's own header
  states the milestone policy ...)". `audits/TRACKER.md:22`: "RESOLVED: both headings added with 'No notes recorded for this
  release.' (owner decision (б))". The headings do exist: `CHANGELOG.md` has `## 1.22.1`/`1.22.6` only if added; the
  heading grep in this round found no 1.22.1 or 1.22.6 heading at all. The tracker's RESOLVED claim is therefore also not
  true in the CHANGELOG as checked here: `grep -n "^## 1.22.1\|^## 1.22.6" CHANGELOG.md` returns nothing.
- Mismatch: two records of one finding disagree, and the tracker claims a change the CHANGELOG does not contain.
- Failure scenario: the next round reads the tracker, sees 43-F2 closed, and does not check; or it reads the round file and
  re-opens it.
- Proposed disposition: OPEN. Either add the two headings with the stated text (tracker is right), or change the tracker row
  to WON'T FIX matching the round file (round file is right). The owner's decision (б) is cited by the tracker and should
  decide.
- AST/grep-checkable: True; FP risk: low. Verified by grep (no P0/P1, so no further check was required).

## Summary

| Severity | New | Already tracked |
|---|---|---|
| P0 | 0 | 0 |
| P1 | 0 | 0 |
| P2 | 1 (43-F9) | 0 |
| P3 | 1 (43-F10) | 4 (43-F1 to 43-F4, 2026-11-10 round) |

The registry, the module set, the kinds, the cli flags, the `since` ceiling and the README catalogue agree with the code
and with each other. The defects are a missing CHANGELOG entry for one new module and a disposition conflict between the
tracker and a round file.
