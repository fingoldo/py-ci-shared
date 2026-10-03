# 40_gaps dispositions, part a (G-1, G-2, G-3, G-4, G-5)

Consumers validated through read-only worktrees (local clones) or `git clone --depth 50` (GitHub-only repos) under the
session scratchpad. HEADs: mlframe 34d31238b (origin/master), pyutilz origin/master, llm_bench origin/main,
glossum_backend_scripts origin/main, noema_app origin/master, autopsia 3d62be4, social 2dee1a3, dash_app_core fbee1c9,
algopacksimple f51ef43, claude-usage-notifier b99672a.

### G-1

**Disposition:** RESOLVED -- `src/py_ci_shared/hook_attestation.py`: `install` writes a `commit-msg` hook that appends
`Hooks-Verified: <sha256[:12] of the staged .pre-commit-config.yaml>[; skipped=<$SKIP ids>]` (nothing when no
`pre-commit` hook is installed); `check --range A..B [--warn-only] [--allow-author]` reports `hook-unverified`,
`hook-stale-config` (trailer hash differs from the commit's own config) and `hook-skipped`, merges and `*[bot]` authors
excluded, an all-zero base checks the tip. The module doc states that `git commit --no-verify` skips `commit-msg` as
well as `pre-commit`, so a missing trailer is the evidence. Tests: `tests/test_hook_attestation.py` (19, real git: a
hooked commit carries the trailer, `--no-verify` none, `SKIP=mypy` is recorded, a hand-copied trailer over a changed
config is stale). Canary: EXEMPT in `tests/test_gate_teeth.py` (its subject is history). Validation: `check --range
HEAD~30..HEAD` flags every commit in pyutilz 28, mlframe 34, glossum 30, autopsia 32, social 30, dash_app_core 30,
algopacksimple 30 (no commit carries the trailer yet; this can only be measured forward, as the audit said); noema_app 0
(no `.pre-commit-config.yaml`, nothing to attest). Not done here: `install_safe_hook.py` does not call `install` yet
(not my file); a consumer runs `python -m py_ci_shared.hook_attestation install` once per clone.

### G-2

**Disposition:** RESOLVED -- `src/py_ci_shared/sibling_floor_skew.py` (`find_sibling_floor_skew`,
`assert_sibling_floor_skew`, CLI `--resolve-in sibling=path`). Floors from `dependencies`, `optional-dependencies`,
`dependency-groups`; install sites `git -C <s> checkout`, `github.com/<o>/<s>.git@ref`, `uses: <o>/<s>/...@ref`,
`git clone --branch`, `actions/checkout repository+ref`, `[tool.uv.sources]`, `name @ git+...@ref`. A release tag is its
own version, else `pyproject.toml` at the ref in the local clone, else raw.githubusercontent.com; an unresolvable ref is
a `sibling-ref-unresolved` finding. Tests: `tests/test_sibling_floor_skew.py` (18); canary `tests/canary/sibling_floor_skew/`.
Validation: llm_bench@57e79a7 6 findings, all real (`ci.yml:73,188,231,257,291`, `mypy-full.yml:43`, pyutilz@8ffd7e6 =
1.0.0 < 1.1); llm_bench@d19b2b0 0 (11 sites at 1.1.0 / 1.18.0); HEADs: llm_bench 0 (11 sites), mlframe 0 (30 sites),
pyutilz 0, glossum 0, py-ci-shared 0, dash_app_core 0, algopacksimple 0, claude-usage-notifier 0; autopsia, social,
noema_app have no root `pyproject.toml` (the gate raises `EmptyScanError`, not applicable). 0 false positives.

### G-3

**Disposition:** RESOLVED -- `src/py_ci_shared/optional_truthiness.py`: `find_attribute_truthiness_tests` and
`assert_optionals_test_for_none(follow_attributes=True, bound_names=BOUND_NAMES)`. Follows `self.<attr>` (from an
`__init__` parameter annotated Optional number, or a class-level annotated field), `getattr(self, "<attr>", ...)`,
locals bound from either (closures included), and one hop of forwarding by keyword or position to a uniquely named
function. Precision rules, each measured on mlframe master: budget-like names only (`BOUND_NAMES`: without it 17
findings, with it 7); a class that defines the attribute is judged on its own annotation, a mixin or module-level
`def f(self)` on the nearest classes, all of which must agree (removes `max_train_size`, an unannotated
`__init__`). Tests: `tests/test_optional_truthiness.py` (+24, 45 total); canary `attr_seed.py` added. Acceptance on
mlframe origin/master 34d31238b: 7 findings, all true positives: the three live zero-budget bugs the audit named
(`boruta_shap/_fit_explain.py:535`, `shap_proxied_fs/_shap_proxied_fit.py:184`, `training/cb/_cb_gpu_monitor.py:391`)
plus four `max_refits`/`max_runtime_mins` sites that the seed fix 1256fff4d changes (`rfecv/_fit.py:324`,
`_fit.py:391`, `_fit_outer_loop.py:379`) or misses (`rfecv/_mbh_optimizer.py:69`, `min(max_refits, n) if max_refits`).
1256fff4d is not an ancestor of origin/master (`git merge-base --is-ancestor` fails), which is why they are still
there. At 1256fff4d itself: exactly the three named bugs plus `_mbh_optimizer.py:69`. At 1256fff4d^: the attribute pass
finds three of the six seed sites (`_fit.py:372,454`, `_fit_outer_loop.py:367`), the per-parameter check two
(`_futility_stop.py:224,226`); `_outer_loop_bookkeeping.py` is two hops away and stays unseen. So the request "exactly
those 3" does not hold on today's origin/master. The four extra sites are the same defect, so they stay findings.
Considered and dropped: excusing a log-only branch (1256fff4d fixed exactly such a branch) and excusing an attribute
whose 0 `__init__` rejects (`set_params` bypasses it, the reason 1256fff4d gives). Other repos: pyutilz, llm_bench,
glossum, dash_app_core, algopacksimple, notifier, autopsia and social were not run through the attribute pass. Only
mlframe was, because the audit's acceptance test is there.

### G-4

**Disposition:** RESOLVED -- `src/py_ci_shared/api_floor.py` (`find_api_floor`, `assert_api_floor`): vermin
`-t=<floor>- --violations --no-parse-comments --backport typing_extensions --format parsable` over the parsed files
(batched under the Windows command-line limit), dropping hits behind `sys.version_info`/`hasattr`/`getattr`/
`TYPE_CHECKING` tests (either branch, and later `and` operands) or inside `try` bodies with an ImportError/
AttributeError/TypeError/Exception handler. vermin is optional (`[api]` extra, also in `dev`); without it the gate
reports `api-floor-unavailable` and the assert fails. Python-2-only readings (`'long' member`, `tensor.long()`) and
`'int.is_integer' member` (float has it in every 3.x) are dropped. Tests: `tests/test_api_floor.py` (17, real vermin);
canary `tests/canary/api_floor/`. Validation: pyutilz@14dcfc5^ 1 finding = the seed `dev/block_extract.py:473`
`Path.write_text(newline)` (the 5 guarded hits the audit listed are filtered); pyutilz@d660504^ 2 = both seeds
(`effect_flag_outside_its_effect.py:88` `ast.unparse`, `system/distributed.py:85` `md5(usedforsecurity)`); HEADs:
pyutilz 0, llm_bench 0, py-ci-shared 0, dash_app_core 0, claude-usage-notifier 0, glossum 0 (after the
`int.is_integer` rule; before it 1 FP, `refsuite/parse_answer.py:107` on a float), mlframe 1 true positive:
`training/crash_diagnostics.py:212` `threading.__excepthook__` (3.10) under `requires-python >=3.9`, latent because the
installed lambda always passes `_prev`. algopacksimple has no `requires-python`: `CorpusError` asks for `target=`.

### G-5

**Disposition:** RESOLVED -- `src/py_ci_shared/committed_line_endings.py`: index blobs via `git ls-files -s` +
`git cat-file --batch`. git's `i/` class cannot be the filter: the seed file reads as `i/-text`, so a git-binary blob
is still checked when it has no NUL byte and decodes as UTF-8. `-text`/`binary` attributes opt a path out. `eol=crlf`
does not, because git stores such files with LF. Rules `committed-bare-cr`, `committed-mixed-endings`,
`committed-crlf`; baseline key = rule + path. Tests: `tests/test_committed_line_endings.py` (12, real git); canary
`tests/canary/committed_line_endings/` (`{CR}` written as a bare CR at run time). Validation: pyutilz@fa1aab0 1 =
the seed (`constructor_param_overwritten.py`, 306 bare CR, 18 CRLF, 0 LF), pyutilz@60c59c3 0, pyutilz HEAD 0; mlframe
133 `committed-crlf` + 1 mixed (`raw_progress_2026-06-18.txt`, 40 CRLF + 1 LF), matching the audit's
`git ls-files --eol` count; social 1 (`realtime_applications/sql/schema.sql`, `\r\r\n` twice, git calls it binary);
autopsia 1 (`bench/gap_disease_src/PMC5892178.txt`, 2 bare CR + 55 CRLF, a downloaded source text: true positive whose
fix is a `-text` line or renormalising); llm_bench, glossum, noema_app, dash_app_core, algopacksimple,
claude-usage-notifier, py-ci-shared 0.
