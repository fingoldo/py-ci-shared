# py-ci-shared

Shared CI/lint tooling for fingoldo Python projects (`mlframe`, `pyutilz`, and future repos). Single source of truth for the CI/lint conventions that used to be independently duplicated (and drift) across each project's `.github/workflows/`, `scripts/`, and `pyproject.toml`.

Two things live here, each solving a different half of the duplication:

1. **Reusable GitHub Actions workflows** (`.github/workflows/*.yml`, invoked via `workflow_call`) for the pieces of CI that are identical in *behavior* across repos: the blocking ruff gate, the filtered Black check, the mypy strict-mode-beachhead pattern, the mypy-full advisory pass, the advisory lint bundle (codespell/yamllint/bandit/actionlint/vulture/pip-audit), and the MkDocs docs build/deploy.
2. **An installable package** (`py_ci_shared`, on PyPI-style via `pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git"`) for the pieces that are identical in *code*: `black_filtered_apply.py` (the two-excluded-Black-behavior-classes script) and the warn-only pre-commit wrappers (`format_warn.py`, `bandit_warn.py`, `vulture_warn.py`).

Plus `configs/ruff-base.toml`: the shared `[tool.ruff.lint] select`/`ignore` superset, pulled into each consuming repo's own `pyproject.toml` via ruff's native `extend` mechanism (a real config-merge, not copy-paste) — see below.

**Deliberately NOT here:** anything whose shared surface is small relative to the parametrization cost (`sklearn-matrix-ci.yml`, `gpu-matrix.yml`, `release.yml`, `numba-coverage.yml`'s hardcoded test-path lists), and anything inherently project-specific (vulture whitelists, per-repo meta-test suites, the `test`/`build` jobs in each repo's own `ci.yml`). Those stay local to each repo.

## Why a separate repo, not part of `pyutilz`

`pyutilz` is a runtime dependency (real library code other repos `import`). Bundling CI tooling into it would couple tooling-script releases to runtime-code releases, and `pyutilz`'s own meta-tests (import-cycle checks, docstring-coverage snapshots, etc.) are specific to *its own* codebase structure — not generic/reusable against arbitrary consumer repos. Keeping this concern separate keeps both repos' release history clean.

## Using the reusable workflows

A consuming repo's own workflow file becomes a thin wrapper:

```yaml
# .github/workflows/black-filtered.yml
name: Black
on:
  push: {branches: [main, master]}
  pull_request: {branches: [main, master]}
jobs:
  black:
    uses: fingoldo/py-ci-shared/.github/workflows/black-filtered.yml@v1.0.0
    with:
      check-path: src/mlframe
```

See each workflow file's header comment for its full input list. Available workflows: `ruff-blocking.yml`, `black-filtered.yml`, `mypy-beachhead.yml`, `mypy-full.yml`, `lint-blocking.yml`, `lint-advisory.yml`, `docs.yml`.

**Pin to a tagged release (`@v1.0.0`), not `@master` or `@main`.** Tagged releases exist precisely
because a floating `@master` ref means any push to this repo instantly changes CI behavior for
every consumer with no review gate in between (flagged in a 2026-07-09 CI/CD architecture review;
`mlframe` and `pyutilz` both floated on `@master` before this tag existed). Bump the pin in both
consumers deliberately when a new tag is cut, so a behavior change is a reviewable diff instead of
an invisible side effect of an unrelated commit here. `@main` specifically never resolves at
all — this repo's default branch is `master` — and GitHub does not fall back to the actual default
branch when a `uses:` ref doesn't resolve; a stale/wrong ref fails the whole calling workflow at
parse time with "reference to workflow should be either a valid branch, tag, or commit" and zero
jobs ever run, which is easy to lose time to since the error never shows up in any job log.

## Using the installable package

```bash
pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@v1.0.0"
```

Then, in place of the old `python scripts/black_filtered_apply.py ...`:

```bash
python -m py_ci_shared.black_filtered_apply --config pyproject.toml --check src/mlframe
```

`format_warn.py` is already fully generic. `bandit_warn.py` / `vulture_warn.py` read `PY_CI_SHARED_SRC_PATH` (required) and `vulture_warn.py` additionally reads `PY_CI_SHARED_VULTURE_WHITELIST` (optional — whitelists are project-specific, never shipped here) from the environment; set both in the calling repo's `.pre-commit-config.yaml` hook `env:` block.

## Code-audit baseline meta-test (`code_audit_meta`)

Shared harness for the "run `pyutilz.dev.code_audit.run_all()` against this repo's own source,
gate on a committed baseline JSON" pattern used by every consumer (glossum_backend_scripts,
llm_bench, realtime_applications, production_scrapers, pyutilz itself, mlframe, algopacksimple).
`pyutilz`, `pytest`, and `orjson` are imported lazily inside the functions, not at module level,
so this stays consistent with the rest of the package being otherwise dependency-free — every
real caller already depends on `pyutilz` directly (it's what's being scanned).

In a consuming repo's `tests/test_meta/test_code_audit_baseline.py` (or an equivalent root-level
file for flat-layout repos):

```python
from pathlib import Path
from py_ci_shared.code_audit_meta import assert_no_new_code_audit_findings

import mypackage

def test_no_new_code_audit_findings():
    assert_no_new_code_audit_findings(
        root=Path(mypackage.__file__).resolve().parent,
        baseline_path=Path(__file__).resolve().parent / "_code_audit_baseline.json",
        exclude_dirs=frozenset({"tests", "docs", "legacy"}),  # repo-specific, merged with the built-in cache/vcs defaults
    )
```

And in the same directory's `conftest.py` (or the repo's root conftest.py if there's only one),
register the refresh flag so pytest accepts `--refresh-code-audit-baseline`:

```python
from py_ci_shared.code_audit_meta import register_refresh_option

def pytest_addoption(parser):
    register_refresh_option(parser)
```

## Surviving concurrent-session commits (`safe_precommit`)

`pre-commit` stashes a repo's unstaged tracked-file changes to a patch file before running hooks
(so hooks see only staged content), then tries to restore that patch afterward. When multiple git
sessions share one working copy (parallel agents, multiple terminals), another session's commit
can advance `HEAD` while ours is stashed; the restore-patch was computed against the OLD `HEAD`
blob and no longer matches the new one, so `git apply` fails even after pre-commit's own
checkout-and-retry, and the uncaught error aborts the ENTIRE commit — even though every real hook
(mypy, tests, lint...) already ran and passed. The files actually being committed are unaffected;
only some OTHER unstaged edit (not part of this commit) couldn't be silently restored.

`py_ci_shared.safe_precommit` is a drop-in `pre-commit` replacement that patches this one failure
mode to a warning (the un-restorable patch is preserved on disk, never silently dropped) instead
of aborting. One-time per clone, after `pre-commit install`:

```bash
python -m py_ci_shared.install_safe_hook
```

This rewrites the generated `.git/hooks/pre-commit` to invoke `python -m
py_ci_shared.safe_precommit hook-impl ...` instead of `python -mpre_commit hook-impl ...`, so plain
`git commit` gets the patched behavior automatically — no wrapper command to remember, and it
works on any machine as soon as `pip install -e ".[dev]"` (or however the consuming repo installs
this package) makes `py_ci_shared` importable in that Python. Idempotent; safe to re-run any time,
including after `pre-commit install` regenerates the hook file (which resets this override).

Re-running `python -m py_ci_shared.install_safe_hook` after each `pre-commit install` (or `pre-commit autoupdate`) is the only maintenance this needs.

## Using the shared ruff config

Ruff natively supports `extend = "<path>"` pointing at another ruff config file — a real merge (select/ignore/per-file-ignores/pep8-naming all combine), not copy-paste. `configs/ruff-base.toml` is NOT shipped inside the pip package (ruff needs a real filesystem path, and `extend` is resolved at ruff-invocation time, not import time) — instead, consuming repos clone this repo as a **sibling directory**, exactly the same pattern already used for `pyutilz` itself as an mlframe dependency:

```toml
# consuming repo's pyproject.toml
[tool.ruff]
extend = "../py-ci-shared/configs/ruff-base.toml"
target-version = "py39"          # each repo's own minimum-Python floor
exclude = [...]                  # each repo's own dir excludes

[tool.ruff.lint]
extend-select = [...]            # optional: rules on TOP of the shared select
extend-ignore = [...]            # optional: rules ignored on TOP of the shared ignore

[tool.ruff.lint.per-file-ignores]
"src/<pkg>/**" = [...]           # each repo's own path-specific ignores (these MERGE with the base)

[tool.ruff.lint.mccabe]
max-complexity = <N>             # each repo's OWN measured threshold -- never copy another repo's number
```

Local dev: clone `py-ci-shared` next to the consuming repo (`C:\Users\<you>\Machine learning\py-ci-shared`, sibling of `mlframe`/`pyutilz`) so the relative path resolves the same way locally and in CI. CI clones it automatically (see `ruff-blocking.yml`'s "Clone py-ci-shared (sibling)" step) — a calling repo's own workflow doesn't need to do this itself, it's handled inside the reusable workflow.

**CRITICAL:** never invoke ruff with `--select <subset>` in a blocking gate — it REPLACES the effective rule set instead of narrowing the extended config, silently dropping the whole shared ignore list and breaking RUF100's own "is this noqa still needed" determination. Always use `--ignore <code>` to ADD to the resolved ignore list. See `configs/ruff-base.toml`'s header comment and the `mlframe`/`pyutilz` `CLAUDE.md` files for the incident this rule postdates (2026-07-09).

## Keeping this repo in sync with consumers

There is no automated config-drift check yet (a natural follow-up: a script here that clones both consumer repos and diffs their `[tool.ruff]`/`[tool.mypy]` sections against this repo's documented conventions). Until then, when you change something here that consuming repos should also pick up, open a matching PR in each — `git grep py-ci-shared` in each consumer finds every reference point.
