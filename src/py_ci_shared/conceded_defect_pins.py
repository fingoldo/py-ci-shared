"""Tests that say the behaviour is wrong and then pin it exactly: a reading list, not a gate.

A test's docstring that concedes the behaviour is degenerate - "does not perfectly reconstruct", "is a no-op", "lossy by
design" - while its body asserts exact equality to that behaviour turns a known defect into a contract: fixing the defect
turns the test red, and the fix gets reverted to make it green. Seven such tests guarded four real defects in one audit.

The phrases are common in honest tests too (a docstring explaining WHY a value is lossy while asserting it is within a
bound), so this is a report with a count ratchet, not a failure per site. The companion convention: a deliberately pinned
known defect lives in a test named ``test_known_defect_<id>_...``, which the scan accepts, and whose id an open audit
finding names.

Usage from a repository's meta tests::

    from py_ci_shared.conceded_defect_pins import find_conceded_defect_pins

    def test_conceded_defect_pins_do_not_grow():
        assert len(find_conceded_defect_pins(TEST_FILES, REPO_ROOT)) <= RECORDED_COUNT
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

from ._core import UnparsedFilesError, scan_python

__all__ = ["ConcededPin", "CONCESSION_RE", "find_conceded_defect_pins"]

CONCESSION_RE = re.compile(
    r"does not perfectly|degenerates?\b|is inert|\bno-op\b|\blossy\b|by design|not renormal|known (?:bug|defect)",
    re.IGNORECASE,
)
_KNOWN_DEFECT_PREFIX = "test_known_defect_"


class ConcededPin:
    """One test that concedes a defect and pins it: where it is, what it concedes, and its first exact assertion."""

    __slots__ = ("function", "lineno", "path", "phrase")

    def __init__(self, path: str, function: str, lineno: int, phrase: str) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.phrase = phrase

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.function} concedes '{self.phrase}' and pins it exactly"


def _is_exact_pin(node: ast.AST) -> bool:
    """An assertion of exact equality: ``assert a == b``, ``np.testing.assert_array_equal``, ``assert_allclose(..., rtol=0,
    atol=0)``, or ``pytest.approx`` with no tolerance."""
    if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
        return any(isinstance(op, ast.Eq) for op in node.test.ops)
    if isinstance(node, ast.Call):
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name in {"assert_array_equal", "assert_equal", "assertEqual"}:
            return True
        if name == "assert_allclose":
            kw = {k.arg: k.value for k in node.keywords}
            values = [kw.get(t) for t in ("rtol", "atol")]
            return all(isinstance(v, ast.Constant) and v.value == 0 for v in values)
    return False


def _comment_lines_above(source_lines: list[str], lineno: int, span: int = 3) -> str:
    """The comment lines directly above ``lineno`` (1-based), which carry a concession as often as the docstring does."""
    out: list[str] = []
    i = lineno - 2
    while i >= 0 and len(out) < span and source_lines[i].lstrip().startswith("#"):
        out.append(source_lines[i])
        i -= 1
    return "\n".join(out)


def find_conceded_defect_pins(files: Iterable[Path], repo_root: Path) -> list[ConcededPin]:
    """Every test function whose docstring or leading comment concedes a defect and whose body pins a value exactly.

    A file that cannot be read or parsed raises ``_core.UnparsedFilesError`` (an ``AssertionError``) rather than being
    skipped: a count ratchet over a file it never read would under-count and pass.
    """
    out: list[ConcededPin] = []
    scan = scan_python([Path(p) for p in files], root=Path(repo_root), min_files=0)
    if scan.unparsed:
        raise UnparsedFilesError("could not read or parse, so conceded pins in them were not counted:\n  " + "\n  ".join(p.render() for p in scan.unparsed))
    for parsed in scan:
        tree, rel = parsed.tree, parsed.rel
        lines = parsed.source.splitlines()
        for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            if not func.name.startswith("test_") or func.name.startswith(_KNOWN_DEFECT_PREFIX):
                continue
            prose = (ast.get_docstring(func) or "") + "\n" + _comment_lines_above(lines, func.lineno)
            match = CONCESSION_RE.search(prose)
            if match is None:
                continue
            if any(_is_exact_pin(n) for n in ast.walk(func)):
                out.append(ConcededPin(rel, func.name, func.lineno, match.group(0)))
    return sorted(out, key=lambda c: (c.path, c.lineno))
