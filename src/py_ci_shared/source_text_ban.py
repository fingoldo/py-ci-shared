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

import re
from pathlib import Path

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
#: then a reputation.
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
        if not match or not READ_CALL.search(line):
            continue
        if PY_PATH_ON_LINE.search(line) or any(re.search(rf"\b{re.escape(n)}\b", line) for n in names):
            names.add(match.group(1))
    return names


def offending_lines(path: Path, *, extra_patterns: tuple[re.Pattern[str], ...] = ()) -> list[tuple[int, str]]:
    """Lines in *path* that READ Python source, as `(line number, stripped line)`.

    Lines that merely DISCUSS the pattern are not offending. Every conversion away from a source-text
    test leaves a docstring saying what it used to read, and a check that flagged those would push
    the next author to delete the explanation rather than keep it -- so the patterns require the
    call's own `(`, which a mention does not have.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    py_names = _binds_python_source(lines)

    out: list[tuple[int, str]] = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(_COMMENT_OR_PROSE):
            continue
        offending = bool(GETSOURCE.search(line)) or any(p.search(line) for p in extra_patterns)
        if not offending and READ_CALL.search(line):
            names_python_source = bool(PY_PATH_ON_LINE.search(line)) or any(re.search(rf"\b{re.escape(name)}\b", line) for name in py_names)
            offending = names_python_source and not NON_PY_LITERAL.search(line)
        if offending:
            out.append((number, stripped))
    return out
