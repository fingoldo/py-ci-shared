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


def _under(name: str, prefix: str) -> bool:
    """*name* is *prefix* or a module under it (dotted boundary); a prefix ending in ``.`` matches raw."""
    if prefix.endswith("."):
        return name.startswith(prefix)
    return name == prefix or name.startswith(prefix + ".")


def run_package_doctests(
    package: ModuleType | str,
    *,
    skip_prefixes: Iterable[str] = (),
    skip_parts: Iterable[str] = (),
    optionflags: int = 0,
) -> tuple[int, list[str], list[str]]:
    """``(examples attempted, failures, modules that would not import)`` over *package* and every module under it.

    *package* may also be a plain module. ``__init__`` modules are included. *skip_prefixes* match on dotted
    boundaries (``pkg.io`` skips ``pkg.io`` and ``pkg.io.x``, not ``pkg.iostats``). *skip_parts* drops any module
    whose dotted name has one of them as a segment (``"_benchmarks"`` skips every nested benchmark package, which a
    prefix cannot express). A module whose source has no ``>>>`` is not imported: importing a large package's
    every module only to find nothing to run is the expensive part, and some modules are scripts that run on import.
    A subpackage that fails to import while being walked is listed as unimportable (``<name>: cannot walk``): every
    module below it is invisible, so it is never silently dropped.
    """
    pkg = importlib.import_module(package) if isinstance(package, str) else package
    skip = tuple(skip_prefixes)
    parts = frozenset(skip_parts)

    def skipped(name: str) -> bool:
        return bool(parts and parts.intersection(name.split("."))) or any(_under(name, p) for p in skip)

    names = [pkg.__name__]
    unwalkable: list[str] = []
    if hasattr(pkg, "__path__"):
        names += [
            info.name
            for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}.", onerror=unwalkable.append)
            if not (parts and parts.intersection(info.name.split(".")))
        ]
    attempted = 0
    failures: list[str] = []
    unimportable: list[str] = [f"{name}: cannot walk (import failed)" for name in unwalkable if not skipped(name)]
    for name in names:
        if skipped(name) or not _has_example(name):
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
    tolerate_unimportable: Iterable[str] = (),
) -> None:
    """Fail on a failing doctest, on a module with examples (or a subpackage) that will not import, or when fewer
    than *min_examples* ran.

    *tolerate_unimportable* names modules (dotted-boundary prefixes) known not to import here, e.g. behind an optional
    dependency; an entry that no longer matches an unimportable module fails too, so the list cannot rot.
    """
    import pytest

    attempted, failures, unimportable = run_package_doctests(package, skip_prefixes=skip_prefixes, skip_parts=skip_parts, optionflags=optionflags)
    tolerated = list(tolerate_unimportable)
    blocking = [u for u in unimportable if not any(_under(u.split(":", 1)[0], t) for t in tolerated)]
    stale = [t for t in tolerated if not any(_under(u.split(":", 1)[0], t) for u in unimportable)]
    problems: list[str] = []
    if failures:
        problems.append("doctests failed; Fix the example or the function:\n  " + "\n  ".join(failures))
    if blocking:
        problems.append(
            "module(s) with doctests that will not import, so their examples never ran; fix the import or name them in "
            "tolerate_unimportable:\n  " + "\n  ".join(blocking)
        )
    if stale:
        problems.append("tolerate_unimportable entr(ies) that now import fine -- remove them:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))
    if attempted < min_examples:
        pytest.fail(f"only {attempted} doctest example(s) ran; expected at least {min_examples}. Check skip_prefixes and imports -- an empty run is not a pass")
