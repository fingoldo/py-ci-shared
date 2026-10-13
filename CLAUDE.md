# py-ci-shared: working rules

## Adding a gate

- Start with `py-ci-shared new-gate <name> [--kind gate|library] --summary "..."`, or follow the layout it writes:
  `src/py_ci_shared/<name>.py`, `tests/test_<name>.py`, `tests/canary/<name>/{violation,clean,bom,unparsable}/` (a text-corpus gate declared `parses=False` in `tests/test_gate_teeth.py` has no syntax to break and may omit `unparsable/`; it needs an undecodable-bytes variant instead),
  a `CANARIES` entry in `tests/_gate_teeth_canaries.py` (`new-gate` writes it; `tests/_gate_teeth_canaries_more.py` holds the later `CANARIES.update` blocks and `tests/test_gate_teeth.py` the tests), a `registry.toml` entry and the README catalogue row. The
  scaffolder never overwrites a file; its tests and canary fail until you write them.
- Read and parse through `_core`: `scan_python`/`read_source`/`parse_file`, `Finding`, `Baseline`,
  `ImportAliases`. Never `ast.parse(path.read_text(...))` with `except SyntaxError: continue`: a file the gate
  cannot read is a finding (`UnparsedFilesError`), not a pass. Keep a `min_files` floor and an `allow_unparsed`
  switch (`tests/test_gate_entry_contract.py` fails a new corpus gate entry without them; `_core.gate_contract`).
- Run git only through `_core.git.run_git`/`git_output` (UTF-8 output, a timeout, hook-safe `GIT_*` env);
  `tests/test_core_git.py` fails a direct `subprocess` git call and `text=True` without `encoding=`.
- Register every public module in `src/py_ci_shared/registry.toml` (sorted by name; `since` = the version in
  `pyproject.toml`). Private helpers go in `registry.INTERNAL_MODULES` with a reason.
- A corpus-scanning gate needs a canary in `tests/canary/<name>/` (fixture files end in `.canary`), or an `EXEMPT`
  entry in `tests/test_gate_teeth.py` that says why its subject cannot be seeded as files.
- A new `find_*` must bind from a repo root in `corpus_drift.BINDINGS` or be listed in `corpus_drift.NON_CORPUS`
  with a reason.

## Running tests

- Run pytest through `py-ci-shared tool pytest_run OUTFILE <paths>` (README, "Running pytest on a developer machine"): unbuffered, `-s`, `--durations=200`, no coverage, one process, the whole output in a file that you read after the run. Do not use `-q`, and do not pipe a live run through `tail` or `grep`: the file stays empty until the process ends.
- Run the tests of what you changed, not a whole directory.

## Before pushing

- Format with `uvx black==26.5.1` (`BLACK_VERSION` in `tool_versions.py`, which `black-filtered.yml` runs); lint with `uvx ruff@<RUFF_VERSION> check src tests` (`RUFF_VERSION` in `tool_versions.py`; a test fails when a literal `ruff@X` or `black==X` in this file differs from it).
- Run `tests/test_package_inventory.py` and `tests/test_gate_teeth.py`, plus the tests of what you changed.
- Code must support Python 3.9 and pass `mypy src/py_ci_shared`.
- Install the hooks once per clone: `python -m pre_commit install --hook-type pre-commit --hook-type pre-push` then `python -m py_ci_shared.install_safe_hook`. The pre-push hook runs mypy, the self-gate tests and `py-ci-shared run-all` (about 5 minutes); the rest of `tests/` runs in the
  release script (below).
- GitHub Actions is permanently unavailable on this account (every job fails to start on billing): no workflow here runs, for
  pushes, tags or schedules. The hooks and `.github/scripts/release_local.py` are the only checks that run.

## Consumers

- `configs/consumers.toml` feeds `consumer-pins.yml` (adoption_matrix with `--resolve-in . --allow-behind 2`: a pin
  may lag the latest release by 2 tags; the tolerance only applies with `--resolve-in` and a full-history checkout),
  `ci-health.yml` (`ci_health`: days each consumer workflow has been red; billing-blocked runs reported apart) and
  `corpus-drift.yml`. Private consumers need the `CONSUMER_READ_TOKEN` secret and are skipped with a warning without it.

## Versions and releases

- `version` in `pyproject.toml` (and `__version__` in `src/py_ci_shared/__init__.py`) is the NEXT release tag
  without its `v`. `tests/test_release_version.py` holds them equal, strictly ahead of every release tag (equal to
  one only on that tag's own commit), and every `registry.toml` `since` at or below it. Bump it in the first commit
  after a release.
- The four reusable workflows' `py-ci-shared-ref` default (`black-filtered`, `ruff-blocking`, `lint-advisory`, `gates`) is the LAST PUBLISHED tag
  between releases, never the next one: a caller on master or a SHA gets that default, and a tag that does not exist fails the install with no
  fallback. The bump commit after a release leaves them alone; only `prepare --write` moves all four to the release being tagged. The test holds
  the four equal and equal to the newest tag or to `v<version>` (the prepared commit). Tag within minutes of the green prepared commit, because
  that commit is the one window in which master names a not-yet-existing tag.
- Every CHANGELOG heading is `## X.Y.Z` for a tag, plus one `## Unreleased (next: <version>)` on top; `tests/test_release_version.py` requires a
  heading for every tag from v1.23.0, and on the tagged commit itself the top heading must already be `## <version>`. A **Behaviour change** entry from 1.23.0
  on must contain `What to do:` or `Nothing to do` (`tests/test_changelog_actions.py`).
- BEFORE tagging run `python .github/scripts/release_guard.py prepare X.Y.Z --write`, commit, and wait for green CI: it brings pyproject, `__version__`, the README install tag and gate catalogue, the four reusable workflows' default
  ref and the `since` of every module not yet in a published release to X.Y.Z (`--check` exits 1 while anything differs; the Release run's `verify` job runs it too, so the tagged commit must be a fixed point). Rename the CHANGELOG's `Unreleased (next: X)` heading to `X` in the same commit. v1.22.0 and v1.22.2 were tagged without it, failed `verify`, and are dead tags: a pushed tag is never moved, so each cost a version.
- A release is a pushed `vX.Y.Z` tag equal to that version, on master, and the highest `v1.*.*` tag. The `verify`
  job of `.github/workflows/release.yml` checks those, waits for self-ci of the tagged commit to have succeeded
  (all OSes and Pythons), and runs the whole suite; `publish` then moves `v1` and creates the GitHub release. Both
  publish steps are idempotent: after a failure, "Re-run failed jobs". Nobody moves `v1` by hand. The rules live in
  `.github/scripts/release_guard.py`.
- Without a working Actions run, `python .github/scripts/release_local.py X.Y.Z` on a clean checkout of `origin/master` does the
  release.yml checks locally (`prepare --check`, every pre-commit hook over all files, the whole suite on Python 3.9 to 3.13, `run-all`,
  ruff, mypy, black-filtered, the isolated mypy), then creates the annotated tag and prints the pushes. `--push` runs them after a typed
  confirmation; `--rollback-to vX.Y.Z` replaces the rollback dispatch. Only the release script or release.yml moves `v1`.
- A back-port tag below the newest release (v1.18.1 after v1.19.0) is refused: it would move `v1` backwards for
  every consumer.
- `publish` and `rollback` run in the `release` environment (`environment: release` in release.yml). Verified 2026-11-07 with the GitHub API:
  the environment exists, `RELEASE_TOKEN` is an ENVIRONMENT secret (not a repository secret), and its deployment policy allows only the
  `master` branch and `v*` tags, so a tag pushed from an edited release.yml on another ref cannot read it. Not configured, by the owner's
  decision: a required reviewer (so a push to master that edits release.yml can still publish without a human approval). Accepted risk;
  audits/2026-11-07/51_security_supply_chain.md (51-F8) has the one-step way to add it.
  Also accepted (owner, 2026-11-10, audit 51-F2): the release gates come from the tagged commit, so a `v*` tag pushed from another branch
  can reach `RELEASE_TOKEN`. The environment's deployment policy limits that to `master` and `v*` tags; no check narrows it further.
- Consumers track `@v1`, so keep README's "Compatibility promise for `@v1`": within v1 only additive changes and
  defect fixes that make a gate stricter. Any change that can turn a green consumer red, or that changes what a
  refresh writes, gets a CHANGELOG entry starting **Behaviour change** with what to do.

## Rolling back a release

- When a release breaks `@v1` consumers and the fix is not minutes away:
  `python .github/scripts/release_local.py --rollback-to v1.19.0` (the last good `vX.Y.Z`; add `--push` to run it).
  It checks the target is an existing release and prints the `v1` move. Consumers pinned to a SHA are unaffected.
- Then fix forward: a new commit, the next version, a new tag. That tag is higher than the bad one, so the release script
  moves `v1` to it as usual. Never delete or re-point the bad `vX.Y.Z` tag itself.
- A consumer with a local clone of this repo needs `git fetch --tags --force` to see the moved `v1`.
