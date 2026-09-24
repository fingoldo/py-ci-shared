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
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Baseline, UnparsedFilesError, refresh_requested, register_refresh_options, relative_posix, scan_python

REFRESH_FLAG = "--refresh-uncalled-functions-baseline"
_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def register_refresh_option(parser) -> None:  # type: ignore[no-untyped-def]
    """Register ``--refresh-uncalled-functions-baseline`` (and the generic ``--py-ci-refresh``) on pytest's parser.

    Same rationale as ``code_audit_meta.register_refresh_option``: pytest rejects unrecognized CLI
    options before test code runs, so every consuming repo's conftest.py must call this from its own
    ``pytest_addoption``.
    """
    register_refresh_options(parser, [REFRESH_FLAG])


def _refresh_requested(request: Optional[Any] = None) -> bool:
    return refresh_requested(REFRESH_FLAG, request)


def _module_level_defs(body: "list[ast.stmt]") -> Iterator[_FuncDef]:
    """Functions defined at module level, including under a module-level ``if``/``try``/``with`` (a platform or
    optional-dependency switch still defines a module function); never inside a class or another function."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
        elif isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            yield from _module_level_defs(node.body)
            yield from _module_level_defs(node.orelse)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            yield from _module_level_defs(node.body)
        elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            yield from _module_level_defs(node.body)  # type: ignore[attr-defined]
            for handler in node.handlers:  # type: ignore[attr-defined]
                yield from _module_level_defs(handler.body)
            yield from _module_level_defs(node.orelse)  # type: ignore[attr-defined]
            yield from _module_level_defs(node.finalbody)  # type: ignore[attr-defined]


def _definitions(tree: ast.Module, path: Path, root: Path) -> dict[str, str]:
    """Module-level function names defined in *tree*, mapped to ``relative/path.py::name``.

    MODULE LEVEL ONLY. A method is reached through an instance and its name is often shared across
    unrelated classes, so resolving "is this method called" needs type information this check does
    not have; reporting one would be a guess. Nested functions are excluded for the same reason plus
    a stronger one: they are called by their enclosing function or they are unreachable, and the
    enclosing function is itself in scope here.
    """
    out: dict[str, str] = {}
    rel = relative_posix(path, root)
    for node in _module_level_defs(tree.body):
        out[node.name] = f"{rel}::{node.name}"
    return out


def _local_bindings(fn: "Union[_FuncDef, ast.Lambda]") -> set[str]:
    """Names *fn* binds in its own scope (parameters, assignments, loop/with/except targets, local imports and
    defs), minus those it declares ``global``/``nonlocal``."""
    args = fn.args
    bound = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, *(a for a in (args.vararg, args.kwarg) if a)]}
    declared_outer: set[str] = set()
    stack: list[ast.AST] = list(fn.body) if isinstance(fn.body, list) else [fn.body]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared_outer.update(node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            continue  # its body is its own scope
        elif isinstance(node, ast.Lambda):
            continue
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # Not a shadow: a local import binds the name to the imported object itself, so ``from ids import
            # make_id`` inside a function followed by ``make_id()`` IS a call of make_id. Counting it as a local
            # binding reported every lazily imported function as dead (49 in glossum on 2026-09-24).
            pass
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        stack.extend(ast.iter_child_nodes(node))
    return bound - declared_outer


def _referenced_names(tree: ast.Module) -> set[str]:
    """Every module-level name this module LOADS, by any mechanism that could reach a function.

    Deliberately generous about what counts as a reference, because a false "this is dead" is far
    more expensive than a missed one: it invites someone to delete working code. A bare mention as a
    value -- ``handlers = [f]``, ``partial(f, x)``, ``@f``, ``getattr(mod, "f")`` -- all count.

    Two loads do NOT count, since neither reaches the module function: a load inside a function of a name that
    function (or an enclosing one) binds locally, and a function's load of its OWN name (self-recursion: a
    function that only calls itself is still never called).

    ``getattr``/``hasattr`` with a literal name is included: dynamic dispatch is a real call site even though no
    ``Name`` node names it.

    ALIASED IMPORTS resolve to the original name. ``from secrets_scrub import redact_secrets as
    _redact_secrets`` followed by ``_redact_secrets(...)`` is a call to ``redact_secrets``, and the
    first version of this module reported that function as dead because the two names never met.
    An import is not itself a use -- importing something and never calling it is exactly the state
    this check hunts -- so the alias only counts when the LOCAL name is loaded somewhere.
    """
    loaded: set[str] = set()
    aliases: dict[str, str] = {}

    def visit(node: ast.AST, shadowed: "frozenset[str]", own: "frozenset[str]") -> None:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in shadowed and node.id not in own:
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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            outer_parts: list[ast.expr] = [*getattr(node, "decorator_list", []), *node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
            for part in outer_parts:
                visit(part, shadowed, own)  # evaluated in the ENCLOSING scope
            for param in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if param.annotation is not None:
                    visit(param.annotation, shadowed, own)
            inner_shadow = shadowed | _local_bindings(node)
            inner_own = own | ({node.name} if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else set())
            body: list[ast.AST] = list(node.body) if isinstance(node.body, list) else [node.body]
            for statement in body:
                visit(statement, frozenset(inner_shadow), frozenset(inner_own))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, shadowed, own)

    visit(tree, frozenset(), frozenset())
    return loaded | {original for local, original in aliases.items() if local in loaded}


def find_uncalled_functions(files: Iterable[Path], root: Path, *, allow_unparsed: bool = False) -> dict[str, str]:
    """Return ``{"rel/path.py::name": name}`` for module-level functions nothing in *files* loads.

    *files* is the PRODUCTION set: the caller decides what that means, and must exclude tests. A
    test calling a function is not a production call site -- that is precisely how the two findings
    this module generalises stayed hidden, both of them fully covered by tests.

    The defining module is included when counting references, so a private helper used elsewhere in
    its own file is correctly seen as live. A file that cannot be read or parsed raises
    :class:`py_ci_shared._core.UnparsedFilesError`: its call sites are unknown, so every function it calls would be
    reported dead. ``allow_unparsed=True`` restores the old skip for callers that report problems themselves.
    """
    scan = scan_python([Path(p) for p in files], min_files=0, root=root)
    if scan.unparsed and not allow_unparsed:
        scan.check_unparsed()

    definitions: dict[str, str] = {}
    for parsed in scan:
        for name, key in _definitions(parsed.tree, parsed.path, root).items():
            definitions.setdefault(key, name)

    referenced: set[str] = set()
    for parsed in scan:
        referenced |= _referenced_names(parsed.tree)

    return {key: name for key, name in definitions.items() if name not in referenced}


def assert_no_new_uncalled_function(
    files: Iterable[Path],
    root: Path,
    baseline_path: Path,
    ignore: Iterable[str] = (),
    *,
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
) -> None:
    """Fail if a module-level function has no production call site, unless it is baselined.

    The baseline is a committed multiset (``_core.Baseline``). A MISSING baseline fails and names the refresh
    command; it is written only when a refresh is requested (``refresh=True``, ``--refresh-uncalled-functions-baseline``
    via the pytest *request* or command line, or env ``PY_CI_SHARED_REFRESH=uncalled-functions``), then the run is
    skipped. A walk that parsed fewer than *min_files* files, or hit an unparsable one, fails and never writes.

    ``ignore`` takes bare function NAMES for the cases this check cannot judge and should not guess
    at: a library's public API, a framework callback invoked by name from outside the repo, a
    plugin hook. Prefer listing those over lowering the bar, so the entries stay readable as
    decisions rather than as noise.
    """
    import pytest

    paths = [Path(p) for p in files]
    scan = scan_python(paths, min_files=min_files, root=root)
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    try:
        scan.check_unparsed()
    except UnparsedFilesError as exc:
        problems.append(str(exc))
    if problems:  # never write or compare a baseline from a broken walk
        pytest.fail("\n".join(problems), pytrace=False)

    ignored = set(ignore)
    current = {k: v for k, v in find_uncalled_functions(paths, root).items() if v not in ignored}
    do_refresh = refresh if refresh is not None else _refresh_requested(request)
    baseline = Baseline(baseline_path, gate="uncalled-functions", refresh_command=f"pytest {REFRESH_FLAG} (or PY_CI_SHARED_REFRESH=uncalled-functions)")
    outcome = baseline.enforce(sorted(current), refresh=do_refresh)
    if outcome.refreshed:
        pytest.skip(f"uncalled-function baseline refreshed at {baseline_path.name} ({len(current)} entry/entries)")
    if outcome.missing:
        pytest.fail(outcome.message, pytrace=False)
    if outcome.new:
        pytest.fail(
            "these functions are defined and never called by production code, so whatever they "
            "enforce is not enforced:\n  " + "\n  ".join(outcome.new) + "\n\nA test calling it is not a production call site, and neither is an `__all__` "
            "entry or a doctest -- that is how this class of dead control hides. Either wire it in, "
            "delete it, or add its name to `ignore` with a reason.",
            pytrace=False,
        )
    if outcome.unjustified:
        pytest.fail(outcome.message, pytrace=False)
    if outcome.stale:
        pytest.fail(
            "these are no longer uncalled and must be dropped from the baseline, or it stops " "meaning anything for them:\n  " + "\n  ".join(outcome.stale),
            pytrace=False,
        )
