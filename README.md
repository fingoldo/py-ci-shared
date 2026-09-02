# py-ci-shared

[![CI](https://github.com/fingoldo/py-ci-shared/workflows/CI/badge.svg)](https://github.com/fingoldo/py-ci-shared/actions/workflows/self-ci.yml)
[![Config drift check](https://github.com/fingoldo/py-ci-shared/actions/workflows/config-drift-check.yml/badge.svg)](https://github.com/fingoldo/py-ci-shared/actions/workflows/config-drift-check.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

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
    uses: fingoldo/py-ci-shared/.github/workflows/black-filtered.yml@v1
    with:
      check-path: src/mlframe
```

See each workflow file's header comment for its full input list. Available workflows: `ruff-blocking.yml`, `black-filtered.yml`, `mypy-beachhead.yml`, `mypy-full.yml`, `lint-blocking.yml`, `lint-advisory.yml`, `docs.yml`.

**Use the moving `@v1` tag (2026-08-22 policy change).** `v1` always points at the latest `v1.x`
release, so cutting a release here propagates to every consumer at once — no per-repo SHA bump.
Re-point it as part of each release:

```bash
git tag -f -a v1 -m "…" && git push -f origin v1
```

This replaces the previous "pin every consumer to an exact tag/SHA" rule, which did not survive
contact with reality: consumers drifted onto *different* pins of the same workflow (`algopacksimple`
held three distinct SHAs across its own workflow files, `llm_bench` two), and the manual bump was
skipped often enough that most satellites sat many releases behind. The old rule's stated benefit —
"a behavior change is a reviewable diff, not an invisible side effect" — assumed CI actually runs
and gates on every consumer; for private repos out of free GitHub Actions minutes that review gate
was fictional, so the pin bought toil without buying safety.

Still pin to a full SHA when *you* are not the owner of the upstream: the threat a SHA pin defends
against is an upstream maintainer moving a tag under you, which does not apply to a first-party
repo (whoever could move this tag could equally push to the consumer directly).

`@main` specifically never resolves at all — this repo's default branch is `master` — and GitHub
does not fall back to the actual default branch when a `uses:` ref doesn't resolve; a stale/wrong
ref fails the whole calling workflow at parse time with "reference to workflow should be either a
valid branch, tag, or commit" and zero jobs ever run, which is easy to lose time to since the error
never shows up in any job log.

## Using the installable package

```bash
pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git"
```

To hack on this repo itself, install a local clone in editable mode instead, so edits under `src/py_ci_shared` take effect without reinstalling. The `[dev]` extra pulls in `pytest`, `black`, `pydantic`, and `pyutilz` (needed by the test suite and by `code_audit_meta`); drop it for a runtime-only install:

```bash
git clone https://github.com/fingoldo/py-ci-shared.git
cd py-ci-shared
pip install -e ".[dev]"
```

Quote the argument: unquoted `[dev]` is glob syntax in most shells (and `pip install -e .[dev]` fails outright in zsh). On Windows, call the interpreter explicitly (`python.exe -m pip install -e ".[dev]"`) so the install lands in the environment you expect. The console scripts (`safe-precommit`, `py-ci-install-safe-hook`, `py-ci-setup-env`) are on `PATH` right after this, and `python -m py_ci_shared.setup_env` will point `PY_CI_SHARED_DIR` at the clone.

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
baseline/refresh mechanism: for a **third-party** git dependency the SHA pin is the only defence,
so there's no legitimate grandfathered case.

```python
from pathlib import Path
from py_ci_shared.git_dependency_pins import assert_all_git_dependencies_pinned

def test_all_git_dependencies_pinned():
    assert_all_git_dependencies_pinned(Path(__file__).resolve().parents[2] / "pyproject.toml")
```

**First-party exemption** (`allow_unpinned_url_prefixes`, added 2026-08-22): a satellite that
depends on another repo *the same owner controls* may declare it without a SHA and let its
committed lockfile (`uv.lock`) pin the resolved commit instead. Reproducibility is unchanged — the
lock is the pin — while bumping becomes `uv lock --upgrade-package <name>` rather than
hand-copying a 40-hex string into every satellite:

```python
    assert_all_git_dependencies_pinned(
        Path(__file__).resolve().parents[2] / "pyproject.toml",
        allow_unpinned_url_prefixes=("git+https://github.com/fingoldo/",),
    )
```

Only exempt a URL that a lockfile actually covers, and only for first-party upstreams; the
allowlist defaults to empty, so third-party deps keep the strict behaviour.

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

## CI test-dir reachability check (`ci_test_dir_reachability`)

Fails if any `tests/<subdir>` in a consuming repo is never invoked (directly, or via a bare
`pytest tests/` not `--ignore`'d for that subdir) by any CI workflow. Catches a whole test category
silently going CI-blind: a new test subdirectory added and never wired into a workflow's `pytest`
invocation still runs fine locally, so nobody notices merges are no longer gated on it. Generalized
from a check first written directly in a consuming repo (glossum_backend_scripts) after that repo's
own audit surfaced the pattern. Text-based, matching this package's other `ci_*` checks.

```python
from pathlib import Path
from py_ci_shared.ci_test_dir_reachability import assert_every_test_subdir_reachable

def test_every_test_subdir_reachable_by_some_ci_job():
    assert_every_test_subdir_reachable(
        repo_root=Path(__file__).resolve().parents[2],
        workflows_dir=Path(__file__).resolve().parents[2] / ".github" / "workflows",
        intentionally_unreached={"live"},  # e.g. a paid-API tier deliberately excluded from CI
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

Also in this module: `assert_no_module_scope_frozen_cli_defaults` — fails if a `cfg().get(...)`
read sits at MODULE scope (not inside a function/closure) and its result feeds an
`argparse.add_argument(..., default=...)` value. `cfg().get(...)` is normally re-read on every
call so a live config edit takes effect within the reload interval; reading it once at import
time freezes that ONE CLI flag's effective value for the process lifetime while every sibling
knob stays hot-reloadable — invisible in a diff (the code looks like every other `cfg().get(...)`
call site) and only shows up as "I edited config.toml and nothing happened" for that one flag.

```python
from py_ci_shared.config_call_site_parity import assert_no_module_scope_frozen_cli_defaults

def test_no_module_scope_frozen_cli_defaults():
    assert_no_module_scope_frozen_cli_defaults(
        ROOT, FILES,
        known_intentional_freezes={("traffic", "batch_size"): "reviewed, deliberately frozen at startup"},
    )
```

## CHANGELOG promise / fix-sensor cross-walk (`changelog_promise_parity`)

Shared engine for "a CHANGELOG bullet claims something (a fix was made, a follow-up will
happen) — does the claim actually hold." Generalizes two independently-built checks: mlframe's
"every `fix(...)`-tagged bullet must also cite a regression test/sensor" (self-contained
satisfaction — the sensor reference lives in the SAME bullet), and production_scrapers's
"every bullet promising deferred follow-up ('flagged for the final disposition report', 'tracked
under...') must actually be resolved" (cross-document satisfaction — the resolution lives in a
DIFFERENT file, e.g. a `DISPOSITION.md`). Both checks are one call into
`assert_changelog_bullets_satisfy_pattern`, differing only in which satisfaction mode(s) are
wired up.

```python
from pathlib import Path
import re
from py_ci_shared.changelog_promise_parity import (
    assert_changelog_bullets_satisfy_pattern,
    DEFAULT_PROMISE_PATTERN,
)

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
DISPOSITION = Path(__file__).resolve().parents[2] / "DISPOSITION.md"

# mlframe-style: every fix(...)-tagged bullet in a dated audit-cycle section must cite a sensor,
# self-contained within the bullet's own text. Soft threshold tolerates some doc-only fixes.
_AUDIT_SECTION = re.compile(r"^##\s+\d{4}-\d{2}-\d{2}.*?(audit cycle|wave[ -]?\d+)", re.IGNORECASE | re.MULTILINE)
_FIX_BULLET = re.compile(r"(fix\([^)]*\)|\bbug\b|\bregression\b)", re.IGNORECASE)
_SENSOR_REF = re.compile(r"(test_[a-zA-Z0-9_]+\.py|tests/[\w/]+\.py|sensor[: ])", re.IGNORECASE)

def test_each_fix_bullet_cites_a_sensor():
    assert_changelog_bullets_satisfy_pattern(
        CHANGELOG, _FIX_BULLET, _SENSOR_REF,
        section_pattern=_AUDIT_SECTION, max_unsatisfied_fraction=0.15, label="fix bullet",
    )

# production_scrapers-style: every promise ("flagged for the final disposition report", "tracked
# under...") must be resolved -- either mentioned later in CHANGELOG.md itself, or its title
# appearing in DISPOSITION.md. Strict (default 0.0) -- every promise must be kept.
def test_changelog_promises_resolved():
    assert_changelog_bullets_satisfy_pattern(
        CHANGELOG, DEFAULT_PROMISE_PATTERN,
        other_resolution_paths=[DISPOSITION], label="promise",
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

## Content-hash / version-bump gate (`content_hash_version_bump_gate`)

Fails if a set of tracked source files changed content but a version constant that's supposed to
be bumped in lockstep (a prompt version, a cache-key version, a schema/serialization version) was
NOT — the classic "forgot to bump the version" bug, where a stale cached/persisted artifact
silently stays valid under the OLD version even though the code that produces it has since
changed. A version bump is self-certifying: if the version constant differs from the baseline's
pinned value, that's accepted as deliberate and the baseline is silently re-pinned — no separate
refresh flag needed for the normal "I bumped it" workflow. Register `--refresh-content-hash-version-baseline`
in `conftest.py` via `content_hash_version_bump_gate.register_refresh_option`, same as the other
baseline-style checks, for bootstrapping a missing/corrupted baseline only.

```python
from pathlib import Path
from py_ci_shared.content_hash_version_bump_gate import assert_version_bumped_with_content
from myproject.prompt_version import USER_PROMPT_VERSION

_SOURCE_FILES = [
    Path(__file__).resolve().parents[2] / "prompt_builder" / f
    for f in ("word_count.py", "truncation.py", "user_prompt.py")
]

def test_user_prompt_version_bumped_when_prompt_builder_changes():
    assert_version_bumped_with_content(
        files=_SOURCE_FILES,
        version=USER_PROMPT_VERSION,
        baseline_path=Path(__file__).resolve().parent / "_user_prompt_version_baseline.json",
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

## Checks derived from the 2026-09-02 Flutter audit round

Eleven modules added after a full audit of two Flutter repositories (`polyvocab_app`,
`flutter_app_core`) filed 271 findings, of which 154 turned out to be statically detectable and
only three had been caught by a blocking gate. Each module is one finding class, generalised to
whatever language the repository is written in; the docstring of each names the findings it would
have caught. They are consumed exactly like the checks above -- a `find_*` function returning a
list of problem strings, and an `assert_*` wrapper that imports `pytest` lazily -- so a Dart or
TypeScript repository with no pytest harness can call the `find_*` half from a plain script.

| Module | Answers |
|---|---|
| `sql_function_privileges` | Is a `SECURITY DEFINER` function still executable by every signed-in user? PostgreSQL grants `EXECUTE` to `PUBLIC` by default; on Supabase that is one HTTP call from any session. This is the check that would have caught the round's only P0 (a function that granted the caller a permanent admin plan). Also enforces `SET search_path`. |
| `ci_workflow_paths` | Does a workflow name a `working-directory` or run a script that does not exist? Optionally: is there a top-level `permissions:`, and is every third-party action pinned to a SHA? |
| `hook_hygiene` | Does a git hook skip a missing guard silently, stage files the author did not, decide a verdict by grepping a tool's human-readable output, or run guards CI never runs? |
| `repo_hygiene` | Are generated artefacts tracked, required config files missing, or does a numeric CI gate pass when its input fails to parse? |
| `test_partition_reachability` | Is a declared test tag, Playwright project or standalone script selected by any runner, or does a permanent `test.skip` sit at the top of a spec? |
| `baseline_hygiene` | Does every accepted baseline entry carry a human reason, is any entry stale, and does any contain an absolute path? Also exposes `body_asserts_only_absence_of_crash` for assertion scanners. |
| `import_layering` | Does the layer that exists to be reusable import the product it was extracted from? Rules are `from_glob !-> to_glob`; both relative and `package:` imports resolve to the same repo-relative path. |
| `stale_comment_age` | How old is that TODO? `git blame` decides; an issue reference exempts a line. Also catches commented-out calls. |
| `arb_checks` | Flutter `.arb` catalogues: key parity, ICU plural per locale's own CLDR categories, a `{count}` outside a plural, informal register (advisory), dead keys. |
| `dart_scanners` | Seven structural scanners over Dart source (painters and animations, repaint isolation, hardcoded UI strings, tappable/semantics hygiene, non-directional layout, parse/serialise/catch, provider state), returning the `{key: description}` shape a repo's own baseline ratchet already consumes. |
| `edge_function_hygiene` | Serverless functions: a catch that answers 200, an uncapped request body, a secret compared with `===`, an IP in a log line, the forgeable first `x-forwarded-for` hop. |
| `guard_population` | Runs each guard's own file-selection command and fails when it matches nothing -- the failure mode where a guard has been passing for weeks without examining a single file. |
| `version_tag_currency` | Does the declared version have a tag, is that tag reachable from HEAD, and how many releases behind is a consumer's pin? |

### Consuming these from a Dart repository

There is no pytest harness in a Flutter repo, but there is already Python on the hook and in CI, so
the whole cost is one script that calls the `find_*` halves and exits non-zero:

```python
# tool/check_shared_scanners.py
from pathlib import Path
from py_ci_shared.sql_function_privileges import find_unlocked_definer_functions
from py_ci_shared.arb_checks import find_plural_problems

REPO = Path(__file__).resolve().parents[1]
problems = find_unlocked_definer_functions(REPO / "supabase" / "migrations")
problems += find_plural_problems({"en": ARB / "app_en.arb", "ru": ARB / "app_ru.arb"})
if problems:
    raise SystemExit("\n".join(problems))
```

Install it the same way the Python consumers do: `pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared@<sha>"`.

## Using the shared ruff config

Ruff natively supports `extend = "<path>"` pointing at another ruff config file — a real merge (select/ignore/per-file-ignores/pep8-naming all combine), not copy-paste. `configs/ruff-base.toml` is NOT shipped inside the pip package (ruff needs a real filesystem path, and `extend` is resolved at ruff-invocation time, not import time) — but ruff DOES expand `~` and environment variables in that path (docs.astral.sh/ruff/settings), so consuming repos point at an env var instead of a fixed relative location:

```toml
# consuming repo's pyproject.toml
[tool.ruff]
extend = "$PY_CI_SHARED_DIR/configs/ruff-base.toml"
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

`PY_CI_SHARED_DIR` just needs to point at SOME real `py-ci-shared` checkout — it no longer has to be a sibling of the consuming repo (that was the prior convention through 2026-07-22; a repo move or an unusual checkout layout would silently break the relative path). Clone this repo anywhere and point the var at it:

```bash
git clone https://github.com/fingoldo/py-ci-shared.git ~/dev/py-ci-shared
```

Then set `PY_CI_SHARED_DIR` **persistently at the OS/user level**, not just in a shell rc file — a GUI-launched editor (VS Code opened from the Dock/Start Menu rather than `code .` from a terminal) doesn't always inherit shell-only exports, especially on macOS, and would silently lint against the unmerged base config instead of the full ruleset. Either do it by hand (`setx PY_CI_SHARED_DIR <path>` on Windows; a `launchctl setenv` + LaunchAgent plist on macOS; a `~/.config/environment.d/*.conf` entry on Linux) or run the bundled helper once, which does the OS-appropriate thing for you:

```bash
pip install -e ~/dev/py-ci-shared
python -m py_ci_shared.setup_env
```

Restart your terminal/IDE afterward so the new value is picked up.

CI resolves `PY_CI_SHARED_DIR` itself — see `ruff-blocking.yml` / `lint-advisory.yml`'s "Resolve PY_CI_SHARED_DIR" step — a calling repo's own workflow doesn't need to do anything extra.

**CRITICAL:** never invoke ruff with `--select <subset>` in a blocking gate — it REPLACES the effective rule set instead of narrowing the extended config, silently dropping the whole shared ignore list and breaking RUF100's own "is this noqa still needed" determination. Always use `--ignore <code>` to ADD to the resolved ignore list. See `configs/ruff-base.toml`'s header comment and the `mlframe`/`pyutilz` `CLAUDE.md` files for the incident this rule postdates (2026-07-09).

## Keeping this repo in sync with consumers

A weekly scheduled workflow (`config-drift-check.yml`, running `py_ci_shared.config_drift_check`) fetches both consumer repos' `pyproject.toml` and reports (informationally, never failing the run) any divergence in their `[tool.ruff]`/`[tool.mypy]` fields that are meant to stay in sync — trigger it on demand via `workflow_dispatch`. It does not replace opening a matching PR when you change something here that consuming repos should also pick up — `git grep py-ci-shared` in each consumer finds every reference point.
