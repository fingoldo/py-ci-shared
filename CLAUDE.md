# py-ci-shared: working rules

## Adding a gate

- Start with `py-ci-shared new-gate <name> [--kind gate|library] --summary "..."`, or follow the layout it writes:
  `src/py_ci_shared/<name>.py`, `tests/test_<name>.py`, `tests/canary/<name>/{violation,clean,bom,unparsable}/`,
  a `CANARIES` entry in `tests/test_gate_teeth.py`, a `registry.toml` entry and the README catalogue row. The
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

## Before pushing

- Format with `uvx black==26.5.1` (`BLACK_VERSION` in `tool_versions.py`, which `black-filtered.yml` runs); lint with `uvx ruff@0.16.1 check src tests`.
- Run `tests/test_package_inventory.py` and `tests/test_gate_teeth.py`, plus the tests of what you changed.
- Code must support Python 3.9 and pass `mypy src/py_ci_shared`.
- Install the hooks once per clone: `python -m pre_commit install --hook-type pre-commit --hook-type pre-push` then `python -m py_ci_shared.install_safe_hook`. The pre-push hook runs mypy and the self-gate tests (about 4 minutes); the rest of `tests/` is left to CI.

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
- A release is a pushed `vX.Y.Z` tag equal to that version, on master, and the highest `v1.*.*` tag. The `verify`
  job of `.github/workflows/release.yml` checks those, waits for self-ci of the tagged commit to have succeeded
  (all OSes and Pythons), and runs the whole suite; `publish` then moves `v1` and creates the GitHub release. Both
  publish steps are idempotent: after a failure, "Re-run failed jobs". Nobody moves `v1` by hand. The rules live in
  `.github/scripts/release_guard.py`.
- A back-port tag below the newest release (v1.18.1 after v1.19.0) is refused: it would move `v1` backwards for
  every consumer.
- `publish` and `rollback` run in the `release` environment. Owner step (repository settings, not code): give the
  environment a deployment rule allowing only `v*` tags and the default branch, and move `RELEASE_TOKEN` from the
  repository secrets into it, so a tag pushed from an edited release.yml cannot read it.
- Consumers track `@v1`, so keep README's "Compatibility promise for `@v1`": within v1 only additive changes and
  defect fixes that make a gate stricter. Any change that can turn a green consumer red, or that changes what a
  refresh writes, gets a CHANGELOG entry starting **Behaviour change** with what to do.

## Rolling back a release

- When a release breaks `@v1` consumers and the fix is not minutes away: Actions, Release, "Run workflow" on
  master with `rollback-to` = the last good `vX.Y.Z` (or `gh workflow run release.yml -f rollback-to=v1.19.0`).
  It checks the target is an existing release and points `v1` at it. Consumers pinned to a SHA are unaffected.
- Then fix forward: a new commit, the next version, a new tag. That tag is higher than the bad one, so release.yml
  moves `v1` to it as usual. Never delete or re-point the bad `vX.Y.Z` tag itself.
- A consumer with a local clone of this repo needs `git fetch --tags --force` to see the moved `v1`.
