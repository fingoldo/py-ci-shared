### INFRA-6

**Disposition:** RESOLVED -- `src/py_ci_shared/adoption_matrix.py` (registered as a `cli` module, `py-ci-shared tool adoption_matrix`) reads consumer repo roots (args or `--repos-file` TOML) read-only and reports pins per location (pyproject dependency tables, `[tool.uv.sources]`, ruff `extend`, requirements, `uv.lock`, pre-commit `rev`, workflow `uses:` refs, `py-ci-shared-ref` inputs, pip installs, `git clone`, `actions/checkout`), pin agreement (`--resolve-in` resolves tags and short SHAs through a py-ci-shared checkout), moving pins, module usage (imports, `python -m`, `py-ci-shared run`, `[tool.py_ci_shared]` tables), silent skips (importorskip, collect_ignore, exit 0, pytest.skip, availability flag nobody checks loudly, swallowed ImportError) and local copies via `local_copy_report.find_local_copies`. The output is a markdown summary, pin table, modules x repos matrix, workflow matrix and findings list. Exit 1 on disagreeing pins, moving pins or silent skips; `--advisory` exits 0. Real run: `audits/2026-09-24/adoption_matrix_2026-09-24.md`; regression test: tests/test_adoption_matrix.py

## Real run vs hand matrix

Run on 2026-09-25 over the 13 repos in the brief, with `--resolve-in ../py-ci-shared`. The module matrix matches `33_adoption.md` for 11 of 13 repos. Each discrepancy is listed below.

Tool bugs found by the comparison and fixed before the saved run:

- `uv.lock` line: the first `name = "py-ci-shared"` match was a dependency entry (`uv.lock:960`), not the `[[package]]` header. The tool now matches the whole line (`uv.lock:2560`).
- noema_app `tool/meta/scanners.py:61` was reported as an availability flag. Every reader of `SHARED_AVAILABLE` (`check-baselined-rules.py:68`, `regen_baselines.py:25`) fails when the flag is False. A flag that is checked loudly by `if not FLAG:` somewhere in the repo is no longer reported. flutter_app_core and polyvocab_app `_SHARED_AVAILABLE` are read only as `if _SHARED_AVAILABLE:`, so they are still reported, as ADOPT-5 says.
- `[tool.py_ci_shared.gates.x]` headers were counted as a module named `gates`. Fixed with a lookbehind.
- Moving refs (`v1`, `master`) were keyed by what the local py-ci-shared clone resolves them to. They are now keyed by name, because a moving ref names no commit.

Errors or staleness in the hand matrix:

- autopsia `pyproject.toml:691` is the deptry `DEP002` ignore list, not a dependency. Only `:118` is a pin (ADOPT-2 cites both).
- mlframe `.pre-commit-config.yaml:641` pins `repo: fingoldo/py-ci-shared` at `rev: v1.0.0`, a sixth ref. ADOPT-13 names five.
- pyutilz `.pre-commit-config.yaml:295` pins `rev: v1.3.5`. The hand table does not list it.
- claude-usage-notifier `pyproject.toml:18` has `extend = "../py-ci-shared/configs/ruff-base.toml"`, the same sibling path as flutter_uptime_monitor. ADOPT-20 names only flutter_uptime_monitor.
- mlframe has moved since the survey: `requirements-dev.txt:58` and the ruff-blocking `uses:` are now at `94b1c0d`, not `41cbadc`. It also uses five more modules (`conceded_defect_pins`, `config_getattr_default_parity`, `env_flag_parsing`, `printed_advice`, `survivorship_scoring`), which were wired on 2026-09-24. The hand matrix shows 66 modules and the tool shows 71.
- noema_app `phantom_code_references`: the only mention is a comment in `test/meta/comments_name_real_things_test.dart:11`. The tool reads no `.dart` files and does not count a prose mention as use, so the hand matrix over-counts noema (5 vs 4).
- Silent skips the hand list missed: autopsia `test_unasserted_effects.py:43` (a second importorskip in the same file as `:71`); social `dashboard/.../test_unasserted_effects.py:37` and the matching `:37` in the other two sub-projects, plus `production_scrapers/tests/test_meta/test_no_invented_module_attributes.py:41`; mlframe `test_printed_advice_wired.py:16` (new); pyutilz `tests/conftest.py:163` (`except ImportError: pass` around the refresh-option registration, the same shape as dash_app_core `conftest.py:20`).
- noema_app is correctly clean: the tool agrees with the ADOPT-4/5 text that noema fails loudly.

Per-repo verdict from the saved run: pins agree only in flutter_app_core, polyvocab_app and flutter_uptime_monitor (each has a single pin, and each is moving) and in noema_app (no pins). mlframe has no moving pins but six different SHAs. All 13 repos except noema_app fail the default (non-advisory) run.
