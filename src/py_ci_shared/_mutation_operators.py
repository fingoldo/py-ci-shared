"""Mutation operators of :mod:`py_ci_shared.mutation_teeth`: what a token may become, and where.

Split out of ``mutation_teeth`` so each part stays readable; everything here is re-exported from there.
Each operator takes the source and, optionally, its already-parsed tree: :func:`generate_mutants` parses
a target once and hands the same tree to every operator.
"""

from __future__ import annotations

import ast
import bisect
import hashlib
import io
import re
import tokenize
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._mutation_model import _CONTAINER_SAMPLE, Mutant, MutationHarnessError, read_target

Candidate = tuple[int, int, int, str, str, str]


def _tree_of(source: str, tree: Optional[ast.AST]) -> Optional[ast.AST]:
    """*tree* when given, else the parse of *source*, else ``None`` for a source that does not parse."""
    if tree is not None:
        return tree
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


# ── what a single token may become ──────────────────────────────────────────────────────────────
_OP_SWAP = {
    ">": (">=", "> becomes >="),
    ">=": (">", ">= becomes >"),
    "<": ("<=", "< becomes <="),
    "<=": ("<", "<= becomes <"),
    "==": ("!=", "== becomes !="),
    "!=": ("==", "!= becomes =="),
    "+": ("-", "+ becomes -"),
    "-": ("+", "- becomes +"),
    "*": ("/", "* becomes /"),
    "/": ("*", "/ becomes *"),
    # Each of these is a single OP token and none of them was in the table, so a whole family of
    # real edits could not be expressed. `+=` -> `=` on an accumulator is the exact shape of the
    # attachment-budget defect this harness was pointed at: the running total stops accumulating
    # and every item is measured against the full budget.
    "//": ("/", "// becomes /"),
    "%": ("*", "% becomes *"),
    "**": ("*", "** becomes *"),
    "+=": ("=", "+= becomes ="),
    "-=": ("=", "-= becomes ="),
    "*=": ("=", "*= becomes ="),
    "|=": ("=", "|= becomes ="),
    "&=": ("=", "&= becomes ="),
}
_NAME_SWAP = {
    "and": ("or", "and becomes or"),
    "or": ("and", "or becomes and"),
    "True": ("False", "True becomes False"),
    "False": ("True", "False becomes True"),
    "is": ("is not", "is becomes is not"),
    "in": ("not in", "in becomes not in"),
    # Cap-enforcement loops end with one of these, and swapping them is the difference between
    # "skip this item" and "stop processing items" -- the same class as the off-by-one at a cap.
    "continue": ("break", "continue becomes break"),
    "break": ("continue", "break becomes continue"),
    "max": ("min", "max becomes min"),
    "min": ("max", "min becomes max"),
    "any": ("all", "any becomes all"),
    "all": ("any", "all becomes any"),
}


def _has_constant(node: ast.AST | None) -> bool:
    """Does this annotation carry a literal INSIDE a subscript? Then it can carry a real bound.

    The distinction is not "contains a literal". A bare string annotation is a forward reference
    and, under ``from __future__ import annotations``, is never evaluated at all -- mutating it is
    provably unkillable, which is why annotations were excluded in the first place. A literal inside
    a subscript is a different animal: `Literal["draft", "sent"]` and `Annotated[int, Field(ge=1)]`
    are read by pydantic when the model is built and enforced on every instance, so a moved bound
    there is a defect a test can and does catch.
    """
    if node is None:
        return False
    return any(
        isinstance(sub, ast.Subscript) and any(isinstance(inner, ast.Constant) and inner.value is not None for inner in ast.walk(sub.slice))
        for sub in ast.walk(node)
    )


#: Families whose mutation can change the SYNTAX, and therefore have to be parsed before being
#: emitted. Both substitute a token for another token of the same class, in positions where that
#: class carries structure -- `*` inside a signature becoming `/`, a guard gaining a `not`.
#:
#: Everything else swaps a COMPLETE expression or literal for another, or replaces a statement with
#: `pass`, and cannot produce a syntax error. Re-parsing the whole file for those was 45% of
#: generation time (11.97s of a 26.4s profile; `models.py` alone 7.35s for 696 candidates, of which
#: 21 could fail).
#: Removing a `not` is in the set too: `not` can sit directly against its operand, and whatever follows it
#: decides whether what remains is still an expression.
_SYNTAX_RISKY_PREFIXES = ("logic:", "operator:", "comparison:", "dropped a `not`")


def _line_starts(source: str) -> list[int]:
    """Absolute character index of each line's first character.

    Split on ``"\\n"`` only. ``str.splitlines`` also breaks on ``\\x0c``, ``\\x85`` and ``\\u2028``,
    which Python's tokenizer does not -- a file containing any of them desynchronised line numbers
    and produced ZERO candidates, which reads exactly like "nothing here to check".
    """
    starts, index = [0], 0
    for line in source.split("\n")[:-1]:
        index += len(line) + 1
        starts.append(index)
    return starts


def _abs_index(source: str, starts: list[int], line: int, col: int) -> int:
    """Absolute character index of a BYTE column on a 1-based line.

    ``ast`` reports columns as UTF-8 byte offsets; the source is a ``str``. Converting through the
    line's encoded bytes is the entire point: without it, one em dash or one Cyrillic letter earlier
    on the line moves every edit made after it.
    """
    if line - 1 >= len(starts):
        return len(source)
    start = starts[line - 1]
    end = starts[line] if line < len(starts) else len(source)
    prefix = source[start:end].encode("utf-8")[:col].decode("utf-8", errors="ignore")
    return start + len(prefix)


def _excluded_ranges(source: str, tree: Optional[ast.AST] = None) -> list[tuple[int, int]]:
    """Character ranges whose contents must never be mutated, because no test can observe them.

    Docstrings (module, class, function), any other bare string EXPRESSION statement (a section
    banner or a PEP-258 attribute docstring is as unobservable as a real one), every annotation
    (with ``from __future__ import annotations`` they are never evaluated, so a forward-reference
    string mutant is provably equivalent), and ``__all__`` entries.

    ``__all__`` is excluded unconditionally. It costs the ability to catch a test asserting the
    public surface; that is inventory, already covered by ``phantom_code_references`` and
    ``docs_inventory_parity``, and it was 18 of 293 candidates on the measured sample -- eight of
    them filling the first slots of a truncated run.
    """
    tree = _tree_of(source, tree)
    if tree is None:
        return []
    starts = _line_starts(source)

    def span(node: ast.AST | None) -> tuple[int, int] | None:
        line = getattr(node, "lineno", None)
        end_line = getattr(node, "end_lineno", None)
        if node is None or line is None or end_line is None:
            return None
        return (
            _abs_index(source, starts, line, getattr(node, "col_offset", 0)),
            _abs_index(source, starts, end_line, getattr(node, "end_col_offset", 0)),
        )

    out: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        # Any bare string statement, not only the first one in a body.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if (s := span(node)) is not None:
                out.append(s)
        elif isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets) and (s := span(node.value)) is not None:
                out.append(s)
        elif isinstance(node, ast.AnnAssign):
            # Only annotations with nothing mutable in them. `Literal["draft", "sent"]` and
            # `Annotated[int, Field(ge=1, le=5)]` are ENFORCED at runtime by pydantic, and tests
            # observe the enforcement -- excluding them wholesale hid a bound that a mutation can
            # really move. A bare `int` or `str | None` contains no constant, so excluding it costs
            # nothing.
            if not _has_constant(node.annotation) and (s := span(node.annotation)) is not None:
                out.append(s)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in [*args.args, *args.posonlyargs, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is None or arg.annotation is None or _has_constant(arg.annotation):
                    continue
                if (s := span(arg.annotation)) is not None:
                    out.append(s)
            if (s := span(node.returns)) is not None:
                out.append(s)
    return out


def _container_members(source: str, tree: Optional[ast.AST] = None) -> list[tuple[int, int, str]]:
    """``(start, end, container name)`` for each SUPPRESSED row of a large module-level table."""
    return _container_rows(source, tree)[0]


def _container_rows(source: str, tree: Optional[ast.AST] = None) -> tuple[list[tuple[int, int, str]], dict[str, tuple[int, int]]]:
    """Suppressed rows of every large data table, and ``name -> (rows kept, rows in the table)``.

    Used to SAMPLE rather than exhaust data tables. Rotation is seeded from the file's content hash
    so successive runs cover different rows: a row missed today is missed temporarily, not
    permanently, which is the difference between sampling and a blind spot.

    A ROW is one entry: a dict's key and value together, or one element of a list, set or tuple.
    Counting keys and values separately reported a six-entry dict as "3 of 12" and could keep a
    key while suppressing its own value.
    """
    tree = _tree_of(source, tree)
    if tree is None:
        return [], {}
    starts = _line_starts(source)
    seed = int(hashlib.sha256(source.encode("utf-8")).hexdigest()[:8], 16)
    out: list[tuple[int, int, str]] = []
    totals: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        # AnnAssign as well as Assign: a module constant written `_BANNED: frozenset = {...}` is
        # the SAME data table, and matching only the unannotated form meant the annotated ones were
        # exhausted instead of sampled -- the whole survivor list then fills with rows of one table
        # and nobody reads to the end of it. Typed module constants are the house style here, so
        # this was not an edge case.
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, (ast.Dict, ast.Set, ast.List, ast.Tuple)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        name = next((t.id for t in targets if isinstance(t, ast.Name)), "<container>")
        if name in totals:
            name = f"{name}@{node.lineno}"
        rows: list[tuple[ast.expr, ast.expr]] = []
        if isinstance(node.value, ast.Dict):
            rows = [(k if k is not None else v, v) for k, v in zip(node.value.keys, node.value.values)]
        else:
            rows = [(e, e) for e in node.value.elts]
        if len(rows) <= _CONTAINER_SAMPLE:
            continue
        # A row that CONTAINS A CALL is code, not data. The sampler exists so a lookup table of
        # country codes does not bury the survivor list; applied to a table of `re.compile(...)`
        # guards it would suppress the substance of the module instead of its noise, and the
        # content-hash seed means the same rows would stay suppressed until the file changed. The
        # line is the presence of an expression that DOES something, which is exactly the
        # difference between a data table and a table of behaviour.
        if any(isinstance(sub, ast.Call) for first, last in rows for part in {id(first): first, id(last): last}.values() for sub in ast.walk(part)):
            continue
        keep = {(seed + i) % len(rows) for i in range(_CONTAINER_SAMPLE)}
        totals[name] = (len(keep), len(rows))
        for index, (first, last) in enumerate(rows):
            if index in keep:
                continue
            head = _span_of(source, starts, first)
            tail = _span_of(source, starts, last)
            if head is None or tail is None:  # pragma: no cover - ast always sets these
                continue
            out.append((head[0], tail[1], name))
    out.sort()
    return out, totals


def _span_of(source: str, starts: list[int], node: ast.AST) -> tuple[int, int] | None:
    """Absolute character span of an AST node, or ``None`` when it has no position.

    Every boundary goes through :func:`_abs_index` because ``ast`` reports BYTE columns. Taking
    them as character offsets is the bug that once replaced an entire statement and blamed a line
    it had not touched, so there is no shortcut here even for the end position.
    """
    line = getattr(node, "lineno", None)
    col = getattr(node, "col_offset", None)
    end_line = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    if line is None or col is None or end_line is None or end_col is None:
        return None
    return _abs_index(source, starts, line, col), _abs_index(source, starts, end_line, end_col)


def _argument_transpositions(source: str, tree: Optional[ast.AST] = None) -> list[Candidate]:
    """Swap two adjacent positional arguments of a call.

    The shape this exists for is a format string and its values: ``"%s kept %d of %d", uid, n, cap``
    passes type checking, reads correctly, and lies. Nothing in a token-level operator set can
    express it, because no single token is wrong.

    Adjacent pairs only. Transposing arbitrary pairs would grow quadratically for no extra signal --
    a call whose neighbours are interchangeable is a call whose argument order is unchecked, and one
    demonstration of that per pair is enough.
    """
    tree = _tree_of(source, tree)
    if tree is None:
        return []
    starts = _line_starts(source)
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if any(isinstance(a, ast.Starred) for a in node.args):
            continue  # `*args` has no fixed position to swap
        for left, right in zip(node.args, node.args[1:]):
            left_span = _span_of(source, starts, left)
            right_span = _span_of(source, starts, right)
            if left_span is None or right_span is None or left_span[1] > right_span[0]:
                continue
            left_text = source[left_span[0] : left_span[1]]
            right_text = source[right_span[0] : right_span[1]]
            if left_text == right_text:
                continue  # a no-op, and it would be filtered later anyway
            between = source[left_span[1] : right_span[0]]
            if "#" in between or "\n" in left_text or "\n" in right_text:
                # A comment between the two, or an argument spanning lines: the splice would be
                # correct but the mutant would be unreadable, and a survivor nobody can read is a
                # survivor nobody acts on.
                continue
            out.append(
                (
                    left_span[0],
                    right_span[1],
                    left.lineno,
                    right_text + between + left_text,
                    f"arguments transposed: {left_text} <-> {right_text}",
                    "",
                )
            )
    return out


def _slice_bound_candidates(source: str, tree: Optional[ast.AST] = None) -> list[Candidate]:
    """Remove a slice bound, making the slice unbounded on that side.

    ``text[:limit]`` -> ``text[:]`` is the defect a cap is written to prevent, and the numeric
    operator reached it only when the bound was a literal. Here the bound is usually a name -- a
    configured maximum, a remaining budget -- which is exactly the interesting case.
    """
    tree = _tree_of(source, tree)
    if tree is None:
        return []
    starts = _line_starts(source)
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Slice):
            continue
        for bound, side in ((node.lower, "lower"), (node.upper, "upper")):
            if bound is None or isinstance(bound, ast.Constant):
                continue  # a literal bound is already covered by the numeric operator
            span = _span_of(source, starts, bound)
            if span is None:
                continue
            text = source[span[0] : span[1]]
            if "\n" in text:
                continue
            out.append((span[0], span[1], bound.lineno, "", f"slice: {side} bound {text} removed", ""))
    return out


#: Calls whose string argument is a regular expression. A pattern is only worth perturbing where it
#: is actually used as one -- the same text sitting in a message is prose.
_REGEX_CALLS = {"compile", "match", "search", "fullmatch", "sub", "subn", "split", "findall", "finditer"}

#: Each perturbation WIDENS the pattern, which is the direction a guard fails in: it begins matching
#: what it was written to exclude. A narrowed guard fails loudly the first time it misses something;
#: a widened one just quietly says yes.
_REGEX_WIDENINGS = (
    ("\\b", "", "a word-boundary anchor removed"),
    ("$", "", "the end anchor removed"),
    ("^", "", "the start anchor removed"),
    ("+", "*", "one-or-more became zero-or-more"),
)


def _string_literal_sites(tree: ast.AST) -> list[tuple[ast.Constant, bool]]:
    """Every string constant, paired with whether it is used as a regular expression."""
    sites: list[tuple[ast.Constant, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        regexish = name in _REGEX_CALLS
        sites.extend((arg, regexish) for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
        sites.extend((kw.value, False) for kw in node.keywords if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str))
    return sites


def _substitution_candidates(source: str, tree: Optional[ast.AST] = None) -> list[Candidate]:
    """Replace a string with a confusable neighbour, and widen a regex that guards something."""
    tree = _tree_of(source, tree)
    if tree is None:
        return []
    starts = _line_starts(source)
    sites = _string_literal_sites(tree)
    # The vocabulary is the file's own. Short values only: a long string is prose, and swapping two
    # sentences tests nothing that emptying one does not already test.
    vocabulary = sorted({v for n, _r in sites if isinstance(v := n.value, str) and 1 <= len(v) <= 12 and v.strip()})
    out: list[Candidate] = []
    for node, regexish in sites:
        span = _span_of(source, starts, node)
        if span is None:
            continue
        raw = source[span[0] : span[1]]
        if "\n" in raw:
            continue
        value = node.value if isinstance(node.value, str) else ""
        if not value:
            continue
        if regexish:
            for needle, replacement, description in _REGEX_WIDENINGS:
                if needle not in value:
                    continue
                widened = value.replace(needle, replacement, 1)
                if widened == value:
                    continue
                try:
                    re.compile(widened)
                except re.error:
                    continue  # a perturbation that does not compile is not a mutant
                out.append(
                    (
                        span[0],
                        span[1],
                        node.lineno,
                        raw.replace(needle, replacement, 1),
                        f"regex widened: {description}",
                        "",
                    )
                )
            continue
        if not (1 <= len(value) <= 12):
            continue
        neighbour = _confusable(value, vocabulary)
        if neighbour is None:
            continue
        out.append(
            (
                span[0],
                span[1],
                node.lineno,
                raw.replace(value, neighbour, 1),
                f"constant: {value!r} became {neighbour!r}",
                "",
            )
        )
    return out


def _confusable(value: str, vocabulary: list[str]) -> str | None:
    """The closest OTHER literal in the file, or ``None`` when nothing is close enough.

    Closeness is a shared prefix of at least half the shorter string. `"NFKD"` and `"NFKC"` qualify;
    `"replace"` and `"ignore"` do not, and that is the honest limit of a rule that invents no
    vocabulary -- it finds the pairs an author actually confused, not the ones a thesaurus would.
    """
    best: str | None = None
    best_shared = 0
    for other in vocabulary:
        if other == value:
            continue
        shared = 0
        for a, b in zip(value, other):
            if a != b:
                break
            shared += 1
        if shared > best_shared and shared * 2 >= min(len(value), len(other)):
            best, best_shared = other, shared
    return best


class _Intervals:
    """Containment queries over a set of character ranges in O(log n), instead of a scan per token."""

    def __init__(self, ranges: Iterable[tuple[int, int]]) -> None:
        merged: list[list[int]] = []
        for lo, hi in sorted(ranges):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        self._lo = [m[0] for m in merged]
        self._hi = [m[1] for m in merged]

    def contains(self, a: int, b: int) -> bool:
        i = bisect.bisect_right(self._lo, a) - 1
        return i >= 0 and b <= self._hi[i]


def _string_emptied(token: str) -> Optional[str]:
    """The empty literal of the same TYPE as *token*, or ``None`` when emptying it changes nothing.

    ``b"ab"`` -> ``""`` changed bytes into str, a type error no test needs to be sensitive to, and
    ``r""`` -> ``""`` is the same value spelled differently: a mutant that cannot be killed.
    """
    try:
        value = ast.literal_eval(token)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    if not isinstance(value, (str, bytes)) or not value:
        return None
    return 'b""' if isinstance(value, bytes) else '""'


def _token_candidates(source: str, skip_coupled_constants: bool = False, tree: Optional[ast.AST] = None) -> list[Candidate]:
    """``(abs_start, abs_end, line, replacement, description, category)`` per mutable token.

    Token positions from :mod:`tokenize` are CHARACTER offsets, so no byte conversion is needed
    here -- unlike the AST spans above. Table sampling is applied by :func:`generate_mutants` to
    every operator's candidates alike, so it is not repeated here.
    """
    starts = _line_starts(source)
    tree = _tree_of(source, tree)
    excluded = _Intervals(_excluded_ranges(source, tree) if tree is not None else [])
    coupled = _repr_coupled_lines(source, tree) if skip_coupled_constants and tree is not None else set()

    out: list[Candidate] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source, newline="").readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return []

    for index, tok in enumerate(tokens):
        row, col = tok.start
        if row - 1 >= len(starts):
            continue
        abs_start = starts[row - 1] + col
        abs_end = starts[tok.end[0] - 1] + tok.end[1] if tok.end[0] - 1 < len(starts) else abs_start
        if excluded.contains(abs_start, abs_end):
            continue

        def emit(end: int, replacement: str, description: str, _start: int = abs_start, _row: int = row) -> None:
            out.append((_start, end, _row, replacement, description, ""))

        if tok.type == tokenize.NAME and tok.string == "if" and index + 1 < len(tokens):
            following = tokens[index + 1]
            # `if flag:` -> `if not flag:`. A guard with no comparison and no `not` in it was the
            # one shape with nothing to swap, so an inverted guard -- the most ordinary logic bug
            # there is -- could not be expressed at all.
            if following.type == tokenize.NAME and following.string not in ("not", "None", "True", "False"):
                emit(abs_end, "if not", "logic: guard inverted")

        if tok.type == tokenize.OP and tok.string in _OP_SWAP:
            new, why = _OP_SWAP[tok.string]
            emit(abs_end, new, f"operator: {why}")

        elif tok.type == tokenize.NAME and tok.string in _NAME_SWAP:
            nxt = tokens[index + 1] if index + 1 < len(tokens) else None
            if tok.string == "is" and nxt is not None and nxt.string == "not":
                emit(starts[nxt.end[0] - 1] + nxt.end[1], "is", "comparison: is not becomes is")
                continue
            if tok.string == "in" and index and tokens[index - 1].string == "not":
                continue  # the `not in` pair is emitted by the `not` branch below
            new, why = _NAME_SWAP[tok.string]
            emit(abs_end, new, f"logic: {why}")

        elif tok.type == tokenize.NAME and tok.string == "not":
            nxt = tokens[index + 1] if index + 1 < len(tokens) else None
            if nxt is not None and nxt.string == "in":
                emit(starts[nxt.end[0] - 1] + nxt.end[1], "in", "comparison: not in becomes in")
            else:
                # The `not` and the whitespace up to the next token on the same line, and nothing
                # more. A fixed `+ 1` assumed a space and ate the `(` of `not(x)`, leaving `x)`.
                end = abs_end
                if nxt is not None and nxt.start[0] == row:
                    end = starts[row - 1] + nxt.start[1]
                emit(end, "", "dropped a `not`")

        elif tok.type == tokenize.NUMBER:
            text = tok.string.replace("_", "")
            try:
                value: int | float = int(text, 0)
            except ValueError:
                try:
                    value = float(text)
                except ValueError:
                    continue
            # Floats are mutated too. A threshold is exactly the kind of constant a test should pin,
            # and one of the four cases that motivated this module was a threshold test.
            new_value = value + 1 if isinstance(value, int) else round(value + 0.1, 10)
            emit(abs_end, repr(new_value), f"constant: {value} becomes {new_value}")

        elif tok.type == tokenize.STRING and len(tok.string) > 2:
            if row in coupled:
                continue  # its numeric partner on this line states the same fact; see _repr_coupled_lines
            # f-strings are skipped: emptying one literal part turns `f"{x} and {'q'}"` into
            # concatenated literals where the second interpolation stops being one. That mutant is
            # labelled "emptied a string" and is not one. (On 3.12+ f-strings are FSTRING_* tokens
            # and never reach here; the prefix check covers older tokenisations.)
            if tok.string.lstrip("rRbBuU")[:1] in ("f", "F"):
                continue
            emptied = _string_emptied(tok.string)
            if emptied is not None:
                emit(abs_end, emptied, "constant: emptied a string")

        elif tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1) and tok.string:
            # The literal text BETWEEN interpolations, which on 3.12+ is where most of this
            # project's prose lives: an entire prompt template rendered as one f-string produced
            # mutants only for its `{...}` slots, so the sentences themselves could not be
            # challenged at all. Deleting one segment is not the mutation the branch above rejects
            # -- that one empties a whole f-string token and breaks the interpolations with it.
            # A segment is exactly a run of literal characters, and removing it leaves every
            # `{...}` in place, which is what makes it the same operator as "emptied a string".
            if row in coupled:
                continue
            emit(abs_end, "", "constant: emptied an f-string segment")

    return out


def _statement_call_candidates(source: str, tree: Optional[ast.AST] = None) -> list[Candidate]:
    """Statement-level calls, replaced by ``pass``.

    The operator that catches "the guard exists and nothing invokes it" -- the class behind a
    cleanup function that was defined, exported, tested and never called. Measured over four real
    modules it was the highest-PRECISION operator (6 of 8 informative), which is why the obvious
    "skip logging and metrics calls by callee name" filter is deliberately NOT applied: it would
    remove under one percent of candidates while hiding a class this repo asserts heavily -- 152
    ``caplog`` references, 54 of them asserting record contents, including redaction tests whose
    entire subject is that a log call happened and said the right thing.

    ``await foo()``, ``yield x`` and ``yield from x`` are included. An earlier version matched only
    ``Expr(Call)`` and was therefore blind on every async call site.

    Unlike the token operators, this one replaces a whole STATEMENT, so a multi-line call takes its
    interior comments with it. That is inherent to the unit, and is why it is the only operator
    whose diff can span more than one line.
    """
    tree = _tree_of(source, tree)
    if tree is None:
        return []
    starts = _line_starts(source)
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        inner = node.value
        if isinstance(inner, (ast.Await, ast.YieldFrom)):
            inner = inner.value
        elif isinstance(inner, ast.Yield):
            inner = inner.value if inner.value is not None else inner
        if not isinstance(inner, (ast.Call, ast.Yield)):
            continue
        a = _abs_index(source, starts, node.lineno, node.col_offset)
        b = _abs_index(source, starts, node.end_lineno, node.end_col_offset)  # type: ignore[arg-type]
        out.append((a, b, node.lineno, "pass", "deleted a statement-level call", ""))
    return out


def _repr_coupled_lines(source: str, tree: Optional[ast.AST] = None) -> set[int]:
    """Lines where a numeric constant and a string constant state the same fact.

    ``text[: max_len - 3] + "..."`` is the canonical shape: the ``3`` and the ``"..."`` are two
    spellings of one decision, so mutating both produces two survivors that a reader must think
    about twice to learn one thing.

    Off by default. What it hides is real: the case where the two have drifted apart and only one
    direction is covered -- a truncation that reserves three characters and appends four. The agent
    that proposed it marked it optional for exactly that reason, and this keeps that judgement with
    the caller instead of making it silently.
    """
    tree = _tree_of(source, tree)
    if tree is None:
        return set()
    numbers: dict[int, int] = {}
    strings: dict[int, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or getattr(node, "lineno", None) is None:
            continue
        if isinstance(node.value, bool):
            continue
        if isinstance(node.value, int):
            numbers[node.lineno] = numbers.get(node.lineno, 0) + 1
        elif isinstance(node.value, str) and node.value:
            strings[node.lineno] = strings.get(node.lineno, 0) + 1
    return {line for line in numbers if line in strings}


def _normalise_lines(lines: "Iterable[range] | range | None") -> Optional[list[range]]:
    """*lines* as a list of ranges, materialised ONCE, or ``None`` for "the whole file".

    Materialised because a generator was consumed by the fingerprint and arrived empty at the
    generator of mutants, which then swept the whole file and cached it under the narrow key. An
    empty list stays an empty list: it selects nothing, and is keyed apart from ``None``. Every
    entry must be a ``range``; anything else is rejected here rather than dropped from the key and
    failing later.
    """
    if lines is None:
        return None
    if isinstance(lines, range):
        return [lines]
    if isinstance(lines, (str, bytes)):
        raise TypeError(f"`lines` must be a range or an iterable of ranges, not {type(lines).__name__}")
    out = list(lines)
    bad = [entry for entry in out if not isinstance(entry, range)]
    if bad:
        raise TypeError(f"`lines` must contain only range objects; got {bad[0]!r} ({type(bad[0]).__name__})")
    return out


def _scope_of(lines: Optional[list[range]]) -> Optional[list[list[int]]]:
    """The JSON-able form of normalised *lines* for a fingerprint: ``None`` and ``[]`` stay distinct."""
    return None if lines is None else [[r.start, r.stop] for r in lines]


def generate_mutants(
    path: Union[Path, str],
    lines: "Iterable[range] | range | None" = None,
    limit: Optional[int] = None,
    skip_coupled_constants: bool = False,
) -> tuple[list[Mutant], int, dict[str, tuple[int, int]]]:
    """``(mutants, candidates_total, sampled_containers)`` in SOURCE ORDER.

    *lines* accepts several ranges, because a real commit touches several hunks. A candidate is in
    scope when its span OVERLAPS any of them: testing only the start line skipped an expression that
    began above the changed hunk and extended into it -- the very expression the commit edited.
    ``None`` means the whole file; an empty iterable selects nothing.

    Source order matters when *limit* is set. An earlier version walked the AST breadth-first and
    truncated, so on a 132-candidate file it covered one line near the end and never reached whole
    functions. A truncated run's empty survivor list is indistinguishable from a complete one's,
    which is why the count is returned rather than discarded.

    Raises :class:`MutationHarnessError` for a file that cannot be read or parsed: every AST operator
    would produce nothing for it, and "no mutants" reads like "nothing to check".
    """
    ranges = _normalise_lines(lines)
    source = read_target(path).text
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise MutationHarnessError(f"{path} does not parse ({exc.msg}, line {exc.lineno}), so no mutant of it would mean anything") from exc
    starts = _line_starts(source)
    candidates = (
        _token_candidates(source, skip_coupled_constants, tree)
        + _statement_call_candidates(source, tree)
        + _argument_transpositions(source, tree)
        + _slice_bound_candidates(source, tree)
        + _substitution_candidates(source, tree)
    )
    candidates.sort(key=lambda c: (c[0], c[4]))
    suppressed, table_sizes = _container_rows(source, tree)
    suppressed_starts = [lo for lo, _hi, _n in suppressed]

    def sampled_out(index: int) -> Optional[str]:
        i = bisect.bisect_right(suppressed_starts, index) - 1
        if i >= 0 and index < suppressed[i][1]:
            return suppressed[i][2]
        return None

    def line_of(index: int) -> int:
        return bisect.bisect_right(starts, index)

    mutants: list[Mutant] = []
    sampled: dict[str, tuple[int, int]] = {}
    total = 0
    for abs_start, abs_end, line, replacement, description, category in candidates:
        if ranges is not None:
            end_line = line_of(max(abs_start, abs_end - 1))
            if not any(line <= r.stop - 1 and end_line >= r.start for r in ranges):
                continue
        # Every operator's candidates, not only the token ones: a transposition or a substitution
        # inside a sampled-out row is as much table noise as a swapped operator there.
        table = sampled_out(abs_start)
        if table is not None:
            sampled[table] = table_sizes[table]
            total += 1  # dropped by SAMPLING: a real omission, and the caller must be told
            continue
        mutated = source[:abs_start] + replacement + source[abs_end:]
        # Counted only from here on. A candidate that is a no-op or does not compile is not a
        # mutant that was left out -- there was never anything to run -- and counting it made
        # `candidates_total > len(mutants)` true on files where nothing at all was omitted, which
        # is how a TRUNCATED banner ends up on a report that is complete. A banner that cries wolf
        # is worse than none: it trains the reader past the one line that matters.
        if mutated == source:
            continue
        if description.startswith(_SYNTAX_RISKY_PREFIXES):
            try:
                ast.parse(mutated)
            except SyntaxError:
                continue  # a mutant that does not compile tests nothing
        total += 1
        if limit is not None and len(mutants) >= limit:
            continue
        mutants.append(
            Mutant(
                # Both branches must produce a Path: the first yielded a str, so `.key` raised on
                # any mutant used before `find_surviving_mutants` overwrote the field -- which is
                # every direct caller of `generate_mutants`.
                path=Path(Path(path).name),
                line=line,
                column=abs_start - starts[line - 1],
                description=description,
                original_span=source[abs_start:abs_end],
                mutated_span=replacement,
                category=category,
                context=source[starts[line - 1] : (starts[line] if line < len(starts) else len(source))],
                mutated_file_text=mutated,
            )
        )
    return mutants, total, sampled
