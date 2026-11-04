# 36: hit lists of the F8 / F6 / F13 gates on mlframe and pyutilz (2026-11-05)

Read-only measurement of the three gates built from 32-F8, 32-F6 and 32-F13 against `mlframe/src/mlframe` and
`pyutilz/src/pyutilz`, default configuration (no `exclude`, no `allowed`). Paths are relative to each `src/<package>`.
Verdicts: TRUE = fix it (or mark it); BY-DESIGN = intended behaviour, add the marker with the reason; FALSE = the gate is wrong here.

## unseeded_rng_fallback (blocking in consumers): mlframe 6, pyutilz 1

| Site | Verdict | Note |
| --- | --- | --- |
| mlframe feature_engineering/transformer/swap_noise.py:74 | TRUE | `rng if rng is not None else default_rng()` in `swap_noise_augment`: unseeded augmentation |
| mlframe feature_selection/filters/_fe_stability_vote.py:325 | TRUE | `confirm_recipes_cross_fold` picks CV folds for the stability vote from entropy |
| mlframe models/tuning_rules.py:214 | TRUE | `double_check_dist_params` resamples params from entropy when `rng is None` |
| mlframe feature_selection/wrappers/rfecv/_cv_setup.py:180 | BY-DESIGN | `_fixed_shuffle_seed`: one entropy seed per fit on purpose (docstring); mark `# rng-entropy-ok: ...` |
| mlframe utils/_param_oracle.py:521 | BY-DESIGN | `random.Random()` for epsilon-greedy exploration when no rng is passed; mark or derive from the oracle seed |
| mlframe feature_selection/filters/_family_maxt_stream.py:82 | FALSE | `replay = default_rng()` has its `bit_generator.state` overwritten on the next line; mark with a reason |
| pyutilz text/humanizer.py:470 | BY-DESIGN | `introduce_typos` entropy typo injection (`# nosec B311` already says non-security sampling); mark |

## caller_frame_mutation (ADVISORY in consumers): mlframe 45, pyutilz 3

Read of each site: numpy arrays and a dict are FALSE (the gate cannot see types); in-place cleaners that document or
need `update_data`/in-place semantics are CONVENTION (accepted pattern, add `# frame-mutation-ok` or a docstring
phrase); TRUE = an undocumented mutation of a frame the caller owns.

TRUE (mutates the caller's DataFrame, not documented as in place):
- mlframe feature_engineering/bruteforce.py:102 (`median_fill_pandas`)
- mlframe feature_selection/boruta_shap/_io_plot.py:118 (`box_plot` shifts `data["value"]`)
- mlframe training/pipeline/__init__.py:254 (`transform` appends the PySR columns to the caller's `df`; docstring says "Append", so document or copy)
- mlframe training/pipeline/_pipeline_extensions_pysr.py:178, 180, 182 (`_add_pysr_equation_columns` writes into train/val/test frames)
- mlframe training/_training_loop_fallback_helpers.py:68 (`_decategorize_text_cols`)
- mlframe training/utils.py:871 (`_restore_datetime_columns`)
- mlframe feature_selection/filters/_mrmr_fit_impl/_friend_graph_and_redundancy/_group2.py:79 (`X[_hn] = _vals` on the fit working frame; verify it is the fit-time copy)
- mlframe core/helpers.py:299 (`ensure_no_infinity_pd`, public in-place cleaner)

CONVENTION (deliberate in-place cleaners / private steps of one):
- mlframe preprocessing/_cleaning_helpers.py:314, 315, 394, 398, 431, 535 (535 has `update_data=True` documented in the noqa)
- mlframe preprocessing/transforms.py:220, 223, 248, 249, 271 (private helpers of `prepare_pandas_categoricals_for_catboost`)
- mlframe feature_selection/filters/mrmr/_mrmr_class_fit_body_blocks.py:247 (noqa says it must mutate the caller's stored frame)
- mlframe feature_selection/filters/discretization/_discretization_dataset.py:208 (`create_redundant_continuous_factor` adds a column to `df` by design)
- pyutilz data/pandaslib/dtypes.py:164, io_ops.py:130, 132 (private helpers on a frame the caller function built)

FALSE (numpy array / dict / benchmark, or a fallback after a non-mutating path):
- mlframe data/synthetic.py:319, 353, 387, 389, 391, 405, 407, 409, 425, 443 (numpy buffer `X` built inside the generator)
- mlframe feature_engineering/transformer/gradient_direction_agreement.py:67, 74 (numpy column perturbed then restored)
- mlframe feature_engineering/_benchmarks/bench_graddir_gradient_colcopy.py:51, 53 and feature_selection/filters/_dynamic_cluster_discovery/_benchmarks/bench_member_null_parallel.py:38, 43 (benchmarks, numpy)
- mlframe feature_selection/filters/_hermite_fe_optimise_helpers.py:14 (numpy batch buffer)
- mlframe feature_selection/filters/_mrmr_fit_impl/_fit_impl_stages/_binning.py:122 (numpy code matrix)
- mlframe feature_selection/wrappers/_helpers_importance.py:386, 397 (`X_perm` scratch buffer)
- mlframe training/_model_configs.py:138 (`data` is a dict in a validator)
- mlframe training/_feature_name_sanitize.py:110 (last-resort fallback after the non-mutating `rename`)

## loop_invariant_cast heavy=True, rule loop-invariant-heavy-call (blocking in consumers): mlframe 5, pyutilz 0

The first prototype (positional operands only) found 14 mlframe sites; 9 were `np.bincount(codes, weights=<varies per iteration>)`
whose keyword differs per iteration, so the gate also requires every keyword value to be loop-invariant (all 9 vanish).
Remaining:

| Site | Verdict | Note |
| --- | --- | --- |
| mlframe feature_selection/filters/_binned_agg_linear_usability.py:208 | TRUE | `np.bincount(cells_s, minlength=n_cells)` (the denominator) is recomputed per candidate; hoist above the loop (the `weights=cand_s` numerator stays) |
| mlframe feature_selection/_benchmarks/fe_operator_factory/stat_study/exp2_general.py:131 | TRUE (benchmark) | `np.quantile(u[rows], q)` inside the `for r in QG` loop is invariant to `r` |
| mlframe feature_selection/_benchmarks/target_lens/exp_target_mix.py:224 | TRUE (benchmark) | `np.median(base)` recomputed per arm; trivial size |
| mlframe metrics/_benchmarks/bench_ndcg_sort_count_hoist_cpx23.py:76 | BY-DESIGN | `argsort` per k is the labelled "DE-OPTIMISED" arm of the benchmark; mark `# loop-invariant-ok` or exclude `_benchmarks/` |
| mlframe metrics/_benchmarks/bench_ndcg_sort_count_hoist_cpx23.py:78 | BY-DESIGN | same arm (`-np.sort(-y_t)`) |

Production true hits: 1 (`_binned_agg_linear_usability.py:208`). Consumers may pass `exclude=("_benchmarks/",)`.
