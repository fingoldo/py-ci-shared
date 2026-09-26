"""Tests that assert on SOURCE TEXT instead of running the code: one AST detector for every repo.

A test that reads the code under test and checks a substring passes against dead code, an inverted
condition and a comment. Four repositories banned the practice independently and each ban caught a
different subset of the spellings, because each was widened only for the evasion its own repo had
just hit (2026-09-11 comparison, recorded in the py-ci-shared history of this module):

* ``inspect.getsource`` bare or aliased, ``getsourcelines``, ``ast.unparse``, ``dis`` and ``co_code``
  -- one repo saw the bare name, one the aliases, one the ``dis`` family;
* a ``.py`` or ``.sql`` file read through ``__file__``, a module constant, or a ``glob("*.py")`` loop
  variable or a generator over one -- one repo saw the variables, another only a literal on the same line;
* the text travelling under a new name, sliced (``src[src.index("def f"):]``), or returned by a
  module-level ``def _read(rel)`` helper -- each seen by exactly one repo.

This module is the union, as an AST walk rather than a regex so that aliases, scopes and helpers
resolve. ``.sql`` counts as source: a SQL-substring assertion passes against a constant the call site
never uses, which is how one of the two shipped defects that started all this happened.

A file read through a path the detector cannot place (a parameter, ``ROOT / name``) is source only by how it is
checked: the claim fires when the assertion takes the POSITION of a substring in that text (``.find`` / ``.index`` /
``.rfind`` / ``.rindex``, directly or through a name bound to one), or searches it for a code-like literal (``"def "``,
``"import "``, ``"self."``, ``"name("``). A data suffix (``.json``, ``.md`` ...), a deserialiser, and a path under
pytest's ``tmp_path`` / ``tmpdir`` (output the code under test wrote) keep such a read out.

A claim is also a ``return`` of such a check (a helper that answers "is this substring in the source" for a caller's
assert), and a later ``assert found`` / ``assert idx > 0`` over a name bound to one.

What is deliberately NOT a claim: ``ast.parse(text)`` and a walk over its nodes. That is how every
meta-linter in these repositories works, and a rule that flagged it would be switched off the day it
landed. Reading a fixture, a JSON cache, a README or a prompt file is not a claim either.

Two modes. ``"assertion"`` (the default) flags an ``assert`` -- or an ``if`` whose body calls
``.fail(...)`` -- that tests source text's CONTENT (``in``, ``==``, ``.count``, a regex, ``.find`` /
``.index`` / ``.startswith``, or the truth of a regex match taken over the source). ``"read"`` flags every read of source in a test, asserted on or not,
for a repo that wants the stricter rule.

Names resolve through the file's imports (``from inspect import getsource as gs``, ``from dis import
get_instructions``); a nested function sees the source-holding names of the functions around it; and a
``@pytest.fixture`` in the same file that returns or yields source taints every test parameter named after it.

The baseline is a multiset (``_core.Baseline``): a key ``rel::function::kind`` accepts as many claims as it
counted when recorded, so a sixth claim of the same kind in the same function is new. A missing baseline fails,
and a refresh is read with ``_core.refresh_requested`` (pytest option, ``PY_CI_SHARED_REFRESH`` or argv).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ._core import Baseline, BaselineError, ImportAliases, SourceError, parse_source, read_source, relative_posix
from ._core.node_index import walk as _fast_walk

__all__ = [
    "DEFAULT_READERS",
    "REFRESH_FLAG",
    "SourceTextClaim",
    "assert_no_new_source_text_claims",
    "find_source_text_claims",
    "write_source_text_baseline",
]

REFRESH_FLAG = "--refresh-source-text-baseline"

#: Calls whose result is program text about the program itself.
DEFAULT_READERS: frozenset[str] = frozenset({"getsource", "getsourcelines", "getsourcefile", "getclosurevars", "unparse"})
_DISASSEMBLERS = frozenset({"dis", "get_instructions", "code_info"})
#: Attributes that hand back a function's own bytecode or constants. ``__code__`` alone is not here: reaching
#: through it to CALL a wrapped function is behavioural testing.
_CODE_ATTRS = frozenset({"co_code", "co_consts", "co_names"})
_FILE_READS = frozenset({"read_text", "read_bytes", "read", "readlines"})
#: Results of these are structure, not text: parsing is how a meta-linter works, and a regex over source
#: that yields a list of matches has already stopped being a substring claim.
_STRUCTURAL = frozenset({"parse", "walk", "iter_child_nodes", "literal_eval", "findall", "finditer", "splitlines"})
#: Deserialisers: what they return is data (a coverage.json whose keys happen to be ``.py`` paths), not program text.
_DESERIALISERS = frozenset(
    {
        "json.loads",
        "json.load",
        "orjson.loads",
        "tomllib.loads",
        "tomllib.load",
        "tomli.loads",
        "tomli.load",
        "yaml.safe_load",
        "yaml.load",
        "yaml.safe_load_all",
    }
)
_CONTENT_METHODS = frozenset({"count", "search", "match", "fullmatch", "findall", "index", "find", "rfind", "rindex", "startswith", "endswith"})
#: Substrings at least one of which any claim's file must contain: the file reads, `open(`, the code attributes and
#: the `dis` functions. Reader names are added per call.
_TRIGGER_TOKENS = ("read_text", "read_bytes", "readlines", ".read(", "open(", "co_code", "co_consts", "co_names", "get_instructions", "code_info", "dis(")
_POSITION_METHODS = frozenset({"find", "index", "rfind", "rindex"})
#: Pytest fixtures whose paths hold output the code under test wrote: reading it back is a behavioural check.
_TMP_ROOTS = frozenset({"tmp_path", "tmpdir", "tmp_path_factory", "tmpdir_factory"})
_CODE_LIKE = re.compile(
    r"^\s*(?:async\s+def|def|class|import|from\s+[\w.]+\s+import|return|raise|await|yield|lambda|with|if\s+__name__)\b"
    r"|^\s*@\w"
    r"|\bself\.\w"
    r"|(?<![\\\w])[A-Za-z_][\w.]*\("
    r"|\b[a-z_]\w*\s*(?:=|\+=|-=|:=)\s*\S"
)
_NON_SOURCE_SUFFIXES = (".json", ".toml", ".md", ".txt", ".csv", ".ini", ".cfg", ".yml", ".yaml", ".html", ".css", ".js", ".log", ".env", ".lock", ".jsonl")


@dataclass(frozen=True)
class SourceTextClaim:
    """One claim: where it is, which function holds it, and how the source was read."""

    line: int
    function: str
    kind: str

    def key(self, rel: str) -> str:
        """``rel::function::kind`` -- stable across line shifts, which is what a baseline needs."""
        return f"{rel}::{self.function}::{self.kind}"


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _string_constants(node: ast.AST) -> Iterator[str]:
    for sub in _fast_walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


class _Detector:
    def __init__(self, tree: ast.Module, *, readers: frozenset[str], treat_sql_as_source: bool, follow_helpers: bool) -> None:
        self.tree = tree
        self.readers = readers
        self.aliases = ImportAliases.from_tree(tree)
        self.suffixes = (".py", ".sql") if treat_sql_as_source else (".py",)
        self.dis_names = {"dis"} | {a.asname or a.name for n in _fast_walk(tree) if isinstance(n, ast.Import) for a in n.names if a.name == "dis"}
        self.module_paths = self._path_names(tree.body)
        self.helpers: set[str] = set()
        self.fixtures: set[str] = set()
        if follow_helpers:
            self.helpers = self._reader_helpers()
            self.fixtures = self._source_fixtures()

    # -- what counts as a path to source ------------------------------------------------------------
    def _is_source_path_expr(self, node: ast.AST, path_names: set[str]) -> bool:
        literals = list(_string_constants(node))
        if any(lit.lower().endswith(self.suffixes) for lit in literals):
            return True
        if any(lit.lower().endswith(_NON_SOURCE_SUFFIXES) for lit in literals):
            return False
        for sub in _fast_walk(node):
            if isinstance(sub, ast.Name) and (sub.id == "__file__" or sub.id in path_names):
                return True
            if isinstance(sub, ast.Attribute) and sub.attr == "__file__":  # `Path(module.__file__)`
                return True
        return False

    def _path_names(self, body: Iterable[ast.stmt], inherited: frozenset[str] = frozenset()) -> set[str]:
        """Names bound to a source path: ``_SCRIPT = ROOT / "x.py"`` and ``for p in DIR.glob("*.py")``."""
        names: set[str] = set(inherited)
        for stmt in body:
            for node in _walk_scope([stmt]):
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and not _contains_read(node.value):
                    if self._is_source_path_expr(node.value, names):
                        names.update(_target_names(node))
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    # `with open("pkg/mod.py") as fh:` -- `fh.read()` then reads source through a name with no path of its own.
                    for item in node.items:
                        expr = item.context_expr
                        if item.optional_vars is not None and isinstance(expr, ast.Call) and _call_name(expr) == "open" and expr.args:
                            if self._is_source_path_expr(expr.args[0], names):
                                names.update(n.id for n in _fast_walk(item.optional_vars) if isinstance(n, ast.Name))
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    # `ast.comprehension` covers `"".join(p.read_text() for p in DIR.glob("*.py"))`, which reads the same files.
                    globs = [c for c in _fast_walk(node.iter) if isinstance(c, ast.Call) and _call_name(c) in ("glob", "rglob", "iterdir")]
                    if any(lit.lower().endswith(self.suffixes) for c in globs for lit in _string_constants(c)):
                        names.update(n.id for n in _fast_walk(node.target) if isinstance(n, ast.Name))
        return names

    # -- what counts as reading source --------------------------------------------------------------
    def _is_deserialiser(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and (self.aliases.qualified_name(node) or "") in _DESERIALISERS

    def _walk_text(self, node: ast.AST) -> Iterator[ast.AST]:
        """``ast.walk`` that does not enter a deserialiser call: ``json.loads(p.read_text())`` hands back data."""
        stack = [node]
        while stack:
            sub = stack.pop()
            if self._is_deserialiser(sub):
                continue
            yield sub
            stack.extend(ast.iter_child_nodes(sub))

    def reader_kind(self, node: ast.AST, path_names: set[str], tainted: set[str]) -> str | None:
        """How *node* yields source text, or None. Checks readers, file reads, helpers and tainted names."""
        for sub in self._walk_text(node):
            if isinstance(sub, ast.Call):
                name = _call_name(sub)
                qualified = self.aliases.qualified_name(sub) or name
                resolved = qualified.rsplit(".", 1)[-1]
                if name in self.readers or (resolved in self.readers and qualified != resolved):
                    return f"{resolved if resolved in self.readers else name}()"
                if (
                    name in _DISASSEMBLERS
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id in self.dis_names
                ):
                    return f"dis.{name}()"
                if qualified.startswith("dis.") and resolved in _DISASSEMBLERS:
                    return f"dis.{resolved}()"
                if name in _FILE_READS and isinstance(sub.func, ast.Attribute) and self._is_source_path_expr(sub.func.value, path_names):
                    return "reads a source file"
                if name == "open" and sub.args and self._is_source_path_expr(sub.args[0], path_names):
                    return "reads a source file"
                if isinstance(sub.func, ast.Name) and sub.func.id in self.helpers:
                    return f"via helper {sub.func.id}()"
            elif isinstance(sub, ast.Attribute) and sub.attr in _CODE_ATTRS:
                return sub.attr
        return None

    def _reader_helpers(self) -> set[str]:
        """Module-level functions whose return value is source text -- the ubiquitous ``def _read(rel)``."""
        out: set[str] = set()
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                paths = self._path_names(node.body, frozenset(self.module_paths) | _param_names(node))
                tainted = self._tainted(node.body, paths, set())
                for ret in _fast_walk(node):
                    if isinstance(ret, ast.Return) and ret.value is not None and (self.reader_kind(ret.value, paths, tainted) or _uses(ret.value, tainted)):
                        out.add(node.name)
        return out

    def _source_fixtures(self) -> set[str]:
        """Same-file ``@pytest.fixture`` functions whose return or yield value is source text."""
        out: set[str] = set()
        for node in self.tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [d.func if isinstance(d, ast.Call) else d for d in node.decorator_list]
            if not any((self.aliases.qualified_name(d) or "").endswith("fixture") for d in decorators):
                continue
            paths = self._path_names(node.body, frozenset(self.module_paths) | _param_names(node))
            tainted = self._tainted(node.body, paths, set())
            for sub in _walk_scope(node.body):
                value = sub.value if isinstance(sub, (ast.Return, ast.Yield)) else None
                if isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Yield):
                    value = sub.value.value
                if value is not None and (self.reader_kind(value, paths, tainted) or _uses(value, tainted)):
                    out.add(node.name)
        return out

    def _tainted(self, body: list[ast.stmt], path_names: set[str], inherited: set[str]) -> set[str]:
        """Names holding source text in this scope, propagated through derivations until nothing changes."""
        tainted = set(inherited)
        for _ in range(6):
            before = len(tainted)
            for node in _walk_scope(body):
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign)) and node.value is not None:
                    value = node.value
                    if isinstance(value, ast.Call) and (_call_name(value) in _STRUCTURAL or self._is_deserialiser(value)):
                        continue
                    if self.reader_kind(value, path_names, tainted) or _uses(value, tainted):
                        tainted.update(_target_names(node))
                elif (
                    isinstance(node, (ast.For, ast.AsyncFor))
                    and _uses(node.iter, tainted)
                    and not (isinstance(node.iter, ast.Call) and _call_name(node.iter) in _STRUCTURAL)
                ):
                    tainted.update(n.id for n in _fast_walk(node.target) if isinstance(n, ast.Name))
            if len(tainted) == before:
                break
        return tainted

    # -- arbitrary file reads ----------------------------------------------------------------------
    def _is_other_path(self, node: ast.AST, tmp_names: set[str]) -> bool:
        """A path that is neither known source nor data nor pytest temp output: its text is judged by its use."""
        if any(lit.lower().endswith(_NON_SOURCE_SUFFIXES) for lit in _string_constants(node)):
            return False
        return not any(isinstance(sub, ast.Name) and (sub.id in _TMP_ROOTS or sub.id in tmp_names) for sub in _fast_walk(node))

    def file_read(self, node: ast.AST, tmp_names: set[str], file_tainted: set[str]) -> bool:
        """Does *node* yield an arbitrary file's text: ``p.read_text()``, ``p.read_bytes()``, ``open(p).read()``, or a
        name holding one? Deserialised and structural results are data, as for source reads."""
        for sub in self._walk_text(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                name, owner = sub.func.attr, sub.func.value
                if name in ("read_text", "read_bytes") and self._is_other_path(owner, tmp_names):
                    return True
                if name == "read" and isinstance(owner, ast.Call) and _call_name(owner) == "open" and owner.args:
                    if self._is_other_path(owner.args[0], tmp_names):
                        return True
        return _uses(node, file_tainted)

    def tmp_names(self, body: list[ast.stmt], inherited: set[str]) -> set[str]:
        """Names bound to a path whose text is not program text: under a pytest temp fixture (``out = tmp_path / "x"``)
        or ending in a data suffix (``README = ROOT / "README.md"``), through derivations."""
        names = set(inherited)
        for _ in range(3):
            before = len(names)
            for node in _walk_scope(body):
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and not _contains_read(node.value):
                    value = node.value
                    if any(isinstance(sub, ast.Name) and (sub.id in _TMP_ROOTS or sub.id in names) for sub in _fast_walk(value)) or any(
                        lit.lower().endswith(_NON_SOURCE_SUFFIXES) for lit in _string_constants(value)
                    ):
                        names.update(_target_names(node))
            if len(names) == before:
                break
        return names

    def file_tainted(self, body: list[ast.stmt], tmp_names: set[str], inherited: set[str]) -> set[str]:
        """Names holding an arbitrary file's text in this scope, propagated like :meth:`_tainted`."""
        tainted = set(inherited)
        for _ in range(6):
            before = len(tainted)
            for node in _walk_scope(body):
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign)) and node.value is not None:
                    value = node.value
                    if isinstance(value, ast.Call) and (
                        _call_name(value) in _STRUCTURAL | _POSITION_METHODS | _CONTENT_METHODS or self._is_deserialiser(value)
                    ):
                        continue
                    if isinstance(value, ast.Compare):
                        continue
                    if self.file_read(value, tmp_names, tainted):
                        tainted.update(_target_names(node))
            if len(tainted) == before:
                break
        return tainted

    # -- scopes -------------------------------------------------------------------------------------
    def scopes(self) -> Iterator[_Scope]:
        """One :class:`_Scope` for the module and one for every function."""
        module_tainted = self._tainted(self.tree.body, self.module_paths, set())
        tmp = self.tmp_names(self.tree.body, set())
        scope = _Scope("<module>", self.tree.body, self.module_paths, module_tainted, tmp, self.file_tainted(self.tree.body, tmp, set()))
        yield scope
        yield from self._nested(scope)

    def _nested(self, outer: _Scope) -> Iterator[_Scope]:
        """Functions defined in *outer* (through classes too), each inheriting the enclosing scope's names: a closure
        reading its outer function's source text is the same claim."""
        for node in _defs_in(outer.body):
            if isinstance(node, ast.ClassDef):
                yield from self._nested(_Scope(outer.function, node.body, outer.paths, outer.tainted, outer.tmp, outer.file_tainted))
                continue
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = _param_names(node) & self.fixtures
            inner_paths = self._path_names(node.body, frozenset(outer.paths))
            inner_tainted = self._tainted(node.body, inner_paths, set(outer.tainted) | set(params))
            tmp = self.tmp_names(node.body, set(outer.tmp))
            scope = _Scope(node.name, node.body, inner_paths, inner_tainted, tmp, self.file_tainted(node.body, tmp, set(outer.file_tainted)))
            yield scope
            yield from self._nested(scope)


@dataclass
class _Scope:
    """One function (or the module): its body and the names that hold source paths, source text, temp paths and file text."""

    function: str
    body: list[ast.stmt]
    paths: set[str]
    tainted: set[str]
    tmp: set[str]
    file_tainted: set[str]


_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_scope(body: Iterable[ast.stmt]) -> Iterator[ast.AST]:
    """Every node in *body* without descending into nested function or class definitions -- they are scopes of their own."""
    stack: list[ast.AST] = [node for node in body if not isinstance(node, _NESTED_SCOPES)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, _NESTED_SCOPES))


def _defs_in(body: Iterable[ast.stmt]) -> Iterator[ast.AST]:
    """Function and class definitions directly in *body* (inside if/try/with/for too), not inside other defs."""
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node
            continue
        if isinstance(node, ast.Lambda):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _target_names(node: ast.AST) -> set[str]:
    """The names a binding statement writes to; ``src += path.read_text()`` taints ``src`` exactly as a plain assign would."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]  # type: ignore[attr-defined]
    return {n.id for t in targets for n in _fast_walk(t) if isinstance(n, ast.Name)}


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    args = fn.args
    return frozenset(a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs])


def _contains_read(node: ast.AST) -> bool:
    return any(isinstance(s, ast.Call) and _call_name(s) in _FILE_READS | {"open"} for s in _fast_walk(node))


def _uses(node: ast.AST, names: set[str]) -> bool:
    """Does *node* read one of *names* without CALLING it -- calling a name exercises behaviour."""
    if not names:
        return False
    called = {c.func.id for c in _fast_walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    return any(isinstance(s, ast.Name) and s.id in names and s.id not in called for s in _fast_walk(node))


def _tests_content(test: ast.AST) -> bool:
    for sub in _fast_walk(test):
        if isinstance(sub, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq)) for op in sub.ops):
            return True
        if isinstance(sub, ast.Call) and _call_name(sub) in _CONTENT_METHODS:
            return True
    return False


_MATCHERS = frozenset({"search", "match", "fullmatch"})


def _is_content_check(value: ast.AST) -> bool:
    """A regex match, a ``.find`` / ``.count`` style call, or an ``in`` / ``==`` comparison: its result says what the text holds."""
    if isinstance(value, ast.Call):
        return _call_name(value) in _MATCHERS | _CONTENT_METHODS
    return isinstance(value, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq)) for op in value.ops)


def _match_names(body: list[ast.stmt], det: _Detector, paths: set[str], tainted: set[str]) -> set[str]:
    """Names bound to a content check over source: ``m = re.search(pat, src)``, ``i = src.find("def f")`` or
    ``ok = "x" in src`` makes a bare ``assert m`` (or ``assert i > 0``) a content check."""
    out: set[str] = set()
    for node in _walk_scope(body):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None and _is_content_check(node.value):
            if det.reader_kind(node.value, paths, tainted) or _uses(node.value, tainted):
                out.update(_target_names(node))
    return out


def _position_calls(node: ast.AST) -> Iterator[ast.Call]:
    for sub in _fast_walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in _POSITION_METHODS:
            yield sub


def _literal_search(node: ast.AST) -> bool:
    """A literal substring test (``in``, ``==``, ``.count``, ``.startswith`` ...), not a regex: over an arbitrary file a
    regex is usually EXTRACTING a value (a version, a code block to run), which is how the text gets used for real."""
    for sub in _fast_walk(node):
        if isinstance(sub, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq)) for op in sub.ops):
            return True
        if isinstance(sub, ast.Call) and _call_name(sub) in _CONTENT_METHODS - _MATCHERS - {"findall"}:
            return True
    return False


def _code_like(node: ast.AST) -> bool:
    return any(_CODE_LIKE.search(lit) for lit in _string_constants(node))


def _file_checks(det: _Detector, scope: _Scope) -> tuple[set[str], set[str]]:
    """``(position names, code-search names)``: names bound to a position taken in, or a code-like search of, file text."""
    positions: set[str] = set()
    searches: set[str] = set()
    for node in _walk_scope(scope.body):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) or node.value is None or not _is_content_check(node.value):
            continue
        if any(det.file_read(c.func.value, scope.tmp, scope.file_tainted) for c in _position_calls(node.value)):  # type: ignore[attr-defined]
            positions.update(_target_names(node))
        elif _literal_search(node.value) and det.file_read(node.value, scope.tmp, scope.file_tainted) and _code_like(node.value):
            searches.update(_target_names(node))
    return positions, searches


def _file_claim_kind(det: _Detector, test: ast.AST, scope: _Scope, positions: set[str], searches: set[str]) -> Optional[str]:
    """How *test* claims something about an arbitrary file's text, or None (see the module docstring)."""
    if any(det.file_read(c.func.value, scope.tmp, scope.file_tainted) for c in _position_calls(test)) or _uses(test, positions):  # type: ignore[attr-defined]
        return "position of a substring in a file's text"
    if _uses(test, searches) or (det.file_read(test, scope.tmp, scope.file_tainted) and _literal_search(test) and _code_like(test)):
        return "code-like text searched in a file"
    return None


def _fails_in_body(node: ast.If) -> bool:
    return any(
        isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and isinstance(s.value.func, ast.Attribute) and s.value.func.attr == "fail" for s in node.body
    )


def _source_claim_kind(det: _Detector, test: ast.AST, scope: _Scope, matches: set[str]) -> Optional[str]:
    """How *test* checks the content of source text (a reader call or a name holding its text), or None."""
    kind = det.reader_kind(test, scope.paths, scope.tainted)
    if kind is None:
        used = next((s.id for s in _fast_walk(test) if isinstance(s, ast.Name) and s.id in scope.tainted), None)
        kind = f"text held in `{used}`" if used and _uses(test, scope.tainted) else None
    return kind if kind and (_tests_content(test) or _uses(test, matches)) else None


def _claim_site(node: ast.AST, function: str) -> Optional[ast.expr]:
    """The expression a claim would test at *node*: an ``assert``'s test, an ``if`` whose body fails, or a function's
    ``return`` value when it is itself a content check (a helper answering an assert elsewhere)."""
    if isinstance(node, ast.Assert):
        return node.test
    if isinstance(node, ast.If) and _fails_in_body(node):
        return node.test
    if isinstance(node, ast.Return) and function != "<module>" and node.value is not None and _is_content_check(node.value):
        return node.value
    return None


def find_source_text_claims(
    path: Path,
    *,
    mode: str = "assertion",
    readers: Iterable[str] = DEFAULT_READERS,
    treat_sql_as_source: bool = True,
    follow_helpers: bool = True,
) -> list[SourceTextClaim]:
    """Every source-text claim in *path*, innermost function first, one per statement.

    Raises ``SourceError`` for a file that cannot be read or decoded, and for one that names a reader or a file read
    but does not parse: returning nothing for it would read as "no claims".
    """
    if mode not in ("assertion", "read"):
        raise ValueError(f"mode must be 'assertion' or 'read', not {mode!r}")
    reader_names = frozenset(readers)
    text = read_source(path)
    # Every claim needs a reader, an import of one, or a file read somewhere in the file, so a file naming none of
    # them is skipped unparsed: on mlframe's 3,597 test files that is the difference between a scan measured in
    # minutes and one that fits a pre-commit hook. That skip is sound: such a file cannot hold a claim.
    if not any(token in text for token in (*reader_names, *_TRIGGER_TOKENS, "inspect", "import dis", "from dis")):
        return []
    _, tree = parse_source(path)
    det = _Detector(tree, readers=reader_names, treat_sql_as_source=treat_sql_as_source, follow_helpers=follow_helpers)
    claims: dict[int, SourceTextClaim] = {}
    for scope in det.scopes():
        function, body, paths, tainted = scope.function, scope.body, scope.paths, scope.tainted
        matches = _match_names(body, det, paths, tainted)
        positions, searches = _file_checks(det, scope) if mode == "assertion" else (set(), set())
        for node in _walk_scope(body):
            if mode == "read":
                if isinstance(node, (ast.Call, ast.Attribute)) and node.lineno not in claims:
                    kind = det.reader_kind(node, paths, set())
                    # Parsing a file's content as Python says the file IS Python, wherever its path came from: a
                    # parameter, or a list a helper built. A read mode that tracked only literal paths missed both.
                    if kind is None and isinstance(node, ast.Call) and _call_name(node) == "parse" and any(_contains_read(a) for a in node.args):
                        kind = "parses a file as Python"
                    if kind:
                        claims[node.lineno] = SourceTextClaim(node.lineno, function, kind)
                continue
            # `node.test` is read through the isinstance itself, not through an `is_check` flag: mypy
            # narrows the former and not the latter, and the flag hid that the branch also accepts an
            # `ast.If` whose body fails.
            test = _claim_site(node, function)
            if test is None or not isinstance(node, ast.stmt) or node.lineno in claims:
                continue
            kind = _source_claim_kind(det, test, scope, matches) or _file_claim_kind(det, test, scope, positions, searches)
            if kind:
                claims[node.lineno] = SourceTextClaim(node.lineno, function, kind)
    return sorted(claims.values(), key=lambda c: c.line)


def _keys(files: Iterable[Path], repo_root: Path, **kwargs: object) -> tuple[dict[str, list[int]], int, list[str]]:
    """``(key -> claim lines, files checked, problems)``; a file that cannot be read or parsed is a problem."""
    found: dict[str, list[int]] = {}
    scanned = 0
    problems: list[str] = []
    for path in files:
        rel = relative_posix(path, repo_root)
        try:
            claims = find_source_text_claims(Path(path), **kwargs)  # type: ignore[arg-type]
        except SourceError as exc:
            problems.append(f"{rel}:{exc.line or 1}: {exc.kind}: {exc.message} - not checked")
            continue
        scanned += 1
        for claim in claims:
            found.setdefault(claim.key(rel), []).append(claim.line)
    return found, scanned, problems


def _baseline(baseline_path: Path) -> Baseline:
    return Baseline(baseline_path, gate="source-text", refresh_command=f"pytest {REFRESH_FLAG} (or PY_CI_SHARED_REFRESH=source-text)")


def write_source_text_baseline(baseline_path: Path, keys: "Iterable[str] | Mapping[str, Any]", *, grow: Optional[bool] = None, request: Any = None) -> None:
    """Record today's claims as accepted debt. A mapping ``key -> lines`` records one count per claim; a plain
    iterable of keys records one per occurrence. Shrink-only unless growth is allowed (``BaselineGrowthError``)."""
    if isinstance(keys, Mapping):
        items = [k for k, v in keys.items() for _ in range(len(v) if isinstance(v, (list, tuple, set)) else 1)]
    else:
        items = list(keys)
    _baseline(baseline_path).regenerate(items, grow=grow, request=request)


def assert_no_new_source_text_claims(
    files: Iterable[Path],
    repo_root: Path,
    *,
    allowlist: Mapping[str, str] | None = None,
    baseline_path: Path | None = None,
    min_files: int = 1,
    request: Optional[Any] = None,
    **detector_kwargs: object,
) -> None:
    """Fail on any claim not accepted by *allowlist* (``rel path -> reason``, whole files) or *baseline_path*.

    Both lists shrink only: an allowlisted file that no longer holds a claim, or a baseline key that counts more
    claims than exist, fails too -- an entry that suppresses nothing would absorb the same claim if it came back.
    A refresh (``REFRESH_FLAG``, ``PY_CI_SHARED_REFRESH=source-text``; pass the pytest ``request`` for xdist and
    ``pytest.main``) rewrites the baseline and skips; a missing baseline fails. A file that cannot be read or parsed
    fails, and *min_files* counts files actually checked.
    """
    import pytest

    from ._core import refresh_requested

    files = list(files)
    found, scanned, unreadable = _keys(files, repo_root, **detector_kwargs)
    if scanned < min_files:
        pytest.fail(
            f"only {scanned} file(s) scanned; expected at least {min_files} -- the file list lost its subject" + "".join("\n    " + u for u in unreadable)
        )
    allow = dict(allowlist or {})
    short = [f"allowlist entry has no reason: {rel}" for rel, why in allow.items() if len(why.strip()) < 20]
    by_file: dict[str, list[str]] = {}
    for key in found:
        by_file.setdefault(key.split("::", 1)[0], []).append(key)
    unallowed = {k: v for k, v in found.items() if k.split("::", 1)[0] not in allow}
    stale_allow = sorted(rel for rel in allow if rel not in by_file)

    problems: list[str] = []
    if unreadable:
        problems.append("file(s) that could not be read or parsed, so their claims are unknown:\n    " + "\n    ".join(unreadable))
    new: list[str] = []
    stale_base: list[str] = []
    if baseline_path is not None:
        baseline = _baseline(baseline_path)
        if refresh_requested(REFRESH_FLAG, request):
            try:
                write_source_text_baseline(baseline_path, unallowed, request=request)
            except BaselineError as exc:
                pytest.fail(str(exc), pytrace=False)
            pytest.skip(f"source-text baseline written: {sum(len(v) for v in unallowed.values())} claim(s) in {baseline_path.name}")
        if not baseline.exists():
            pytest.fail(f"source-text baseline {baseline_path} does not exist, so nothing is accepted. Create it with: {baseline.refresh_command}")
        accepted, _notes = baseline.load()
        for k, lines in sorted(unallowed.items()):
            extra = len(lines) - accepted.get(k, 0)
            if extra > 0:
                new.append(f"{k} (line {', '.join(map(str, lines))}; {extra} more than the {accepted.get(k, 0)} accepted)")
        stale_base = sorted(f"{k} ({n - len(unallowed.get(k, []))} fewer)" for k, n in accepted.items() if n > len(unallowed.get(k, [])))
    else:
        new = sorted(f"{k} (line {', '.join(map(str, v))})" for k, v in unallowed.items())
    if new:
        problems.append(
            f"{len(new)} test(s) assert on SOURCE TEXT -- they pass against dead code and against a comment. Call the code and "
            "assert on what it returns or does:\n    " + "\n    ".join(new)
        )
    if stale_allow:
        problems.append("allowlisted file(s) that no longer read source; remove them:\n    " + "\n    ".join(stale_allow))
    if stale_base:
        problems.append(f"baseline entr(ies) that no longer match a claim; remove them (or pass {REFRESH_FLAG}):\n    " + "\n    ".join(stale_base))
    problems.extend(short)
    if problems:
        pytest.fail("\n".join(problems))
