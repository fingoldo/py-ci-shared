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

Usage (a consuming repo's ``tests/test_meta/test_comments_name_real_things.py``)::

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
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable
from pathlib import Path

# A backticked token. Kept narrow on purpose: identifiers, dotted members, a trailing "(" and test-file
# names; anything with spaces, operators or quotes is prose in code font, not a reference.
_BACKTICK_RE = re.compile(r"`([^`\n]{1,120})`")
_TEST_FILE_RE = re.compile(r"^(?:[\w/\\.-]*?)(\w+_test\.dart|test_\w+\.py|\w+_test\.py)$")
_IDENT_RE = re.compile(r"^(?P<head>[A-Za-z_]\w*)(?:\.(?P<member>[A-Za-z_]\w*))?(?P<call>\(\)?)?$")
# The numeric-claim shape: "the TWO known ..." / "four exceptions" / "3 cases", then a numbered list.
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_COUNT_CLAIM_RE = re.compile(
    r"\b(?:the\s+)?(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:known|deliberate|named|remaining|documented|distinct)?\s*"
    r"(?:exceptions?|cases?|forms?|reasons?|rules?|steps?|classes?|sites?|files?|kinds?|checks?|guards?)\b",
    re.IGNORECASE,
)
_NUMBERED_ITEM_RE = re.compile(r"^\s*(?:#|//|///|\*|\"\"\")?\s*(\d+)[.)]\s+\S")

# Words that appear in backticks constantly and are never a code reference in the repos this serves:
# language keywords and literals, common type names, and the punctuation-ish tokens people quote.
_NEVER_REFERENCES = frozenset(
    {
        "true", "false", "null", "None", "True", "False", "self", "this", "super", "async", "await", "const",
        "final", "static", "var", "let", "def", "class", "return", "yield", "import", "from", "as", "is", "in",
        "int", "str", "bool", "float", "list", "dict", "set", "tuple", "bytes", "object", "Any", "String",
        "double", "num", "void", "dynamic", "List", "Map", "Set", "Future", "Stream", "Object", "Widget",
        "Text", "Row", "Column", "Center", "Padding", "SizedBox", "Expanded", "Flexible", "Semantics",
        "Color", "Duration", "Size", "Offset", "Rect", "Key", "BuildContext", "State", "Exception",
        "Error", "Iterable", "Optional", "Union", "Literal", "Path", "Field", "BaseModel", "dataclass",
        "pytest", "flutter", "dart", "python", "json", "yaml", "toml", "csv", "jsonl", "utf-8", "ascii",
        "e", "x", "y", "n", "i", "j", "k", "s", "r", "p", "q", "t", "a", "b", "c", "d", "f", "g", "m", "v", "w",
        # JSON/JS literals people quote.
        "NaN", "Infinity", "undefined",
    }
)
# A dotted token whose tail is a file extension is a file name, which is `phantom_markdown_links`' territory.
_FILE_EXT_RE = re.compile(r"\.(?:py|md|json|jsonl|csv|tsv|txt|xml|yaml|yml|toml|cfg|ini|gz|zip|sql|db|html|css|js|dart|arb|sh|ps1|log|pdf|png|svg)$", re.IGNORECASE)


def python_declarations(files: Iterable[Path]) -> set[str]:
    """Every class, function, method (as ``Class.member``), module-level name and module stem declared in
    ``files``, via ``ast`` - never a regex over source."""
    names: set[str] = set()
    for path in files:
        if path.suffix != ".py":
            continue
        names.add(path.stem)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names.add(node.name)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.add(f"{node.name}.{child.name}")
                        names.add(child.name)
                    elif isinstance(child, ast.Assign):
                        for target in child.targets:
                            if isinstance(target, ast.Name):
                                names.add(f"{node.name}.{target.id}")
                                names.add(target.id)
                    elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                        names.add(f"{node.name}.{child.target.id}")
                        names.add(child.target.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in _DART_DECL_RE.finditer(source):
            for group in m.groups():
                if group:
                    names.add(group)
    return names


def _comment_lines(path: Path) -> list[tuple[int, str]]:
    """``(lineno, text)`` for every line that is a comment or lies inside a Python docstring."""
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    out: list[tuple[int, str]] = []
    if path.suffix == ".py":
        in_doc = False
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            quotes = stripped.count('"""') + stripped.count("'''")
            if in_doc:
                out.append((lineno, line))
                if quotes % 2 == 1:
                    in_doc = False
                continue
            if stripped.startswith(("'''", '"""')) or (quotes and stripped.startswith(("r'''", 'r"""'))):
                out.append((lineno, line))
                if quotes % 2 == 1:
                    in_doc = True
                continue
            if "#" in line:
                out.append((lineno, line[line.index("#") :]))
    else:
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith(("//", "///", "/*", "*")):
                out.append((lineno, stripped))
    return out


def find_phantom_code_references(
    files: Iterable[Path], repo_root: Path, declared: set[str], *, extra_known: Iterable[str] = ()
) -> list[str]:
    """``"<rel>:<line>: `<token>` names nothing in this repo"`` for every backticked reference in a comment
    or docstring that resolves to neither a test file under ``repo_root`` nor a declared name.

    Dotted tokens resolve if the whole ``Class.member`` is declared OR the head alone is (a member of a
    library class - ``Text.rich``, ``pytest.mark`` - is not this repo's to declare). A trailing ``()`` or
    ``(`` is stripped. Tokens containing anything but identifier characters, dots and a call suffix are
    prose and skipped."""
    import builtins
    import sys

    files = list(files)
    # Python's own names resolve without a declaration: builtins (`ValueError`) and stdlib modules (`ftplib.FTP`).
    known = set(declared) | set(extra_known) | _NEVER_REFERENCES | set(dir(builtins)) | set(getattr(sys, "stdlib_module_names", ()))
    test_files = {p.name for p in repo_root.rglob("*") if _TEST_FILE_RE.match(p.name)}
    violations: list[str] = []
    for path in files:
        try:
            rel = str(path.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            rel = str(path)
        for lineno, text in _comment_lines(path):
            for m in _BACKTICK_RE.finditer(text):
                token = m.group(1).strip()
                tf = _TEST_FILE_RE.match(token)
                if tf:
                    if tf.group(1) not in test_files:
                        violations.append(f"{rel}:{lineno}: `{token}` names a test file that does not exist")
                    continue
                if _FILE_EXT_RE.search(token) or (token.isupper() and "_" in token):
                    continue  # a file name, or an environment variable / another system's constant
                im = _IDENT_RE.match(token)
                if not im:
                    continue
                head, member = im.group("head"), im.group("member")
                if head in known and (member is None or f"{head}.{member}" in known or member in known):
                    continue
                if head in known:
                    # A declared head with an undeclared member is most often a library member; resolving
                    # library APIs is out of scope, so the head carries the claim.
                    continue
                # A lowercase bare identifier in backticks is a parameter, a keyword argument or a local far more
                # often than a claim about a declared function; only a call suffix or a dotted member makes it one.
                if head[0].islower() and member is None and not im.group("call"):
                    continue
                violations.append(f"{rel}:{lineno}: `{token}` names nothing declared in this repo")
    return violations


def count_claim_mismatches(files: Iterable[Path]) -> list[str]:
    """``"<file>:<line>: says N ..., lists M"`` for every comment block that claims a count ("the TWO
    known exceptions", "four cases") and then enumerates a numbered list of a different length within
    the next 60 comment lines. Only that exact shape is checked: a number word with no list under it is
    prose and passes."""
    violations: list[str] = []
    for path in files:
        lines = _comment_lines(path)
        by_index = {i: (ln, txt) for i, (ln, txt) in enumerate(lines)}
        for i, (lineno, text) in enumerate(lines):
            m = _COUNT_CLAIM_RE.search(text)
            if not m or not text.rstrip().endswith(":"):
                continue
            raw = m.group("n").lower()
            claimed = int(raw) if raw.isdigit() else _WORD_NUMBERS[raw]
            seen: list[int] = []
            j = i + 1
            while j in by_index and j < i + 60:
                ln, txt = by_index[j]
                if ln != lines[j - 1][0] + 1:
                    break  # the comment block ended
                item = _NUMBERED_ITEM_RE.match(txt)
                if item:
                    number = int(item.group(1))
                    if seen and number != seen[-1] + 1:
                        break
                    if not seen and number != 1:
                        break
                    seen.append(number)
                elif seen and not txt.strip(" /#*"):
                    break
                j += 1
            if seen and len(seen) != claimed:
                violations.append(f"{path}:{lineno}: says {claimed}, lists {len(seen)}")
    return violations


def assert_no_phantom_code_references(
    files: Iterable[Path],
    repo_root: Path,
    declared: set[str],
    *,
    baseline_path: Path | None = None,
    extra_known: Iterable[str] = (),
) -> None:
    """Fail on any phantom reference not in the committed baseline; also fail when a baseline entry is no
    longer reproduced (the debt was paid - prune it), so the baseline only ever shrinks."""
    import pytest

    violations = set(find_phantom_code_references(files, repo_root, declared, extra_known=extra_known))
    baseline: set[str] = set()
    if baseline_path is not None and baseline_path.exists():
        baseline = set(json.loads(baseline_path.read_text(encoding="utf-8"))["phantom_references"])
    new = sorted(violations - baseline)
    stale = sorted(baseline - violations)
    problems = []
    if new:
        problems.append("comments naming things that do not exist (fix the comment, do not extend the baseline):\n  " + "\n  ".join(new))
    if stale:
        problems.append(f"baseline entries no longer reproduced - remove from {baseline_path.name if baseline_path else 'baseline'}:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))


def assert_no_count_claim_mismatches(files: Iterable[Path]) -> None:
    """Fail on any "the N ..." claim whose numbered list has a different length. No baseline: a wrong count
    is unconditionally wrong."""
    import pytest

    violations = count_claim_mismatches(files)
    if violations:
        pytest.fail("comments whose stated count disagrees with the list under it:\n  " + "\n  ".join(violations))
