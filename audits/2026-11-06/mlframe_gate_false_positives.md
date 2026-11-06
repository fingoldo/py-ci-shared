Written for: py-ci-shared maintainers, deciding which gate rules to narrow.

# mlframe gate false positives, 2026-11-06

Source: the mlframe push `6a73a7b2d` (merge of origin/master, commits `15e10766c`, `1f592dbcd`). The mlframe agent re-recorded these baseline entries with reasons instead of fixing the gates. Each class below names the mlframe baseline keys as evidence. None is fixed in py-ci-shared yet.

## Class A: `admit_on_error` on fail-open handlers that log at WARNING

Gate: `fail_open_handlers`. Rule: a handler that catches an error and stores a value, with no `log_throttle`/logger call in the handler, is flagged.

Evidence (mlframe baseline keys, reasons as recorded):
- `src/mlframe/feature_selection/filters/_dcd_pair_su_batch.py::pair_su_batch::admit_on_error`: "a pair that is not an index pair scores 0.0 (no admission) and logs at WARNING; the rule reads the constant store out[i] = 0.0 as keeping the element".
- `_gpu_resident_operand_build.py::_upload_dtype_groups::admit_on_error`: "a failed dtype-group upload stores None under the group key (its columns take the per-column path) and logs at WARNING; no element is admitted".
- `_mrmr_fingerprints.py::_replay_fitted_state::admit_on_error`: "a non-deepcopyable fitted attribute is shared with the cached source (documented read-only sharing) and logs at WARNING; the store is an attribute, not an element".
- `polynom_pair_fe.py::_run_polynom_pair_f_same_pairs_old_per::admit_on_error`: "an uncoercible column is marked in the exclusion mask and filled with NaN, so it never enters a pair; logs at WARNING".

Why it is a false positive: the store is a constant under a loop key, and the handler logs at WARNING (the `log_throttle` call is in the handler body). The rule does not follow the logging call or the key.

Proposed narrowing: count a handler that logs through `log_throttle` or a logger at WARNING or above as not fail-open, and do not treat a constant store under a loop key as admission.

## Class B: `literal-set` on per-kernel sweep grids

Gate: `drifted_duplicate_literals` / literal-set rule. Rule: the same literal set of row counts appearing in several files is flagged as drift.

Evidence (mlframe baseline keys, reasons as recorded):
- `literal-set::(10000, 50000)::...`: "row-count sweep grids of separate kernels (basis cells, LOF, the summary-pair prerank and per-member tuning), each sized to its own crossover".
- `literal-set::(20000, 100000)::...`: same class.
- `literal-set::(5000, 10000)::models/ensembling/per_member_tuning.py preprocessing/cleaning.py`: separate sweeps.
- `literal-set::(8000, 32000)::feature_selection/filters/_fe_interaction_prerank_kernels.py feature_selection/filters/_pair_mi_perm_cuda.py`: "Sample-size sweep for two separate tuning passes ... each sized to its own kernel's crossover".

Why it is a false positive: the sets are measurement grids that happen to share points, not copies that must stay in sync. Changing one must not change the others.

Proposed narrowing: exempt a literal set that carries a `# literal-set-ok:` marker (or a per-file reason) over reading it as drift; it is the same mechanism the other gates use.

## Class C: xfail skip gate

Gate: the xfail/skip gate. Rule: a skip with no matching marker is flagged.

Evidence: mlframe re-recorded the xfail baseline with reasons in `15e10766c` (commit message: "Re-record the gate baselines after merging origin/master"). The agent's summary names "the xfail skip". Exact baseline keys are in the diff of that commit; they were not re-listed here.

Status: class recorded, keys not yet enumerated. Enumerate them from `git show 15e10766c` before narrowing the rule.

## Class D: literal gates do not follow variable-built markers

Gate: literal-marker gates (the `*-ok:` markers). Rule: a marker built at runtime (a variable or f-string) is not seen, so the gate reports the site as unmarked.

Evidence: the agent's summary names "the literal gates' non-following of variable-built markers". Exact sites are not listed in its report.

Status: class recorded, sites not yet enumerated. Enumerate them from the mlframe baselines before narrowing.

## Class E: `log_throttle` handlers

Gate: `fail_open_handlers`. Rule: a handler whose only log is `log_throttle(...)` is treated as silent.

Evidence: agent summary names "`log_throttle` handlers". Overlaps Class A: the handler logs, so the rule should not flag it.

## Not done here

- No gate was changed. The fix belongs to each gate's own rule, with a test that fails before the change (CLAUDE.md, "Adding a gate").
- No mlframe baseline was touched. mlframe's baselines stay as the agent recorded them.
- Classes C and D need the exact keys before the rule can be narrowed.
