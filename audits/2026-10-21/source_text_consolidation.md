# Source-text rules: one detector, measured

Two py-ci-shared gates detect the same practice, a test that asserts on source text:
`no_source_text_tests` (library `source_text_ban`, no baseline) and `source_text_claims`
(library `source_text_claims`, shrink-only baseline).

## Measured divergence on `tests/` (this repository, 2026-10-21)

| | lines |
|---|---|
| flagged by both | 17 |
| only `source_text_ban` | 44 |
| only `source_text_claims` | 23 |

Samples, read by hand:

- `test_git_changed_lines.py` (ban only): the test writes `a.py` into a temporary repo and reads
  it back. Not a source assertion. **Ban false positive.**
- `test_naive_utcnow.py:168` (claims only): `Path(mod.__file__).read_text()` followed by
  `assert "node.lineno" in source`. A real source-text claim. **Ban miss.**
- `test_release_prep.py` (claims only): asserts on text the release tool generated into a temporary
  tree. Not production source. **Claims false positive.**

## Decision

One implementation: `source_text_claims`, extended with an exemption for reads of files the test
itself wrote or generated (a temporary tree, a file the test wrote). `source_text_ban` then becomes
a thin consumer of it, and `no_source_text_tests` delegates to it.

Behaviour change for `@v1` consumers, to be recorded in the CHANGELOG: `no_source_text_tests`
starts flagging the claims-only shapes (a real miss today) and stops flagging write-then-read
fixtures. Its `.sql` stance follows the claims default (`.sql` counts as source) unless the
measurement shows that costs more false positives than it catches.

## Not yet done

The exemption, the delegation and the baseline refresh are not implemented here. They change what a
blocking gate reports, so they land in one reviewed commit with the measured before/after counts.
