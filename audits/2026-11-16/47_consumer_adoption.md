# 47 consumer_adoption: round 2026-11-16 (T)

Audited commit `48e49b1` (master; `pyproject.toml` already says 1.23.12, latest tag `v1.23.11`). Read-only. Consumer trees were read as a snapshot at about 02:40 to 03:20 local on 2026-11-16 (mlframe was moving: its pins were read at `d2ef3afd6`).

Method and commands (scratch: `scratchpad\audit_T\47\`):
- `python -m py_ci_shared.adoption_matrix --resolve-in py-ci-shared --allow-behind 2 --waivers configs/pin_waivers.toml` over all 11 sibling consumers that are on disk (pyutilz, mlframe, social, dash_app_core, llm_bench, glossum_backend_scripts, flutter_app_core, polyvocab_app, noema_app, algopacksimple, claude-usage-notifier), run with `PYTHONPATH=py-ci-shared/src` because the interpreter's installed copy is 1.23.9 and has no `--waivers`. It took about 25 minutes, mostly social and mlframe. Output: `matrix.txt` (exit 1: social, dash_app_core, llm_bench, polyvocab_app). Smaller runs for `finance` and `flutter_uptime_monitor` (`m_two.txt`).
- `adoption_sha_ref` over eight consumers (`repos.toml`).
- A script (`inputs.py`) that reads every consumer `uses: fingoldo/py-ci-shared/.github/workflows/X@ref`, `git show ref:path` of the reusable workflow, and compares the caller's `with:` keys to that workflow's `workflow_call` inputs (no unknown or missing-required inputs found; see Considered).
- `ci_pin_version_skew.find_pin_skew_findings` over mlframe, pyutilz, llm_bench, social, algopacksimple.
- A clean-clone simulation in `scratchpad\audit_T\47\clean\` (`git init`, `pre-commit install`, `install_safe_hook`, `py-ci-shared run-all` / `run` / `refresh` / `tool` / `config-path`, the `pinned-tool-versions` and `mypy-gate` hook entries, a ruff `extend = "$PY_CI_SHARED_DIR/..."` with the variable unset).
- Hook files of 11 consumer clones (`.git/hooks/pre-commit`, `pre-merge-commit`).

Prior state read first: `audits/2026-11-11/47_consumer_adoption.md` and TRACKER rows 47-F1..F12, 47-R6..R12, 47-S13..S15 (all RESOLVED or by-policy; none re-proposed below).

Not covered: whether each consumer's baseline files are still valid for the current gates. Checked only that no consumer baseline carries a `NEEDS-JUSTIFICATION` note (pyutilz and mlframe `tests/test_meta/*.json`: 0 hits). Re-running every consumer's gates against 1.23.12 needs the consumers' test environments and is a consumer-side job; I did not run consumer pytest (read-only, moving tree). The GitHub-hosted behaviours (a caller passing an input the callee does not declare; `consumer-pins.yml` itself) were not run, no network.

Current pin table (matrix.txt, latest `v1.23.11`):

| consumer | pins | ref | behind | notes |
|---|---|---|---|---|
| mlframe | 28 | v1.23.11 (tag commit) | 0 | clean |
| pyutilz | 14 | `89c04e6` labelled `v1.23.9` | 2 | commit is after v1.23.9 (first contained in v1.23.10) |
| glossum_backend_scripts, algopacksimple, claude-usage-notifier, flutter_app_core, noema_app, flutter_uptime_monitor | 1 to 10 | v1.23.9 (tag commit) | 2 | at the limit |
| llm_bench, dash_app_core | 7, 1 | `e13229e` labelled `v1.23.8` | 4 | RED |
| social | 18 | `e13229e`, `177acd3` (v1.23.3), unpinned | 4 and 9 | RED, pins disagree, 2 moving |
| polyvocab_app | 1 | v1.22.10 | 13 | RED |

No consumer uses `gates.yml`; dash_app_core, llm_bench and algopacksimple run `py-ci-shared run-all` from `[tool.py_ci_shared]`; pyutilz and mlframe use hand-written meta-tests (149 test files import py_ci_shared) and no `[tool.py_ci_shared]` table.

## 47-T1 (P2) The 2-release window is counted in releases, not days: seven consumers sit exactly at the limit and the next release turns them all red

- **Evidence:** `matrix.txt` summary: pyutilz, glossum_backend_scripts, flutter_app_core, noema_app, algopacksimple, claude-usage-notifier (and flutter_uptime_monitor, `m_two.txt`) are "2 releases behind" with exit 0; llm_bench, dash_app_core, social, polyvocab_app are 4, 4, 9, 13 behind and fail. `git for-each-ref refs/tags`: v1.23.8 and v1.23.9 were both created 2026-11-07, v1.23.10 and v1.23.11 both 2026-11-13. `pyproject.toml:7` is already `1.23.12`. A consumer that bumped to v1.23.9 on 2026-11-07 had three days of tolerance and is now one release from red; one more tag (1.23.12) makes seven consumers fail `stale-pin` in the same 05:17 run.
- **Impact:** the tolerance exists to give owners time to bump; two patch tags cut the same day spend all of it. A release train that cuts several tags per day makes "2 behind" a few days at most, so the daily job reports a wall of red on the day after any release burst, and the useful signal (a consumer that is genuinely abandoned: polyvocab_app, 13 behind) is buried among consumers that are merely one day late.
- **Proposed fix:** count lag in releases AND age: fail only when the pin is more than `--allow-behind` releases behind AND the first release it lacks is older than a grace period (`--grace-days N`, default 0 so nothing changes unless the workflow passes it; consumer-pins.yml would pass 7). `releases_behind` (`adoption_matrix.py`, near `:181`) already has the tag list; creator dates come from `git for-each-ref`. ADDITIVE (new flag, default off); turning it on in consumer-pins.yml is a workflow decision for the owner.
- **Disposition:** FIXED (behaviour change, `--grace-days`, default 0; changelog: What to do).

## 47-T2 (P3) `configs/pin_waivers.toml` says every pin is within tolerance; four consumers are not

- **Evidence:** `configs/pin_waivers.toml:5`: "No consumer needs a waiver today: every pin is within the tolerance." The matrix run above has 4 repos failing `stale-pin` (llm_bench, dash_app_core 4 behind; social 9; polyvocab_app 13), and the file has no `[[waiver]]` row. The statement was true when 47-R7 closed (2026-11-11) and stopped being true with v1.23.10/11 on 2026-11-13; it is a hand-written claim that nothing re-checks.
- **Impact:** a reader of the waiver file concludes the red consumer-pins job is a bug in the job, not four overdue bumps (the 47-R7 mistake again, one release later). It also hides the decision the owner has to make: bump, or write dated waivers.
- **Proposed fix:** delete the sentence (the file's own mechanism already says "add a row only for ..."), or make `tests/test_adoption_waivers.py` fail when the header claims "within the tolerance" while rows is empty is not checkable offline, so remove the claim. ADDITIVE (comment only).
- **Disposition:** FIXED (`configs/pin_waivers.toml` header no longer claims it; test `test_adoption_docs_47`).

## 47-T3 (P2) `install_safe_hook` reports success without checking that the hook's interpreter can run the wrapper; the next commit fails with a bare traceback and no remedy

- **Evidence:** `src/py_ci_shared/install_safe_hook.py:72-91` rewrites `-mpre_commit` to `-m py_ci_shared.safe_precommit` and prints `Patched ...`, exit 0, without looking at `INSTALL_PYTHON`. Demonstration in `clean\` (pre-commit's `INSTALL_PYTHON` pointed at a venv without the package, as when pre-commit was installed into a different environment than the project's):
  ```
  $ python -m py_ci_shared.install_safe_hook
  Patched ...\.git\hooks\pre-commit: `python -mpre_commit` -> `python -m py_ci_shared.safe_precommit`.   (rc=0)
  $ git commit -m t
  ...\v1\Scripts\python.exe: Error while finding module specification for 'py_ci_shared.safe_precommit' (ModuleNotFoundError: No module named 'py_ci_shared')   (rc=1, commit blocked)
  ```
  The second variant (module found, `pre_commit` missing) ends in `safe_precommit.py:205` `from pre_commit.main import main` with a full traceback: `ModuleNotFoundError: No module named 'pre_commit'`. Neither message says "re-run `pre-commit install -f` from the project environment" or "pip install py-ci-shared there".
- **Impact:** every commit from every git client (IDE integrations, GUI clients) is blocked until the owner works out which interpreter the hook baked in. README `:1020` says it "works on any machine as soon as ... makes `py_ci_shared` importable in that Python", which is the condition nothing checks. The patched hook has no fallback either: the `elif command -v pre-commit` branch of pre-commit's template only runs when `INSTALL_PYTHON` is not executable.
- **Proposed fix:** in `_patch_one`, run `[INSTALL_PYTHON, "-c", "import py_ci_shared.safe_precommit, pre_commit"]` (INSTALL_PYTHON parsed from the hook text; fall back to `sys.executable` when absent) and refuse to patch with a message naming the interpreter and the fix; in `safe_precommit.main` wrap the `pre_commit` import and print "pre-commit is not installed in <sys.executable>; install it there or re-run `pre-commit install -f`". ADDITIVE (new refusal only where the patched hook would have failed anyway).
- **Disposition:** FIXED (`install_safe_hook` probes the hook interpreter and refuses; `safe_precommit` names the missing pre-commit; tests `test_install_safe_hook_check`).

## 47-T4 (P3) Nothing says whether a clone's hook is patched, and none of the eight real pre-commit clones is

- **Evidence:** hook files read: pyutilz, mlframe (both `pre-commit` and `pre-merge-commit`), social, dash_app_core, llm_bench, glossum_backend_scripts, algopacksimple, claude-usage-notifier: `grep -c safe_precommit` is 0 in every one. `grep -rn "safe_precommit\|install_safe_hook" pyutilz/CLAUDE.md mlframe/CLAUDE.md pyutilz/README.md mlframe/README.md` returns nothing: no consumer documents the step. `install_safe_hook.main` rejects any argument (`:98-100`), so there is no `--check`. `hook_attestation.py` only mentions safe_precommit in a docstring. The README `:1026` states the step must be repeated after each `pre-commit install`, so a patched clone silently reverts.
- **Impact:** the concurrent-session stash race that `safe_precommit` exists for (a whole commit aborted after the hooks passed) is open in every clone, and the README's "one-time per clone ... re-run after each pre-commit install" has no sensor. This repo's own hook-file note (`install_safe_hook.py:15-23`) describes a real merge-commit gap found on mlframe, and mlframe's `pre-merge-commit` hook is still unpatched.
- **Proposed fix:** `python -m py_ci_shared.install_safe_hook --check` (exit 1 and name each unpatched or missing hook; exit 0 when patched), and a `py-ci-shared-hooks-patched` entry in `.pre-commit-hooks.yaml` (stage `manual`, so it never blocks a commit) that consumers can run. ADDITIVE.
- **Disposition:** FIXED (`install_safe_hook --check` and manual manifest hook `py-ci-shared-hooks-patched`).

## 47-T5 (P2) `adoption_sha_ref` tells three callers to add an input that the called workflow does not have

- **Evidence:** `adoption_sha_ref.py:23-24` flags every `uses: .../workflows/<any>.yml@<non-release sha>` not paired with `py-ci-shared-ref`; it never asks whether that workflow declares the input. `grep py-ci-shared-ref .github/workflows/lint-blocking.yml mypy-full.yml docs.yml` finds nothing (the input exists only in `ruff-blocking.yml`, `black-filtered.yml`, `lint-advisory.yml`, `gates.yml`; the `workflow_call` blocks listed in `inputs.py` output). Run:
  ```
  ::warning title=uses-sha-without-ref-input::pyutilz .github/workflows/docs.yml:33: 89c04e629d31
  ::warning title=uses-sha-without-ref-input::llm_bench .github/workflows/ci.yml:264: e13229e5707e      (lint-blocking.yml)
  ::warning title=uses-sha-without-ref-input::llm_bench .github/workflows/mypy-full.yml:36: e13229e5707e
  uses-sha-without-ref-input: 3 pin(s) to a between-release SHA without py-ci-shared-ref
  ```
  All three are false: `with: py-ci-shared-ref:` on those workflows is an undefined input, which GitHub rejects at workflow load.
- **Impact:** the daily job warns, and the message tells the owner to follow README `:617-623`; doing so would break the consumer's CI. It is report-only today (47-F10), but the decision to make it failing "once consumers carry the input" can never be met for these three.
- **Proposed fix:** read the `workflow_call.inputs` of the pinned workflow (`git show <sha>:.github/workflows/<name>` in the `--resolve-in` checkout, or the workflow in the checkout at HEAD) and skip workflows with no `py-ci-shared-ref` input. ADDITIVE (removes false warnings only).
- **Disposition:** FIXED (declared input read from the pinned workflow at that SHA; test `test_adoption_sha_ref_inputs`).

## 47-T6 (P3) `adoption_sha_ref` has a different command line from `adoption_matrix` and cannot be pointed at a repo

- **Evidence:** `python -m py_ci_shared.adoption_sha_ref --help`: `--repos-file REPOS_FILE` and `--resolve-in` are both required, no positional repos; `adoption_matrix` takes positional roots plus an optional `--repos-file`. Run with roots: `error: the following arguments are required: --repos-file`. The docstring example (`:9`) shows `--repos-file repos.toml` but not the file format; `--help` says only "the repos file a checkout wrote". A hand-written file needs `[[repo]]` tables with `name` and `path` (I had to read `load_repo_list`); `repos = [...]` form also works.
- **Impact:** an owner who wants to see what the job warns about on one working clone must first write a TOML file.
- **Proposed fix:** accept positional `repos` like adoption_matrix and reuse `load_repo_list`/`_unique_names`; make `--resolve-in` default to the package's own checkout when run inside one. ADDITIVE.
- **Disposition:** FIXED (positional roots; `--resolve-in` defaults to the package checkout).

## 47-T7 (P2) A repo that runs py_ci_shared in its hooks but pins nothing, and is not in `consumers.toml`, is invisible and reads as healthy

- **Evidence:** `C:\Users\Admin\Machine learning\finance` (remote `fingoldo/finance`, 19 tracked `.py` files): `.pre-commit-config.yaml:37` runs `python -m py_ci_shared.black_filtered_apply --config pyproject.toml --check`; its only dependency file is `pyproject.toml`, which names no py-ci-shared. `configs/consumers.toml` lists 12 repos and not `finance`. `adoption_matrix finance` (`m_two.txt`): `pins 0 | distinct refs none | pins agree yes | releases behind ? | modules used 1`, `## Findings: None.`, exit 0. Same shape for the duplicate local clone `claude_notifier` (same remote as `claude-usage-notifier`).
- **Impact:** the hook works only on a machine that happens to have py-ci-shared installed; a fresh clone fails its first commit with "No module named py_ci_shared". The package's own adoption report cannot see it: neither the lag check nor the daily job covers a consumer that is not listed, and a listed one with zero pins would pass.
- **Proposed fix:** (a) add `finance` to `configs/consumers.toml` (ADDITIVE; repo public/private flag to be set by the owner); (b) in `adoption_matrix`, a new finding `unpinned-use` when a repo's `modules` is non-empty and `pins` is empty (BEHAVIOUR CHANGE for any listed repo that uses modules without a pin; today none of the 11 on disk is affected, so the CHANGELOG entry reads "Behaviour change ... What to do: pin py-ci-shared in the repo's dev dependencies"); (c) a `consumer_import_census`-style listing of sibling repos that name `py_ci_shared` but are not in `consumers.toml`.
- **Disposition:** FIXED for (b) `unpinned-use` (behaviour change); DEFERRED for (a) and (c): adding `finance` to `configs/consumers.toml` needs the owner to set its public/private flag, and the sibling-repo census is a new scanner that needs a scope decision.

## 47-T8 (P2) The upgrade is a manual edit of 14 to 28 lines in up to 9 files per repo, with no tool and no list of the places

- **Evidence:** pins per repo (matrix): mlframe 28, pyutilz 14, social 18, glossum_backend_scripts 10, algopacksimple 8, llm_bench 7, claude-usage-notifier 6. pyutilz's 14 are spread over `black-filtered.yml` (2), `ci.yml` (7), `codecov-full.yml`, `numba-coverage.yml`, `docs.yml`, `requirements-dev.txt` and the pre-commit `rev:` (`.pre-commit-config.yaml:299`), each a workflow `uses:`, a `py-ci-shared-ref:` input or a package pin, and each with a `# vX.Y.Z` comment to rewrite. README "Pinning and releases" (`:615-624`) says what a pin is but not which lines move together. No `bump`/`upgrade` module exists in `src/py_ci_shared` (listing). The owner's scratch `old_pin.py`/`new_pin.py` at the workspace root are the de-facto tool and are not in the package.
- **Impact:** a partial bump fails only after the fact: `pins-disagree` (social now shows 3 refs) or `ci-pin-installed-newer`; social, llm_bench, dash_app_core still carry a stale and a wrong comment next to the new SHA. The v1.23.9 -> v1.23.11 bump touched 28 lines in mlframe and was done completely there, but pyutilz stayed on a between-release SHA labelled `v1.23.9`.
- **Proposed fix:** `python -m py_ci_shared.bump_pin <tag> [REPO]` (dry run by default, `--write` rewrites every `fingoldo/py-ci-shared...@<ref>` and `py-ci-shared-ref:` occurrence in `.github/`, `.pre-commit-config.yaml`, `pyproject.toml`, `requirements*.txt` to the tag's commit and rewrites the trailing `# vX.Y.Z`), reusing `_adoption_pins` for discovery. ADDITIVE. List the files to touch in README "Pinning and releases".
- **Disposition:** DEFERRED: a `bump_pin` tool would rewrite files in the consumer repos (9 file kinds, `# vX.Y.Z` comments); the rewrite scope and whether it may write into other repos is an owner decision. Not built in this round.

## 47-T9 (P3) `ci-pin-comment-not-release` is still report-only and still fires on four consumers; `--strict-pin-comments` is not used by any workflow

- **Evidence:** matrix findings: pyutilz 12 lines (`# v1.23.9` on `89c04e6`, which `git tag --contains` puts first in v1.23.10), social 17, llm_bench 5, dash_app_core 1 (`# v1.23.8` on `e13229e`, whose tag commit is `630d5b7`). `consumer-pins.yml:66-67` does not pass `--strict-pin-comments`; `failing` (`adoption_matrix.py:403-411`) counts comments only when `enforce_comments`. So pyutilz's only findings are 12 wrong comments and the run's error line names only `social, dash_app_core, llm_bench, polyvocab_app` (pyutilz absent). 47-F2 recorded "correcting the wrong comments is work in the consumer repos"; five days later none of the four has done it, and no sensor would tell. The suggested fix text is also wrong for these pins: "write `master e13229e`" names `master` for a commit that is four releases back.
- **Impact:** `ci_pin_version_skew` and every human reading `# v1.23.9` believe pyutilz runs v1.23.9 gates; it runs the one commit after it. The `--strict-pin-comments` mechanism was built for exactly this and has no caller.
- **Proposed fix:** word the fix with the nearest release containing the commit instead of `master <sha>` ("the pin is in v1.23.10, not v1.23.9: pin the tag commit `5264e5d` / `2e3181f`, or write `# between v1.23.9 and v1.23.10`"), and turn `--strict-pin-comments` on in consumer-pins.yml once the four consumers are corrected (the bump to the current tag does it, T8). Wording change ADDITIVE; enabling strict is a BEHAVIOUR CHANGE for the job only.
- **Disposition:** FIXED (wording names the tag commit; test `test_adoption_unpinned_use`). Enabling `--strict-pin-comments` in consumer-pins.yml is DEFERRED until the four consumers are corrected, which depends on 47-T8.

## 47-T10 (P3) One root cause is reported once per pin line, so a single stale or wrong pin becomes 7 to 25 identical findings

- **Evidence:** `matrix.txt` Findings: llm_bench 7 `stale-pin` lines plus 5 `ci-pin-comment-not-release` lines for one SHA; social 18 and 17; `ci_pin_version_skew` on mlframe returns 25 findings with the identical text `py-ci-shared 1.23.12 is installed but CI pins 1.23.11 ...` (one per `uses:` line), pyutilz 10, social 14. The job summary and the `::error` line count repos, but the artifact and the pytest failure of the consumer's meta-test list every line.
- **Impact:** the line that matters (the one fix: bump) is repeated; real distinct problems (a `moving-pin`, `swallowed` ImportError in social `_live_dsn.py:38`) are hidden between them.
- **Proposed fix:** group findings by (repo, rule, ref) in `_finding_lines`, listing `path:line` once per group, and make `ci_pin_version_skew` emit one finding per distinct pinned version with the file:line list. Both change finding text and count, not outcome; BEHAVIOUR CHANGE only for a consumer that pins the old count in a test (none found); CHANGELOG entry "Nothing to do".
- **Disposition:** FIXED (`_grouped_finding_lines`: one line per rule and pinned ref, listing the files).

## 47-T11 (P3) README gives adopters no starting path, and three of its adoption examples are stale

- **Evidence:** `grep -i "quick start|getting started|adopting|onboard" README.md CLAUDE.md` finds one unrelated line (`:919`). The steps a new repo needs are spread over `:29` (workflows), `:85` (install), `:424` (`[tool.py_ci_shared]`), `:1000` (safe hook), `:1276` (ruff extend, `PY_CI_SHARED_DIR`) and `:615` (pins). Stale or contradictory:
  1. `:85-88` installs with `pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git"` (unpinned, tracks master) three screens after `:615` says to pin and the consumers' own header says an unpinned install changes without a commit; the intro at `:14` and `:625` pin `@v1.23.11`.
  2. `.pre-commit-hooks.yaml:3` and README example `rev: v1.17.0` (latest v1.23.11).
  3. `:29-60` lead with `@v1` ("Use the moving @v1 tag") while 0 of the 11 consumers use it; all 11 pin a SHA. README `:615` allows both, but the first instruction a new adopter sees contradicts what every real adopter does.
  Running the steps as written in a clean clone worked except that `py-ci-shared run-all` in a fresh repo reports min-file floors and "missing required argument" errors per gate (good messages, see Considered) and takes 3 to 7 s for a one-file repo.
- **Impact:** an adopter copying `:85` gets master, not a release; copying the hook manifest header gets a 6-month-old rev.
- **Proposed fix:** add a short "Adopting in a new repo" section that lists, in order: dev dependency pinned to a release SHA with `# vX.Y.Z`; `[tool.py_ci_shared]` with one gate; `pre-commit` hooks `py-ci-shared-run-all` and `pinned-tool-versions`; `install_safe_hook`; `setup_env`; the lines a bump touches (T8). Fix the three stale examples. ADDITIVE (docs).
- **Disposition:** FIXED (new "Adopting in a new repo" section; stale examples replaced with release pins).

## 47-T12 (P3) A clean clone's first `py-ci-shared run-all` reports the consequence of an empty repo in a gate-specific way and does not say that `enable` and a gate table cannot both name a gate until it is read twice

- **Evidence:** in `clean\`: `naive_utcnow` enabled in a repo with no `.py` file: `FAIL naive_utcnow (7.18s) only 0 file(s) parsed under <path>; expected at least 1 ...` rc=1. `function_length` listed in `enable` and given a table: `gate 'function_length' is enabled twice: both enable and [tool.py_ci_shared.gates.function_length] ... remove the name from enable` rc=2 (good message). With the table only and one function: `only 1 function(s) measured; expected at least 50 ... lower min_functions in [tool.py_ci_shared.gates.function_length]` rc=1; `refresh function_length` hits the same floor and writes no baseline, so the documented first step (`refresh`, README `:540`) cannot create a baseline in a small repo until `min_functions` is lowered; the floor message does tell how. Unknown-key error: `unknown key(s) in [tool.py_ci_shared]: ['bogus_gate', 'code_audit_meta']` (`_core/config.py:146`) does not list the valid keys (`enable`, `budget`, `gates`) nor say that a gate belongs in `enable = [...]`.
- **Impact:** small; the most likely first mistake (`code_audit_meta = true`) gets an error that only repeats the key.
- **Proposed fix:** append "valid keys: enable, budget, gates; enable a gate with `enable = [\"name\"]`" to `config.py:146`. ADDITIVE.
- **Disposition:** FIXED (the error lists the valid keys and the `enable = [...]` form).

## 47-T13 (P3) The interpreter's installed copy is a stale release, and its metadata disagrees with itself

- **Evidence:** `pip show py-ci-shared` and `site-packages\py_ci_shared-1.23.9.dist-info` say 1.23.9; with `PYTHONPATH=src`, `importlib.metadata.version("py-ci-shared")` printed `1.23.10` while `__version__` printed `1.23.12` (the `ci_pin_version_skew` consumer path `installed_py_ci_shared_version`, `ci_pin_version_skew.py:72`, reads metadata). `python -m py_ci_shared.adoption_matrix --waivers ...` against that install fails with `unrecognized arguments: --waivers`, i.e. the command the README shows (`:1352`) does not exist in the copy that `python -m` finds first. Not a package defect per se (an editable install would not have it), but it is the first thing a maintainer hits when running the documented adoption commands outside CI.
- **Impact:** a local "clean" `ci_pin_version_skew` run depends on which of several py_ci_shared copies is first on `sys.path`; the finding text says "installed 1.23.12" when the metadata says 1.23.10.
- **Proposed fix:** in `installed_py_ci_shared_version` prefer `py_ci_shared.__version__` when the module file is not under a `site-packages` directory that holds a dist-info (or report both when they differ: `ci-pin-installed-metadata-mismatch`). ADDITIVE.
- **Disposition:** FIXED (`ci_pin_version_skew` reports `ci-pin-installed-metadata-mismatch` when metadata and module disagree).

## Summary

| id | sev | title |
|---|---|---|
| 47-T1 | P2 | The 2-release window is counted in releases, not days: seven consumers are at the limit |
| 47-T2 | P3 | `pin_waivers.toml` claims every pin is within tolerance; four are not |
| 47-T3 | P2 | `install_safe_hook` reports success though the hook's interpreter cannot run it; commits then fail with a bare traceback |
| 47-T4 | P3 | No way to check a clone's hook is patched; 0 of 8 real clones are |
| 47-T5 | P2 | `adoption_sha_ref` advises an input that three called workflows do not declare |
| 47-T6 | P3 | `adoption_sha_ref` command line differs from `adoption_matrix` and cannot take a repo |
| 47-T7 | P2 | a repo using py_ci_shared hooks with zero pins and no consumers.toml row is green and invisible (`finance`) |
| 47-T8 | P2 | Upgrading a pin is a manual edit of 14 to 28 lines in up to 9 files; no tool, no list |
| 47-T9 | P3 | `ci-pin-comment-not-release` report-only; fires on 4 consumers; suggested wording wrong |
| 47-T10 | P3 | One root cause becomes 7 to 25 identical findings |
| 47-T11 | P3 | readme has no adoption path; three stale examples |
| 47-T12 | P3 | Config error for an unknown `[tool.py_ci_shared]` key does not list valid keys |
| 47-T13 | P3 | Installed copy is a stale release whose metadata disagrees with the module |

Counts: P0 0, P1 0, P2 5, P3 8.

## Considered and not raised

- **A caller passes an input the pinned workflow does not declare, or omits a required one.** `inputs.py` checked every consumer `uses:` against `git show <pinned sha>:<workflow>`: 0 unknown inputs, 0 missing required inputs, and no input added since any pin that a caller lacks (`inputs added since pin=[]` for all 37 calls). Rejected.
- **`PY_CI_SHARED_DIR` points at a dev checkout whose ruff config differs from the pinned release.** `diff --strip-trailing-cr` of `configs/ruff-base.toml` between v1.22.10, v1.23.3, v1.23.8, v1.23.9, v1.23.11 and HEAD: 0 changed lines. Not demonstrable today.
- **`PY_CI_SHARED_DIR` unset.** `ruff check .` prints `Invalid extend value: error looking key 'PY_CI_SHARED_DIR' up: environment variable not found` and exits 2. That is ruff's wording and is clear enough; README `:1276-1316` documents `setup_env`. Not raised.
- **Hook `language: system` using `python` from PATH breaks under an IDE git integration.** On this machine `which -a python` resolves to a WindowsApps alias and to `Python\bin\python`; both launch the same 3.14 interpreter. Cannot show a different interpreter without a second environment; the real hazard (INSTALL_PYTHON without the package) is 47-T3. Rejected as a separate finding.
- **`pre-commit autoupdate` resets the patched hook** (README `:1026`). `autoupdate` rewrites `.pre-commit-config.yaml` revs, not hook files; claim could not be reproduced offline. NOT REPRODUCED, left as is.
- **Local `v1` tag points at v1.22.12 (`531a0e51`).** The remote moves `v1`; a local clone fetched earlier is simply stale. `adoption_matrix` ignores `v1` (not a `vX.Y.Z`). Not a package defect.
- **`install_safe_hook` hook-names list omits `pre-push`/`commit-msg`.** pre-commit's stash happens only when the run has no `from_ref`/files (commit stage); pre-push runs pass `--from-ref`, so no stash race. Rejected on that reading of pre-commit's `run`, not run.
- **mlframe, pyutilz, others have no `[tool.py_ci_shared]` table.** They use hand-written meta-tests (149 test files import py_ci_shared); the README supports both. `ci_pin_version_skew` is called from mlframe, social (3 sub-projects), glossum_backend_scripts; not from pyutilz, algopacksimple, llm_bench, dash_app_core (grep of `.py`/`.toml`/`.yml` under those repos was cut by the tool's time limit on social, so the pyutilz result is not exhaustive). Not raised.
- **Baseline validity for current gate versions.** Not measurable without running consumer suites (see Not covered). `NEEDS-JUSTIFICATION`: 0 in pyutilz and mlframe baselines.
- **`consumer-pins.yml` drops the "Not checked" section on a red run (47-S13)** and **stale check silent without tags (47-S14)**, **duplicate waiver names (47-S15)**: re-read; the workflow now keeps `rc` (`:64-73`) and the matrix run above exits 1 on a stale pin as designed. Resolved, not re-raised.
- **polyvocab_app is 13 behind and flutter/noema repos use custom `.githooks`.** The lag is correctly reported (stale-pin, red); it is an owner bump (T1/T8), not a package defect. The custom-hook repos are not pre-commit-based so `install_safe_hook` does not apply.
