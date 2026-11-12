# Dimension 44: test quality (round 2026-11-11)

Read-only audit. No file under src/ or tests/ was edited, and the pytest suite was not run. Hand-reasoned
mutations only; nothing below was confirmed by a run unless it says so.

## Already tracked

- The 2026-11-10 report's 44-F1 (env_flag_parsing NotIn and case-insensitive shapes) is already tracked as
  `44-F1` (TRACKER.md line 25, RESOLVED). The named tests exist: `tests/test_env_flag_parsing.py:141` and `:147`.
- The 2026-11-10 report's 44-F2 (fast-path test passes with the fast path disabled) is already tracked as
  `44-F2` (TRACKER.md line 26, RESOLVED). The `_FAST` assertion exists at `tests/test_core_fast_paths.py:42`.
- Note on IDs: TRACKER.md lines 1139-1147 use the `44-F1..F9` labels for a different set of findings in the
  2026-11-10 section, and the 2026-11-10 report only has two. The labels collide across rounds. Not a new
  finding, but a future reader should match by content, not by label.

## New findings

## 44-S1 (P2) The `$INTERROGATE` exemption lets an unpinned interrogate install pass the pin guard

- Location: `tests/test_security_supply_chain_workflows.py:50`
  (`assert "==" in stripped or "$INTERROGATE" in stripped, stripped`).
- Target: `.github/workflows/lint-blocking.yml:254` (`INTERROGATE="interrogate==$PIN"`) and the `uvx "$INTERROGATE"`
  lines at 256 and 258.
- Why it cannot fail on the broken behaviour: the test accepts any `uvx|uv pip install` line that contains the
  literal text `$INTERROGATE`, without checking where that variable was set. If line 254 is changed to
  `INTERROGATE="interrogate"` (no pin), lines 256 and 258 still contain `$INTERROGATE`, so the test passes.
  No other test in `tests/` references the interrogate assignment (grep for `INTERROGATE=` and `interrogate==`
  finds only this line in tests).
- Scope: the test is the only guard for the "no unpinned install path" rule (`test_lint_blocking_has_no_unpinned_install_path`).
- Proposed disposition: OPEN. Assert the assignment itself: parse the run block and require that the
  `INTERROGATE=` assignment contains `==` and `$PIN`, or drop the exemption and require that every `uvx` line
  name the variable's pinned value. Then mutate line 254 to an unpinned value; the test must fail.

## 44-S2 (P3) The unpinned-install regex covers only `uvx` and `uv pip install`

- Location: `tests/test_security_supply_chain_workflows.py:46-50`
  (`re.search(r"uvx|uv pip install", stripped)`).
- Why it cannot fail: a future step written as `pip install black`, `uv tool install ruff`, or `uv pip install ruff`
  inside a different shape would not match the pattern, so the `==` check never runs. The current
  `lint-blocking.yml` has no such line (checked: its only installs are `uvx` and the `uv pip install --system "deptry==$PIN"` at line 287),
  so this is a latent gap, not a live defect.
- Proposed disposition: OPEN. Widen the pattern to `pip install|uv tool|uv pip install|uvx` and add a negative
  control: a fixture step `pip install ruff` must be reported.

## Sampled, no finding

- Source-text meta-gate (`tests/test_source_text_claims.py`): the positive and negative cases are both present, with
  named regression cases. Not a finding.
- `tests/test_install_safe_hook.py`, `tests/test_safe_precommit.py`: assertions check the hook text and the
  stderr message, with a behaviour check (the stash-restore path) alongside. Not a finding.
- `tests/test_security_supply_chain_workflows.py::test_install_pyutilz_refuses_anything_but_a_full_sha`: parametrized
  over bad refs with a return code, a message and a no-side-effect check (`--upload-pack` case). Strong.
- `pytest.importorskip("pre_commit")` in `tests/test_safe_precommit.py:19`: `pre-commit` is in the `dev` extra
  (`pyproject.toml`), so the skip only triggers in an environment that did not install dev deps. Not a finding.
- The `assert True` and `assert 1 == 1` hits in `tests/` are fixture strings written into tmp files for the gates
  under test, not tautologies in the tests themselves.
- Truthy-only asserts (`assert findings`, `assert steps`) in `tests/code_audit/` and `tests/test_mutation_teeth*.py`
  were checked where they appeared; each is followed by a content assertion or is a fixture string.

## Coverage of this round

- Files read in full or in part: `tests/test_source_text_claims.py`, `tests/test_security_supply_chain_workflows.py`,
  `tests/test_nondiscriminating_shapes.py` (partial), `tests/test_install_safe_hook.py` and `tests/test_safe_precommit.py`
  (grep plus targeted reads), `tests/code_audit/test_hardcoded_test_path.py`, `tests/code_audit/test_registry_invariants.py`
  (targeted reads), `.github/workflows/lint-blocking.yml` (install lines).
- Grep-only sweeps: all `importorskip` and in-body `pytest.skip` sites, truthy-only asserts, `assert True` hits.
- Not covered: the remaining roughly 380 test files were not sampled, so absence of findings there is not proof of none.
  The most consequential gates (env_flag_parsing, skip_as_pass, fail_open_handlers, baseline_ratchet,
  destructive_tests_throwaway_only) were not re-read in this round; the 2026-11-10 and earlier rounds' notes cover them.
- No mutation was run this round.

## Summary

- New findings: 2 (P0: 0, P1: 0, P2: 1, P3: 1).
- Already tracked: 2 (44-F1 and 44-F2 of the 2026-11-10 report, TRACKER.md lines 25-26).
