# py-ci-shared: working rules

## Adding a gate

- Start with `py-ci-shared new-gate <name> [--kind gate|library] --summary "..."`, or follow the layout it writes:
  `src/py_ci_shared/<name>.py`, `tests/test_<name>.py`, `tests/canary/<name>/{violation,clean,bom,unparsable}/`,
  a `CANARIES` entry in `tests/test_gate_teeth.py`, a `registry.toml` entry and the README catalogue row. The
  scaffolder never overwrites a file; its tests and canary fail until you write them.
- Read and parse through `_core`: `scan_python`/`read_source`/`parse_file`, `Finding`, `Baseline`,
  `ImportAliases`. Never `ast.parse(path.read_text(...))` with `except SyntaxError: continue`: a file the gate
  cannot read is a finding (`UnparsedFilesError`), not a pass. Keep a `min_files` floor and an `allow_unparsed`
  switch.
- Register every public module in `src/py_ci_shared/registry.toml` (sorted by name; `since` = the version in
  `pyproject.toml`). Private helpers go in `registry.INTERNAL_MODULES` with a reason.
- A corpus-scanning gate needs a canary in `tests/canary/<name>/` (fixture files end in `.canary`), or an `EXEMPT`
  entry in `tests/test_gate_teeth.py` that says why its subject cannot be seeded as files.
- A new `find_*` must bind from a repo root in `corpus_drift.BINDINGS` or be listed in `corpus_drift.NON_CORPUS`
  with a reason.

## Before pushing

- Format with `uvx black==26.5.1` (the version `black-filtered.yml` runs); lint with `uvx ruff@0.16.1 check src tests`.
- Run `tests/test_package_inventory.py` and `tests/test_gate_teeth.py`, plus the tests of what you changed.
- Code must support Python 3.9 and pass `mypy src/py_ci_shared`.

## Versions and releases

- `version` in `pyproject.toml` (and `__version__` in `src/py_ci_shared/__init__.py`) is the NEXT release tag
  without its `v`; `tests/test_release_version.py` holds them equal and ahead of the newest tag.
- A release is a pushed `vX.Y.Z` tag equal to that version, on master. `.github/workflows/release.yml` verifies
  it, runs the tests, then moves `v1` to it and creates the GitHub release. Nobody moves `v1` by hand.
