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
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._core import DEFAULT_EXCLUDE, iter_files, relative_posix, scan_python
from ._core.node_index import walk as _fast_walk

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
    rel = Path(relative_posix(path, root)).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _bound_names(target: ast.expr) -> "list[str]":
    """Every module-level name an assignment target binds, including tuple and list unpacking.

    `m_app_name, m_scraper_name, m_version, m_ip = None, None, None, None` binds four names, and
    collecting only bare `Name` targets bound none of them -- so a test patching any of the four was
    reported as inventing an attribute the module very much has. Found on pyutilz, five findings,
    all of them this. Starred targets (`a, *rest = ...`) unpack the same way.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _bound_names(element)]
    return []


_TRY_TYPES: tuple[type, ...] = (ast.Try,) + ((getattr(ast, "TryStar"),) if hasattr(ast, "TryStar") else ())


def _module_scope(body: "list[ast.stmt]") -> Iterator[ast.stmt]:
    """Statements that bind at MODULE scope: the body and every ``if``/``try``/``with``/loop body in it -- these
    projects put bindings inside `try:`/`if TYPE_CHECKING:`/`if sys.platform` blocks -- but never a function's or a
    class's body, whose names are locals or class attributes, not module attributes."""
    stack = list(body)
    while stack:
        node = stack.pop(0)
        yield node
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
        elif isinstance(node, _TRY_TYPES):
            stack.extend(getattr(node, "body", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))


def _globals_assigned_in_functions(tree: ast.AST) -> "set[str]":
    """Names a function declares ``global`` and assigns: those are module attributes too."""
    out: set[str] = set()
    for fn in _fast_walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        declared = {n for node in _fast_walk(fn) if isinstance(node, ast.Global) for n in node.names}
        if not declared:
            continue
        for node in _fast_walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in declared:
                out.add(node.id)
    return out


def _module_facts(tree: ast.AST) -> "tuple[set[str], set[str], bool]":
    """``(bound, defined, forwards_dynamically)`` for one module.

    ``bound``/``defined`` come from module-scope statements only (plus names a function declares ``global``): a
    function's local ``NAME = 1`` is not ``module.NAME``, and counting it let ``m.NAME = 2`` in a test pass as setting
    an attribute the module has. ``forwards`` still looks everywhere, because a ``globals()[...]`` loop or a
    ``__getattr__``/``__setattr__`` decides the module's attributes wherever it sits.
    """
    bound: set[str] = set()
    imported: set[str] = set()
    forwards = False
    body = tree.body if isinstance(tree, ast.Module) else []
    for node in _module_scope(body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    imported.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                bound.update(_bound_names(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            bound.update(_bound_names(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bound.update(_bound_names(node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    bound.update(_bound_names(item.optional_vars))
        if isinstance(node, _TRY_TYPES):
            bound.update(h.name for h in getattr(node, "handlers", []) if h.name)
        # A walrus at module scope binds a module name too.
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for sub in ast.iter_child_nodes(node):
                if not isinstance(sub, ast.stmt):
                    bound.update(n.target.id for n in _fast_walk(sub) if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name))
    bound |= _globals_assigned_in_functions(tree)
    for walked in _fast_walk(tree):
        if isinstance(walked, (ast.FunctionDef, ast.AsyncFunctionDef)) and walked.name in {"__getattr__", "__setattr__"}:
            forwards = True
        elif isinstance(walked, ast.Assign):
            for target in walked.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Call)
                    and isinstance(target.value.func, ast.Name)
                    and target.value.func.id == "globals"
                ):
                    forwards = True
        elif isinstance(walked, ast.Call) and isinstance(walked.func, ast.Attribute) and walked.func.attr == "update":
            if isinstance(walked.func.value, ast.Call) and isinstance(walked.func.value.func, ast.Name) and walked.func.value.func.id == "globals":
                forwards = True
    bound |= imported
    return bound, bound - imported, forwards


def module_index(roots: "list[Path]", *, package_root: Path, unparsed: "Optional[list[str]]" = None) -> "dict[str, ModuleFacts]":
    """Module-level names for every first-party module under *roots*, keyed by dotted name.

    Files are decoded BOM-safe. A module that cannot be parsed has no entry (so tests patching it cannot be checked);
    pass *unparsed* to collect those files as ``path:line: why`` and report them.
    """
    index: dict[str, ModuleFacts] = {}
    for root in roots:
        result = scan_python(iter_files(Path(root), ("*.py",), exclude=DEFAULT_EXCLUDE), root=package_root)
        if unparsed is not None:
            unparsed.extend(u.render() for u in result.unparsed)
        for parsed in result:
            bound, defined, forwards = _module_facts(parsed.tree)
            name = _module_name(parsed.path, package_root)
            index[name] = ModuleFacts(name=name, path=parsed.path, bound=frozenset(bound), defined=frozenset(defined), forwards=forwards)
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
                if alias.asname:
                    if alias.name in known:
                        aliases[alias.asname] = alias.name
                    continue
                # `import a.b.c` binds `a`, and `a` is the PACKAGE -- not `a.b.c`. Mapping the bare
                # name to the submodule reads `a.thing = x` against the submodule's namespace, so a
                # name the package re-exports is reported absent. That is not hypothetical: three
                # such findings in one repo were all `import pkg` followed by `import pkg.sub`.
                root = alias.name.split(".")[0]
                if root in known:
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in known:
                    aliases[alias.asname or alias.name] = candidate
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.For, ast.With)):
            for sub in _fast_walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    aliases.pop(sub.id, None)
    return aliases


#: {attr: sentinel names} for the file currently being scanned. Module-level because the guard sits
#: in a `finally` many scopes below the `getattr` that produced the sentinel, and threading it
#: through the recursive walk would change a signature three call sites rely on.
_FILE_SENTINELS: "dict[str, set[str]]" = {}


def _names_in(node: ast.expr) -> "set[str]":
    """Every bare name mentioned in an expression."""
    return {n.id for n in _fast_walk(node) if isinstance(n, ast.Name)}


def _guards_presence_of(test: ast.expr, attr: str) -> bool:
    """Whether *test* is an existence check for *attr*, so an assignment under it is not inventing.

    The careful shape looks like this, and it is the one this check WANTS people to write::

        _SENTINEL = object()
        saved = getattr(mod, "NAME", _SENTINEL)
        if saved is not _SENTINEL:
            mod.NAME = 0          # only when the module really has NAME
        ...
        elif hasattr(mod, "NAME"):
            delattr(mod, "NAME")  # restores its ABSENCE

    Reporting that assignment says the test invents an attribute, when the guard is precisely what
    stops it from doing so -- and the obvious response to the finding is to delete a correct test.
    Recognised: a `hasattr(..., "NAME")` test, and a sentinel comparison against a name that a
    `getattr(..., "NAME", sentinel)` produced in the same scope.
    """
    for node in _fast_walk(test):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hasattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == attr:
                return True
        if isinstance(node, ast.Constant) and node.value == attr:
            return True
    return False


def _sentinel_names_for(body: "list[ast.stmt]", attr: str) -> "set[str]":
    """Names assigned from ``getattr(mod, attr, <sentinel>)`` anywhere in this scope."""
    names: set[str] = set()
    for node in _fast_walk(ast.Module(body=list(body), type_ignores=[])):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "getattr" and len(call.args) >= 3):
            continue
        if not (isinstance(call.args[1], ast.Constant) and call.args[1].value == attr):
            continue
        names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _attribute_targets(target: ast.expr) -> "list[ast.Attribute]":
    """``m.X`` targets of an assignment, including inside tuple/list/starred unpacking (``m.A, m.B = 1, 2``)."""
    if isinstance(target, ast.Attribute):
        return [target]
    if isinstance(target, ast.Starred):
        return _attribute_targets(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return [a for element in target.elts for a in _attribute_targets(element)]
    return []


def _statement_sets(node: ast.stmt, aliases: "dict[str, str]"):
    """``(lineno, dotted_module, attr)`` for every module attribute *node* itself sets: ``m.X = ...`` (plain, annotated,
    augmented or unpacked) and ``setattr(m, "X", ...)``."""
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    for target in targets:
        for attr in _attribute_targets(target):
            if isinstance(attr.value, ast.Name) and attr.value.id in aliases:
                yield node.lineno, aliases[attr.value.id], attr.attr
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        if (
            isinstance(call.func, ast.Name)
            and call.func.id == "setattr"
            and len(call.args) >= 3
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id in aliases
            and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, str)
        ):
            yield node.lineno, aliases[call.args[0].id], call.args[1].value


def _module_assignments(body: "list[ast.stmt]", known: "set[str]", inherited: "dict[str, str]"):
    """Yield `(lineno, dotted_module, attr)` for `mod.NAME = ...` visible in this scope and below."""
    aliases = _aliases_in(body, known, inherited)
    for node in body:
        yield from _statement_sets(node, aliases)
        inner = getattr(node, "body", None)
        if isinstance(inner, list):
            # An `if` whose test checks whether the module HAS the attribute is the careful shape,
            # not the defect: the assignment under it cannot invent anything. The branch is walked once.
            hits = list(_module_assignments(inner, known, aliases))
            if isinstance(node, ast.If):
                guarded = {a for _l, _d, a in hits if _guards_presence_of(node.test, a) or _FILE_SENTINELS.get(a, frozenset()) & _names_in(node.test)}
                yield from (t for t in hits if t[2] not in guarded)
            else:
                yield from hits
        for extra in ("orelse", "finalbody"):
            branch = getattr(node, extra, None)
            if isinstance(branch, list) and branch:
                yield from _module_assignments(branch, known, aliases)
        for handler in getattr(node, "handlers", []) or []:
            yield from _module_assignments(handler.body, known, aliases)


def scan(test_paths: "list[Path]", index: "dict[str, ModuleFacts]") -> "list[Finding]":
    """Report `module.NAME = ...` (or `setattr(module, "NAME", ...)`) in tests where the module has no `NAME` to set.

    A test file that cannot be parsed is reported as a finding with target ``<unparsable>``: nothing in it was checked.
    """
    findings: list[Finding] = []
    known = set(index)
    result = scan_python([p for p in test_paths if "__pycache__" not in Path(p).parts])
    findings.extend(
        Finding(path=u.path, lineno=u.line, target="<unparsable>", detail=f"{u.kind}: {u.message} -- nothing in this file was checked") for u in result.unparsed
    )
    for parsed in result:
        path, tree = parsed.path, parsed.tree

        _FILE_SENTINELS.clear()
        for node in _fast_walk(tree):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                continue
            call = node.value
            if not (isinstance(call.func, ast.Name) and call.func.id == "getattr" and len(call.args) >= 3):
                continue
            if not isinstance(call.args[1], ast.Constant):
                continue
            bound_to = {t.id for t in node.targets if isinstance(t, ast.Name)}
            _FILE_SENTINELS.setdefault(str(call.args[1].value), set()).update(bound_to)

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
