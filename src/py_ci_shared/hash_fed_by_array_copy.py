"""An array must not be copied just to be hashed.

``h.update(a.tobytes())`` allocates a second full copy of the array purely to feed the hash.
``h.update(np.ascontiguousarray(a).data)`` hands the hash the existing buffer instead, and produces the
identical digest -- ``tobytes()`` serialises in C order, which is what ``ascontiguousarray`` guarantees.

There is nothing to weigh at any site: the copy-free form is never worse, so this is a mechanical rewrite
rather than a judgement. What makes it worth gating is scale. The sites that motivated it hashed whole
training frames -- a KeyBank fingerprint over ``X_train``, a collinearity cache key over the feature matrix,
an RFECV signature over X and y -- on data this kind of code sizes in the tens of gigabytes, and the copy is
paid on every cache lookup.

Two shapes are reported: the incremental ``h.update(a.tobytes())`` and the one-shot
``hashlib.blake2b(a.tobytes(), ...)``.

Deliberately NOT reported: ``hash(a.tobytes())`` and ``a.tobytes()`` used as a dict key. Python's ``hash``
needs a hashable object and a memoryview is not one, so the rewrite does not apply there; flagging it would
send a reader to a change that cannot be made. Nor is a concatenation like ``head.tobytes() + b"|" +
tail.tobytes()`` reported -- a buffer read cannot be joined with ``+``, and the fix is to feed the hash
incrementally, which is a restructuring rather than a substitution.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = [
    "Finding",
    "assert_no_hash_fed_by_array_copy",
    "find_hashes_fed_by_array_copy",
]

# Names that take the bytes to be hashed as their first positional argument.
_HASH_CONSTRUCTORS = frozenset({"sha256", "sha512", "sha1", "md5", "blake2b", "blake2s", "new"})


class Finding:
    """One array copy made only to feed a hash."""

    def __init__(self, path: Path, lineno: int, source: str) -> None:
        """Record the site."""
        self.path = path
        self.lineno = lineno
        self.source = source

    def __str__(self) -> str:
        """Render as ``path:line  expression``, the form an editor can jump to."""
        return f"{self.path.as_posix()}:{self.lineno}  {self.source}"


def _is_tobytes_call(node: ast.AST) -> bool:
    """Whether the node is an ``x.tobytes()`` call."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "tobytes" and not node.args


def _hash_sink_arguments(node: ast.Call) -> list[ast.expr]:
    """The arguments of ``node`` that are being hashed, or an empty list when it is not a hash sink."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return []
    if func.attr == "update":
        return list(node.args)
    if func.attr in _HASH_CONSTRUCTORS:
        return list(node.args[:1])
    return []


class _Visitor(ast.NodeVisitor):
    """Collects every hash call whose input is a fresh array copy."""

    def __init__(self, path: Path) -> None:
        """Start with no findings for ``path``."""
        self.path = path
        self.findings: list[Finding] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Flag a hash sink handed the result of ``.tobytes()``."""
        for argument in _hash_sink_arguments(node):
            if _is_tobytes_call(argument):
                try:
                    rendered = ast.unparse(node)
                except Exception:
                    rendered = "<unrenderable>"
                self.findings.append(Finding(self.path, node.lineno, rendered[:160]))
                break
        self.generic_visit(node)


def find_hashes_fed_by_array_copy(roots: Sequence[Path], exclude: Iterable[str] = ()) -> list[Finding]:
    """Scan every ``*.py`` under ``roots`` and return each hash call fed by a fresh array copy."""
    excluded = tuple(exclude)
    findings: list[Finding] = []
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            if any(fragment in path.as_posix() for fragment in excluded):
                continue
            try:
                tree = ast.parse(path.read_bytes().decode("utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            visitor = _Visitor(path)
            visitor.visit(tree)
            findings.extend(visitor.findings)
    return findings


def assert_no_hash_fed_by_array_copy(roots: Sequence[Path], exclude: Iterable[str] = (), allow: Iterable[str] = ()) -> None:
    """Raise ``AssertionError`` listing every hash fed by an array copy that is not explicitly allowed.

    ``allow`` holds ``path:line`` strings. It should stay empty: the rewrite is a substitution with an
    identical digest, so a site that needs allowing is usually a site where the value is not going into a
    hash at all, which this check already excludes.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    findings = [f for f in find_hashes_fed_by_array_copy(roots, exclude) if f"{f.path.as_posix()}:{f.lineno}" not in allowed]
    if not findings:
        return
    newline = chr(10)
    listing = (newline + "  ").join(str(f) for f in findings)
    raise AssertionError(
        newline.join(
            [
                f"{len(findings)} hash(es) fed by a full copy of an array.",
                "  `.tobytes()` allocates a second copy of the whole array purely to be hashed. Feed the buffer:",
                "      h.update(np.ascontiguousarray(a).data)",
                "  The digest is identical -- tobytes() serialises in C order, which ascontiguousarray guarantees.",
                f"  {listing}",
            ]
        )
    )
