"""The doctests a package ships actually run, and there are some to run.

A doctest that never executes is worse than no example: a reader takes its stated output as verified behaviour.
glossum found one stating ``'hix'`` where the function returned ``'hi x'`` (audit 2026-09-01 12-M1) -- nothing ever
ran doctests, since its addopts carried no ``--doctest-modules``. mlframe sets ``doctest_optionflags`` and also
never passes the flag. This runs every module's doctests through ``doctest.testmod`` and requires a minimum number
of examples, so deleting the last doctest cannot turn the check green by emptying it.
"""

from __future__ import annotations

import doctest
import importlib
import pkgutil
from collections.abc import Iterable
from types import ModuleType

__all__ = ["assert_package_doctests_pass", "run_package_doctests"]


def run_package_doctests(package: ModuleType | str, *, skip_prefixes: Iterable[str] = (), optionflags: int = 0) -> tuple[int, list[str], list[str]]:
    """``(examples attempted, failures, modules that would not import)`` over every non-package module of *package*."""
    pkg = importlib.import_module(package) if isinstance(package, str) else package
    skip = tuple(skip_prefixes)
    attempted = 0
    failures: list[str] = []
    unimportable: list[str] = []
    for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
        if info.ispkg or (skip and info.name.startswith(skip)):
            continue
        try:
            module = importlib.import_module(info.name)
        except Exception as exc:  # an unimportable module is another check's problem, but it is reported
            unimportable.append(f"{info.name}: {type(exc).__name__}")
            continue
        result = doctest.testmod(module, verbose=False, report=False, optionflags=optionflags)
        attempted += result.attempted
        if result.failed:
            failures.append(f"{info.name}: {result.failed} of {result.attempted} failed")
    return attempted, failures, unimportable


def assert_package_doctests_pass(
    package: ModuleType | str,
    *,
    skip_prefixes: Iterable[str] = (),
    min_examples: int = 1,
    optionflags: int = doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
) -> None:
    """Fail on a failing doctest, or when fewer than *min_examples* ran."""
    import pytest

    attempted, failures, _unimportable = run_package_doctests(package, skip_prefixes=skip_prefixes, optionflags=optionflags)
    if failures:
        pytest.fail("doctests failed; Fix the example or the function:\n  " + "\n  ".join(failures))
    if attempted < min_examples:
        pytest.fail(f"only {attempted} doctest example(s) ran; expected at least {min_examples}. Check skip_prefixes and imports -- an empty run is not a pass")
