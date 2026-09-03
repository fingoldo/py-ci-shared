"""Shared check: a function that production code never calls.

The sibling of ``guard_population.py``. That module catches a guard SCRIPT whose file selection
matches nothing; this one catches a guard FUNCTION that nothing invokes. Both are the same failure:
the control exists, the suite is green, and the rule has not been enforced since it was written.

Generalises three 2026-09-03/04 findings in the Upwork proposal generator, all found by hand:

* ``prompt_safety.redact_injection_attempts`` -- defined, listed in ``__all__``, doctested, and
  called from nowhere. A prompt-injection redactor that never redacted anything.
* ``pipeline.humanize._ascii_cleanup`` -- defined, re-exported through ``pipeline/__init__.py``,
  covered by a parametrised test, called from nowhere. Every cover letter shipped with the bullet,
  ellipsis and arrow characters it exists to remove, while the letter scorer separately deducted
  points for those same characters.
* The prompt rule "NEVER WITHDRAW THE APPLICATION" -- not a function, so out of scope here, but the
  same shape: a control with no enforcement. Four of nineteen real letters withdrew the application.

WHY THE EXISTING GATE DOES NOT SEE THIS
---------------------------------------
Vulture is the obvious answer and it cannot help at the setting these repos run. Measured:

    $ python -m vulture probe.py --min-confidence 0
    probe.py:1: unused import 'os'          (90% confidence)
    probe.py:4: unused function 'f'         (60% confidence)

An unused import scores 90 and an unused function scores 60, so a hook running
``--min-confidence 80`` -- which is what the realtime_applications gate runs, tuned to catch exactly
the seven stale ``timezone`` imports it was triaged against -- reports imports and is blind to
functions by construction. Lowering the threshold to 60 is not the fix: it also surfaces every
legitimate extension point, framework callback and backward-compat re-export, which is why the
threshold is where it is.

WHY AST AND NOT GREP
--------------------
The interesting part of this check is what does NOT count as a call, and with ``ast`` that falls out
for free rather than needing an exception list:

* ``__all__ = ["f"]`` is a list of STRING constants. A string is not a ``Name`` load, so exporting a
  function does not make it used -- which is the single most common way dead code hides.
* A docstring, including its own doctests, is a string constant too.
* A comment is not in the tree at all.

A grep-based version would have to special-case all three, and would still count ``# TODO: call f()``
as a call.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

REFRESH_FLAG = "--refresh-uncalled-functions-baseline"


def register_refresh_option(parser) -> None:
    """Register ``--refresh-uncalled-functions-baseline`` as a no-op boolean flag.

    Same rationale as ``code_audit_meta.register_refresh_option``: pytest rejects unrecognized CLI
    options before test code runs, so every consuming repo's conftest.py must call this from its own
    ``pytest_addoption``.
    """
    try:
        parser.addoption(
            REFRESH_FLAG,
            action="store_true",
            default=False,
            help="Rewrite the uncalled-function baseline instead of asserting against it.",
        )
    except Exception:  # noqa: BLE001 -- a duplicate registration must not break collection
        pass


def _refresh_requested() -> bool:
    import sys

    return REFRESH_FLAG in sys.argv


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (OSError, SyntaxError):
        return None


def _definitions(tree: ast.Module, path: Path, root: Path) -> dict[str, str]:
    """Module-level function names defined in *tree*, mapped to ``relative/path.py::name``.

    MODULE LEVEL ONLY. A method is reached through an instance and its name is often shared across
    unrelated classes, so resolving "is this method called" needs type information this check does
    not have; reporting one would be a guess. Nested functions are excluded for the same reason plus
    a stronger one: they are called by their enclosing function or they are unreachable, and the
    enclosing function is itself in scope here.
    """
    out: dict[str, str] = {}
    rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = f"{rel}::{node.name}"
    return out


def _referenced_names(tree: ast.Module) -> set[str]:
    """Every name this module LOADS, by any mechanism that could reach a function.

    Deliberately generous about what counts as a reference, because a false "this is dead" is far
    more expensive than a missed one: it invites someone to delete working code. A bare mention as a
    value -- ``handlers = [f]``, ``partial(f, x)``, ``@f``, ``getattr(mod, "f")`` -- all count.

    ``getattr``/``hasattr`` with a literal name is included for that reason: dynamic dispatch is a
    real call site even though no ``Name`` node names it.

    ALIASED IMPORTS resolve to the original name. ``from secrets_scrub import redact_secrets as
    _redact_secrets`` followed by ``_redact_secrets(...)`` is a call to ``redact_secrets``, and the
    first version of this module reported that function as dead because the two names never met.
    An import is not itself a use -- importing something and never calling it is exactly the state
    this check hunts -- so the alias only counts when the LOCAL name is loaded somewhere.
    """
    loaded: set[str] = set()
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loaded.add(node.id)
        elif isinstance(node, ast.Attribute):
            loaded.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name.rsplit(".", 1)[-1]
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"getattr", "hasattr", "setattr"}:
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        loaded.add(arg.value)
    return loaded | {original for local, original in aliases.items() if local in loaded}


def find_uncalled_functions(files: Iterable[Path], root: Path) -> dict[str, str]:
    """Return ``{"rel/path.py::name": name}`` for module-level functions nothing in *files* loads.

    *files* is the PRODUCTION set: the caller decides what that means, and must exclude tests. A
    test calling a function is not a production call site -- that is precisely how the two findings
    this module generalises stayed hidden, both of them fully covered by tests.

    The defining module is included when counting references, so a private helper used elsewhere in
    its own file is correctly seen as live.
    """
    paths = [p for p in files]
    trees: dict[Path, ast.Module] = {}
    for path in paths:
        tree = _parse(path)
        if tree is not None:
            trees[path] = tree

    definitions: dict[str, str] = {}
    for path, tree in trees.items():
        for name, key in _definitions(tree, path, root).items():
            definitions.setdefault(key, name)

    referenced: set[str] = set()
    for tree in trees.values():
        referenced |= _referenced_names(tree)

    return {key: name for key, name in definitions.items() if name not in referenced}


def assert_no_new_uncalled_function(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    ignore: Iterable[str] = (),
) -> None:
    """Fail if a module-level function has no production call site, unless it is baselined.

    Seeds or refreshes ``baseline_path`` (first run, or ``--refresh-uncalled-functions-baseline``)
    and ``pytest.skip()``s that run instead of comparing, mirroring ``loc_budget`` and
    ``code_audit_meta``.

    ``ignore`` takes bare function NAMES for the cases this check cannot judge and should not guess
    at: a library's public API, a framework callback invoked by name from outside the repo, a
    plugin hook. Prefer listing those over lowering the bar, so the entries stay readable as
    decisions rather than as noise.
    """
    import orjson
    import pytest

    ignored = set(ignore)
    current = {k: v for k, v in find_uncalled_functions(files, root).items() if v not in ignored}

    if _refresh_requested() or not baseline_path.exists():
        baseline_path.write_text(
            orjson.dumps(sorted(current), option=orjson.OPT_INDENT_2).decode("utf-8"),
            encoding="utf-8",
        )
        pytest.skip(f"uncalled-function baseline refreshed at {baseline_path.name} ({len(current)} entry/entries)")

    baseline = set(orjson.loads(baseline_path.read_bytes()))
    new = sorted(set(current) - baseline)
    if new:
        pytest.fail(
            "these functions are defined and never called by production code, so whatever they "
            "enforce is not enforced:\n  "
            + "\n  ".join(new)
            + "\n\nA test calling it is not a production call site, and neither is an `__all__` "
            "entry or a doctest -- that is how this class of dead control hides. Either wire it in, "
            "delete it, or add its name to `ignore` with a reason."
        )

    stale = sorted(baseline - set(current))
    if stale:
        pytest.fail(
            "these are no longer uncalled and must be dropped from the baseline, or it stops "
            "meaning anything for them:\n  " + "\n  ".join(stale)
        )
