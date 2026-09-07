"""Assert that no test reads or writes the on-disk state a real run of the program uses.

A conftest that refuses real database connections and real network can still let every test share
production's CHECKPOINT FILES, and that is the third shared resource nobody guards.

WHAT IT COST, and it is why this is shared rather than local. A scraper's `_ActiveJobsState()` loaded
its disappearance counts from `checkpoints/disappearance_counts.json` and its loop saved them back,
so a test that built a state shared one file with every other test AND with any real run made from
that checkout. It did not look like shared state -- it looked like a flaky test, green alone and red
in the full suite. The mechanism was ALTERNATION, not a race: one run leaves `{"j1": 1}`, the next
loads it, reaches the give-up threshold, tombstones and pops, leaving `{}`. Four runs read 1, {}, 1,
{}. Worse, an order-dependent test corrupts a mutation sweep -- it appeared in the failure list of
two sweep cases and one verdict had to be re-established afterwards.

The same measurement then found five more in a sibling package, four of them read back, including a
CIRCUIT BREAKER whose state is loaded at module import: a test that tripped it left a file a real run
would start OPEN from.

HOW TO FIND THEM -- do not read imports, measure. Snapshot the mtime of every non-source file under
the state directories, run the whole suite, and diff. That named nine files in one package and five
in another, including two nobody would have guessed: a rotating log a test run could push real
history out of, and a lock file relative to the CWD.

Two traps this module cannot remove for you, both found by checking rather than assuming:

* Patch the module the readers USE, not the one the constant is DEFINED in. Two modules can each
  hold an independent binding of the same name, and patching the definition site reaches nobody.
* A constant read at IMPORT time -- a logger built at module scope, say -- is fixed before any
  fixture runs. That redirect belongs at conftest import, or in an environment variable a
  subprocess can inherit.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["assert_outside", "offending_state_paths"]


def assert_outside(value: Path | str, repo_root: Path | str, *, what: str) -> None:
    """Fail if *value* resolves inside *repo_root*.

    `what` names the constant, because the failure has to tell a reader which redirect is missing
    rather than that "a path was wrong".
    """
    target = Path(value).resolve()
    root = Path(repo_root).resolve()
    if target.is_relative_to(root):
        raise AssertionError(
            f"{what} resolves to {target}, inside the checkout. A test that writes it changes what the "
            f"NEXT test reads, and what a real run started from this directory would read. Redirect it "
            f"per test (or at conftest import, if it is read while a module is being imported)."
        )


def offending_state_paths(constants: dict[str, Path | str], repo_root: Path | str) -> list[str]:
    """Every entry of *constants* that resolves inside *repo_root*, as reviewer-readable lines.

    Takes a mapping so one meta-test can cover a package's whole set and report ALL of them at once;
    finding them one failure per run is how a sweep like this gets abandoned half-done.
    """
    problems: list[str] = []
    root = Path(repo_root).resolve()
    for name, value in sorted(constants.items()):
        target = Path(value).resolve()
        if target.is_relative_to(root):
            problems.append(f"{name} -> {target}")
    return problems
