"""Shared check: a comment that names a test file, a class or a function must name one that exists.

Generalises the 2026-09-02 Noema comment-truth audit: seven doc comments described tests that did not
cover what they said, a widget that was not in the file, a formatter the code had stopped using, and a
map that no longer existed. Each was a backticked name in a comment that resolved to nothing, and nothing
mechanical asked. This module asks the two questions that ARE mechanical:

1. Every backticked test-file name (``foo_test.dart``, ``test_foo.py``, ``foo_test.py``) in a comment or
   docstring must exist somewhere under the repo.
2. Every backticked ``ClassName``/``function_name(``/``Class.member`` token must be declared somewhere in
   the repo's source, by the declaration set the caller supplies (so the module stays language-neutral:
   the caller decides how to find declarations - ``ast`` for Python, a regex over ``class X``/``X(`` for
   Dart - and passes the names in).

Deliberately scoped to BACKTICKED names, the same argument ``phantom_markdown_links`` makes about
explicit markdown links: a name in backticks is an unambiguous claim that the thing exists, while a bare
word in prose is not. A separate function checks the one numeric-claim shape that is also exact: a
comment saying "the N known ..." (or "N exceptions", "N cases", ...) followed by a numbered list in the
same comment block must list exactly N items.

Usage (a consuming repo's test_meta test module)::

    from pathlib import Path
    from py_ci_shared.phantom_code_references import (
        assert_no_phantom_code_references, python_declarations, count_claim_mismatches,
    )

    ROOT = Path(__file__).resolve().parents[2]
    FILES = list((ROOT / "pkg").rglob("*.py")) + list((ROOT / "tests").rglob("*.py"))

    def test_comments_name_real_things():
        assert_no_phantom_code_references(
            files=FILES, repo_root=ROOT, declared=python_declarations(FILES),
            baseline_path=Path(__file__).with_name("_phantom_code_references_baseline.json"),
        )

Deliberately dependency-light: ``pytest`` is imported lazily, matching the package's other modules.

Counterpart: ``pyutilz.dev.code_audit.comment_names_missing_symbol`` also runs ``scan_comment_cites_absolute_line``,
which flags EVERY absolute-line citation unconditionally (the practice itself is the risk, not just a currently-stale
instance); :func:`find_stale_absolute_line_citations` here only reports one that is ALREADY wrong, which is what a
self-verifying, no-allowlist check can state without judging the many still-correct ones.
"""

from __future__ import annotations

import ast
import errno
import functools
import importlib
import io
import logging
import re
import tokenize
import types
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from ._core import DEFAULT_EXCLUDE, SourceError, iter_files, load_json, parse_source, read_source, relative_posix
from ._core.node_index import walk as _fast_walk

logger = logging.getLogger(__name__)

# A backticked token. Kept narrow on purpose: identifiers, dotted members, a trailing "(" and test-file
# names; anything with spaces, operators or quotes is prose in code font, not a reference.
_BACKTICK_RE = re.compile(r"`([^`\n]{1,120})`")
_TEST_FILE_RE = re.compile(r"^(?:[\w/\\.-]*?)(\w+_test\.dart|test_\w+\.py|\w+_test\.py)$")
_IDENT_RE = re.compile(r"^(?P<head>[A-Za-z_]\w*)(?:\.(?P<member>[A-Za-z_]\w*))?(?P<rest>(?:\.[A-Za-z_]\w*)*)(?P<call>\(\)?)?$")
# The numeric-claim shape: "the TWO known ..." / "four exceptions" / "3 cases", then a numbered list.
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_COUNT_CLAIM_RE = re.compile(
    r"\b(?:the\s+)?(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:known|deliberate|named|remaining|documented|distinct)?\s*"
    r"(?:exceptions?|cases?|forms?|reasons?|rules?|steps?|classes?|sites?|files?|kinds?|checks?|guards?)\b",
    re.IGNORECASE,
)
_NUMBERED_ITEM_RE = re.compile(r"^\s*(?:#|//|///|\*|\"\"\")?\s*(\d+)[.)]\s+\S")
#: A claim line that ends in "<number> <one or two words>:" ("Two substitutions:"), checked only against a bullet list.
_TAIL_CLAIM_RE = re.compile(r"\b(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+[A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*)?\s*:\s*$", re.IGNORECASE)
_NAME = r"(?:`[^`\n]+`|[A-Za-z_][\w.]*)"
#: One bullet naming a thing: ``#   NAME - text``, ``# - NAME: text``, ``// * NAME - text``. Captures the indent after the
#: comment marker, so a deeper-indented line under an item reads as that item's continuation.
_BULLET_ITEM_RE = re.compile(r"^(?P<indent>\s*)(?:(?:[-*]\s+" + _NAME + r"\s*(?::|\s-)\s+\S)|(?:" + _NAME + r"\s+-\s+\S))")
#: A line's comment marker, so indentation is measured the same way with one (``#   NAME``) and without (a docstring).
_COMMENT_MARKER_RE = re.compile(r"^\s*(?:#|///|//)")

# Words that appear in backticks constantly and are never a code reference in the repos this serves:
# language keywords and literals, common type names, and the punctuation-ish tokens people quote.
_NEVER_REFERENCES = frozenset(
    {
        "true",
        "false",
        "null",
        "None",
        "True",
        "False",
        "self",
        "this",
        "super",
        "async",
        "await",
        "const",
        "final",
        "static",
        "var",
        "let",
        "def",
        "class",
        "return",
        "yield",
        "import",
        "from",
        "as",
        "is",
        "in",
        "int",
        "str",
        "bool",
        "float",
        "list",
        "dict",
        "set",
        "tuple",
        "bytes",
        "object",
        "Any",
        "String",
        "double",
        "num",
        "void",
        "dynamic",
        "List",
        "Map",
        "Set",
        "Future",
        "Stream",
        "Object",
        "Widget",
        "Text",
        "Row",
        "Column",
        "Center",
        "Padding",
        "SizedBox",
        "Expanded",
        "Flexible",
        "Semantics",
        "Color",
        "Duration",
        "Size",
        "Offset",
        "Rect",
        "Key",
        "BuildContext",
        "State",
        "Exception",
        "Error",
        "Iterable",
        "Optional",
        "Union",
        "Literal",
        "Path",
        "Field",
        "BaseModel",
        "dataclass",
        "pytest",
        "flutter",
        "dart",
        "python",
        "json",
        "yaml",
        "toml",
        "csv",
        "jsonl",
        "utf-8",
        "ascii",
        "e",
        "x",
        "y",
        "n",
        "i",
        "j",
        "k",
        "s",
        "r",
        "p",
        "q",
        "t",
        "a",
        "b",
        "c",
        "d",
        "f",
        "g",
        "m",
        "v",
        "w",
        # JSON/JS literals people quote.
        "NaN",
        "Infinity",
        "undefined",
    }
)
# A dotted token whose tail is a file extension is a file name, which is `phantom_markdown_links`' territory.
_FILE_EXT_RE = re.compile(
    r"\.(?:py|md|json|jsonl|csv|tsv|txt|xml|yaml|yml|toml|cfg|ini|gz|zip|sql|db|html|css|js|dart|arb|sh|ps1|log|pdf|png|svg"
    r"|dct|dta|rrf|parquet|pkl|npz|npy|xlsx|xls|bz2|tgz)$",
    re.IGNORECASE,
)
# The Python protocol names: every ``__x__`` the object, type, module and function types define, plus the module attributes and
# the directory name a runtime creates (``__main__``, ``__file__``, ``__pycache__``). The interpreter's, never this repo's; any
# other dunder is still judged, so a made-up ``__ghost__`` still fires.
_PYTHON_PROTOCOL_NAMES = frozenset(
    {"__main__", "__file__", "__pycache__"}
    | {n for t in (object, type, types.ModuleType, types.FunctionType, types.MethodType, property) for n in dir(t) if n.startswith("__")}
)
# A standard identifier of an external vocabulary: an ICD-10 code or category range (``E11``, ``A15-A19``, ``K60.1``), a UMLS
# concept unique identifier (``C0020538``), a Cochrane review id (``CD000097.pub2``). Data, not a name this repo declares.
_STANDARD_CODE_RE = re.compile(r"^(?:[A-Z]\d{2}(?:[.-][A-Za-z0-9]+)*|C\d{7}|CD\d{6}(?:\.pub\d+)?)$")


def _base_names(node: ast.ClassDef) -> list[str]:
    out = []
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        out.append(ast.unparse(target).split(".")[-1])
    return out


def _collect_class(node: ast.ClassDef, names: set[str], members: dict[str, set[str]], bases: dict[str, list[str]]) -> None:
    """Record a class, its direct members, its ``self.x``/``cls.x`` assignments and its bases."""
    names.add(node.name)
    own = members.setdefault(node.name, set())
    bases.setdefault(node.name, []).extend(_base_names(node))
    for child in node.body:
        for member in _direct_member_names(child):
            own.add(member)
            names.add(member)
    _collect_self_attrs(node, own)
    names.update(own)


def _direct_member_names(child: ast.stmt) -> list[str]:
    """Names a single class-body statement declares: def/class names, assignment targets."""
    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [child.name]
    if isinstance(child, ast.Assign):
        return [t.id for t in child.targets if isinstance(t, ast.Name)]
    if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
        return [child.target.id]
    return []


def _collect_self_attrs(node: ast.ClassDef, own: set[str]) -> None:
    """Add every ``self.x`` / ``cls.x`` assignment target inside the class to *own*."""
    for sub in _fast_walk(node):
        if isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in ("self", "cls"):
                    own.add(target.attr)


def _collect_non_class(node: ast.AST, names: set[str]) -> None:
    """Record the names a function, module-level assignment or import declares."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        names.add(node.name)
        for arg in node.args.args + node.args.kwonlyargs:
            names.add(arg.arg)
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.add(node.target.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            names.add((alias.asname or alias.name).split(".")[-1])


def _resolved_members(cls: str, seen: frozenset[str], members: dict[str, set[str]], bases: dict[str, list[str]]) -> "Optional[set[str]]":
    """Members of *cls* including inherited ones; ``None`` when some base is not declared here (open class)."""
    out = set(members.get(cls, ()))
    for base in bases.get(cls, ()):
        if base == "object":
            continue
        if base not in members or base in seen:
            return None
        inherited = _resolved_members(base, seen | {base}, members, bases)
        if inherited is None:
            return None
        out |= inherited
    return out


def _collect_file(path: Path, names: set[str], members: dict[str, set[str]], bases: dict[str, list[str]]) -> None:
    """Record the module stem (and package name for ``__init__``) and every declaration of one ``.py`` file."""
    names.add(path.stem)
    if path.stem == "__init__":
        names.add(path.parent.name)
    try:
        _, tree = parse_source(path)
    except SourceError as exc:
        logger.warning("phantom_code_references: %s was skipped, its names are not known: %s", path, exc)
        return
    for node in _fast_walk(tree):
        if isinstance(node, ast.ClassDef):
            _collect_class(node, names, members, bases)
        else:
            _collect_non_class(node, names)


def python_declarations(files: Iterable[Path]) -> set[str]:
    """Every class, function, method (as ``Class.member``), module-level name and module stem declared in
    ``files``, via ``ast`` - never a regex over source.

    Class members include methods, class attributes, nested classes and ``self.x`` assignments in methods, and
    are inherited from base classes declared in ``files``. A class with any base NOT declared there (``BaseModel``,
    ``Enum``, ``Exception``...) also gets ``Class.*``: its members cannot all be listed, so a ``Class.member``
    reference to it is not judged. A package's ``__init__.py`` also declares the package directory name.
    """
    names: set[str] = set()
    members: dict[str, set[str]] = {}
    bases: dict[str, list[str]] = {}
    for path in files:
        if path.suffix == ".py":
            _collect_file(path, names, members, bases)

    for cls in members:
        found = _resolved_members(cls, frozenset({cls}), members, bases)
        if found is None:
            names.add(f"{cls}.*")
            found = members[cls]
        names.update(f"{cls}.{m}" for m in found)
        names.add(f"{cls}.__init__")
    return names


# Dart has no stdlib parser here; a declaration regex is the honest tool, and it is what the Dart-side
# twin of this check uses too. Kept permissive: anything declared as a class/mixin/enum/typedef/extension,
# any top-level or member function/getter, any named parameter or field.
_DART_DECL_RE = re.compile(
    r"\b(?:class|mixin|enum|typedef|extension)\s+([A-Za-z_]\w*)"
    r"|(?:^|\s)(?:[A-Za-z_][\w<>, ?]*\s+)?(?:get\s+)?([a-z_]\w*)\s*(?:\(|=>|=)"
    r"|(?:this\.|required\s+this\.)([a-z_]\w*)"
    r"|\bfinal\s+(?:[\w<>, ?]+\s+)?([a-z_]\w*)\b",
    re.MULTILINE,
)


def dart_declarations(files: Iterable[Path]) -> set[str]:
    """Every name a regex can see declared in the Dart ``files``, plus each file's stem."""
    names: set[str] = set()
    for path in files:
        if path.suffix != ".dart":
            continue
        names.add(path.stem)
        try:
            source = read_source(path)
        except SourceError:
            continue
        for m in _DART_DECL_RE.finditer(source):
            for group in m.groups():
                if group:
                    names.add(group)
    return names


def _is_test_file(path: Path) -> bool:
    """A test module by its name (``test_*.py``, ``*_test.py``) or by a ``tests`` directory on its path."""
    return path.name.startswith("test_") or path.name.endswith("_test.py") or "tests" in path.parts


def _docstring_ids(tree: ast.Module) -> set[int]:
    """``id()`` of the bare string statements (docstrings) of a module."""
    return {
        id(node.value) for node in _fast_walk(tree) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }


def _data_name_index(files: Iterable[Path]) -> "tuple[frozenset[str], dict[str, frozenset[int]], list[str]]":
    """What the repo's code SPELLS OUT, from its string constants. A string that a NON-TEST module uses as a literal, in
    any role (a comparison, a call argument, a key, a column read, an assignment), is a data name: the repo handles that
    value, so a comment naming it is not a phantom. Docstrings are excluded (a docstring naming a thing is the claim being
    checked, not evidence for it) and test modules never contribute. Returns the exact production constant values, a
    word -> constant-index map for ``table.column`` SQL checks (every constant, tests included), and the constants."""
    exact: set[str] = set()
    constants: list[str] = []
    words: dict[str, set[int]] = {}
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            _, tree = parse_source(path)
        except SourceError:
            continue
        docstrings = _docstring_ids(tree)
        production = not _is_test_file(path)
        for node in _fast_walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings and node.value:
                if production:
                    exact.add(node.value)
                index = len(constants)
                constants.append(node.value)
                for word in set(re.findall(r"[A-Za-z_]\w*", node.value)):
                    words.setdefault(word, set()).add(index)
    return frozenset(exact), {w: frozenset(ix) for w, ix in words.items()}, constants


def _names_imported_by(path: Path) -> frozenset[str]:
    """The names a Python file binds with ``import`` / ``from ... import`` (a file that imports ``StataReader`` has it)."""
    if path.suffix != ".py":
        return frozenset()
    try:
        _, tree = parse_source(path)
    except SourceError:  # best-effort: an unparsable file imports nothing here; the comment check reports the file itself
        return frozenset()
    out: set[str] = set()
    for node in _fast_walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add((alias.asname or alias.name).split(".")[0])
    return frozenset(out)


def _python_comment_lines(source: str, tree: ast.Module) -> list[tuple[int, str]]:
    """COMMENT tokens (a ``#`` inside a string is not one) and the lines of every bare string statement (docstrings
    and attribute docstrings; an assigned triple-quoted literal such as ``SQL = ...`` is code, not documentation)."""
    lines = source.splitlines()
    out: dict[int, str] = {}
    for node in _fast_walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for lineno in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                out[lineno] = lines[lineno - 1] if lineno - 1 < len(lines) else ""
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            out.setdefault(tok.start[0], tok.string)
    return sorted(out.items())


def _comment_lines_checked(path: Path) -> "tuple[list[tuple[int, str]], Optional[SourceError]]":
    """``(lines, problem)``: comment/docstring lines, or the reason the file could not be read or parsed."""
    try:
        if path.suffix == ".py":
            source, tree = parse_source(path)
            return _python_comment_lines(source, tree), None
        source = read_source(path)
    except SourceError as exc:
        logger.debug("could not parse source for comment lines: %s", type(exc).__name__)
        return [], exc
    markers: tuple[str, ...] = ("//", "///", "/*", "*")
    if _hash_commented(path, source):
        markers = ("#",)
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith(markers) and not stripped.startswith("#!"):
            out.append((lineno, stripped))
    return out, None


#: Suffixes whose comments start with ``#``: shell, YAML, TOML, INI-style configs. A ``Dockerfile*`` and an extensionless
#: script with a ``#!`` line are read the same way.
_HASH_COMMENT_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".conf", ".template"})


def _hash_commented(path: Path, source: str) -> bool:
    return path.suffix in _HASH_COMMENT_SUFFIXES or path.name.startswith("Dockerfile") or (not path.suffix and source.startswith("#!"))


def _comment_lines(path: Path) -> list[tuple[int, str]]:
    """``(lineno, text)`` for every line that is a comment or lies inside a Python docstring."""
    return _comment_lines_checked(path)[0]


def _import_or_none(name: str) -> object:
    """The module ``name``, or None when importing it raises anything (a module may even call ``sys.exit``)."""
    try:
        return importlib.import_module(name)
    except (Exception, SystemExit) as exc:  # best-effort: a module that cannot be imported is reported as unresolved by the caller, which names it
        logger.debug("module could not be imported: %s", type(exc).__name__)
        return None


@functools.cache
def _importable(name: str) -> bool:
    """Is *name* a module this interpreter can import? Covers the stdlib on 3.9, which has no ``sys.stdlib_module_names``."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError) as exc:
        logger.warning("phantom_code_references.py handler at line 407 substituted a fallback for %s: %s", type(exc).__name__, exc)
        logger.debug("module spec lookup failed: %s", type(exc).__name__)
        return False


def _resolves_by_import(dotted: str) -> bool:
    """Whether ``a.b.c`` names a real object of an installed module or of ``builtins``: import the longest importable
    prefix, then ``getattr`` the rest. Nothing runs beyond the import itself, and whatever the import raises counts
    as "does not resolve"."""
    import builtins

    parts = dotted.split(".")
    obj: object = None
    rest: list[str] = []
    for i in range(len(parts), 0, -1):
        obj = _import_or_none(".".join(parts[:i]))
        if obj is not None:
            rest = parts[i:]
            break
    else:
        if not hasattr(builtins, parts[0]):
            return False
        obj, rest = getattr(builtins, parts[0]), parts[1:]
    try:
        for name in rest:
            obj = getattr(obj, name)
    except (
        Exception
    ) as exc:  # a property or a module __getattr__ may raise anything  # best-effort: a property or module __getattr__ may raise anything; the name is then unresolved by import, which the caller reports
        logger.warning("phantom_code_references.py handler at line 433 substituted a fallback for %s: %s", type(exc).__name__, exc)
        logger.debug("dotted name does not resolve by import: %s", type(exc).__name__)
        return False
    return True


_VIOLATION_KEY_RE = re.compile(r"^(?P<rel>.+?):\d+: (?:`(?P<token>[^`\n]*)`|(?P<kind>[a-z_]+):)")


def _violation_key(entry: str) -> str:
    """The baseline identity of a violation: ``<rel>::<token>`` (or ``<rel>::<kind>`` for an unreadable file), with
    no line number and no message wording, so a moved line or a reworded message keeps its entry. An entry
    already in key form is returned unchanged."""
    m = _VIOLATION_KEY_RE.match(entry)
    if m is None:
        return entry
    return f"{m.group('rel')}::{m.group('token') if m.group('token') is not None else '<' + m.group('kind') + '>'}"


def _test_file_index(repo_root: Path) -> "tuple[set[str], set[str]]":
    """``(basenames, repo-relative paths)`` of the test files git would commit under *repo_root* (venvs, build
    output and other excluded directories never count as the file a comment names)."""
    rels = {relative_posix(p, repo_root) for p in iter_files(repo_root, ("*_test.dart", "test_*.py", "*_test.py"), exclude=DEFAULT_EXCLUDE)}
    return {r.rsplit("/", 1)[-1] for r in rels}, rels


def _test_file_problem(token: str, basename: str, test_names: set[str], test_paths: set[str]) -> "Optional[str]":
    """The violation text for a backticked test-file *token* that no test file under the repo matches."""
    wanted = token.replace("\\", "/").lstrip("./")
    exists = (wanted in test_paths or any(p.endswith("/" + wanted) for p in test_paths)) if "/" in wanted else basename in test_names
    return None if exists else f"`{token}` names a test file that does not exist"


def _outside_head_verdict(token: str, im: "re.Match[str]", known: set[str], imported: frozenset[str]) -> "Optional[bool]":
    """For ``head.member`` whose head is not this repo's: True when it resolves (by import, or as a library member of a name
    the commented file imports), False when the module or builtin exists but the attribute does not, None when no module
    decides and the caller judges it as an undeclared name."""
    head, member = im.group("head"), im.group("member")
    if _resolves_by_import(f"{head}.{member}{im.group('rest')}"):
        return True
    if head in known or _importable(head):
        return False
    if head in imported:
        return True  # the commented file imports ``head`` itself, so the claim is about a library member it has in hand
    return None


def _spelled_out_elsewhere(token: str, spelled_exact: frozenset[str], spelled_words: dict[str, frozenset[int]], spelled_constants: list[str]) -> bool:
    """A token that is not a claim about this repo's declarations: a Python protocol name, a standard vocabulary code, an
    OS error constant, a name the repo's code spells out as a string, a one-character symbol, or a ``table.column`` the
    repo builds in SQL. Each rule has its own unit test in ``tests/test_phantom_code_references.py``."""
    if _FILE_EXT_RE.search(token) or (token.isupper() and "_" in token):
        return True  # a file name, or an environment variable / another system's constant
    if token in _PYTHON_PROTOCOL_NAMES or _STANDARD_CODE_RE.match(token):
        return True  # a Python protocol name, or an identifier of an external code vocabulary (ICD-10, UMLS, Cochrane)
    if token.isupper() and hasattr(errno, token):
        return True  # an OS error constant (``ENOENT``) spelled as the stdlib spells it
    im = _IDENT_RE.match(token)
    if not im:
        return False
    head, member = im.group("head"), im.group("member")
    if member is None and not im.group("call"):
        if token in spelled_exact:
            return True  # the repo's code spells this name out as a string: a data name, column, label or key it defines
        if len(head) == 1:
            return True  # a one-character symbol or placeholder (``Z`` in an equation, ``X`` in ``mirrors X.as_dict()``)
    if member is not None and not im.group("call") and not im.group("rest"):
        return _sql_spells_both(head, member, spelled_words, spelled_constants)  # ``fts_backfill_progress.last_rowid`` in SQL
    return False


def _token_problem(
    token: str,
    declared: set[str],
    known: set[str],
    repo_known: set[str],
    test_names: set[str],
    test_paths: set[str],
    spelled: "tuple[frozenset[str], dict[str, frozenset[int]], list[str]]",
    imported: frozenset[str],
    declared_heads: frozenset[str],
) -> "Optional[str]":
    """The violation text for one backticked *token* (without the ``rel:line:`` prefix), or None when it is fine."""
    tf = _TEST_FILE_RE.match(token)
    if tf:
        return _test_file_problem(token, tf.group(1), test_names, test_paths)
    if _spelled_out_elsewhere(token, *spelled):
        return None
    im = _IDENT_RE.match(token)
    if not im:
        return None
    head, member = im.group("head"), im.group("member")
    if member is not None and head not in repo_known:
        verdict = _outside_head_verdict(token, im, known, imported)
        if verdict is not None:
            return None if verdict else f"`{token}` does not resolve by import"
    repo_class = member is not None and f"{head}.*" not in known and head in declared_heads
    if head in known and (member is None or f"{head}.{member}" in known):
        return None
    if head in known and not repo_class:
        # A declared head with an undeclared member of a LIBRARY class; resolving library APIs is out
        # of scope, so the head carries the claim. A closed class of this repo has every member listed.
        return None
    if head in known and repo_class:
        return f"`{token}` names a member `{head}` does not declare"
    # A lowercase bare identifier in backticks is a parameter, a keyword argument or a local far more
    # often than a claim about a declared function; only a call suffix or a dotted member makes it one.
    if head[0].islower() and member is None and not im.group("call"):
        return None
    return f"`{token}` names nothing declared in this repo"


def find_phantom_code_references(
    files: Iterable[Path], repo_root: Path, declared: set[str], *, extra_known: Iterable[str] = (), allow_unparsed: bool = False
) -> list[str]:
    """``"<rel>:<line>: `<token>` names nothing in this repo"`` for every backticked reference in a comment
    or docstring that resolves to neither a test file under ``repo_root`` nor a declared name.

    Dotted tokens resolve if the whole ``Class.member`` is declared, or the head is declared and is not a class
    of this repo whose members are all known (a member of a library class - ``Text.rich``, ``pytest.mark`` - is
    not this repo's to declare; a member of a closed repo class is). Only the first member of ``a.b.c`` is judged.
    A test-file token with a directory must match that repo-relative path. A trailing ``()`` or ``(`` is stripped.
    Tokens containing anything but identifier characters, dots and a call suffix are prose and skipped. A file
    that cannot be read or parsed is reported as ``<rel>:<line>: unparsable: ...``.

    A dotted token whose head this repo does not declare (a stdlib module, a builtin, an installed dependency such as
    ``pyutilz.llm.get_llm_provider``) is real when it resolves by import, attribute by attribute, so a misspelled
    ``os.path.joinn`` is reported and an external path a comment cites is not."""
    import builtins
    import sys

    files = list(files)
    # Python's own names resolve without a declaration: builtins (`ValueError`) and stdlib modules (`ftplib.FTP`).
    repo_known = set(declared) | set(extra_known) | _NEVER_REFERENCES
    known = repo_known | set(dir(builtins)) | set(getattr(sys, "stdlib_module_names", ()))
    # The heads of every dotted declaration, once: `_token_problem` asks per token whether a head has any member declared.
    declared_heads = frozenset(k.partition(".")[0] for k in declared if "." in k)
    test_names, test_paths = _test_file_index(repo_root)
    spelled_exact, spelled_words, spelled_constants = _data_name_index(files)
    violations: list[str] = []
    for path in files:
        rel = relative_posix(path, repo_root)
        imported = _names_imported_by(path)
        lines, problem = _comment_lines_checked(path)
        if problem is not None and not allow_unparsed:
            violations.append(f"{rel}:{(problem.line if problem.line is not None else 1)}: {problem.kind}: {problem.message}")
        for lineno, text in lines:
            for m in _BACKTICK_RE.finditer(text):
                token = m.group(1).strip()
                problem_text = _token_problem(
                    token,
                    declared,
                    known,
                    repo_known,
                    test_names,
                    test_paths,
                    (spelled_exact, spelled_words, spelled_constants),
                    imported,
                    declared_heads,
                )
                if problem_text is not None:
                    violations.append(f"{rel}:{lineno}: {problem_text}")
    return violations


def _sql_spells_both(head: str, member: str, spelled_words: dict[str, frozenset[int]], constants: list[str]) -> bool:
    """Some string constant of the repo contains both ``head`` and ``member`` as whole words."""
    hits = spelled_words.get(head, frozenset()) & spelled_words.get(member, frozenset())
    return any(re.search(rf"\b{re.escape(head)}\b", constants[i]) and re.search(rf"\b{re.escape(member)}\b", constants[i]) for i in hits)


def _numbered_count(lines: list[tuple[int, str]], i: int) -> int:
    """How many consecutive ``1.``/``2)`` items follow the claim at *i* in the same comment block."""
    seen: list[int] = []
    j = i + 1
    while j < len(lines) and j < i + 60:
        ln, txt = lines[j]
        if ln != lines[j - 1][0] + 1:
            break  # the comment block ended
        item = _NUMBERED_ITEM_RE.match(txt)
        if item:
            number = int(item.group(1))
            if (seen and number != seen[-1] + 1) or (not seen and number != 1):
                break
            seen.append(number)
        elif seen and not txt.strip(" /#*"):
            break
        j += 1
    return len(seen)


def _bullet_count(lines: list[tuple[int, str]], i: int) -> int:
    """How many contiguous ``NAME - text`` bullets start directly under the claim at *i*. A line indented deeper than the
    first bullet continues the previous item; anything else (a blank comment line, prose, the block's end) ends the list."""
    count, indent = 0, -1
    j = i + 1
    while j < len(lines) and j < i + 60:
        ln, txt = lines[j]
        if ln != lines[j - 1][0] + 1:
            break
        body = _COMMENT_MARKER_RE.sub("", txt, count=1).rstrip()
        item = _BULLET_ITEM_RE.match(body)
        depth = len(body) - len(body.lstrip())
        if item and (indent < 0 or len(item.group("indent")) == indent):
            indent = len(item.group("indent"))
            count += 1
        elif count and body.strip() and depth > indent:
            pass  # continuation of the previous bullet
        else:
            break
        j += 1
    return count


def count_claim_mismatches(files: Iterable[Path]) -> list[str]:
    """``"<file>:<line>: says N ..., lists M"`` for every comment block that claims a count ("the TWO
    known exceptions", "four cases", "Two substitutions:") on a line ending in ``:`` and then enumerates a list of a
    different length directly under it: a numbered list (``1.``/``2)``, within the next 60 comment lines) or a contiguous
    bullet list naming one thing per item (``#   NAME - text``, ``# - NAME: text``, ``// * `name` - text``). Only that
    exact shape is checked: a number word with no list under it is prose and passes.

    Comments are Python comment tokens and docstrings, ``//``/``*`` lines of other sources, and ``#`` lines of shell,
    YAML, TOML, INI-style files, ``Dockerfile*`` and extensionless ``#!`` scripts."""
    violations: list[str] = []
    for path in files:
        lines = _comment_lines(path)
        for i, (lineno, text) in enumerate(lines):
            if not text.rstrip().endswith(":"):
                continue
            m = _COUNT_CLAIM_RE.search(text)
            tail = _TAIL_CLAIM_RE.search(text)
            if not m and not tail:
                continue
            listed = _numbered_count(lines, i) if m else 0
            if not listed:
                listed = _bullet_count(lines, i)
            raw = (m or tail).group("n").lower()  # type: ignore[union-attr]
            claimed = int(raw) if raw.isdigit() else _WORD_NUMBERS[raw]
            if listed and listed != claimed:
                violations.append(f"{path}:{lineno}: says {claimed}, lists {listed}")
    return violations


#: Deliberately narrow to an EXPLICIT self-reference ("of this file" / "in this file"), not bare "line N": a
#: real-tree run of the wider `` line\s+\d+ `` form found two false-positive classes bare digits cannot tell
#: apart from a genuine self-citation -- a pasted traceback frame (``File "...", line 212 in __call__``) and
#: prose that names a DIFFERENT file a few words earlier ("the path in _cross_target.py around line 521").
#: Neither says "of/in this file", so requiring that phrase removes both without giving up the proposal's
#: own example, which does say it.
_ABSOLUTE_LINE_RE = re.compile(r"\bline\s+(\d+)\s+(?:of|in)\s+this\s+file\b", re.IGNORECASE)


def find_stale_absolute_line_citations(files: Iterable[Path]) -> list[str]:
    """``"<rel>:<line>: cites line N, but this file has only M lines"`` for every comment or docstring naming
    an absolute line of ITS OWN file (``# the retry budget is computed on line 482 of this file``) where N is
    past the file's actual end. Self-verifying like the count-claim check above: no allowlist needed, since a
    line number bigger than the file cannot ever be right.

    Scoped to the EXPLICIT "of this file" / "in this file" phrasing (see :data:`_ABSOLUTE_LINE_RE`); a bare
    "line N" is not judged; the same is true for a citation naming another file (``a.py:12``), which is
    ``stale_source_citations``'s shape -- it resolves those, filename and all, and can also tell a correct
    one from a stale one rather than only a past-the-end one."""
    violations: list[str] = []
    for path in files:
        lines, problem = _comment_lines_checked(path)
        if problem is not None:
            continue
        try:
            n_lines = len(read_source(path).splitlines())
        except SourceError:
            continue
        for lineno, text in lines:
            for m in _ABSOLUTE_LINE_RE.finditer(text):
                cited = int(m.group(1))
                if cited > n_lines:
                    violations.append(f"{path}:{lineno}: cites line {cited}, but this file has only {n_lines} lines")
    return violations


def assert_no_phantom_code_references(
    files: Iterable[Path],
    repo_root: Path,
    declared: set[str],
    *,
    baseline_path: Path | None = None,
    extra_known: Iterable[str] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Fail on any phantom reference not in the committed baseline; also fail when a baseline entry is no
    longer reproduced (the debt was paid - prune it), so the baseline only ever shrinks. Fewer than *min_files*
    files fails too: a glob that lost its subject hands over an empty list, which would otherwise read as clean.

    A baseline entry is matched on file and name: ``"<rel>::<token>"``, or a full violation line whose line number
    and wording are ignored, so moving a comment or rewording a message does not churn the baseline."""
    import pytest

    files = list(files)
    if len(files) < min_files:
        pytest.fail(f"only {len(files)} file(s) given; expected at least {min_files} -- this would check nothing")
    violations = set(find_phantom_code_references(files, repo_root, declared, extra_known=extra_known, allow_unparsed=allow_unparsed))
    baseline: set[str] = set()
    if baseline_path is not None and baseline_path.exists():
        baseline = set(load_json(baseline_path)["phantom_references"])
    # Entries match on file + name (either the key form or a full violation line), never on line or wording.
    baseline_keys = {_violation_key(e) for e in baseline}
    found_keys = {_violation_key(v) for v in violations}
    new = sorted(v for v in violations if _violation_key(v) not in baseline_keys)
    stale = sorted(e for e in baseline if _violation_key(e) not in found_keys)
    problems = []
    if new:
        problems.append("comments naming things that do not exist (fix the comment, do not extend the baseline):\n  " + "\n  ".join(new))
    if stale:
        problems.append(f"baseline entries no longer reproduced - remove from {baseline_path.name if baseline_path else 'baseline'}:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))


def _unreadable(paths: list[Path]) -> list[str]:
    """One line per file the comment scan could not read or parse: its comments were never checked."""
    out: list[str] = []
    for path in paths:
        problem = _comment_lines_checked(path)[1]
        if problem is not None:
            out.append(f"{path}:{problem.line if problem.line is not None else 1}: {problem.kind}: {problem.message}")
    return out


def assert_no_count_claim_mismatches(files: Iterable[Path], *, min_files: int = 1, allow_unparsed: bool = False) -> None:
    """Fail on any "the N ..." claim whose numbered list has a different length. No baseline: a wrong count
    is unconditionally wrong."""
    import pytest

    paths = list(files)
    if len(paths) < min_files:
        pytest.fail(f"only {len(paths)} file(s) given; expected at least {min_files} -- this would check nothing")
    violations = count_claim_mismatches(paths)
    if not allow_unparsed:
        violations += _unreadable(paths)
    if violations:
        pytest.fail("comments whose stated count disagrees with the list under it:\n  " + "\n  ".join(violations))


def assert_no_stale_absolute_line_citations(files: Iterable[Path], *, min_files: int = 1, allow_unparsed: bool = False) -> None:
    """Fail on any comment citing an absolute line of its own file that is past the file's end. No baseline:
    a line number bigger than the file cannot ever be right."""
    import pytest

    paths = list(files)
    if len(paths) < min_files:
        pytest.fail(f"only {len(paths)} file(s) given; expected at least {min_files} -- this would check nothing")
    violations = find_stale_absolute_line_citations(paths)
    if not allow_unparsed:
        violations += _unreadable(paths)
    if violations:
        pytest.fail("comments citing an absolute line of their own file past its end (cite the symbol instead):\n  " + "\n  ".join(violations))
