# Plan, round 2026-11-07: full agent audit of py_ci_shared

Run after: release v1.23.8, the 1.23.9 fixes and the duplicate-function version fix are on origin/master and the local
checkout is on master at origin. Standard: audits/README.md (read-only agents, one per dimension, in parallel batches;
every finding gets a disposition; nothing is deleted; a fully dispositioned file moves to implemented/<date>/).

## Method

1. **Audit (read-only).** One agent per dimension below. Each writes `audits/2026-11-07/<NN>_<name>.md` and `.json`
   (JSON list of `{id, sev, title, evidence, failure_scenario, proposed_fix, ast_checkable, fp_risk, disposition}`,
   `id` = `NN-Fk`, `sev` = P0..P3, `disposition` starts `OPEN`). Agents do not edit the package, commit or push.
   Every claim carries a demonstration (a command and its output, or a file:line), because a reading alone has been
   wrong before (README, "A demonstration beats a reading").
2. **Triage.** The coordinator verifies every P0 and P1 itself against the tree before acting, then adds one tracker row
   per finding to `audits/TRACKER.md` (state OPEN).
3. **Work every finding.** Implementation agents in separate worktrees, disjoint files, parent commits; each fix has a
   test that fails before it. A finding that is not a defect is closed WON'T FIX with the reason; one that waits on a
   decision is DEFERRED with what it waits on.
4. **Generalise.** Fixed classes become shared meta-tests (owner rule 2026-10-12).
5. **Close.** Tracker rows set, files moved to `implemented/2026-11-07/`.

## Dimensions

| NN | Name | Question |
|----|------|----------|
| 40 | gate_false_negatives | Which real patterns do the gates miss? Fuzz the detectors on consumer trees. |
| 41 | gate_false_positives | Verify the closed false-positive classes (37-F*, mlframe, autopsia, glossum) hold; find new ones in baselines. |
| 42 | core_baseline_semantics | `_core`: parsing, git listing, corpus, Baseline shrink-only ratchet, keying and counting soundness. |
| 43 | registry_docs_parity | registry.toml, README catalogue, CHANGELOG, `since`, INTERNAL_MODULES, canary coverage, public API. |
| 44 | test_quality | Mutation-resistance, teeth and canaries, order and environment dependence, flaky tests. |
| 45 | cross_platform_version | Windows, macOS, Python 3.9 to 3.14: paths, encodings, ast differences, tool versions. |
| 46 | ci_release_surface | self-ci, release.yml, reusable workflows, release_guard, pins, permissions, concurrency cancel. |
| 47 | consumer_adoption | consumers.toml, adoption matrix, pin skew, Behaviour-change completeness, breakage per release. |
| 48 | performance | Scan speed, memory, pre-push time, mutation harness. |
| 49 | diagnostics_output | Gate messages, exit codes, silent self-failure, truncation banners, misleading success text. |
| 50 | new_gate_mining | Recent consumer defects and this week's incidents turned into proposed gates with hit counts. |
| 51 | security_supply_chain | Deserialization, subprocess and git env, secrets, workflow tokens, pinned actions. |
| 52 | self_hygiene | Gates run on py_ci_shared itself, allowlists and baselines, stale entries, dead code. |

## Batches

Machine load is a real constraint (many parallel Python processes, hooks slow to minutes). Batch 1 runs 40 to 46, batch 2
runs 47 to 52 as batch 1 finishes. Each agent runs targeted tests only, never the whole suite.

## Out of scope this round

Mutation runs (the owner runs them by hand) and any change to consumer repos.
