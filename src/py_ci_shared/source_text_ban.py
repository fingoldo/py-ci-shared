"""Find tests that assert on a module's SOURCE TEXT instead of running it.

WHY THIS IS SHARED. A test that greps production source passes against dead code, against an
inverted condition, and against a comment. Two projects have now banned the practice independently,
and the ban has been widened four times in each -- every time by banning the SPELLING of the last
evasion rather than the practice:

    1. `inspect.getsource(...)`                     the original ban
    2. `Path(module.__file__).read_text()`          same claim, different spelling
    3. a package constant or a module-level `_SCRIPT` holding the path
    4. `for path in DIR.glob("*.py")`               a loop variable

Each widening was written twice, in two repositories, and the second copy has lagged the first every
time: when this module was extracted, one consumer was still running the shape its sibling had
replaced two rounds earlier, and three known evasions were live in it. That is the argument for one
implementation -- not tidiness, but that a rule maintained in two places is a rule enforced at the
weaker of the two.

WHAT IS *NOT* BANNED, and this is deliberate. Reading a `.sql` file, a fixture, a JSON cache or a
README is untouched. A rule that fired on those would be turned off within a week, and then the real
one would be gone with it. What makes a read offending is that its subject is PYTHON SOURCE.

The rule is still a heuristic over text, and it is meant to be: the alternative -- resolving every
path expression -- would be a rule nobody could reason about, in service of a check whose job is to
make an author stop and justify themselves. A read whose path is built in a way this module cannot
see will not be caught, and the honest answer to that is the next widening, in one place.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path
from typing import Optional

from ._core import ImportAliases, SourceError, parse_source, read_source
from ._core.node_index import walk as _fast_walk

__all__ = ["offending_lines", "READ_CALL", "PY_PATH_ON_LINE", "NON_PY_LITERAL", "GETSOURCE"]

#: `inspect.getsource(...)` -- the original form, and still the bluntest.
GETSOURCE = re.compile(r"\binspect\.getsource\s*\(")

#: Reading a file's bytes, and parsing them as Python. `ast.parse` is here because an AST walk is a
#: source-text claim with better manners: it survives reformatting, which makes it MORE durable than
#: a substring and no more behavioural.
READ_CALL = re.compile(r"\.\s*read_(?:text|bytes)\s*\(|\bast\.parse\s*\(")

#: The subject is Python source, said on the line itself.
PY_PATH_ON_LINE = re.compile(r"""__file__|\.py['"]""")

#: ...unless the line names a file that is NOT Python. `(Path(__file__).parent / "README.MD")` and
#: `... / "config.toml"` both carry the `__file__` token while reading something this ban has no
#: opinion about, and a rule that flags a README read is a rule that earns an allowlist entry and
#: then a reputation. On a file that parses, this is only consulted on the PATH being read, so
#: `"x.json" in Path(__file__).read_text()` is still a read of Python source.
NON_PY_LITERAL = re.compile(
    r"""['"][^'"]*\.(?:sql|json|toml|md|txt|csv|ini|cfg|ya?ml|html|css|js|log|env|lock)['"]""",
    re.IGNORECASE,
)

#: `_SCRIPT = _ROOT / "scripts" / "verify.py"` -- the NAME, so a read through it is recognised on the
#: line that reads it rather than only where it was built.
_PY_PATH_BINDING = re.compile(r"""^\s*([A-Za-z_]\w*)\s*=.*\.py['"]""")

#: `for path in sorted(_VIEWS_DIR.glob("*.py")):` -- the fourth evasion, and the one that let a test
#: parse an entire package of production modules without ever being asked to justify it.
_PY_LOOP_BINDING = re.compile(r"""^\s*for\s+([A-Za-z_]\w*)\s+in\s+.*\br?glob\s*\(\s*['"][^'"]*\.py['"]""")

#: `source = path.read_text(...)` where `path` is a .py path: the TEXT then travels under a new name,
#: and `ast.parse(source)` two lines later carries no path of its own.
_TEXT_BINDING = re.compile(r"""^\s*([A-Za-z_]\w*)\s*=\s*""")

_COMMENT_OR_PROSE = ("#", '"', "'", "*")

#: Import-resolved callables that return a function's or module's source text.
_GETSOURCE_NAMES = frozenset({"inspect.getsource", "inspect.getsourcelines", "inspect.findsource", "inspect.getsourcefile"})
_OPEN_NAMES = frozenset({"open", "io.open", "codecs.open", "tokenize.open", "builtins.open"})
_PARSE_NAMES = frozenset({"ast.parse", "compile"})
_READ_ATTRS = frozenset({"read_text", "read_bytes"})


def _binds_python_source(lines: list[str]) -> set[str]:
    """Names that hold either a `.py` path or the text read from one."""
    names: set[str] = set()
    for line in lines:
        for pattern in (_PY_PATH_BINDING, _PY_LOOP_BINDING):
            match = pattern.match(line)
            if match:
                names.add(match.group(1))
    # A second pass, because the text-carrying name can only be recognised once the path names are
    # known: `source = module_path.read_text(...)` binds `source` to Python source only if
    # `module_path` is one of the names found above (or the line names a `.py` path itself).
    for line in lines:
        match = _TEXT_BINDING.match(line)
        if not match or not (READ_CALL.search(line) or re.search(r"\.\s*read\s*\(", line)):
            continue
        if PY_PATH_ON_LINE.search(line) or any(re.search(rf"\b{re.escape(n)}\b", line) for n in names):
            names.add(match.group(1))
    return names


def _docstring_lines(tree: ast.Module) -> set[int]:
    """Lines of every bare string statement (docstrings and prose blocks): they DISCUSS code, never run it."""
    out: set[int] = set()
    for node in _fast_walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


def _without_comments(source: str) -> list[str]:
    """*source*'s lines with every comment blanked (tokenizer-exact); the raw lines when it cannot tokenize."""
    lines = source.splitlines()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return lines
    for tok in tokens:
        if tok.type == tokenize.COMMENT and tok.start[0] - 1 < len(lines):
            row, col = tok.start
            lines[row - 1] = lines[row - 1][:col].rstrip()
    return lines


def _mentions(text: str, py_names: set[str]) -> bool:
    return bool(PY_PATH_ON_LINE.search(text)) or any(re.search(rf"\b{re.escape(n)}\b", text) for n in py_names)


def _read_subject(call: ast.Call, aliases: ImportAliases) -> Optional[str]:
    """Source text of what *call* reads or parses, or ``None`` when it is not a read/parse call."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _READ_ATTRS:
        return ast.unparse(func.value)
    if isinstance(func, ast.Attribute) and func.attr in ("read", "readlines") and isinstance(func.value, ast.Call):
        opener = func.value
        if aliases.qualified_name(opener) in _OPEN_NAMES and opener.args:
            return ast.unparse(opener.args[0])
    if aliases.qualified_name(call) in _PARSE_NAMES and call.args:
        return ast.unparse(call.args[0])
    return None


def _ast_offending(tree: ast.Module, lines: list[str], extra_patterns: tuple[re.Pattern[str], ...]) -> set[int]:
    aliases = ImportAliases.from_tree(tree)
    skip = _docstring_lines(tree)
    code = _without_comments("\n".join(lines))
    py_names = _binds_python_source(code)
    hits: set[int] = set()
    for node in _fast_walk(tree):
        if not isinstance(node, ast.Call) or node.lineno in skip:
            continue
        if aliases.qualified_name(node) in _GETSOURCE_NAMES:
            hits.add(node.lineno)
            continue
        subject = _read_subject(node, aliases)
        if subject is not None and _mentions(subject, py_names) and not NON_PY_LITERAL.search(subject):
            hits.add(node.lineno)
    for number, line in enumerate(code, 1):
        if number not in skip and any(p.search(line) for p in extra_patterns):
            hits.add(number)
    return hits


def _line_offending(lines: list[str], extra_patterns: tuple[re.Pattern[str], ...]) -> set[int]:
    """The text heuristic, for a file that does not parse."""
    py_names = _binds_python_source(lines)
    hits: set[int] = set()
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(_COMMENT_OR_PROSE):
            continue
        offending = bool(GETSOURCE.search(line)) or any(p.search(line) for p in extra_patterns)
        if not offending and READ_CALL.search(line):
            offending = _mentions(line, py_names) and not NON_PY_LITERAL.search(line)
        if offending:
            hits.add(number)
    return hits


def offending_lines(path: Path, *, extra_patterns: tuple[re.Pattern[str], ...] = ()) -> list[tuple[int, str]]:
    """Lines in *path* that READ Python source, as `(line number, stripped line)`.

    Lines that merely DISCUSS the pattern are not offending. Every conversion away from a source-text
    test leaves a docstring saying what it used to read, and a check that flagged those would push
    the next author to delete the explanation rather than keep it. On a file that parses, the check
    is made on Call nodes (a comment, a docstring or a string literal holding the text is not a call,
    while a continuation line of a multi-line ``assert`` is), with ``getsource``/``open``/``ast.parse``
    resolved through the file's imports (``from inspect import getsource as gs``). A file that does
    not parse falls back to the line heuristic, which skips quote-led lines.
    """
    try:
        source, tree = parse_source(path)
    except SourceError:
        try:
            source = read_source(path)
        except SourceError:
            source = Path(path).read_bytes().decode("utf-8", errors="replace")
        lines = source.splitlines()
        hits = _line_offending(lines, extra_patterns)
    else:
        lines = source.splitlines()
        hits = _ast_offending(tree, lines, extra_patterns)
    return [(n, lines[n - 1].strip()) for n in sorted(hits) if n - 1 < len(lines)]
