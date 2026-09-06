"""Find test fixtures that RESET a module attribute the module does not have.

`mod.NAME = value` on a module with no such attribute does not fail -- it CREATES one. A fixture
that does `saved = getattr(mod, "NAME", 0)` ... `mod.NAME = saved` therefore invents a module-level
attribute and leaves it behind for the rest of the process, while appearing to be careful.

Two of these were found in one afternoon in one project:

    test_slow_query_recorder      reset `_db._slow_conn`               lives in `_slow_query`
    test_aborted_scan_is_recorded reset `_core.metrics_write_failures` lives in `_scan`

The first left a MagicMock telemetry connection installed for every later test in the xdist worker,
and the assertion that checked it read back the value the fixture had just written. The second
CREATED the attribute an audit finding said did not exist -- `_observability` read the counter as
`getattr(_core, ...)` from a module that never had it, so the metric was always absent -- which made
that finding pass or fail according to which worker ran first, and unprovable either way.

The cause is always a module carved into pieces that re-exports what it used to own. `_db` no longer
holds `_slow_conn`; `_slow_query` does. Nothing says so at the point of use, and Python will not.

WHAT THIS DELIBERATELY DOES NOT DO, AND WHY THAT MATTERS MORE THAN WHAT IT DOES.

An earlier version of this module also flagged `patch("pkg.NAME")` where `pkg` merely re-exports
`NAME` and no first-party module reads it through `pkg` -- the shape of two more defects found the
same afternoon, where tests patched `upwork_shared.db_connection` and `upwork_shared.curl_session`
while the code called `_core.db_connection` and `_core.curl_session`. Both had been opening real
connections to a PRODUCTION database and making real requests to a live site on every full-suite
run, for months, silently.

That rule was removed after it reported 337 findings on its first real tree, and the reason is not
that it needed tuning. A patch is very often placed precisely so that something is NOT called, and
"nothing reads this name" is then the intended state rather than a defect. The two cases are
indistinguishable from the source. A rule that cannot tell them apart is either ignored or switched
off, and the sound half above goes with it.

A fifth defect the same day settles the point. Five tests saved and restored `CHROME_IMPERSONATE` in
a `finally` -- correctly -- while the function that decides returns `random.choice(
_verified_impersonations)` and consults `CHROME_IMPERSONATE` only when that pool is EMPTY. The tests
filled the pool and never cleared it. Every line was right; the mapping from "what I restored" to
"what production reads" was wrong, and no static rule can see that.

WHAT DOES CATCH THAT FAMILY is a teardown assertion about CONSEQUENCES -- that the test reached no
network, opened no real database connection, left no process-global cache non-empty. All six of that
afternoon's defects were caught by such guards or by the failures they produced, and none by reading
the code. Prefer adding a guard to widening a scanner.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Finding", "ModuleFacts", "module_index", "scan"]


@dataclass(frozen=True)
class Finding:
    """One reset that invents an attribute, with enough detail to act on without reading this file."""

    path: Path
    lineno: int
    target: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover -- formatting only
        return f"{self.path}:{self.lineno}: {self.target} -- {self.detail}"


@dataclass(frozen=True)
class ModuleFacts:
    """What one first-party module holds at module level.

    `bound` is EVERY module-level name, imports included: assigning over an imported name is the
    ordinary way to stub a dependency and must never be reported. `defined` excludes import-only
    names, and exists only to name the module that really owns an attribute in the message -- the
    difference between "this is wrong" and "this is wrong, and here is where it lives".

    `forwards` marks a module whose attributes are not knowable from its source, because it builds
    or resolves them at run time -- `globals()[name] = ...` in a loop, a `__getattr__`, or a
    `__setattr__` that proxies to another module. A whole package can be that: the tree this check
    was written against has a package whose module object is a custom class forwarding `__setattr__`
    to its `_core`, deliberately, so that patching either spelling works.

    Such a module is SKIPPED ENTIRELY. It is the one place the static answer is guaranteed wrong,
    and it is exactly where an author is most likely to be doing the right thing -- an early version
    of this check reported twelve findings against that package, every one of them a module that had
    already solved the problem being reported.
    """

    name: str
    path: Path
    bound: frozenset[str]
    defined: frozenset[str]
    forwards: bool


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _forwards_dynamically(tree: ast.AST) -> bool:
    """Does this module decide its own attributes at run time?

    Three shapes, all of which defeat a source-level answer and all of which are legitimate:
    `globals()[...] = ...` (a re-export loop), a module-level `__getattr__` (PEP 562 lazy
    attributes), and a `__setattr__` on a custom module class (a proxy that forwards assignment to
    another module). Any of them means "do not guess about this module".
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"__getattr__", "__setattr__"}:
            return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Call)
                    and isinstance(target.value.func, ast.Name)
                    and target.value.func.id == "globals"
                ):
                    return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            if isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Name) and node.func.value.func.id == "globals":
                return True
    return False


def _module_level_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(bound, defined) for one module.

    Walks the whole tree rather than just `body`, because these projects put bindings inside
    `try:`/`if TYPE_CHECKING:`/`if sys.platform` blocks and a module-body-only scan reports those as
    missing -- which is a false positive on exactly the carved-up modules this check is for.
    """
    bound: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    bound |= imported
    return bound, bound - imported


def module_index(roots: "list[Path]", *, package_root: Path) -> "dict[str, ModuleFacts]":
    """Module-level names for every first-party module under *roots*, keyed by dotted name."""
    index: dict[str, ModuleFacts] = {}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            bound, defined = _module_level_names(tree)
            name = _module_name(path, package_root)
            index[name] = ModuleFacts(
                name=name,
                path=path,
                bound=frozenset(bound),
                defined=frozenset(defined),
                forwards=_forwards_dynamically(tree),
            )
    return index


def _aliases_in(body: "list[ast.stmt]", known: "set[str]", inherited: "dict[str, str]") -> "dict[str, str]":
    """Local name -> dotted first-party module, for the imports visible in ONE scope.

    SCOPE MATTERS, and getting it wrong is not theoretical. A test file may do
    `import backfill_cl_id as b` inside one test and use `b` as an ordinary local -- a token-bucket
    instance, say -- in another. A file-wide alias map reads `b._rate = 2.0` in the second as a
    module attribute assignment and reports a defect in code that has none. An early version of this
    module did exactly that, twice in one file.

    A name REBOUND in this scope stops being a module here, for the same reason.
    """
    aliases = dict(inherited)
    for node in body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in known:
                    aliases[alias.asname or alias.name] = candidate
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.For, ast.With)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    aliases.pop(sub.id, None)
    return aliases


def _module_assignments(body: "list[ast.stmt]", known: "set[str]", inherited: "dict[str, str]"):
    """Yield `(lineno, dotted_module, attr)` for `mod.NAME = ...` visible in this scope and below."""
    aliases = _aliases_in(body, known, inherited)
    for node in body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    dotted = aliases.get(target.value.id)
                    if dotted:
                        yield node.lineno, dotted, target.attr
        inner = getattr(node, "body", None)
        if isinstance(inner, list):
            yield from _module_assignments(inner, known, aliases)
        for extra in ("orelse", "finalbody"):
            branch = getattr(node, extra, None)
            if isinstance(branch, list) and branch:
                yield from _module_assignments(branch, known, aliases)
        for handler in getattr(node, "handlers", []) or []:
            yield from _module_assignments(handler.body, known, aliases)


def scan(test_paths: "list[Path]", index: "dict[str, ModuleFacts]") -> "list[Finding]":
    """Report `module.NAME = ...` in tests where the module has no `NAME` to set."""
    findings: list[Finding] = []
    known = set(index)
    for path in sorted(test_paths):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        for lineno, dotted, attr in _module_assignments(tree.body, known, {}):
            facts = index[dotted]
            if facts.forwards:
                continue  # its attributes are decided at run time; the source cannot say
            if attr in facts.bound:
                continue  # the name exists here, imported or not: setting it is ordinary
            owner = next((f.name for f in index.values() if attr in f.defined), None)
            where = f" It is defined in `{owner}`." if owner else " Nothing in the tree defines it."
            findings.append(
                Finding(
                    path=path,
                    lineno=lineno,
                    target=f"{dotted}.{attr}",
                    detail=(
                        f"`{dotted}` has no `{attr}`, so this assignment CREATES one rather than setting "
                        f"anything the code reads.{where} A save/restore around it leaves a new module "
                        "attribute behind for the rest of the process, and any assertion about it reads "
                        "back what this line wrote."
                    ),
                )
            )
    return findings
