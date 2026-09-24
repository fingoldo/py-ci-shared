"""An array must not be copied just to be hashed.

``h.update(a.tobytes())`` allocates a second full copy of the array purely to feed the hash.
``h.update(np.ascontiguousarray(a).view(np.uint8).data)`` hands the hash the existing buffer instead, and
produces the identical digest -- ``tobytes()`` serialises in C order, which is what ``ascontiguousarray``
guarantees.

THE ``uint8`` VIEW IS NOT DECORATION, and this docstring used to omit it. ``.data`` on a ``datetime64`` or
``timedelta64`` array raises ``ValueError: cannot include dtype 'M' in a buffer``: those dtypes have no
buffer-protocol format. So the shorter form breaks on two dtypes that are ordinary in exactly the
time-series data these hashes are computed over, and it breaks at the call site rather than in review.
Viewing the contiguous buffer as raw bytes first works for every dtype, including 0-d, empty, structured
and fixed-width string arrays. Found on pyutilz, whose array hasher reduces datetime64 through int64 on
purpose and would have started raising on the rewrite this module recommends.

With that form there is nothing to weigh at any site: the copy-free version is never wrong, so this is a
mechanical rewrite rather than a judgement. On small arrays the two are within noise of each other -- the
buffer setup costs about as much as a tiny copy -- so the win is in avoiding the transient allocation and
shows up as time only once the array is large (measured 1.3x on a 64 MB frame). What makes it worth gating is scale. The sites that motivated it hashed whole
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
from typing import Optional

from ._core import DEFAULT_EXCLUDE, ImportAliases, ScanResult, iter_files, scan_python

__all__ = [
    "Finding",
    "assert_no_hash_fed_by_array_copy",
    "find_hashes_fed_by_array_copy",
]

# Names that take the bytes to be hashed as their first positional argument (or ``data=``).
_HASH_CONSTRUCTORS = frozenset({"sha256", "sha512", "sha1", "md5", "blake2b", "blake2s", "sha224", "sha384", "sha3_256", "sha3_512", "new"})


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
    """Whether the node is an ``x.tobytes()`` call whose bytes the buffer rewrite reproduces.

    ``tobytes(order="F")`` (or ``"A"``/``"K"``) serialises in another order, so the C-order buffer would change the
    digest; only no arguments or an explicit ``order="C"`` is the plain copy this check is about.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "tobytes"):
        return False
    if len(node.args) == 1 and not node.keywords:
        return isinstance(node.args[0], ast.Constant) and node.args[0].value == "C"
    if node.args:
        return False
    return all(kw.arg == "order" and isinstance(kw.value, ast.Constant) and kw.value.value == "C" for kw in node.keywords)


def _hash_sink_arguments(node: ast.Call, aliases: Optional[ImportAliases] = None) -> list[ast.expr]:
    """The arguments of ``node`` that are being hashed, or an empty list when it is not a hash sink.

    ``h.update(x)``; a constructor however it is reached (``hashlib.sha256(x)``, ``sha256(x)`` after ``from hashlib
    import sha256``, an alias); ``hashlib.new(name, x)``, whose data is the SECOND argument; and ``data=x`` on either.
    """
    func = node.func
    data_kw = [kw.value for kw in node.keywords if kw.arg == "data"]
    if isinstance(func, ast.Attribute) and func.attr == "update":
        return list(node.args)
    name: Optional[str] = None
    qualified = aliases.qualified_name(func) if aliases is not None else None
    if qualified and qualified.startswith("hashlib."):
        name = qualified.rsplit(".", 1)[1]
    elif isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name) and func.id != "new":
        name = func.id
    if name not in _HASH_CONSTRUCTORS:
        return []
    if name == "new":
        return list(node.args[1:2]) + data_kw
    return list(node.args[:1]) + data_kw


class _Visitor(ast.NodeVisitor):
    """Collects every hash call whose input is a fresh array copy."""

    def __init__(self, path: Path, aliases: Optional[ImportAliases] = None) -> None:
        """Start with no findings for ``path``."""
        self.path = path
        self.aliases = aliases
        self.findings: list[Finding] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Flag a hash sink handed the result of ``.tobytes()``."""
        for argument in _hash_sink_arguments(node, self.aliases):
            if _is_tobytes_call(argument):
                try:
                    rendered = ast.unparse(node)
                except Exception:
                    rendered = "<unrenderable>"
                self.findings.append(Finding(self.path, node.lineno, rendered[:160]))
                break
        self.generic_visit(node)


def _scan(roots: Sequence[Path], exclude: Iterable[str]) -> ScanResult:
    excluded = tuple(exclude)
    files: list[Path] = []
    for root in roots:
        files.extend(p for p in iter_files(Path(root), ("*.py",), exclude=DEFAULT_EXCLUDE) if not any(fragment in p.as_posix() for fragment in excluded))
    return scan_python(files)


def _findings(scan: ScanResult) -> list[Finding]:
    findings: list[Finding] = []
    for parsed in scan:
        visitor = _Visitor(parsed.path, ImportAliases.from_tree(parsed.tree))
        visitor.visit(parsed.tree)
        findings.extend(visitor.findings)
    return findings


def find_hashes_fed_by_array_copy(roots: Sequence[Path], exclude: Iterable[str] = ()) -> list[Finding]:
    """Scan every ``*.py`` under ``roots`` and return each hash call fed by a fresh array copy. A missing root raises
    ``py_ci_shared._core.CorpusError``; unparsable files are not in this list, and the assert fails on them."""
    return _findings(_scan(roots, exclude))


def assert_no_hash_fed_by_array_copy(roots: Sequence[Path], exclude: Iterable[str] = (), allow: Iterable[str] = (), *, min_files: int = 1) -> None:
    """Raise ``AssertionError`` listing every hash fed by an array copy that is not explicitly allowed, every file that
    could not be parsed, and a scan with fewer than *min_files* parsed files.

    ``allow`` holds ``path:line`` strings. It should stay empty: the rewrite is a substitution with an
    identical digest, so a site that needs allowing is usually a site where the value is not going into a
    hash at all, which this check already excludes.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    scan = _scan(roots, exclude)
    scan.min_files = min_files
    newline = chr(10)
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if scan.unparsed:
        problems.append(
            f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked:" + "".join(f"{newline}  {u.render()}" for u in scan.unparsed)
        )
    findings = [f for f in _findings(scan) if f"{f.path.as_posix()}:{f.lineno}" not in allowed]
    if findings:
        listing = (newline + "  ").join(str(f) for f in findings)
        problems.append(
            newline.join(
                [
                    f"{len(findings)} hash(es) fed by a full copy of an array.",
                    "  `.tobytes()` allocates a second copy of the whole array purely to be hashed. Feed the buffer:",
                    "      h.update(np.ascontiguousarray(a).view(np.uint8).data)",
                    "  The digest is identical -- tobytes() serialises in C order, which ascontiguousarray guarantees.",
                    "  The uint8 view is required, not cosmetic: .data alone raises on datetime64/timedelta64,",
                    "  which have no buffer-protocol format.",
                    f"  {listing}",
                ]
            )
        )
    if problems:
        raise AssertionError(newline.join(problems))
