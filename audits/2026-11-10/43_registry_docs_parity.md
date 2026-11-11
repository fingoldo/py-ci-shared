# Dimension 43: registry and docs parity

Read-only audit of `src/py_ci_shared/registry.toml`, the README gate catalogue, `CHANGELOG.md`, the `__all__` lists of
the public modules, and the module docstrings, against the modules on disk. Version under audit: pyproject 1.23.10
(Unreleased in CHANGELOG, next: 1.23.10). `audits/TRACKER.md` and `audits/2026-11-07/43_registry_docs_parity.md` were
read first; the eight findings of 2026-11-07 (43-F1 to 43-F8) are all RESOLVED there and are not re-reported here.

Method: scratch scripts in the session scratchpad (`a43/chk.py`, `chk2.py`, `chk3.py`, `chk4.py`), run with `python -I`.
They parsed `registry.toml` with `tomllib`, walked each module with `ast`, parsed the README gate-catalogue rows, compared
every row with its registry entry, and listed the git tags against the CHANGELOG headings. Nothing under `src/`, `tests/`
or the docs was edited.

## What was checked and found in parity

- 258 public modules on disk; 254 registry entries; the four unregistered (`cli`, `meta_test_utils`, `pytest_plugin`,
  `registry`) are all listed in `INTERNAL_MODULES` (`src/py_ci_shared/registry.py:74-102`), as the registry docstring requires.
- No registry entry without a module, no duplicate names.
- Every `entries` name is defined in its module; every `__all__` name in every module resolves to a definition or an import
  (the first pass flagged 36 names; all were a checker artefact of annotated assignments and were confirmed by hand).
- Every `cli = true` module defines `main` and a `__main__` guard. Every registered `tests/test_<name>.py` exists or its
  explicit `tests` path exists.
- README gate catalogue: 254 rows, one per registry entry, with matching `kind`, `since`, entry and summary text.
- CHANGELOG: every tag from v1.23.0 has a heading (CLAUDE.md requires this from 1.23.0); the Unreleased heading names
  1.23.10, the next version after the pyproject 1.23.10; every module with `since = "1.23.9"` or `"1.23.10"` is named in the
  matching CHANGELOG section.
- Docstrings: every backticked snake_case function name in a module docstring that the scan could resolve exists in the
  tree; the file names that do not resolve are fixtures inside gate docstrings (for example `pre-commit-config.yaml`,
  `tests/test_zz_resolution_probe.py` in a generated-file string) or consumer-repo paths, and are not stale references.
- `since` values: the `since` ordering test (`tests/test_registry_since.py`) covers the tag checks; this audit did not
  re-derive them.

## Findings

## 43-R1 (P3) Five modules are `kind = "gate"` and `cli = true`; the registry, README and docstrings define `kind = "cli"` as the command-line tool

- Severity: P3 (documentation ambiguity; the catalogue under-reports these tools as command-line tools).
- Evidence: `src/py_ci_shared/registry.toml:239-243` (closed_audit_rounds: `kind = "gate"`, `cli = true`), and the same
  pairing at lines 262/266 (commit_metadata), 341/345 (consumer_import_census), 844/848 (launch_dtype_audit),
  1765/1769 (workflow_runner_labels). Each module defines `main` and a `__main__` guard (checked by script). The registry
  docstring defines `kind` as one of `gate`, `cli`, `library` (`src/py_ci_shared/registry.py:11-16`), with `cli` meaning
  "a command-line tool, run with `py-ci-shared tool <name>`", and defines the `cli` flag separately as "has a `main` and a
  `__main__` guard" (`registry.py:20`). The README explains `kind` at line 136-137 with the same three values and no rule for
  a module that is both. The README row for these five shows `gate` and the `assert_*` entry, so a reader does not learn
  that `py-ci-shared tool <name>` works for them.
- Failure scenario: A consumer reads the catalogue, sees `closed_audit_rounds | gate`, and does not know it can be run as
  `py-ci-shared tool closed_audit_rounds`; or a tool that filters the registry by `kind == "cli"` silently misses five
  command-line tools. Nothing fails today, because the `cli` flag, not `kind`, drives the CLI dispatch.
- Proposed fix: State in `registry.py` (the `kind` and `cli` field notes) and in the README sentence at line 136-137 that
  `cli` is an orthogonal flag: a gate with a `main` sets `cli = true` and keeps `kind = "gate"`. Add an assertion to
  `tests/test_package_inventory.py` that `kind == "cli"` implies `cli = true` (the reverse is allowed by the rule above).
- AST-checkable: True; FP risk: low.
- Disposition: RESOLVED (documentation and one test assertion; registry values unchanged).

## 43-R2 (P3) CHANGELOG has no heading for the tags v1.22.1 and v1.22.6

- Severity: P3 (historical gap; no current rule is broken).
- Evidence: `git tag -l "v1.22*"` lists v1.22.1 and v1.22.6. `CHANGELOG.md` has headings for 1.22.0, 1.22.2, 1.22.3,
  1.22.4, 1.22.5, 1.22.7, 1.22.8, 1.22.9, 1.22.10, 1.22.12, 1.22.13 but none for 1.22.1 or 1.22.6 (the gap is between the
  1.22.7 heading at line 461 and the 1.22.5 heading at line 476, and between 1.22.0 at line 522 and 1.21.1). The
  heading rule in `CLAUDE.md:52-53` and `tests/test_release_version.py` starts at v1.23.0, so neither test nor doc fails.
  The CHANGELOG header (line 3) says "Milestones only", which is the stated reason some tags have no heading.
- Failure scenario: A consumer pinning `v1.22.6` searches the CHANGELOG for it and finds nothing. The header explains the
  omission only in general terms.
- Proposed fix: Leave the history as it is. Optionally add a sentence to the CHANGELOG header naming the rule
  (headings are required from v1.23.0; earlier tags are covered by the milestone above them).
- AST-checkable: True; FP risk: low.
- Disposition: RESOLVED (the file's own header states the milestone policy; backfilling 1.22.x headings would rewrite
  released history for no behavioural gain).

## 43-R3 (P3) CLAUDE.md points at `audits/2026-11-07/51`, which is not a file

- Severity: P3 (dead reference in the contributor guide).
- Evidence: `CLAUDE.md:68` reads "audits/2026-11-07/51 (51-F8) has the one-step way to add it." The directory holds
  `audits/2026-11-07/51_security_supply_chain.md` (and `.json`); the bare `51` resolves to no path. The finding 51-F8 is
  at `audits/2026-11-07/51_security_supply_chain.md:61`.
- Failure scenario: A contributor types the path into a shell or an editor and gets "not found"; the one-step fix described
  there is not reachable from the guide.
- Proposed fix: Replace the reference with `audits/2026-11-07/51_security_supply_chain.md` (section 51-F8).
- AST-checkable: True; FP risk: low.
- Disposition: RESOLVED (one-line path fix in CLAUDE.md:68).

## 43-R4 (P3) Two docstrings cite audit folders that exist only in other repositories

- Severity: P3 (provenance citations that look like local paths).
- Evidence: `src/py_ci_shared/code_audit/skip_masking_except.py:55` and `src/py_ci_shared/code_audit/uncurated_star_export.py:76`
  cite `audits/full_audit_2026-07-21/...` for the mlframe project; `src/py_ci_shared/repo_hygiene.py:46` cites
  `audits/2026-09-01-full-audit/` for the social project. None of these folders exists in this repository (`audits/`
  contains `2026-09-04` through `2026-11-10` and `implemented/`). The citations do not say which repository holds them.
- Failure scenario: A maintainer grepping this repo for the cited audit finds nothing and concludes the docstring is stale
  or the audit was lost.
- Proposed fix: Prefix each citation with the repository name ("mlframe audit", "social audit"), so the text reads as an
  external reference.
- AST-checkable: True; FP risk: low.
- Disposition: RESOLVED (three docstring edits; no code behaviour changes).

## Summary

| Severity | Count | Findings |
|---|---|---|
| P0 | 0 | |
| P1 | 0 | |
| P2 | 0 | |
| P3 | 4 | 43-F1, 43-F2, 43-F3, 43-F4 |

Dispositions: RESOLVED 4 (43-F1, 43-F2, 43-F3, 43-F4); WON'T FIX 0; DEFERRED 0. 43-F2 was changed to RESOLVED after this round's report (both headings added with the owner's decision (b)); TRACKER.md is authoritative.

Registry, README catalogue, `__all__` lists and entry-function names are in parity with the modules on disk. The defects
are documentation wording and one dead path; none changes gate behaviour. Scratch scripts are in
`C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\a43\`.
