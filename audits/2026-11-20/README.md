# Meta-test profiling, 2026-11-20

Measured locally on the owner's machine while other sessions were running, so absolute times are inflated and noisy; the ranking and the ratios are the finding.

- `profile_code_audit.py` / `profile_code_audit_mlframe.txt`: one code-audit session over `src/mlframe` (2242 files) in one process, scanner by scanner. Walk 0.2 s, cold parse of every file 94 s, 105 scanners 463 s in all.
- `dump_code_audit_findings.py`: dumps every finding as sorted JSON; run before and after changing a scanner, the two files must be identical.
- `durations_mlframe_test_meta_top100.txt`: the whole mlframe `tests/test_meta` with `-n 2`, no `MLFRAME_FAST`, GPU visible (40 min, 55 failed from py-ci-shared version skew). `durations_mlframe_test_meta_ci_like_top100.txt`: the same with `MLFRAME_FAST=1` and no GPU (28 min).
- The fleuret test (`test_mrmr_research_doc_consistency.py`) is a real `MRMR.fit`: 470 s without `MLFRAME_FAST`, 168 s with it.
