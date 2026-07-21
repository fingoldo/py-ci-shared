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

## LOC-budget baseline meta-test (`loc_budget`)

Shared harness for the "no production file over N lines" meta-test pattern (realtime_applications
and mlframe each independently wrote a near-identical ~75-line version of this). Same API shape as
`code_audit_meta`: a committed baseline JSON (path -> LOC at capture time) grandfathers existing
oversized files, a `--refresh-loc-budget-baseline` flag reseeds it after an intentional split, and
a small per-file growth slack lets trivial edits to an already-oversized file through while a real
expansion still trips the gate.

```python
from pathlib import Path
from py_ci_shared.loc_budget import assert_no_new_oversized_file

def test_no_new_file_over_1k_loc():
    assert_no_new_oversized_file(
        files=my_production_py_files(),  # project-specific: whatever "production" means for this layout
        root=Path(__file__).resolve().parents[2],
        baseline_path=Path(__file__).resolve().parent / "_loc_over_1k_baseline.json",
    )
```

And register `--refresh-loc-budget-baseline` in `conftest.py` via `py_ci_shared.loc_budget.register_refresh_option`,
same as `code_audit_meta`'s flag above.

## Git-dependency pin check (`git_dependency_pins`)

Fails if any `pyproject.toml` git-URL dependency (`name @ git+https://host/path@ref`) isn't pinned
to a full 40-hex-character commit SHA — a branch name, tag, or short SHA all mean a fresh install
can silently resolve to a different commit than the one actually developed/tested against. No
baseline/refresh mechanism: an unpinned git dependency is unconditionally wrong, so there's no
legitimate grandfathered case.

```python
from pathlib import Path
from py_ci_shared.git_dependency_pins import assert_all_git_dependencies_pinned

def test_all_git_dependencies_pinned():
    assert_all_git_dependencies_pinned(Path(__file__).resolve().parents[2] / "pyproject.toml")
```

## CI continue-on-error gate check (`ci_workflow_gate`)

Fails if a CI workflow file has a `continue-on-error: true` step/job that isn't on an explicit,
by-name reviewed-advisory allowlist. Catches the case where a lint/security gate step is *supposed*
to block on failure but a copy-pasted or refactored `continue-on-error: true` silently turns it
into a no-op — the job still shows green even though the gate step itself failed. Deliberately
line-based (matches a step/job to the nearest preceding `name:` line above it), not a full YAML
parser, to avoid a new PyYAML dependency for a check this structurally simple.

```python
from pathlib import Path
from py_ci_shared.ci_workflow_gate import assert_continue_on_error_is_reviewed

def test_ci_continue_on_error_steps_are_reviewed():
    assert_continue_on_error_is_reviewed(
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml",
        reviewed_advisory_steps={"Run ruff", "Run black --check", "Run bandit security scan", "Run mypy"},
    )
```

## Config call-site vs schema parity (`config_call_site_parity`)

Shared engine for the "`cfg().get(section, key, default, type_)` call site agrees with the
Pydantic schema" meta-test pattern — independently built twice in this ecosystem, catching real
bugs both times: a call site reading a `(section, key)` that doesn't exist in the schema (silently
unreadable — an unknown key is stripped on load, so the call always falls through to its own
hardcoded default), a schema field with zero reader anywhere (a decorative knob), and two call
sites reading the SAME `(section, key)` with a different hardcoded default/type. Assumes a
2-level Pydantic schema (a top-level model whose fields are themselves sub-models, one per config
section) and a `cfg()`/`_cfg()`-named accessor (optionally attribute-qualified, or bound to a
local name first). Each consuming repo supplies its own schema class and, where it has one, its
own whitelist of fields consumed a different way the AST heuristic can't see.

```python
from pathlib import Path
from py_ci_shared.config_call_site_parity import (
    assert_every_cfg_get_call_resolves_to_a_schema_field,
    assert_every_schema_field_has_a_reader,
    assert_no_divergent_cfg_get_call_site_defaults,
    assert_call_site_defaults_match_schema_defaults,
)
from my_project.live_config import AppConfig

ROOT = Path(__file__).resolve().parents[2]
FILES = list(my_production_py_files())  # project-specific, same set used by other meta-tests

def test_every_cfg_get_call_resolves_to_a_schema_field():
    assert_every_cfg_get_call_resolves_to_a_schema_field(ROOT, FILES, AppConfig)

def test_every_schema_field_has_a_reader():
    assert_every_schema_field_has_a_reader(ROOT, FILES, AppConfig, known_indirect_readers={...})

def test_no_divergent_cfg_get_call_site_defaults():
    # Pass default_type_repr if the accessor has its own type_ default (e.g. `type_: type
    # = int`) -- a call site that omits type_ then agrees with one passing it explicitly.
    assert_no_divergent_cfg_get_call_site_defaults(ROOT, FILES, default_type_repr="int")

def test_call_site_defaults_match_schema_defaults():
    assert_call_site_defaults_match_schema_defaults(
        ROOT, FILES, AppConfig, min_checked=50,
        # A call site whose default is DELIBERATELY different from the schema's normal
        # value (a safety-net fallback, e.g. variant_count=1 vs the schema's normal 3
        # for the case where config resolution itself somehow fails) -- not a bug.
        known_intentional_mismatches={("evaluator", "variant_count"): "safety-net single-call fallback, see pipeline/evaluate.py"},
    )
```

## README env-var documentation parity (`readme_env_var_parity`)

Fails if production code reads an environment variable (`os.environ.get(...)` / `os.getenv(...)`)
that isn't documented in the project's README table — an operator can't set a variable they don't
know exists, and a required-but-undocumented var that fails closed when unset fails *silently*.
Two entry points: `assert_readme_documents_every_env_var` (hard-fail on any gap — use once a repo
is already at zero gap) and `assert_no_new_undocumented_env_vars` (baseline/grandfather style,
same shape as `code_audit_meta`/`loc_budget`, for a repo adopting this with existing
undocumented-var debt — only a NEW gap fails).

```python
from pathlib import Path
from py_ci_shared.readme_env_var_parity import assert_readme_documents_every_env_var

def test_readme_documents_every_env_var():
    assert_readme_documents_every_env_var(
        files=my_production_py_files(),
        readme_path=Path(__file__).resolve().parents[2] / "README.md",
        third_party_vars=frozenset({"HF_HOME", "ANTHROPIC_API_KEY"}),
    )
```

Or, for a repo with existing debt (register `--refresh-readme-env-var-baseline` in `conftest.py`
via `readme_env_var_parity.register_refresh_option`, same as the other baseline-style checks):

```python
from py_ci_shared.readme_env_var_parity import assert_no_new_undocumented_env_vars

def test_no_new_undocumented_env_vars():
    assert_no_new_undocumented_env_vars(
        files=my_production_py_files(),
        readme_path=Path(__file__).resolve().parents[2] / "README.md",
        baseline_path=Path(__file__).resolve().parent / "_readme_env_var_baseline.json",
    )
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

A weekly scheduled workflow (`config-drift-check.yml`, running `py_ci_shared.config_drift_check`) fetches both consumer repos' `pyproject.toml` and reports (informationally, never failing the run) any divergence in their `[tool.ruff]`/`[tool.mypy]` fields that are meant to stay in sync — trigger it on demand via `workflow_dispatch`. It does not replace opening a matching PR when you change something here that consuming repos should also pick up — `git grep py-ci-shared` in each consumer finds every reference point.
