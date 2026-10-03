# 2026-10-03 audit, agent 40: gap analysis (defect classes no gate catches)

Scope: which defect classes reached the 13 consumers in the last 30 days without any py-ci-shared gate stopping them
before merge, and what new gates or extensions would. Worktree detached at e53587c. Read-only. I read CLAUDE.md, the
README catalogue (147 modules), `audits/implemented/2026-09-24/` (NEW-1..49, INFRA-1..6, ADOPT-1..30) and
`audits/implemented/2026-10-03/10_new_code.md` (N-1..22). Nothing below re-proposes one of those. Where a proposal extends an
existing module, the module is named.

Evidence base:
- Local clones, `git log --since=2026-09-01 --no-merges -i --grep=fix`: pyutilz 80, mlframe 416,
  glossum_backend_scripts 205, noema_app 23, llm_bench 2 (plus a CI-keyword pass without the `fix` filter).
- GitHub repos, cloned read-only into the scratchpad (`--shallow-since=2026-08-25`): autopsia, social, dash_app_core,
  algopacksimple, claude-usage-notifier, polyvocab_app, flutter_app_core, flutter_uptime_monitor.
- `gh api` read-only: branch protection, check runs.
- Prototype scripts and outputs: `scratchpad/gaps/` (`proto_floor_skew.py`, `proto_attr_truth.py`, `proto_eol.py`,
  `proto_fact_tables.py`, `proto_consumer_imports.py`, `attr_*.txt`, `eol_*.txt`, `v1.txt`, `v2.txt`).

Severity counts: High 3, Med 7, Low 4 (14 proposals). 9 more were evaluated and rejected (end of file).

---

## Seed incidents: what caught them, what did not

| # | Incident | Evidence | Gate that exists now | Verdict |
|---|---|---|---|---|
| a | CI job runs pytest without installing what conftest imports | pyutilz 9b5d22e (publish.yml), e6db9af (numba-coverage nightly); mlframe 127157a28 (fs-benchmark-nightly) | `ci_install_covers_conftest` found 9b5d22e ("Found by the new ci_install_covers_conftest gate") | Covered |
| b | Optional numeric tested for truth (`max_runtime_mins=0`, `max_refits=0` ran unbounded) | mlframe 1256fff4d (5 sites in rfecv) | `optional_truthiness` IS wired (mlframe `tests/test_meta/test_shared_checks_wired.py:400`); `sentinel_or_fallback` IS wired (`test_shared_gates_adopted.py:33`) | Gap: see G-3 |
| c | pyproject floor raised (`pyutilz>=1.1`), CI still clones pyutilz 1.0.0 | llm_bench 57e79a7, fixed d19b2b0 | none | Gap: G-2 |
| d | File grows past LOC budget (round_runner 1000 -> 1020) | llm_bench 57e79a7 -> d19b2b0 | `loc_budget` caught it, in CI after push; the repo's pre-commit runs `tests/test_meta` (`.pre-commit-config.yaml` id `llm_bench-meta-tests`), so the commit did not go through the hook | Gap: G-1 |
| e | pip-audit red from 09-07 for a non-vulnerability reason, hiding nltk PYSEC-2026-3740 | pyutilz e25225c, e4906ae | `ci_health` (added at e53587c, HEAD) reports a workflow red for more than 2 days | Covered from now on. The residual is below the table |
| f | Commented-out code ages past 30 days and turns the build red on a calendar day | pyutilz bf44e93 (9 lines, ubuntu leg) | `stale_comment_age`; its early warning is opt-in, `warn_days=0` by default | Partial: G-12 |
| g | Tests that read source text; tests pinning provider prices | mlframe 127157a28, 009f1e7db, 707a34dcb; pyutilz 423fdc4 (tests re-pinned to new prices) | `source_text_ban`, `source_text_claims` (working: the mlframe commits are their fallout) | Source text covered. Price pins: G-9 |
| h | Stale provider price and model tables | pyutilz 423fdc4 ("claude-sonnet-5-5 was missing (priced at double)", xAI search cost "off by 40%", Gemini "wrong or missing prices"), 60c59c3 | none | Gap: G-9 |
| i | Required status-check name no workflow produces | brief (pyutilz `mypy-full / mypy-full`) | none | Gap: G-10 (low reach, see there) |
| j | `*-latest` labels, deprecated action runtimes | no breakage commit found in 30 days (searched `ubuntu|node ?(20|24)|runner image|deprecat`) | none | G-11 (Low) |
| k | Bare-CR / CRLF / mixed endings in tracked files | pyutilz `constructor_param_overwritten.py`: 306 bare CR + 18 CRLF + 0 LF from fa1aab0 (09-03) to 60c59c3 (09-26); mlframe 7e2c512e8, 572848918, 58af3ebe0 (three "restore LF" commits on 09-21) | `lf_file_writes` catches code that WRITES CRLF; nothing checks the committed blobs | Gap: G-5 |
| l | Test leaves `sys.stdout` swapped (numba ColorShell -> colorama `deinit`) | mlframe 1256fff4d, 66d60766f, 127157a28 ("colorama leak fixture") | `standard_stream_restore` (static, own-code only); `resource_leak_guard` has no stream check, and 0 of 13 consumers enable it | Gap: G-7 |

Residual on (e): a local run of `python -m py_ci_shared.ci_health configs/consumers.toml` (through `gh`, no token env)
printed nothing in 600 s and was killed (`timeout` exit 124). The machine was under memory pressure: a later prototype
hit `WinError 8`. I did not dig further. This is for the core-infrastructure auditor: a daily job that cannot finish
in its `timeout-minutes: 15` would itself go red.

---

### G-1 (High) -- Commits that never went through the hooks: a CI check over the pushed range

**Disposition:** RESOLVED -- `src/py_ci_shared/hook_attestation.py`: `install` writes a `commit-msg` hook that appends `Hooks-Verified: <sha256[:12] of the staged .pre-commit-config.yaml>[; skipped=<$SKIP ids>]` (nothing when no `pre-commit` hook is installed); `check --range A..B [--warn-only] [--allow-author]` reports `hook-unverified`, `hook-stale-config` (trailer hash differs from the commit's own config) and `hook-skipped`, merges and `*[bot]` authors excluded, an all-zero base checks the tip. The module doc states that `git commit --no-verify` skips `commit-msg` as well as `pre-commit`, so a missing trailer is the evidence. Tests: `tests/test_hook_attestation.py` (19, real git: a hooked commit carries the trailer, `--no-verify` none, `SKIP=mypy` is recorded, a hand-copied trailer over a changed config is stale). Canary: EXEMPT in `tests/test_gate_teeth.py` (its subject is history). Validation: `check --range HEAD~30..HEAD` flags every commit in pyutilz 28, mlframe 34, glossum 30, autopsia 32, social 30, dash_app_core 30, algopacksimple 30 (no commit carries the trailer yet; this can only be measured forward, as the audit said); noema_app 0 (no `.pre-commit-config.yaml`, nothing to attest). Not done here: `install_safe_hook.py` does not call `install` yet (not my file); a consumer runs `python -m py_ci_shared.hook_attestation install` once per clone.

Evidence. The most frequent single cause of "CI red on master" this month was a commit that skipped the local hooks:
- mlframe 58715253b: "Another session's 10 commits ... had never been run through the pre-commit gates before
  landing, so CI was red across the board" (ruff, black x12, vulture x11, mypy, uv.lock).
- autopsia 70c28afa: "Clear the meta-test debt the --no-verify pushes left".
- llm_bench 57e79a7: the LOC overrun (d) and the floor skew (c) both reached CI, although the repo's pre-commit runs
  `pytest tests/test_meta/` (`.pre-commit-config.yaml` at 57e79a7, id `llm_bench-meta-tests`, stage pre-commit). Fixed
  in d19b2b0.
- mlframe 7a8fd1d15: "The whole-project mypy hook is skipped for this commit only" (a `SKIP=`).
`hook_hygiene` checks that a hook is honest. `safe_precommit` / `install_safe_hook` make hooks survive concurrent
sessions. Nothing detects that a pushed commit never met them.

Proposal: `hook_attestation`, two halves.
1. `install_safe_hook` also installs a `commit-msg` hook that appends a trailer
   `Hooks-Verified: <first 12 hex of sha256(.pre-commit-config.yaml)>[; skipped=<ids from $SKIP>]`. Git skips both
   `pre-commit` and `commit-msg` under `--no-verify`, so a bypassed commit gets no trailer and no extra logic is
   needed. `commit-msg` runs only after `pre-commit` has passed.
2. A CLI `python -m py_ci_shared.hook_attestation --range "$BEFORE..$AFTER"` for a push-triggered job. It lists the
   non-merge commits in the range with no trailer, a stale config hash, or a non-empty `skipped=`, excluding bot
   authors (`dependabot[bot]`, `github-actions[bot]`) and an allowlist. It warns or fails per config. Pure function
   over `git log --format=%H%x00%an%x00%(trailers:key=Hooks-Verified,valueonly)`.

FP risk: commits made on another machine without the hook installed (that IS the signal), GitHub web-UI edits, and
squash merges that rewrite messages (the trailer survives a squash only if the body is kept). Measuring it: today
no commit carries the trailer, so it can only be measured forward. Run warn-only for 14 days and count flagged
commits per repo against the "fix CI red" commits that follow them.
Prototype: none possible retroactively. The proxy evidence is the four commits above, in three repos in 30 days.
Priority: value high (it is the root cause behind c, d, k and most "clear the CI red" commits) x cost low (one hook
script, one ~80-line CLI on `git_changed_lines`' git helpers).

### G-2 (High) -- A sibling dependency floor in pyproject vs the sibling revision CI actually installs

**Disposition:** RESOLVED -- `src/py_ci_shared/sibling_floor_skew.py` (`find_sibling_floor_skew`, `assert_sibling_floor_skew`, CLI `--resolve-in sibling=path`). Floors from `dependencies`, `optional-dependencies`, `dependency-groups`; install sites `git -C <s> checkout`, `github.com/<o>/<s>.git@ref`, `uses: <o>/<s>/...@ref`, `git clone --branch`, `actions/checkout repository+ref`, `[tool.uv.sources]`, `name @ git+...@ref`. A release tag is its own version, else `pyproject.toml` at the ref in the local clone, else raw.githubusercontent.com; an unresolvable ref is a `sibling-ref-unresolved` finding. Tests: `tests/test_sibling_floor_skew.py` (18); canary `tests/canary/sibling_floor_skew/`. Validation: llm_bench@57e79a7 6 findings, all real (`ci.yml:73,188,231,257,291`, `mypy-full.yml:43`, pyutilz@8ffd7e6 = 1.0.0 < 1.1); llm_bench@d19b2b0 0 (11 sites at 1.1.0 / 1.18.0); HEADs: llm_bench 0 (11 sites), mlframe 0 (30 sites), pyutilz 0, glossum 0, py-ci-shared 0, dash_app_core 0, algopacksimple 0, claude-usage-notifier 0; autopsia, social, noema_app have no root `pyproject.toml` (the gate raises `EmptyScanError`, not applicable). 0 false positives.

Evidence: llm_bench 57e79a7 raised `pyutilz>=1.1`. `ci.yml` (5 sites) and `mypy-full.yml` (1 site) still ran
`git -C pyutilz checkout 8ffd7e6` (pyutilz 1.0.0). uv could not resolve, so both workflows failed at install
(d19b2b0 message). Locally the author had an editable pyutilz 1.1, so nothing failed before the push. Existing gates
look at neighbours of this but not at it: `git_dependency_pins` (git URL pinned to a SHA), `adoption_matrix` /
`version_tag_currency` (py-ci-shared pins only), `checkout_resolution` (the suite tests this checkout).

Proposal: `sibling_floor_skew`. For every first-party sibling named in `configs/consumers.toml` (pyutilz,
py-ci-shared, mlframe), collect the floors in `pyproject.toml` (`dependencies`, `optional-dependencies`,
`dependency-groups`). Then collect every place a workflow installs that sibling at a fixed ref:
`git -C <name> checkout <ref>`, `<name>.git@<ref>`, `uses: fingoldo/<name>/...@<ref>`, and `[tool.uv.sources]` revs.
Resolve each ref's version by reading `pyproject.toml` at that ref (local sibling clone via `--resolve-in`, the way
`adoption_matrix` does, else the GitHub contents API). Report every install site whose version is below the floor,
and every site where the ref cannot be resolved (unknown = finding).
Prototype (`proto_floor_skew.py`, 70 lines, regex over workflows plus `git show <ref>:pyproject.toml` in the local
sibling):
- llm_bench@57e79a7: 6 SKEW (all 6 real: `ci.yml` x5 and `mypy-full.yml`, `pyutilz@8ffd7e6 = 1.0.0 < 1.1`).
- llm_bench@d19b2b0: 0 SKEW (6 sites ok at c4bad3f = 1.1.0).
- HEAD of all 13 consumers: 0 findings. Only mlframe (10 install sites) and llm_bench (5) have sibling floors; the
  others have none, so precision is 6/6 on the seed and nothing is noise elsewhere.
FP risk: very low. The one design choice is a ref the resolver cannot reach (a private sibling without a token). That
must be a finding, not a pass.
Priority: high value (it breaks every job at install, and only on CI) x low cost (~150 lines on `_consumers` and
`adoption_matrix`'s resolver).

### G-3 (High) -- optional_truthiness misses optionals carried on `self` and forwarded to helpers

**Disposition:** RESOLVED -- `src/py_ci_shared/optional_truthiness.py`: `find_attribute_truthiness_tests` and `assert_optionals_test_for_none(follow_attributes=True, bound_names=BOUND_NAMES)`. Follows `self.<attr>` (from an `__init__` parameter annotated Optional number, or a class-level annotated field), `getattr(self, "<attr>", ...)`, locals bound from either (closures included), and one hop of forwarding by keyword or position to a uniquely named function. Precision rules, each measured on mlframe master: budget-like names only (`BOUND_NAMES`: without it 17 findings, with it 7); a class that defines the attribute is judged on its own annotation, a mixin or module-level `def f(self)` on the nearest classes, all of which must agree (removes `max_train_size`, an unannotated `__init__`). Tests: `tests/test_optional_truthiness.py` (+24, 45 total); canary `attr_seed.py` added. Acceptance on mlframe origin/master 34d31238b: 7 findings, all true positives: the three live zero-budget bugs the audit named (`boruta_shap/_fit_explain.py:535`, `shap_proxied_fs/_shap_proxied_fit.py:184`, `training/cb/_cb_gpu_monitor.py:391`) plus four `max_refits`/`max_runtime_mins` sites that the seed fix 1256fff4d changes (`rfecv/_fit.py:324`, `_fit.py:391`, `_fit_outer_loop.py:379`) or misses (`rfecv/_mbh_optimizer.py:69`, `min(max_refits, n) if max_refits`). 1256fff4d is not an ancestor of origin/master (`git merge-base --is-ancestor` fails), which is why they are still there. At 1256fff4d itself: exactly the three named bugs plus `_mbh_optimizer.py:69`. At 1256fff4d^: the attribute pass finds three of the six seed sites (`_fit.py:372,454`, `_fit_outer_loop.py:367`), the per-parameter check two (`_futility_stop.py:224,226`); `_outer_loop_bookkeeping.py` is two hops away and stays unseen. So the request "exactly those 3" does not hold on today's origin/master. The four extra sites are the same defect, so they stay findings. Considered and dropped: excusing a log-only branch (1256fff4d fixed exactly such a branch) and excusing an attribute whose 0 `__init__` rejects (`set_params` bypasses it, the reason 1256fff4d gives). Other repos: pyutilz, llm_bench, glossum, dash_app_core, algopacksimple, notifier, autopsia and social were not run through the attribute pass. Only mlframe was, because the audit's acceptance test is there.

Evidence: mlframe 1256fff4d fixed five truth tests of `max_runtime_mins` / `max_refits` in
`feature_selection/wrappers/rfecv/`. A deliberate 0 ran the whole elimination unbounded. Both gates are wired in
mlframe and neither could see these sites:
- `optional_truthiness` checks a parameter annotated `int | None` inside the function that declares it. RFECV
  declares `max_runtime_mins: Union[float, None]` and `max_refits: Union[int, None]` in `__init__` (rfecv
  `__init__.py:206-207`). `_fit.py` reads them as `max_runtime_mins = self.max_runtime_mins` (line 324) and tests
  `if max_runtime_mins:` (line 372). Elsewhere `max_refits` is passed down as an unannotated parameter
  (`max_refits=max_refits`, line 536/606) and tested there.
- `sentinel_or_fallback` matches only the `x or fallback` shape over a fixed name list (`max_tokens`, `timeout`,
  `seed`, `limit` ...). `max_refits`, `max_runtime_mins` and `*_budget*` are not on it, and `if x:` is not its shape.
autopsia 70c28afa ("or-default traps become explicit None checks") is the same class in another repo.

Proposal (extension of `optional_truthiness`, opt-in `follow_attributes=True`, then default):
1. Collect every class's `__init__` parameter annotated Optional of `int`/`float`/`Decimal` that is stored as
   `self.<attr> = <param>`. Pydantic and dataclass fields with the same annotation join the set (the
   `config_getattr_default_parity` field reader already parses them).
2. In every method, flag a truth test of `self.<attr>`, of `getattr(self, "<attr>", None)`, or of a local bound from
   either.
3. One hop of forwarding: when such a value is passed as `f(..., name=value)` to a function in the same package, flag
   truth tests of `name` inside `f`. Reuse the call-site resolution in `kwarg_forwarding`.
Prototype (`proto_attr_truth.py`, steps 1-2 only, no forwarding):
- mlframe@1256fff4d^: 50 optional numeric attributes, 9 truth tests. It finds `rfecv/_fit.py:372` (one of the five
  seed sites). The `max_refits` sites need step 3.
- mlframe origin/master (1197b39e1): 9 hits. Three of them are LIVE instances of the class just fixed:
  `feature_selection/boruta_shap/_fit_explain.py:535` `if _max_runtime_mins and ...` (bound at line 447 from
  `getattr(self, "max_runtime_mins", None)`), `feature_selection/shap_proxied_fs/_shap_proxied_fit.py:184`
  `if _budget_max_mins and ...` (line 179, same getattr). In both, a budget of 0 is ignored, exactly as in
  1256fff4d. The third is `training/cb/_cb_gpu_monitor.py:391` `if self.time_budget_s and elapsed > ...`
  (`time_budget_s: Optional[float] = None`, line 167). Also flagged: `rfecv/_fit.py:373` (log-only branch, the same
  `if max_runtime_mins:` that survived the fix), `cb_gpu_monitor` x3 `total_iterations`,
  `models/selection.py:100` and its `_old_selection_cpx15.py:91` benchmark copy (`max_train_size`, where 0 is not a
  meaningful size, so FP-ish).
  Precision about 4-5 of 9 on real code; the rest want `# falsy-ok:` markers.
Priority: high value (two live budget bugs today, plus the seed) x medium cost (steps 1-2 are ~60 lines on the
existing scanner; step 3 reuses `kwarg_forwarding`).

### G-4 (Med) -- Stdlib API newer than `requires-python` (guard-aware vermin)

**Disposition:** RESOLVED -- `src/py_ci_shared/api_floor.py` (`find_api_floor`, `assert_api_floor`): vermin `-t=<floor>- --violations --no-parse-comments --backport typing_extensions --format parsable` over the parsed files (batched under the Windows command-line limit), dropping hits behind `sys.version_info`/`hasattr`/`getattr`/ `TYPE_CHECKING` tests (either branch, and later `and` operands) or inside `try` bodies with an ImportError/ AttributeError/TypeError/Exception handler. vermin is optional (`[api]` extra, also in `dev`); without it the gate reports `api-floor-unavailable` and the assert fails. Python-2-only readings (`'long' member`, `tensor.long()`) and `'int.is_integer' member` (float has it in every 3.x) are dropped. Tests: `tests/test_api_floor.py` (17, real vermin); canary `tests/canary/api_floor/`. Validation: pyutilz@14dcfc5^ 1 finding = the seed `dev/block_extract.py:473` `Path.write_text(newline)` (the 5 guarded hits the audit listed are filtered); pyutilz@d660504^ 2 = both seeds (`effect_flag_outside_its_effect.py:88` `ast.unparse`, `system/distributed.py:85` `md5(usedforsecurity)`); HEADs: pyutilz 0, llm_bench 0, py-ci-shared 0, dash_app_core 0, claude-usage-notifier 0, glossum 0 (after the `int.is_integer` rule; before it 1 FP, `refsuite/parse_answer.py:107` on a float), mlframe 1 true positive: `training/crash_diagnostics.py:212` `threading.__excepthook__` (3.10) under `requires-python >=3.9`, latent because the installed lambda always passes `_prev`. algopacksimple has no `requires-python`: `CorpusError` asks for `target=`.

Evidence: pyutilz declares `requires-python = ">=3.8"`. The 3.8/3.9 legs went red after merges again and again:
14dcfc5 (`Path.write_text(newline=)` 3.10+, `ast.Match` 3.10+), d660504 (`hashlib.md5(usedforsecurity=)` 3.9+,
`ast.unparse` 3.9+), 24486a0 (`ast.unparse` and the 3.8 `ast.Index` wrapper), 3e8f2bc, ee24e49, 00aef09. Each
cost a CI round on the matrix. No gate checks API level statically. py-ci-shared itself must support 3.9 (CLAUDE.md).
Proposal: `api_floor`. Run `vermin --target=<floor from requires-python>- --violations --no-parse-comments
--backport typing_extensions` (no `--eval-annotations`; with it, every `list[...]` under `from __future__ import
annotations` is reported, 465 hits on pyutilz). Then drop each hit whose line `_core` places inside a version guard:
`if sys.version_info >= (3, N)`, `hasattr(<mod>, "<name>")` in the same expression or an enclosing `if`, or
`try: ... except (ImportError, AttributeError, TypeError)`. Baseline the rest with `Baseline`.
Prototype (vermin 1.x via `uvx`):
- pyutilz@14dcfc5^ src, target 3.8: 6 hits. 1 real: `dev/block_extract.py` `Path.write_text(newline)`, the
  seed. The other 5 are guarded: `assert_in_loop.py:103` hasattr, `distributed.py:93` version_info,
  `claude_code_cli.py:472` try-import, `_catalogue.py:114` (comment documents a 3.8 fallback),
  `system/config.py` tomllib (try-import). vermin does NOT detect `ast.Match`.
- py-ci-shared src, target 3.9: 2 hits, both guarded (`_mutation_runner.py:200` version_info; `_toml_compat.py`
  tomllib).
- I checked the guards on 4 of the 7 FPs by reading the lines; the guard filter removes those 4 by construction.
Priority: medium. The CI matrix already catches these after the push, so the gain is one CI round per incident
(6 in 30 days in pyutilz). Cost low: a subprocess wrapper plus an AST guard filter. vermin becomes an optional dep.

### G-5 (Med) -- Line endings of the COMMITTED blobs: bare CR, mixed, CRLF in an LF repo

**Disposition:** RESOLVED -- `src/py_ci_shared/committed_line_endings.py`: index blobs via `git ls-files -s` + `git cat-file --batch`. git's `i/` class cannot be the filter: the seed file reads as `i/-text`, so a git-binary blob is still checked when it has no NUL byte and decodes as UTF-8. `-text`/`binary` attributes opt a path out. `eol=crlf` does not, because git stores such files with LF. Rules `committed-bare-cr`, `committed-mixed-endings`, `committed-crlf`; baseline key = rule + path. Tests: `tests/test_committed_line_endings.py` (12, real git); canary `tests/canary/committed_line_endings/` (`{CR}` written as a bare CR at run time). Validation: pyutilz@fa1aab0 1 = the seed (`constructor_param_overwritten.py`, 306 bare CR, 18 CRLF, 0 LF), pyutilz@60c59c3 0, pyutilz HEAD 0; mlframe 133 `committed-crlf` + 1 mixed (`raw_progress_2026-06-18.txt`, 40 CRLF + 1 LF), matching the audit's `git ls-files --eol` count; social 1 (`realtime_applications/sql/schema.sql`, `\r\r\n` twice, git calls it binary); autopsia 1 (`bench/gap_disease_src/PMC5892178.txt`, 2 bare CR + 55 CRLF, a downloaded source text: true positive whose fix is a `-text` line or renormalising); llm_bench, glossum, noema_app, dash_app_core, algopacksimple, claude-usage-notifier, py-ci-shared 0.

Evidence: `src/pyutilz/dev/code_audit/constructor_param_overwritten.py` was committed in fa1aab0 (2026-09-03) with
306 bare-CR line ends, 18 CRLF and 0 LF. It stayed that way until 60c59c3 (09-26), 23 days, and it broke black. I
measured each revision with `git show <rev>:<path>` and counted bytes. mlframe needed three "restore LF" commits on
one day (7e2c512e8, 572848918, 58af3ebe0). `lf_file_writes` checks code that writes files; nothing checks what is
committed. On Windows `core.autocrlf` hides the problem in the worktree, and the pre-commit `mixed-line-ending` hook
does not see a CR-only file as mixed.
Proposal: `committed_line_endings`. Run `git ls-files -z --eol` and take the `i/crlf` and `i/mixed` index entries
for text files. Also byte-scan every text blob from `git cat-file --batch` for a `\r` not followed by `\n`; git's
`i/` classification does not report bare CR, and the seed file would read as `i/crlf`. Honour `.gitattributes`
`eol=crlf` / `-text`. One finding per file with the three counts.
Prototype (`git ls-files --eol`, all 14 repos): mlframe's index today holds 133 `i/crlf` files (121 `.py`, mostly
`profiling/bench_*.py`, 5 `.md`, 4 `.json`) and 1 `i/mixed`
(`feature_selection/_benchmarks/wide_data_scaling/raw_progress_2026-06-18.txt`). The other 13 repos have 0. A
worktree byte scan (`proto_eol.py`) is the WRONG design: on glossum it reported 2448 of 2494 files CRLF, all of them
autocrlf artefacts.
FP risk: data fixtures that must be CRLF. They take a `.gitattributes` line, which the gate honours.
Priority: medium value x very low cost (~60 lines, no parsing).

### G-6 (Med) -- Library repos: every name a consumer imports still resolves

**Disposition:** RESOLVED -- new gate + CLI `consumer_import_census` (`src/py_ci_shared/consumer_import_census.py`): `find_consumer_import_breaks` at line 153, `assert_consumer_imports_resolve`, `python -m py_ci_shared.consumer_import_census --library . --package <pkg> --repos-file|--consumer ... [--since-ref <tag>]` (`--since-ref` extracts the reference with `git archive` and reports only regressions). Resolution goes through `unresolved_imports.ModuleIndex`; the library's alias map is read from module-level dict literals of `<package>/__init__.py` whose values are dotted modules inside the package (`library_alias_map`, line 72), so pyutilz's `_MODULE_ALIASES` is honoured; module `__getattr__`, star imports and consumer `try/except ImportError` guards are not judged. Validation: seed replayed (pyutilz `89eb1f6^` vs glossum at `5a05cefe`): 1/1, exactly `from pyutilz.llm.claude_code_provider import _RATE_LIMIT_PATTERN`. pyutilz HEAD vs the 9 other consumers: 1 finding, TP (`social/upwork/upwork_zendesk.py:28 from pyutilz.python import imitate_delay`: `pyutilz.python` never existed in pyutilz history; the module cannot import). Without the alias map the same run reports 102 false "module gone" lines on mlframe alone; with it, 0. mlframe, py_ci_shared and llm_bench as libraries vs all others: 0. Tests: `tests/test_consumer_import_census.py` (22, including `test_with_a_reference_only_regressions_are_reported`, `test_cli_since_ref_reports_only_what_broke_after_the_ref`); canary `tests/canary/consumer_import_census/`.

Evidence: pyutilz 89eb1f6: "1.1 moved _RATE_LIMIT_PATTERN, _RESET_TIME_PATTERN and _TIMEZONE_PATTERN into
claude_code_cli without re-exporting them, so glossum ... failed at import". `unresolved_imports` checks names
inside one repo, so it only fires in the CONSUMER, after the library has shipped. pyutilz and llm_bench keep local
`_api_snapshot.json` tests, but those cover the declared API, not what consumers actually import (here,
underscore names).
Proposal: `consumer_import_census`, a CLI for a library repo's CI or the nightly `corpus-drift.yml`. Check out the
consumers from `configs/consumers.toml` (`corpus_drift` already does), collect every
`from <lib>[.x] import name` and `import <lib>.x`, and resolve each against THIS checkout with `unresolved_imports`'
`ModuleIndex`. It must honour the library's alias map (pyutilz `__init__.py:20`, e.g. `"pythonlib":
"pyutilz.core.pythonlib"`) and module `__getattr__`. Report a name that resolved at the last release tag and does
not resolve now.
Prototype (`proto_consumer_imports.py`, AST only, no alias map):
- pyutilz@89eb1f6^ vs glossum: 92 imported names checked, 1 unresolved: `glossum/llm/providers/
  claude_code_provider.py:3 pyutilz.llm.claude_code_provider._RATE_LIMIT_PATTERN`, exactly the seed.
- pyutilz origin/master vs glossum + mlframe + llm_bench + autopsia + social + dash_app_core: 25 lines of the form
  "module pyutilz.pythonlib / pandaslib / strings ... gone", all in mlframe and all resolved by the alias map. That is
  why the map must be honoured. The run then died on `WinError 8` (out of memory on this machine) before printing
  its totals, so I have no per-name count for the full corpus.
Priority: medium (one incident, but it broke a downstream at import) x medium cost (reuses `corpus_drift` checkout
and `unresolved_imports`).

### G-7 (Med) -- resource_leak_guard: check the standard streams, cwd, sys.path and warning filters; and get it adopted

**Disposition:** RESOLVED -- `local_copy_report` judges every `conftest.py` under the test dirs by content (`include_conftest`, `src/py_ci_shared/local_copy_report.py:165`; `_conftest_copies` line 144) against the new `CONFTEST_SIGNATURES` (line 99): `resource_leak_checks:streams` = an autouse fixture, an identity comparison on `sys.stdout`/`sys.stderr`, and an assignment back to them. A conftest that imports `py_ci_shared.resource_leak_checks` is not reported. Measured read-only on fresh worktrees of origin HEAD: mlframe 1 finding off -> 2 on, the new one is `tests/conftest.py` (`_restore_closed_standard_streams`, the guard G-7 named); pyutilz 3 -> 3, glossum_backend_scripts 1 -> 1 (no conftest hit in 337 and 1104 parsed files). No consumer calls `local_copy_report` (git grep over every repo under Machine learning: only py-ci-shared's own README, CHANGELOG and an audit), so the default is `True`: no baseline can grow; `include_conftest=False` restores the old scan. Tests: `tests/test_local_copy_report.py::TestConftest` (5: the guard, near misses without a restore / without identity or autouse / importing the central check, a nested conftest outside the meta dirs).

Evidence: mlframe 1256fff4d: numba's `ColorShell.__exit__` calls colorama `deinit()`, which resets `sys.stdout`, so
later doctests printed to the console ("Expected: True, Got nothing"). It was found only by a full-file run. mlframe
then built its own conftest stream-leak guard (66d60766f "a leaked standard stream no longer errors a whole worker")
and a colorama fixture (127157a28). `standard_stream_restore` is static and sees only code in the repo, not a
third-party `deinit()`. `resource_leak_guard` checks processes, threads, sockets, `logging.disable` and env, not
streams. Adoption: a search of every local consumer's `pyproject.toml`/`conftest.py`/`pytest.ini`/`setup.cfg` and
`git grep leak_guard` in the 8 GitHub clones found 0 consumers enabling the plugin (the only hit is py-ci-shared's own
`pyproject.toml`).
Proposal: add `streams` (identity of `sys.stdout`, `sys.stderr`, `sys.stdin` against the before-setup snapshot,
allowing pytest's capture objects), `cwd`, `sys_path` and `warnings` (`warnings.filters` length and head) checks, with
the same restore-after-report behaviour. Add a `local_copy_report` signature for hand-rolled stream guards (mlframe's)
so they migrate. Add the plugin to the ADOPT list `adoption_matrix` reports.
FP risk: libraries that wrap streams once per process (colorama `init()` at import). Treat these like the existing
"variable ADDED during first import" rule: a change that coincides with a new module in `sys.modules` is reported once,
not per test.
Prototype: adoption count above. I did not prototype the stream check itself.
Priority: medium x low cost (~60 lines in an existing plugin).

### G-8 (Med) -- Imports of another package's vendored internals

**Disposition:** RESOLVED -- new gate `vendored_internal_imports` (`src/py_ci_shared/vendored_internal_imports.py`, `vendored_part` line 58, `assert_no_vendored_internal_imports`). Flags `import`/`from`/`importlib.import_module` of a path whose second or later segment is `externals|_externals|_vendor|vendor|_vendored|vendored|extern`, plus `requests.packages`/`urllib3.packages`; the repo's own packages (root or `src/`) and relative imports are first-party; the standalone-first fallback (vendored import inside an `except ImportError` whose `try` imports the same standalone name) is accepted without a marker; anything else needs `# vendored-ok: <reason>`. Validation over the ten consumers: mlframe 7, all TP (`joblib.externals.loky` x6 in `_step_pairmi.py:61`, `training/__init__.py:113`, `_tiny_rerank_process.py:142` and three tests, plus `test_rerank_worker_processes.py:125`, a deliberate regression test that needs the marker); the fixed cloudpickle line `_tiny_rerank_process.py:37` is the accepted fallback shape; mlframe's own `filters._vendored.infonet` is first-party and not reported. The other nine repos and py-ci-shared: 0. Tests: `tests/test_vendored_internal_imports.py` (25); canary `tests/canary/vendored_internal_imports/`.

Evidence: mlframe 7a8fd1d15: `from joblib.externals import cloudpickle`. joblib 1.6.0 dropped that copy, and 35 tests
of the sklearn-matrix job failed at import. deptry does not flag it because joblib is declared.
Proposal: `vendored_internal_imports`. Flag `import`/`from` of `<pkg>.externals`, `<pkg>._vendor`, `<pkg>.vendor`,
`<pkg>.extern`, `<pkg>._vendored` (also `pip._vendor`, `setuptools._vendor`, `pkg_resources.extern`). A line may stay
with `# vendored-ok: <reason>` when it has a guarded fallback to the standalone package. The fix the gate suggests:
import the standalone distribution and declare it.
Prototype (`git grep -E` over `*.py`): mlframe 8 sites, among them `_mrmr_fe_step/_step_pairmi.py:61`,
`training/__init__.py:113` (`joblib.externals.loky`), `_tiny_rerank_process.py:37` (the fixed cloudpickle line, now
behind a fallback) and `:142`. pyutilz, llm_bench and glossum have 0.
Priority: medium (one real 35-test break) x trivial cost (~40 lines, `scan_python`).

### G-9 (Med) -- External-fact tables carry a source and a dated check, and go stale visibly

**Disposition:** RESOLVED -- new gate `external_fact_tables` (`src/py_ci_shared/external_fact_tables.py`, `find_external_fact_table_problems` line 198, `find_expiring_fact_tables`, `find_undeclared_fact_tables`, `assert_external_fact_tables_current`). Declaration convention chosen as least invasive: the tables are listed once in the standard gate table, `[tool.py_ci_shared.gates.external_fact_tables] tables = {"src/pkg/mod.py:_PRICING" = 45}` (or the same mapping from a meta test), so the library modules only need the citation comment most of pyutilz's tables already carry; a renamed or deleted table is a finding. Citation = the contiguous comment block above the assignment or comments inside the literal before its first entry, holding an `http(s)://` URL or `domain.tld/path` and an ISO date (newest date wins). Findings: gone, uncited, undated, future-dated, older than its limit; expiring within `warn_days` are printed, never failed (no `warnings.warn`, so `-W error` cannot turn it red). Validation, declaring every table the name advisory finds: pyutilz 9 candidates, 3 findings, all TP (undated `openai_provider.py:70 _CACHE_HIT_COST` ("from the same pricing page"), `openai_provider.py:169 _CONTEXT_WINDOW`, `xai_provider.py:80 _CACHE_HIT_COST`); the other 6 pass in their current wording, including `docs.x.ai/docs/models, 2026-09-26` inside the literal. glossum `glossum/llm/models.py:585 PROVIDER_PRICING` undated (TP); social `llm_pricing.py:53` dated without a URL (TP). The NAME advisory alone is imprecise, as the report predicted: autopsia `DEFAULT_MISS_COST/DEFAULT_WORK_COST` and glossum `PHASE0_MAX_OUTPUT_REDUCE_BY_STAGE` are internal (3 of 13 candidates), which is why it is only an advisory and the gate works from the declaration. Tests: `tests/test_external_fact_tables.py` (26); canary `tests/canary/external_fact_tables/`.

Evidence: pyutilz 423fdc4, from live verification: "claude-sonnet-5-5 was missing (priced at double)", xAI "live
search cost was off by 40%", "retired model names are silently served and billed as other models", "Gemini: three
served models had wrong or missing prices". 60c59c3 was the earlier pass over the same tables. The tests then pinned
the new prices (`tests/test_llm_xai.py`, `test_llm_deepseek.py` changed in 423fdc4). `prose_numeric_claims` covers
counted facts in prose, `stale_comment_age` covers TODO dates. Nothing covers a literal table of facts copied from a
vendor page.
Proposal: `external_fact_tables`. The consumer DECLARES its tables in `[tool.py_ci_shared.external_fact_tables]` as
`"pkg/mod.py:_PRICING" = {max_age_days = 45}`. Each declared table must have, within the 6 lines above it, a
`Source: <url> (fetched|verified YYYY-MM-DD)` comment. The gate fails when the date is older than `max_age_days`
and warns from `max_age_days - 7`, like `stale_comment_age`'s early warning. It must also fail when a declared table
no longer exists (an unknown table is a finding). Optionally, a `find_undeclared` advisory lists module-level
numeric dicts whose name matches `PRIC|COST|CONTEXT_WINDOW|LIMITS` and are not declared.
Prototype (`proto_fact_tables.py`, name heuristic, no declaration):
- pyutilz src: 7 tables; 2 cite a URL, 3 are dated (7 days old). 4 undated: `deepseek_provider.py:39
  _CONTEXT_WINDOW`, `openai_provider.py:66 _CACHE_HIT_COST`, `openai_provider.py:140 _CONTEXT_WINDOW`,
  `xai_provider.py:77 _CACHE_HIT_COST`. Good shape already: `deepseek_provider.py:18` "Source:
  https://api-docs.deepseek.com/quick_start/pricing (fetched 2026-09-26)".
- glossum: 2 tables, 1 dated (71 days old); 1 undated (`scripts/_run_experiment/_model_caps.py:58`).
- autopsia: 2 hits, `reason/decision.py:149-150 DEFAULT_MISS_COST/DEFAULT_WORK_COST`. These are internal decision
  costs, not vendor facts, which is why the gate must work from a declaration and not from names.
- social: 1 table, dated 30 days.
Priority: medium value (wrong billing for weeks) x low cost (~120 lines).

### G-10 (Low) -- Required status-check contexts that no workflow produces

**Disposition:** RESOLVED -- new CLI `required_check_contexts` (`src/py_ci_shared/required_check_contexts.py`, `check_repo` line 87), a separate module rather than an edit to `ci_health` (reuses its `default_api`/`ApiError`). Required contexts (`contexts` and `checks[].context`) are compared with the check-run names and status contexts of the 5 newest commits of the CI branch: one commit is not enough, the first live run reported mlframe's `CI required checks` missing because CI on the head commit was still queued and had not created that job. 404 = `unprotected`; 403 (including gh's "Upgrade to GitHub Pro") = `unreadable` with a `::warning::` line, exit 1 under `--strict`. Live run 2026-10-03 over `configs/consumers.toml`: ok 2 (mlframe, pyutilz: 3 required each, all produced), unprotected 2 (llm_bench, claude-usage-notifier), unreadable 9, missing 0. Tests: `tests/test_required_check_contexts.py` (7, fake API: the `mypy-full / mypy-full` seed, older-commit and status contexts, the four error classes, CLI exits).

Evidence: brief, item (i): pyutilz once required `mypy-full / mypy-full`, which no workflow produced, so the merge
would block forever. I found no commit for it: the protection lives in GitHub settings, not git.
Proposal: extend `ci_health`, which already reads the Actions API. For each consumer read
`branches/<ci_branch>/protection/required_status_checks`. Compare `contexts` (and `checks[].context`) with the
check-run names on the newest default-branch commit plus the job names declared in `.github/workflows`. A required
context neither source produces is a finding. 403/404 is reported as "no protection readable", not as a pass.
Prototype (`gh api`): pyutilz requires `CI required checks; black / black; mypy (whole project, blocking,
completion-asserted)`. mlframe requires `CI required checks; mypy-full / mypy-full; black / black`. All are present
on master's newest commit (pyutilz 423fdc4, mlframe 1197b39e1): 0 findings. llm_bench and claude-usage-notifier: 404
(master not protected). The 10 private repos: 403 "Upgrade to GitHub Pro". The gate can see only 2 of 13 repos on
the current plan, hence Low.
Priority: low reach x low cost (~50 lines in `ci_health`).

### G-11 (Low) -- Consumer workflows: moving runner labels and no update channel for actions

**Disposition:** RESOLVED -- new gate + autofix `workflow_runner_labels` (`src/py_ci_shared/workflow_runner_labels.py`, `find_workflow_runner_label_problems` line 83, `fix_workflow_runner_labels`, `python -m py_ci_shared.workflow_runner_labels [--fix] [--label ubuntu=ubuntu-22.04] <repo>`), a separate module rather than an edit to `ci_workflow_paths`. Rules: `ubuntu|windows|macos-latest` anywhere in workflow YAML outside comments unless the line has `# moving-label-ok: <reason>`; and a repo with pinned non-local `uses:` and no Dependabot `github-actions` entry or Renovate config. Counts today: mlframe 45, pyutilz 22, social 11, glossum 10, llm_bench 7, algopacksimple 5, dash_app_core 3, claude-usage-notifier 2, autopsia 1 (106 occurrences, matrix values and `COVERAGE_OS` strings included, so above the report's 82 label count), and the update-channel finding in 7 repos (all but mlframe, pyutilz and noema_app, which has no workflows); py-ci-shared itself has 2 (`self-ci.yml:47,49`) and no channel. `--fix` was run on copies of the llm_bench, mlframe, pyutilz, glossum and social `.github` trees: only the label lines change, CRLF is kept, every YAML still parses, and a new `dependabot.yml` is written (an existing one is extended only when `updates:` is its last top-level key). The pins (`ubuntu-24.04`, `windows-2025`, `macos-15`) are what `-latest` pointed at when written and are overridable. Checking each action's `runs.using` stays rejected (report, rejected 2). Tests: `tests/test_workflow_runner_labels.py` (7).

Evidence: no breakage commit in 30 days (searched the four Python repos for
`ubuntu|node ?(20|24)|runner image|deprecat`). py-ci-shared's own `ci-health.yml` pins `ubuntu-24.04` and SHA-pins
actions. Counts in consumer workflows: `*-latest` labels, pyutilz 12, mlframe 24, llm_bench 7, glossum 9,
polyvocab_app 15, social 6, dash_app_core 3, claude-usage-notifier 2, flutter_app_core 2, algopacksimple 1,
autopsia 1 (82 total). `uses:@v<tag>` (not SHA): llm_bench 2, glossum 5, flutter_app_core 4. A Dependabot
`github-actions` ecosystem exists only in pyutilz, mlframe and polyvocab_app. Without one, a SHA pin never moves when
an action's Node runtime is retired.
Proposal: extend `ci_workflow_paths` (it already parses workflows) with two advisory rules. `runs-on` is a
versioned label, or carries `# moving-label-ok: <reason>`. A repo that SHA-pins `uses:` has a Dependabot (or Renovate)
`github-actions` entry. I rejected checking each action's `runs.using` for a deprecated Node version: it needs
network fetches per action, and Dependabot solves the same problem.
Priority: low (no incident) x very low cost.

### G-12 (Med) -- stale_comment_age: make the early warning the default

**Disposition:** RESOLVED -- (a) `python -m py_ci_shared.stale_comment_age --stale-warning-summary REPO [REPO ...] [--scan-dir D] [--max-age-days N] [--warn-days N]` (`stale_warning_summary`, `src/py_ci_shared/stale_comment_age.py:543`; `main` line 581): one line per checked-out repo ("N going stale within W days, M already older than L days"), scope and limits from the repo's own `[tool.py_ci_shared.gates.stale_comment_age]` table, else `.`/30/7, then `stale-comment early warnings: N`. A repo that cannot be dated (shallow, not a git tree, missing dir) is "not checked", counted, and makes the exit code 1. Run on the three consumers: mlframe 0 / 12 stale, pyutilz 1 / 0, glossum_backend_scripts 0 / 5, total 1. Tests: `tests/test_stale_comment_age.py::TestStaleWarningSummary` (3). (b) Precision of the commented-out-code detector. The populations are far below 40, so every flagged item was read: mlframe `src` (its gate's scope) 6 flagged now, 6 real (`# num_zerocross(arr),`, four lines in the vendored infonet model, `# example_highd()`); its baseline `_stale_comment_baseline.json` holds 34 commented-out-code entries, all 34 real (prints, plt calls, `torch.save`, a `super().init(...)`); pyutilz `src` 1 flagged, 0 real (`# self.stats.setdefault("k", 0)` in `stats_key_coverage.py`), and 2 of 2 false at 635d0c5^ (plus `# self._inc_stat("k")`, the line that commit reworded); glossum_backend_scripts `glossum`,`scripts` 0 flagged; py-ci-shared itself 1 flagged, 0 real (`# Path.open(mode, buffering, encoding, errors, newline)` in `lf_file_writes.py`). Precision before: 40/43 overall, mlframe 40/40, pyutilz 0/2, py-ci-shared 0/1. All three false positives are one class: an AST analyser labelling a branch with the call shape it matches. Fix: in a file that imports `ast`, a code-shaped comment whose called name is also a string literal of that file is a label (`_shapes_this_file_matches`, line 335, used in `_candidates`). After: mlframe 6/6 still flagged, pyutilz 0, pyutilz at 635d0c5^ 0, py-ci-shared 0. Tests: `tests/test_stale_comment_age.py::TestShapeLabelsInAnalysers` (3: the labels are not code; a real disabled call in the same analyser still is; the same label outside an AST analyser still is).

Evidence: pyutilz bf44e93: nine commented-out lines crossed the 30-day line, and the ubuntu leg went red on a
calendar day with no code change. e53587c (HEAD) added `warn_days`, but the default is `0`
(`stale_comment_age.py:446`), so no consumer gets the warning unless it opts in. N-21 (10_new_code) reports that the
warning fails under `-W error`. Fixing that first is the precondition.
Proposal: default `warn_days=7`, emitted as a pytest warning that `-W error` lets through (fixes N-21), plus a
`ci_health`-style summary line. Separate small extension: pyutilz 635d0c5 shows the gate reading prose that
describes code ("the increment-helper branch") as commented-out code. Sample the gate's commented-out-code
classifier on the consumers' current baseline entries and record its precision before changing the default, so the
warning does not train people to ignore it.
Priority: medium value (calendar-day red is the worst kind: no diff to blame) x trivial cost.

### G-13 (Low) -- Commit metadata policy: forbidden trailers and BOM-prefixed subjects

**Disposition:** RESOLVED -- new gate + CLI `commit_metadata` (`src/py_ci_shared/commit_metadata.py`, `check_message` line 111, `find_commit_metadata_problems`, `assert_commit_metadata`, `python -m py_ci_shared.commit_metadata --message-file <f> | --range A..B [--forbid Key[: glob]]`). Rules: `bom-subject` (U+FEFF anywhere in the subject), `invisible-subject` (first character a control, format, private-use or whitespace character), `forbidden-trailer` (case-insensitive key, optional value glob). The list defaults to EMPTY; it is read from `[tool.py_ci_shared.gates.commit_metadata] forbidden_trailers` (the plugin's own table, so both CLI and gate read one place). Merges and `*[bot]` authors are skipped; the bot suffix is matched literally (a first draft used `fnmatch`, where `[bot]` is a character class that skipped every author ending in b, o or t, and found 91 of 100 glossum trailers; pinned by `test_bots_and_merges_are_skipped_but_bot_suffix_is_literal`). Validation over 2026-09-01..HEAD of the five local repos (1504 non-merge commits): BOM 1/1, pyutilz `086771d`, 0 false positives; with `--forbid Co-Authored-By`: mlframe 251, pyutilz 44, llm_bench 3, glossum 100, noema_app 20, equal to git's own `%(trailers:key=Co-authored-by)` count minus the 2 dependabot commits in pyutilz. Tests: `tests/test_commit_metadata.py` (20, on real throwaway repositories).

Evidence:
- The memory rule "NEVER Co-Authored-By" (feedback_no_coauthor). Commits since 09-01 on origin/master that carry
  `Co-Authored-By`: mlframe 253 of 947, pyutilz 46 of 188, noema_app 20 of 57. Example: mlframe 127157a28 ends with
  "Co-Authored-By: Claude Sonnet 5.5". The Claude Code harness instructs agents to add this trailer, which conflicts
  with the owner's rule. The gate should enforce whatever list the owner configures; it does not decide the policy.
- The memory rule "PS5 UTF8 = BOM in commit subject": pyutilz 086771d's subject starts with U+FEFF
  ("﻿code_audit: settings_container_field_needs_nodecode"). 1 instance in 30 days.
Proposal: `commit_metadata`. A `commit-msg` hook plus a range check (same CLI shape as G-1): no subject starting with
U+FEFF or other non-printing characters, and no trailer from `[tool.py_ci_shared.commit_metadata] forbidden_trailers`
(empty by default).
Priority: low-medium (policy, not correctness) x trivial cost. It can share G-1's range scanner.

### G-14 (Low) -- Closed audit rounds are append-only

**Disposition:** RESOLVED -- new gate + CLI `closed_audit_rounds` (`src/py_ci_shared/closed_audit_rounds.py`, `find_closed_round_edits` line 107, `assert_closed_audit_rounds_append_only`, `python -m py_ci_shared.closed_audit_rounds --base <ref>`), a separate module rather than an `audit_round_format` option. Files under `audits/implemented/` at the merge base may only gain lines; deleted/rewritten lines (per hunk), deleted files and files moved out are findings; moving an open round in is not; line-ending-only changes are ignored; a commit touching the file with an `Audit-Edit: <reason>` trailer exempts it. Prototype measurement per commit over 2026-09-01..HEAD of py-ci-shared, pyutilz, glossum and noema_app (29 commits touching a closed round): strict append-only flagged 9, all disposition or tracker-status updates (e.g. pyutilz `804ee77`, `824efc8` revising `**Disposition**` lines, glossum `bc71060` restructuring TRACKER tables). That is a false-positive rate the proposal did not anticipate, so `Disposition` lines and rows of `TRACKER*.md` tables are mutable by design; a table row anywhere else stays frozen. After that: 1 flagged, glossum `928b2795a9` rewriting a closed report's evidence sentence (the class itself, legitimately edited, which under the gate needs the trailer). Tests: `tests/test_closed_audit_rounds.py` (9, real repositories).

Evidence: memory, "Skip lists must name `_audits` too": a bulk rewrite falsified 5 reports. I found no SHA for it in
the 30-day window, so this rests on the memory note alone. `audit_round_format` checks that rounds are countable and
that an `implemented/` round has no open row (lines 295-300). `audit_disposition_parity` checks that RESOLVED names
real artefacts. Neither notices that a CLOSED round's finding text changed.
Proposal: in `audit_round_format`, opt-in `frozen_since=<base ref>`. A file under `audits/implemented/**` may only gain
lines relative to the merge base (new dispositions appended). Any deleted or modified line is a finding, unless the
commit range carries an `Audit-Edit: <reason>` trailer. It uses `git_changed_lines`.
Priority: low (one remembered incident) x low cost.

---

## Memory-index lessons: which can be enforced mechanically

Enforceable in CI or a hook (proposed above, or already a gate):

| Memory line | Mechanism |
|---|---|
| Commit message file must be session-scoped; background `git commit -F -` makes no commit | Not in CI. The symptom, a commit whose subject equals another recent commit's subject on any branch, is detectable but weak. Not proposed |
| NEVER Co-Authored-By; PS5 UTF8 BOM in commit subject | G-13 |
| Skip lists must name `_audits`; never `git add -A` | G-14 (closed rounds append-only). The staging habit itself is not observable |
| Pre-commit gotchas, all gates before commit, never `\| tail` hook output | G-1 detects the outcome (unverified commit), not the habit |
| write_bytes on Windows, mixed line endings, auto-fix retry | `lf_file_writes` (writers) + G-5 (committed blobs) |
| JSON hash sort_keys | exists: `hash_key_determinism` |
| np.save appends .npy | exists: `atomic_write_staging` (NEW-6 lineage) |
| Runtime caches break pickle | exists: `pickle_state_completeness` |
| No reload without snapshot | exists: `module_reload_safety` |
| Numba seed int64 | exists: `numba_seed_range` |
| `max_tokens=0` kills the derived timeout | exists: `sentinel_or_fallback`; its blind spot is G-3 |
| Pin --randomly-seed | exists: `randomly_seed_guard` (runtime) |
| coverage fail_under gates every cov run | exists: `coverage_config_parity` |
| addopts applies to every run | exists: `pytest_addopts_path_runs` |
| Polars eq_missing; plotly add_annotation O(n^2) | exist: `polars_null_equality`, `plotly_annotation_loop` |
| No audit junk / audit phase in comments | Partly mechanical: a regex for wave/round IDs (`\b[A-Z]{2,4}-\d{1,3}\b`, `wave ?\d`) in code comments. Not proposed: IDs like `PRF-20` are also legitimate cross-references the owner uses in commit subjects, so FP is high without a per-repo prefix list |
| No `--` in prose; comments to 160 chars | Line length: ruff E501 in `ruff-base.toml`. `--`: a regex over comments and docs would work, but it is taste. Not proposed |
| Stale .pyc reverts edits | Not CI-observable (local interpreter state) |
| Heredoc mangles backslashes | No stable signature. Some outputs are caught by black and ruff (invalid escapes: the `SyntaxWarning: "\R"` lines in my prototype run came from consumer files). Not proposed |
| Store everything a paid LLM returns | exists: `llm_call_archive_gate`, `prompt_field_parity` |
| SQL predicate needs a negative control; fake cursors prove nothing | Partly: `statement_compilation`, `sql_verify`. The negative-control rule is test design, not mechanical |
| Read the TRACKER row before re-measuring; verify dispositions against the tree | exist: `tracker_summary_parity`, `audit_disposition_parity`, `disposition_test_references` |
| Agents: no full suites, wait for notifications, kill own processes only | Agent conduct, not repo state. Not enforceable in CI |

---

## Evaluated and rejected

1. A "job that can never pass" detector separate from `ci_health`. The pip-audit case (e) is a workflow red for 26
   days (09-07 to 10-03). `ci_health --max-red-days 2` reports exactly that. A second detector keyed on "same failing
   step every run" adds nothing a human does not see in the first report.
2. Checking each action's `runs.using` for retired Node runtimes. It needs a network fetch per action per run, and
   Dependabot's github-actions ecosystem (G-11) fixes the cause.
3. A pre-push LOC hook for (d). The repo already runs `loc_budget` in pre-commit. The failure was a bypass, so G-1 is
   the fix.
4. Running the 3.8 matrix locally before pushing. Too slow; G-4 is the static replacement.
5. Branch-protection checks for private repos. The API returns 403 on the current plan for 10 of 13 consumers.
6. Detecting fact tables by name alone (G-9 heuristic). The autopsia `DEFAULT_*_COST` constants are internal; the
   gate must work from a declaration.
7. A worktree byte scan for line endings. It reports 2448 false CRLF files on glossum because of `core.autocrlf`; G-5
   reads index blobs instead.
8. Turning the existing local `_api_snapshot.json` tests (pyutilz, llm_bench) into a central gate. They pin the
   declared API. The incident was an undeclared underscore name a consumer imported, so G-6 measures the real import
   surface instead.
9. A "commit subject duplicates another session's commit" check (memory: session-scoped message file). It is weak
   signal: identical subjects are legitimate for pin bumps ("ci: pin py-ci-shared to v1.18.0" appears in 8 repos).

---

## Prioritised top 10

1. G-1 (High): `hook_attestation`. A commit-msg trailer plus a pushed-range check flags commits that bypassed the hooks (mlframe 58715253b, autopsia 70c28afa, llm_bench 57e79a7).
2. G-2 (High): `sibling_floor_skew`. Catches a pyproject sibling floor above the revision CI installs (llm_bench 57e79a7: 6/6 sites caught, 0 FP across 13 HEADs).
3. G-3 (High): `optional_truthiness` follows `self.<attr>` and one forwarding hop. It finds 3 live zero-budget bugs on mlframe master: boruta_shap `_fit_explain.py:535`, shap_proxied `_shap_proxied_fit.py:184`, `_cb_gpu_monitor.py:391`.
4. G-5 (Med): `committed_line_endings`. Scans index blobs for bare CR, mixed and unwanted CRLF (pyutilz fa1aab0..60c59c3; mlframe has 133 CRLF + 1 mixed today).
5. G-12 (Med): `stale_comment_age` early warning on by default (after N-21), so commented-out code stops turning a build red on a calendar day (pyutilz bf44e93).
6. G-4 (Med): `api_floor`, a guard-aware vermin run against `requires-python` (pyutilz 14dcfc5, d660504: 1 real hit and 5 guarded FPs that the AST filter removes).
7. G-6 (Med): `consumer_import_census`, so library changes cannot break a downstream import (pyutilz 89eb1f6: 1/1 caught; must honour pyutilz's alias map).
8. G-8 (Med): `vendored_internal_imports` (mlframe 7a8fd1d15 broke 35 tests; 8 such imports remain in mlframe).
9. G-7 (Med): `resource_leak_guard` gains stream/cwd/sys.path/warnings checks; it currently has 0 of 13 consumers enabled (mlframe 1256fff4d colorama).
10. G-9 (Med): `external_fact_tables`, so declared vendor price and limit tables carry a source URL and a dated check that expires (pyutilz 423fdc4; 4 undated tables in pyutilz today).
