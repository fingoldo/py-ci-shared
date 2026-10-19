"""Shared check: every value a backend can put in a closed-vocabulary field is in the vocabulary it publishes, and
every published member is something the code can actually emit.

Generalizes Autopsia's ``test_emitted_vocabulary_literals`` (2026-10): ``docs/wire_vocabularies.json`` is what a
client localizes from, and it is built from named constants, so it agrees with itself by construction. What it
cannot see is an emit site that skipped the constant: ``ModeratorDisclosureOut(kind="rebased")`` put a token on
the wire that no client had a translation for, and it reached a Russian screen raw. A recorded vocabulary is only
a contract while the literals at the places that build the wire objects are its members.

The shape: for each :class:`EmitSite` (a callee name plus the keyword and/or positional slots that carry the
field), every argument is resolved statically to the set of strings it can be: a literal, both arms of a
conditional expression, a module-level or class-level string constant (also through ``from x import NAME`` or
``Cls.NAME``), a local bound in the enclosing function (including a tuple unpacked from a same-module function's
returned tuples). A value outside *published* is a finding at its call site. An argument that cannot be resolved
(a parameter forwarded, an attribute of a runtime object) is a finding too, unless its source text is listed in
*pass_through*: a value the gate cannot see is not a value it can vouch for. With *check_unemitted*, a published
member that no site emits (nor any dataclass-style class default of a site's field) is a finding against the
vocabulary itself: a client then carries a translation for a state that cannot occur, and the next reader
believes it can.

Not reported: a call to any other callee, an argument slot not named by a site, a value in *ignore_values* (a
sentinel such as ``""`` that a site passes for "no value").

Usage in a consumer's meta test::

    from py_ci_shared.emitted_vocabulary_literals import EmitSite, assert_emitted_vocabulary_literals

    def test_every_moderator_kind_emitted_is_published():
        assert_emitted_vocabulary_literals(
            "autopsia",
            sites=[EmitSite("ModeratorDisclosureOut", keyword="kind"), EmitSite("UnresolvedModerator", keyword="reason")],
            published=wire_vocabularies()["moderator_kind"],
            vocabulary="docs/wire_vocabularies.json#moderator_kind",
            pass_through={"u.reason"},
        )
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, child_nodes, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "EmitSite", "assert_emitted_vocabulary_literals", "find_emitted_vocabulary_literals"]

RULE = "emitted-vocabulary-literals"

# How many name hops a resolution follows before giving up (a constant defined from a constant from a local...).
_MAX_DEPTH = 8


@dataclass(frozen=True)
class EmitSite:
    """A place that puts a vocabulary value on the wire: calls to *call* (a bare name or the last attribute of a
    dotted callee), the value passed as *keyword* and/or at the 0-based *positions*."""

    call: str
    keyword: str = ""
    positions: tuple[int, ...] = ()

    def label(self) -> str:
        slots = [self.keyword] if self.keyword else []
        slots += [f"arg{i}" for i in self.positions]
        return f"{self.call}({'/'.join(slots)})"


def _str_constant(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _File:
    """One parsed file with the indexes a resolution needs."""

    def __init__(self, rel: str, tree: ast.Module) -> None:
        self.rel = rel
        self.tree = tree
        self.aliases = ImportAliases.from_tree(tree)
        self.parents: dict[int, ast.AST] = {}
        for node in shared_walk(tree):
            for child in child_nodes(node):
                self.parents[id(child)] = node
        self.constants: dict[str, ast.expr] = {}
        self.classes: dict[str, dict[str, ast.expr]] = {}
        self.functions: dict[str, ast.FunctionDef] = {}
        for stmt in tree.body:
            for name, value in _bindings(stmt):
                self.constants[name] = value
            if isinstance(stmt, ast.ClassDef):
                self.classes[stmt.name] = {name: value for inner in stmt.body for name, value in _bindings(inner)}
            if isinstance(stmt, ast.FunctionDef):
                self.functions[stmt.name] = stmt

    def enclosing_function(self, node: ast.AST) -> Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]]:
        current = self.parents.get(id(node))
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current
            current = self.parents.get(id(current))
        return None


def _bindings(stmt: ast.stmt) -> Iterable[tuple[str, ast.expr]]:
    """``NAME = value`` and ``NAME: T = value`` at one statement."""
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
        yield stmt.targets[0].id, stmt.value
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
        yield stmt.target.id, stmt.value


class _Resolver:
    def __init__(self, files: Sequence[_File]) -> None:
        self.files = files
        self.by_module: dict[str, _File] = {_module_name(f.rel): f for f in files}

    def module(self, dotted: str) -> Optional[_File]:
        """The corpus file for *dotted*, wherever the scan root sits relative to the package."""
        # A relative import (`.kinds`) is matched by its tail: the scan root need not be the package root.
        dotted = dotted.lstrip(".")
        if dotted in self.by_module:
            return self.by_module[dotted]
        # The scan root may sit above the package (`src/pkg/mod` for `pkg.mod`) or inside it (`mod` for `pkg.mod`).
        for name, f in self.by_module.items():
            if name.endswith("." + dotted) or (name and dotted.endswith("." + name)):
                return f
        return None

    def values(self, expr: ast.AST, f: _File, site: ast.AST, depth: int = 0) -> Optional[set[str]]:
        """Every string *expr* can evaluate to at *site*, or ``None`` when that is not statically knowable."""
        if depth > _MAX_DEPTH:
            return None
        literal = _str_constant(expr)
        if literal is not None:
            return {literal}
        if isinstance(expr, ast.IfExp):
            body = self.values(expr.body, f, site, depth + 1)
            orelse = self.values(expr.orelse, f, site, depth + 1)
            return None if body is None or orelse is None else body | orelse
        if isinstance(expr, ast.Name):
            return self._name(expr.id, f, site, depth)
        if isinstance(expr, ast.Attribute):
            return self._attribute(expr, f, depth)
        return None

    def _name(self, name: str, f: _File, site: ast.AST, depth: int) -> Optional[set[str]]:
        fn = f.enclosing_function(site)
        if fn is not None:
            args = fn.args
            params = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
            if name in params:
                return None
            local = self._local(name, fn, f, depth)
            if local is not False:
                return local  # type: ignore[return-value]
        if name in f.constants:
            return self.values(f.constants[name], f, f.tree, depth + 1)
        dotted = f.aliases.qualified_name(ast.Name(id=name, ctx=ast.Load()))
        if dotted and "." in dotted:
            module, _, attr = dotted.rpartition(".")
            target = self.module(module)
            if target is not None and attr in target.constants:
                return self.values(target.constants[attr], target, target.tree, depth + 1)
        return None

    def _local(self, name: str, fn: Union[ast.FunctionDef, ast.AsyncFunctionDef], f: _File, depth: int) -> Union[Optional[set[str]], bool]:
        """The values a local is bound to in *fn*; ``False`` when *fn* never binds it (so look further out)."""
        found: set[str] = set()
        bound = False
        for node in shared_walk(fn):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            got: Optional[set[str]]
            if isinstance(target, ast.Name) and target.id == name:
                got = self.values(node.value, f, node, depth + 1)
            elif isinstance(target, ast.Tuple):
                names = [t.id if isinstance(t, ast.Name) else "" for t in target.elts]
                if name not in names:
                    continue
                index = names.index(name)
                if isinstance(node.value, ast.Tuple) and index < len(node.value.elts):
                    got = self.values(node.value.elts[index], f, node, depth + 1)
                elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                    got = self._returned(node.value.func.id, index, f, depth + 1)
                else:
                    got = None
            else:
                continue
            bound = True
            if got is None:
                return None
            found |= got
        return found if bound else False

    def _returned(self, function: str, index: int, f: _File, depth: int) -> Optional[set[str]]:
        """Element *index* of every tuple a same-module *function* returns."""
        fn = f.functions.get(function)
        if fn is None:
            return None
        found: set[str] = set()
        for ret in (r for r in shared_walk(fn) if isinstance(r, ast.Return)):
            if not isinstance(ret.value, ast.Tuple) or index >= len(ret.value.elts):
                return None
            got = self.values(ret.value.elts[index], f, ret, depth + 1)
            if got is None:
                return None
            found |= got
        return found or None

    def _attribute(self, expr: ast.Attribute, f: _File, depth: int) -> Optional[set[str]]:
        """``Cls.NAME`` for a class in this file, or ``module.NAME`` / an imported class's constant."""
        if isinstance(expr.value, ast.Name) and expr.value.id in f.classes:
            members = f.classes[expr.value.id]
            if expr.attr in members:
                return self.values(members[expr.attr], f, f.tree, depth + 1)
            return None
        dotted = f.aliases.qualified_name(expr)
        if not dotted or "." not in dotted:
            return None
        owner, _, attr = dotted.rpartition(".")
        target = self.module(owner)
        if target is not None and attr in target.constants:
            return self.values(target.constants[attr], target, target.tree, depth + 1)
        module, _, cls = owner.rpartition(".")
        target = self.module(module) if module else None
        if target is not None and cls in target.classes and attr in target.classes[cls]:
            return self.values(target.classes[cls][attr], target, target.tree, depth + 1)
        return None


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def find_emitted_vocabulary_literals(
    root: Union[str, Path],
    *,
    sites: Sequence[EmitSite],
    published: Collection[str],
    vocabulary: str = "the published vocabulary",
    pass_through: Collection[str] = (),
    ignore_values: Collection[str] = (),
    check_unemitted: bool = True,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line; see the module docstring for the rule.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    if not sites:
        raise ValueError("emitted_vocabulary_literals: name at least one EmitSite; an empty site list checks nothing")
    if not published:
        raise ValueError("emitted_vocabulary_literals: the published vocabulary is empty; pass its members")
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    files = [_File(parsed.rel, parsed.tree) for parsed in scan]
    resolver = _Resolver(files)
    allowed = set(published)
    by_call: dict[str, list[EmitSite]] = {}
    for site in sites:
        by_call.setdefault(site.call, []).append(site)
    emitted: set[str] = set()
    out: list[Finding] = []
    for f in files:
        for node in shared_walk(f.tree):
            if isinstance(node, ast.Call):
                for site in by_call.get(_callee(node), ()):
                    out.extend(_check_call(node, site, f, resolver, emitted, allowed | set(ignore_values), vocabulary, pass_through))
    if check_unemitted:
        emitted |= _class_defaults(files, sites)
        out.extend(
            Finding(vocabulary, 1, RULE, f"{value!r} is published, and no emit site sends it") for value in sorted(allowed - emitted - set(ignore_values))
        )
    return sorted(out, key=lambda x: (x.path, x.line, x.message))


def _check_call(
    node: ast.Call,
    site: EmitSite,
    f: _File,
    resolver: _Resolver,
    emitted: set[str],
    accepted: set[str],
    vocabulary: str,
    pass_through: Collection[str],
) -> list[Finding]:
    """The findings at one call of *site*; adds every resolved value to *emitted*."""
    args = [k.value for k in node.keywords if site.keyword and k.arg == site.keyword]
    args += [a for i, a in enumerate(node.args) if i in site.positions]
    out: list[Finding] = []
    for arg in args:
        got = resolver.values(arg, f, node)
        if got is None:
            text = ast.unparse(arg)
            if text not in pass_through:
                message = (
                    f"{site.label()} is passed `{text}`, which cannot be resolved to literals; route it through a named "
                    "constant, or list the expression in pass_through if another site emits its values"
                )
                out.append(Finding(f.rel, node.lineno, RULE, message))
            continue
        emitted |= got
        out.extend(Finding(f.rel, node.lineno, RULE, f"{site.label()} emits {v!r}, which {vocabulary} does not hold") for v in sorted(got - accepted))
    return out


def _class_defaults(files: Sequence[_File], sites: Sequence[EmitSite]) -> set[str]:
    """String defaults of a site's keyword field on a corpus class named like the site (``reason: str = "x"``): a
    dataclass built without that argument emits the default."""
    out: set[str] = set()
    wanted = {(s.call, s.keyword) for s in sites if s.keyword}
    for f in files:
        for cls, members in f.classes.items():
            for call, keyword in wanted:
                if cls == call and keyword in members:
                    literal = _str_constant(members[keyword])
                    if literal is not None:
                        out.add(literal)
    return out


def assert_emitted_vocabulary_literals(
    root: Union[str, Path],
    *,
    sites: Sequence[EmitSite],
    published: Collection[str],
    vocabulary: str = "the published vocabulary",
    pass_through: Collection[str] = (),
    ignore_values: Collection[str] = (),
    check_unemitted: bool = True,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    grow: Optional[bool] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept. A *refresh* is
    shrink-only unless *grow* (or ``PY_CI_SHARED_REFRESH_ALLOW_GROW=1``): seed a first baseline with it."""
    found = find_emitted_vocabulary_literals(
        root,
        sites=sites,
        published=published,
        vocabulary=vocabulary,
        pass_through=pass_through,
        ignore_values=ignore_values,
        check_unemitted=check_unemitted,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "emit a named constant that the vocabulary is built from, or publish the new member and its translations"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="emitted_vocabulary_literals", refresh_command="PY_CI_SHARED_REFRESH=emitted_vocabulary_literals")
        baseline.enforce(found, refresh=refresh, guidance=guidance, grow=grow).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} emitted-vocabulary-literals finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
