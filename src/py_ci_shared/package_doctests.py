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
import importlib.util
import pkgutil
from collections.abc import Iterable
from types import ModuleType

__all__ = ["assert_package_doctests_pass", "run_package_doctests"]


def _has_example(module_name: str) -> bool:
    """True unless the module's source is readable and contains no ``>>>`` prompt (then there is nothing to run)."""
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return True
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin or not origin.endswith(".py"):
        return True
    try:
        with open(origin, encoding="utf-8", errors="replace") as fh:
            return ">>>" in fh.read()
    except OSError:
        return True


def run_package_doctests(
    package: ModuleType | str,
    *,
    skip_prefixes: Iterable[str] = (),
    skip_parts: Iterable[str] = (),
    optionflags: int = 0,
) -> tuple[int, list[str], list[str]]:
    """``(examples attempted, failures, modules that would not import)`` over *package* and every module under it.

    *package* may also be a plain module. ``__init__`` modules are included. *skip_parts* drops any module whose
    dotted name has one of them as a segment (``"_benchmarks"`` skips every nested benchmark package, which a
    prefix cannot express). A module whose source has no ``>>>`` is not imported: importing a large package's
    every module only to find nothing to run is the expensive part, and some modules are scripts that run on import.
    """
    pkg = importlib.import_module(package) if isinstance(package, str) else package
    skip = tuple(skip_prefixes)
    parts = frozenset(skip_parts)
    names = [pkg.__name__]
    if hasattr(pkg, "__path__"):
        names += [
            info.name
            for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}.", onerror=lambda _name: None)
            if not (parts and parts.intersection(info.name.split(".")))
        ]
    attempted = 0
    failures: list[str] = []
    unimportable: list[str] = []
    for name in names:
        if (skip and name.startswith(skip)) or not _has_example(name):
            continue
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # an unimportable module is another check's problem, but it is reported
            unimportable.append(f"{name}: {type(exc).__name__}")
            continue
        result = doctest.testmod(module, verbose=False, report=False, optionflags=optionflags)
        attempted += result.attempted
        if result.failed:
            failures.append(f"{name}: {result.failed} of {result.attempted} failed")
    return attempted, failures, unimportable


def assert_package_doctests_pass(
    package: ModuleType | str,
    *,
    skip_prefixes: Iterable[str] = (),
    skip_parts: Iterable[str] = (),
    min_examples: int = 1,
    optionflags: int = doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
) -> None:
    """Fail on a failing doctest, or when fewer than *min_examples* ran."""
    import pytest

    attempted, failures, _unimportable = run_package_doctests(package, skip_prefixes=skip_prefixes, skip_parts=skip_parts, optionflags=optionflags)
    if failures:
        pytest.fail("doctests failed; Fix the example or the function:\n  " + "\n  ".join(failures))
    if attempted < min_examples:
        pytest.fail(f"only {attempted} doctest example(s) ran; expected at least {min_examples}. Check skip_prefixes and imports -- an empty run is not a pass")
