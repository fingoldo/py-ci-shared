"""Shared check: a test that claims to pin a defect must be able to FAIL.

Sibling of :mod:`gate_integrity`. That module answers "a gate declared blocking must be able to
block"; this one answers "a test declared to pin a defect must be able to fail". Both exist because
the same thing keeps happening: a check that exists, is believed, and checks nothing.

WHERE THIS CAME FROM
--------------------
One audit round produced four green tests that were green against the very defect they named, each
read and believed by its author first:

1. The revert script used ``str.replace``, which silently does nothing when it does not match, so
   the defect was never reintroduced and the green run nearly passed for confirmation.
2. An example-extraction regex matched 14 quoted spans instead of 6, so the assertion was true
   regardless of the subject.
3. After that was tightened, the variety still came from a Spanish-language example the hello-word
   list did not recognise.
4. A word-count test chose the one input shape where the old and new thresholds AGREE.

Case 1 is a harness flaw, addressed by :func:`assert_revert_fails_tests`. Cases 2-4 are what
mutation testing finds, addressed by :func:`find_surviving_mutants`.

WHAT THIS CANNOT DO
-------------------
"Is this test meaningful?" is not decidable, and nothing here claims otherwise.

* **Mutation sensitivity is not correctness.** A test that PINS a defect is fully
  mutation-sensitive and completely wrong. The same round found eight such tests, all asserting
  that a missing verdict field should default to a refusal; mutation testing would have defended
  every one.
* **Equivalent mutants are unavoidable.** A change that cannot alter observable behaviour survives
  every test, correctly. Survivors are a reading list, not a verdict.
* **A mutation SCORE is not computed, deliberately.** Measured over four real modules: 79% of
  candidates came from two lookup tables, so the denominator tracks data size rather than logic;
  crash mutants inflate the numerator for free; and at least 20 of 293 were unkillable, putting the
  ceiling below 100% at an unknowable, module-dependent point. No threshold could be set and no
  trend would be comparable. The survivor list is the product; if a scalar is ever wanted, the
  defensible one is the survivor COUNT under a ratchet, not a ratio.
* **Code only.** A third of that round's fixes changed a PROMPT. There is no meaningful mutation of
  an English sentence, which is what :func:`assert_revert_fails_tests` is for.
* **The operator set is not exhaustive.** Comparisons, boolean operators, ``not``, arithmetic,
  numeric and string constants, and statement-level calls. Not: control-flow removal, argument
  reordering, exception-type swaps. "Every single-change mutation" would overstate it.

DESIGN NOTES, each earned
-------------------------
**Mutations are made on a COPY of the working tree, never in place.** An earlier version mutated the
live file and restored it in ``finally``. ``finally`` does not run when the process is killed, and
in one afternoon that left a production module mangled twice. Copying this project's largest
consumer measured 410 files / 5.0 MB / 1.74s, paid ONCE per run rather than per mutant. It is a copy
of the WORKING TREE, not ``git worktree add HEAD``, because the uncommitted change is usually the
fix under test. It also makes moot a whole class of hardening the in-place design needed: dirty-file
refusal, symlink refusal, atomic writes, backup files and a recovery entry point.

**Edits are made at TOKEN level, not by re-rendering an AST node.** Two earlier attempts failed
here. ``ast.unparse`` of a whole module discards every comment, so the "mutant" was a reformatted
skeleton and a survivor meant nothing. Splicing a node's source span using ``col_offset`` was worse:
``col_offset`` is a UTF-8 BYTE offset while the source is a ``str``, so one non-ASCII character
earlier on the line silently moved the edit -- on a verified input the whole statement became
``a >= b``, reported at a line that had not been touched. A false accusation, not noise. Token
positions are character-based and each edit is one token, so a mutant is the original file with one
operator changed and every comment intact.

**Verified sound and deliberately not defended against:** CRLF line endings and tab indentation
both round-trip correctly through the token splice -- checked by execution rather than assumed,
because a harness that mangles a Windows file would be worse than none in these repos. There is
no special handling for either, and none is needed.

**Exit codes are classified, not truth-tested.** ``returncode != 0`` used to mean "killed". pytest
exits 4 on a usage error and 5 when it collects nothing, so a typo in ``test_paths`` reported every
mutant killed and the run as a clean bill of health -- precisely what :mod:`gate_integrity` exists
to prevent. There is now a mandatory unmutated baseline run, and any exit code other than 0 or 1
aborts loudly.

**Results are cached on a fingerprint of everything that could change the answer.** Re-running a
mutation check that cannot have changed is the dominant cost in a hook. The fingerprint covers the
target module's TRANSITIVE first-party import closure -- not just the file itself -- because the
function under test can be unchanged while a function it calls is not. What it cannot see is stated
at :func:`fingerprint`; ``extra_fingerprint_paths`` exists for the part no static analysis reaches.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import warnings
import tokenize
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "HARNESS_VERSION",
    "Mutant",
    "MutationHarnessError",
    "MutationRun",
    "REFRESH_FLAG",
    "assert_no_new_surviving_mutant",
    "assert_revert_fails_tests",
    "find_surviving_mutants",
    "fingerprint",
    "generate_mutants",
    "sweep_files",
    "register_refresh_option",
]

#: Bumped whenever the operator set or the run semantics change, so a cached result computed by an
#: older harness is not reused by a newer one. Without it, adding an operator would silently keep
#: reporting the old survivor list.
HARNESS_VERSION = "8"  # 8: a mutant that stops pytest starting (exit 4/5) is a kill, not a refusal


#: Written in place of a justification by a refresh, and rejected on the next run. A refresh
#: that produced an acceptable-looking note made accepting every survivor a one-command,
#: green-output operation.
_UNJUSTIFIED = "NEEDS-JUSTIFICATION:"

REFRESH_FLAG = "--refresh-mutation-survivors-baseline"

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".hypothesis",
    ".benchmarks", ".cache", ".ruff_cache", "node_modules", ".venv", "venv",
)

#: Container literals larger than this are SAMPLED rather than exhausted. Measured on four real
#: modules: two lookup tables covering 18% of one file's lines produced 64% of its mutants, and a
#: survivor list where half the entries are dictionary rows is one nobody reads to the end.
_CONTAINER_SAMPLE = 3


class MutationHarnessError(RuntimeError):
    """The harness could not do its job. Never confused with "the mutant survived"."""


@dataclass(frozen=True)
class Mutant:
    """The original file with exactly one token changed.

    *path* is repo-relative: an absolute path in a message or a baseline key matches on one machine
    and silently accepts everything everywhere else.
    """

    path: Path
    line: int
    column: int
    description: str
    original_span: str
    mutated_span: str
    category: str = ""
    context: str = ""
    mutated_file_text: str = field(default="", repr=False, compare=False)

    @property
    def key(self) -> str:
        """Stable baseline key: repo-relative path, a digest of the SITE, and the operator.

        Digested rather than line-numbered on purpose. ``code_audit_meta`` records that a
        line-number key produces a false all-clear the moment anything above it shifts, because the
        old key stops matching and the finding reads as new -- or, worse, an accepted key keeps
        matching a different line.

        The digest covers the surrounding LINE as well as the mutated span, which is what makes it
        a site rather than a shape. Over the span alone, every `secrets.token_hex(8)` in a file
        digests to the same thing -- the span is the single character `8` -- so one accepted
        baseline entry silenced four separate call sites, three of which the tests actually kill.
        An accepted mutant is a claim about ONE place in the code; a key that cannot tell two
        places apart turns one reasoned exception into a blanket one. The line text keeps the
        property the line NUMBER was rejected for: it does not move when code above it shifts.
        """
        site = repr((self.original_span, " ".join(self.context.split())))
        digest = hashlib.sha256(site.encode("utf-8")).hexdigest()[:12]
        return f"{self.path.as_posix()}::{digest}::{self.description}"

    def diff_line(self) -> str:
        """The one-line before/after a survivor report needs."""
        return f"{self.original_span!r} -> {self.mutated_span!r}"

    def __str__(self) -> str:  # pragma: no cover - display only
        suffix = f"  [{self.category}]" if self.category else ""
        return f"{self.path.as_posix()}:{self.line}:{self.column}  {self.description}  {self.diff_line()}{suffix}"


@dataclass
class MutationRun:
    """What a run actually did.

    A bare ``list[Mutant]`` return was ambiguous in three directions at once: an empty list meant
    "every mutant was killed" (good), "no mutants were generated" (the line range selected nothing),
    or "every pytest run exited 5" (misconfigured). All three read as success. These fields
    distinguish them.
    """

    survivors: list[Mutant]
    mutants_run: int
    killed: int
    truncated: bool
    candidates_total: int
    sampled_containers: dict[str, tuple[int, int]] = field(default_factory=dict)
    killed_by_crash: int = 0
    coverage_gaps: list[Mutant] = field(default_factory=list)
    inconclusive: list[Mutant] = field(default_factory=list)
    #: Why the wider net was not used, when it was not. Empty when it ran normally.
    wider_net_note: str = ""
    #: Per-mutant cost, measured rather than estimated: totals in seconds for the module purge,
    #: pytest inside the warm worker, the parent-side overhead around it, and the cold re-checks
    #: that only survivors and coverage-map gaps pay. A parent-side profiler cannot attribute any
    #: of the first two -- they happen in another process.
    timings: dict[str, float] = field(default_factory=dict)
    from_cache: bool = False

    def summary(self) -> str:
        if not self.mutants_run and not self.candidates_total:
            # "0 mutants run, 0 killed, 0 survived" is the sentence a clean run would also produce,
            # and the difference matters entirely: nothing was checked here.
            return "NO MUTANTS WERE GENERATED -- nothing was checked (wrong path, or a line scope that selected nothing)"
        parts = [f"{self.mutants_run} mutants run, {self.killed} killed, {len(self.survivors)} survived"]
        if self.killed_by_crash:
            # Not a footnote: these die against any test that reaches the line, so a kill
            # count that includes them overstates how much the tests actually check.
            parts.append(f"{self.killed_by_crash} of the kills were CRASHES, not assertions")
        if self.inconclusive:
            # The one outcome that is neither a kill nor a survival. A mutant whose run timed out
            # was previously dropped from the denominator, and a survivor whose confirmation timed
            # out was counted as killed -- both let an unmeasured mutant read as a measured one.
            parts.append(
                f"{len(self.inconclusive)} mutants were INCONCLUSIVE (the run timed out); they are "
                "neither killed nor survived, so this sweep is incomplete"
            )
        if self.wider_net_note:
            # Loud, because its absence silently changes what a survivor MEANS: without the net,
            # every survivor is "no listed test kills this", which is a weaker claim.
            parts.append(f"WIDER NET UNUSED: {self.wider_net_note}")
        if self.coverage_gaps:
            # Reported separately and loudly: these are NOT test gaps. A reader who treats them as
            # survivors writes a test that already exists.
            named = sorted(
                {m.category.split(":", 1)[1] for m in self.coverage_gaps if m.category.startswith("killed-by:")}
            )
            detail = f" -- add {', '.join(named)}" if named else ""
            parts.append(
                f"{len(self.coverage_gaps)} 'survivors' were killed by a test the coverage map does "
                f"not list -- fix the map, not the tests{detail}"
            )
        if self.truncated:
            parts.append(f"TRUNCATED: {self.candidates_total} candidates existed, {self.mutants_run} were run")
        for name, (shown, total) in sorted(self.sampled_containers.items()):
            parts.append(f"sampled {shown} of {total} entries in {name}")
        if self.from_cache:
            # Says what was checked, not what is true. The fingerprint covers the import
            # closure, the tests, the declared data files and the run's scope -- it does not cover
            # dynamic imports, the environment, or a data file nobody passed in.
            parts.append("(REPLAYED FROM CACHE, not measured: no fingerprinted input changed)")
        return "; ".join(parts)


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
    "min": ("max", "max becomes min"),
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
        isinstance(sub, ast.Subscript)
        and any(isinstance(inner, ast.Constant) and inner.value is not None for inner in ast.walk(sub.slice))
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
_SYNTAX_RISKY_PREFIXES = ("logic:", "operator:", "comparison:")


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


def _excluded_ranges(source: str) -> list[tuple[int, int]]:
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
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    starts = _line_starts(source)

    def span(node: ast.AST | None) -> tuple[int, int] | None:
        if node is None or getattr(node, "lineno", None) is None or getattr(node, "end_lineno", None) is None:
            return None
        return (
            _abs_index(source, starts, node.lineno, node.col_offset),
            _abs_index(source, starts, node.end_lineno, node.end_col_offset),  # type: ignore[arg-type]
        )

    out: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        # Any bare string statement, not only the first one in a body.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if (s := span(node)) is not None:
                out.append(s)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__" and (s := span(node.value)) is not None:
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


def _container_members(source: str) -> list[tuple[int, int, str]]:
    """``(start, end, container name)`` for each SUPPRESSED element of a large module-level table.

    Used to SAMPLE rather than exhaust data tables. Rotation is seeded from the file's content hash
    so successive runs cover different rows: a row missed today is missed temporarily, not
    permanently, which is the difference between sampling and a blind spot.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    starts = _line_starts(source)
    seed = int(hashlib.sha256(source.encode("utf-8")).hexdigest()[:8], 16)
    out: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        # AnnAssign as well as Assign: a module constant written `_BANNED: frozenset = {...}` is
        # the SAME data table, and matching only the unannotated form meant the annotated ones were
        # exhausted instead of sampled -- the whole survivor list then fills with rows of one table
        # and nobody reads to the end of it. Typed module constants are the house style here, so
        # this was not an edge case.
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(
            node.value, (ast.Dict, ast.Set, ast.List, ast.Tuple)
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        name = next((t.id for t in targets if isinstance(t, ast.Name)), "<container>")
        elements: list[ast.AST] = []
        if isinstance(node.value, ast.Dict):
            elements = [k for k in node.value.keys if k is not None] + list(node.value.values)
        else:
            elements = list(node.value.elts)
        if len(elements) <= _CONTAINER_SAMPLE:
            continue
        # A row that CONTAINS A CALL is code, not data. The sampler exists so a lookup table of
        # country codes does not bury the survivor list; applied to a table of `re.compile(...)`
        # guards it would suppress the substance of the module instead of its noise, and the
        # content-hash seed means the same rows would stay suppressed until the file changed. The
        # line is the presence of an expression that DOES something, which is exactly the
        # difference between a data table and a table of behaviour.
        if any(isinstance(sub, ast.Call) for element in elements for sub in ast.walk(element)):
            continue
        keep = {(seed + i) % len(elements) for i in range(_CONTAINER_SAMPLE)}
        for index, element in enumerate(elements):
            if index in keep or getattr(element, "lineno", None) is None:
                continue
            end_line = getattr(element, "end_lineno", None)
            end_col = getattr(element, "end_col_offset", None)
            if end_line is None or end_col is None:  # pragma: no cover - ast always sets these
                continue
            start = _abs_index(source, starts, element.lineno, element.col_offset)
            out.append((start, _abs_index(source, starts, end_line, end_col), name))
    out.sort()
    return out


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


def _argument_transpositions(source: str) -> list[tuple[int, int, int, str, str, str]]:
    """Swap two adjacent positional arguments of a call.

    The shape this exists for is a format string and its values: ``"%s kept %d of %d", uid, n, cap``
    passes type checking, reads correctly, and lies. Nothing in a token-level operator set can
    express it, because no single token is wrong.

    Adjacent pairs only. Transposing arbitrary pairs would grow quadratically for no extra signal --
    a call whose neighbours are interchangeable is a call whose argument order is unchecked, and one
    demonstration of that per pair is enough.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    starts = _line_starts(source)
    out: list[tuple[int, int, int, str, str, str]] = []
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


def _slice_bound_candidates(source: str) -> list[tuple[int, int, int, str, str, str]]:
    """Remove a slice bound, making the slice unbounded on that side.

    ``text[:limit]`` -> ``text[:]`` is the defect a cap is written to prevent, and the numeric
    operator reached it only when the bound was a literal. Here the bound is usually a name -- a
    configured maximum, a remaining budget -- which is exactly the interesting case.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    starts = _line_starts(source)
    out: list[tuple[int, int, int, str, str, str]] = []
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
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sites.append((arg, regexish))
        for kw in node.keywords:
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                sites.append((kw.value, False))
    return sites


def _substitution_candidates(source: str) -> list[tuple[int, int, int, str, str, str]]:
    """Replace a string with a confusable neighbour, and widen a regex that guards something."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    starts = _line_starts(source)
    sites = _string_literal_sites(tree)
    # The vocabulary is the file's own. Short values only: a long string is prose, and swapping two
    # sentences tests nothing that emptying one does not already test.
    vocabulary = sorted({n.value for n, _r in sites if 1 <= len(n.value) <= 12 and n.value.strip()})
    out: list[tuple[int, int, int, str, str, str]] = []
    for node, regexish in sites:
        span = _span_of(source, starts, node)
        if span is None:
            continue
        raw = source[span[0] : span[1]]
        if "\n" in raw:
            continue
        value = node.value
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



def _token_candidates(source: str, skip_coupled_constants: bool = False) -> list[tuple[int, int, int, str, str, str]]:
    """``(abs_start, abs_end, line, replacement, description, category)`` per mutable token.

    Token positions from :mod:`tokenize` are CHARACTER offsets, so no byte conversion is needed
    here -- unlike the AST spans above.
    """
    starts = _line_starts(source)
    excluded = _excluded_ranges(source)
    sampled_out = _container_members(source)
    coupled = _repr_coupled_lines(source) if skip_coupled_constants else set()

    def excluded_at(a: int, b: int) -> bool:
        return any(a >= lo and b <= hi for lo, hi in excluded)

    out: list[tuple[int, int, int, str, str, str]] = []
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
        if excluded_at(abs_start, abs_end):
            continue
        # Containment, not equality. The sampler marks an ELEMENT of a data table; the mutable
        # tokens sit inside it at their own offsets, so an exact-offset test suppressed nothing at
        # all and every row of every sampled table came back as a candidate.
        sampled_name = next((n for lo, hi, n in sampled_out if lo <= abs_start < hi), None)
        category = f"noise:table-sample:{sampled_name}" if sampled_name else ""

        def emit(end: int, replacement: str, description: str) -> None:
            out.append((abs_start, end, row, replacement, description, category))

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
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if tok.string == "is" and following is not None and following.string == "not":
                emit(starts[following.end[0] - 1] + following.end[1], "is", "comparison: is not becomes is")
                continue
            if tok.string == "in" and index and tokens[index - 1].string == "not":
                continue  # the `not in` pair is emitted by the `not` branch below
            new, why = _NAME_SWAP[tok.string]
            emit(abs_end, new, f"logic: {why}")

        elif tok.type == tokenize.NAME and tok.string == "not":
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if following is not None and following.string == "in":
                emit(starts[following.end[0] - 1] + following.end[1], "in", "comparison: not in becomes in")
            else:
                emit(abs_end + 1, "", "dropped a `not`")

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
            emit(abs_end, '""', "constant: emptied a string")

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


def _statement_call_candidates(source: str) -> list[tuple[int, int, int, str, str, str]]:
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
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    starts = _line_starts(source)
    out: list[tuple[int, int, int, str, str, str]] = []
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


def generate_mutants(
    path: Path,
    lines: Iterable[range] | range | None = None,
    limit: int | None = None,
    skip_coupled_constants: bool = False,
) -> tuple[list[Mutant], int, dict[str, tuple[int, int]]]:
    """``(mutants, candidates_total, sampled_containers)`` in SOURCE ORDER.

    *lines* accepts several ranges, because a real commit touches several hunks. A candidate is in
    scope when its span OVERLAPS any of them: testing only the start line skipped an expression that
    began above the changed hunk and extended into it -- the very expression the commit edited.

    Source order matters when *limit* is set. An earlier version walked the AST breadth-first and
    truncated, so on a 132-candidate file it covered one line near the end and never reached whole
    functions. A truncated run's empty survivor list is indistinguishable from a complete one's,
    which is why the count is returned rather than discarded.
    """
    source = io.open(path, encoding="utf-8", newline="").read()
    starts = _line_starts(source)
    candidates = (
        _token_candidates(source, skip_coupled_constants)
        + _statement_call_candidates(source)
        + _argument_transpositions(source)
        + _slice_bound_candidates(source)
        + _substitution_candidates(source)
    )
    candidates.sort(key=lambda c: (c[0], c[4]))

    ranges: list[range] = []
    if isinstance(lines, range):
        ranges = [lines]
    elif lines is not None:
        ranges = list(lines)

    def line_of(index: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= index:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    mutants: list[Mutant] = []
    sampled: dict[str, tuple[int, int]] = {}
    total = 0
    for abs_start, abs_end, line, replacement, description, category in candidates:
        if ranges:
            end_line = line_of(max(abs_start, abs_end - 1))
            if not any(line <= r.stop - 1 and end_line >= r.start for r in ranges):
                continue
        if category.startswith("noise:table-sample:"):
            name = category.split(":", 2)[2]
            shown, seen = sampled.get(name, (0, 0))
            sampled[name] = (shown, seen + 1)
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
    return mutants, total, {n: (_CONTAINER_SAMPLE, seen + _CONTAINER_SAMPLE) for n, (_s, seen) in sampled.items()}


# ── fingerprinting, so an unchanged check is not re-run ──────────────────────────────────────────
def _first_party_imports(path: Path, repo_root: Path, seen: set[Path]) -> set[Path]:
    """Every repo-local module reachable from *path* by static import, transitively."""
    path = path.resolve()
    if path in seen or not path.is_file():
        return seen
    seen.add(path)
    try:
        tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    except (SyntaxError, OSError):
        # The file itself still counts. Dropping it silently removed it AND everything it imports
        # from the fingerprint, so the very edit that fixes a syntax error would not invalidate the
        # cached verdict that was measured without it.
        seen.add(path)
        return seen
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
        elif isinstance(node, ast.ImportFrom) and node.level:
            base = path.parent
            for _ in range(node.level - 1):
                base = base.parent
            # `from . import mod` puts the MODULE in node.names; `from .mod import func` puts the
            # FUNCTION there and the module in node.module. Probing only node.names resolved the
            # first form and silently dropped the second -- which is the dominant style inside a
            # package, so `user_prompt.py`'s closure omitted all five siblings it actually calls
            # (experience, formatting, smiley, truncation, word_count). A cached verdict then
            # survived any edit to them, which is exactly the hole this closure exists to close.
            if node.module:
                for part in (base.joinpath(*node.module.split(".")),):
                    for candidate in (part.with_suffix(".py"), part / "__init__.py"):
                        if candidate.is_file():
                            _first_party_imports(candidate, repo_root, seen)
            for alias in node.names:
                for candidate in (base / f"{alias.name}.py", base / alias.name / "__init__.py"):
                    if candidate.is_file():
                        _first_party_imports(candidate, repo_root, seen)
            if node.module:
                names.append(node.module)
    for name in names:
        parts = name.split(".")
        # `src/` is probed as well as the root. A src-layout package installed editable imports as
        # `pkg.mod` while living at `src/pkg/mod.py`, so probing the root alone found nothing and
        # the closure silently collapsed to the target file -- which is the state this very package
        # would be fingerprinted in.
        for base in (repo_root, repo_root / "src"):
            for candidate in (base.joinpath(*parts).with_suffix(".py"), base.joinpath(*parts, "__init__.py")):
                if candidate.is_file():
                    _first_party_imports(candidate, repo_root, seen)
    return seen


def fingerprint(
    repo_root: Path,
    target: Path,
    test_paths: Sequence[Path | str],
    extra_fingerprint_paths: Sequence[Path | str] = (),
    scope: object = None,
) -> str:
    """A digest of everything that could change this check's answer -- as far as static analysis sees.

    Covers the target module's TRANSITIVE first-party import closure, every test file, every
    ``conftest.py`` between the repo root and each test file, anything named in
    *extra_fingerprint_paths*, and :data:`HARNESS_VERSION`.

    The import closure is the important part and the reason a naive "hash the file and the tests"
    cache is wrong: the function under test can be untouched while a function it CALLS is not.

    WHAT IT STILL CANNOT SEE, stated rather than papered over:

    * **Data files.** A prompt in a ``.txt``, a ``config.toml``, a fixture JSON -- changing one
      changes behaviour without touching a single ``.py``. This is not hypothetical in the repo this
      was written for, where the system prompt is a text file. Pass them in
      *extra_fingerprint_paths*.
    * **Dynamic imports.** ``importlib.import_module(name)``, plugin registries, entry points.
    * **Third-party versions.** An upgraded dependency changes behaviour with no local diff.
    * **The environment.** Environment variables, the clock, the database.

    So a cache hit means "nothing statically reachable changed", not "the answer is certainly the
    same". Deleting the cache file is always a valid reset, and the caller can pass
    ``use_cache=False``.
    """
    repo_root = Path(repo_root).resolve()
    files = _first_party_imports(repo_root / target, repo_root, set())
    for test in test_paths:
        test_path = (repo_root / test).resolve()
        # A directory is a legal pytest target and `read_bytes()` on one raises, which the digest
        # loop swallowed into a constant -- leaving the fingerprint blind to every test file under
        # it, permanently. Expand it to the files pytest would actually collect.
        if test_path.is_dir():
            files.update(sorted(test_path.rglob("test_*.py")))
            files.update(sorted(test_path.rglob("conftest.py")))
        else:
            files.add(test_path)
        parent = test_path.parent
        while parent >= repo_root and parent != parent.parent:
            conftest = parent / "conftest.py"
            if conftest.is_file():
                files.add(conftest)
            if parent == repo_root:
                break
            parent = parent.parent
    for extra in extra_fingerprint_paths:
        files.add((repo_root / extra).resolve())

    digest = hashlib.sha256(HARNESS_VERSION.encode())
    # The machinery the answer was measured on. A Python upgrade or an installed/removed pytest
    # plugin changes what the tests do without touching a byte of the repo, and a replay under
    # different machinery is a verdict about a run that never happened. Cheap to include, and it
    # converts a whole class of silent staleness into a cache miss.
    digest.update(sys.version.encode("utf-8"))
    digest.update(repr(sorted(_installed_pytest_plugins())).encode("utf-8"))
    # The SCOPE of a run is part of its answer, not part of its inputs. A sweep narrowed by
    # `lines=` or capped by `limit=` examines a SUBSET of the mutants, and its verdict is only
    # about that subset -- so replaying it for a wider scope reports "no survivors" about mutants
    # that were never run. `lines` in particular moves on its own: it comes from `git diff`, which
    # changes across an amend, a rebase or a branch switch while every file byte stays identical.
    digest.update(repr(scope).encode("utf-8"))
    for file in sorted(files):
        try:
            body = file.read_bytes()
        except OSError:
            # Distinct per path: one constant for every unreadable file makes two different
            # mistakes (a typo'd extra_fingerprint_paths, a deleted module) digest identically.
            body = b"<unreadable:" + str(file).encode("utf-8", "replace") + b">"
        try:
            name = file.relative_to(repo_root).as_posix()
        except ValueError:
            name = file.name
        digest.update(name.encode("utf-8"))
        digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


def _mutant_json(m: Mutant) -> dict[str, object]:
    """One serialisation, used by every list in the cache entry."""
    return {
        "path": m.path.as_posix(),
        "line": m.line,
        "column": m.column,
        "description": m.description,
        "original_span": m.original_span,
        "mutated_span": m.mutated_span,
        "category": m.category,
        "context": m.context,
    }


def _mutant_from_json(d: dict) -> Mutant:
    return Mutant(
        path=Path(d["path"]),
        line=d["line"],
        column=d["column"],
        description=d["description"],
        original_span=d["original_span"],
        mutated_span=d["mutated_span"],
        category=d.get("category", ""),
        context=d.get("context", ""),
    )


#: Memoised for the life of the process. `importlib.metadata.distributions()` walks every installed
#: package and parses its metadata with the email parser -- measured at 0.8s and 825,000 `readline`
#: calls. `fingerprint` calls this twice per sweep, and it is the ENTIRE cost of a cache hit, so the
#: uncached version made the common case (nothing changed, replay the answer) cost most of a second
#: for no reason. The installed set cannot change while the process runs.
_PLUGIN_CACHE: list[str] | None = None


def _installed_pytest_plugins() -> list[str]:
    """Names and versions of installed pytest plugins, for the fingerprint.

    Read from installed distribution metadata rather than by starting pytest: this runs on every
    fingerprint, including cache hits, and must not cost a process.
    """
    global _PLUGIN_CACHE
    if _PLUGIN_CACHE is not None:
        return _PLUGIN_CACHE
    found = []
    try:
        from importlib.metadata import distributions
    except ImportError:  # pragma: no cover - stdlib since 3.8
        return found
    for dist in distributions():
        try:
            name = dist.metadata["Name"] or ""
        except (KeyError, TypeError):  # pragma: no cover - broken metadata in the wild
            continue
        if name.startswith("pytest") or name.startswith("pytest-"):
            found.append(f"{name}=={dist.version}")
    _PLUGIN_CACHE = found
    return found


def _pytest_env() -> dict[str, str]:
    """Environment for a nested pytest run.

    ``PYTEST_ADDOPTS`` is cleared because a consuming repo's ``--cov-fail-under`` would fail every
    mutant run for the wrong reason and make every mutant look killed -- the same exit-code
    overloading this module was rewritten to abolish.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    # The child inherits PYTHONPATH, never the parent's in-process `sys.path`. Without this, a
    # parent running from a checkout spawns a worker that loads the INSTALLED package instead --
    # measured: parent on a worktree, child on `C:\...\py-ci-shared\src`. Every worker-side change
    # is then untested by any run of the checkout, and silently so, because a reply missing the new
    # fields is still a valid reply. That is this module's own failure mode, applied to itself.
    inherited = env.get("PYTHONPATH", "")
    entries = [p for p in sys.path if p and p not in inherited.split(os.pathsep)]
    env["PYTHONPATH"] = os.pathsep.join([*entries, inherited]) if inherited else os.pathsep.join(entries)
    # Defence in depth, not a fix for a reproduced bug: bytecode caching keys on the source's size
    # and mtime-to-the-second, and an operator swap changes neither. Writing no bytecode at all
    # removes the question for the cost of a recompile that the warm worker already pays.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_ADDOPTS"] = ""
    return env


#: Exceptions that mean the mutant made the code CRASH rather than produce a wrong answer. A crash
#: mutant dies against any test that reaches the line, so it is killed for free and tells nobody
#: anything about test quality -- which is one of the four reasons a mutation SCORE is not computed
#: here. Reported as a label rather than filtered: it never survives, so it costs no survivor-list
#: noise, and knowing how many of the kills were free is exactly what stops a high kill count from
#: being mistaken for good tests.
_CRASH_EXCEPTIONS = (
    "ValueError",
    "LookupError",
    "IndexError",
    "KeyError",
    "TypeError",
    "AttributeError",
    "ZeroDivisionError",
    "OverflowError",
    "RecursionError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    # The classes a MUTATION produces, as opposed to the ones application code raises. An emptied
    # string becomes an invalid name or an empty pattern; a deleted call leaves a name unbound; a
    # changed constant walks off the end of a sequence or opens a path that is not there.
    "NameError",
    "UnboundLocalError",
    "ImportError",
    "ModuleNotFoundError",
    "AssertionError",
    "StopIteration",
    "ArithmeticError",
    "OSError",
    "FileNotFoundError",
    "NotADirectoryError",
    "IsADirectoryError",
    "PermissionError",
    "re.error",
    "json.JSONDecodeError",
)


def _killed_by_crash(output: str) -> bool:
    """True if the failure looks like an exception from the mutated code, not a failed assertion.

    Read from pytest's own summary line rather than from the traceback body: a traceback can mention
    an exception name that a test deliberately asserted with ``pytest.raises``, and counting that as
    a crash would misreport a test doing its job.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("E "):
            continue
        payload = stripped[2:].lstrip()
        if payload.startswith("assert") or payload.startswith("AssertionError"):
            return False
        if any(payload.startswith(name) for name in _CRASH_EXCEPTIONS):
            return True
    return False


def _repr_coupled_lines(source: str) -> set[int]:
    """Lines where a numeric constant and a string constant state the same fact.

    ``text[: max_len - 3] + "..."`` is the canonical shape: the ``3`` and the ``"..."`` are two
    spellings of one decision, so mutating both produces two survivors that a reader must think
    about twice to learn one thing.

    Off by default. What it hides is real: the case where the two have drifted apart and only one
    direction is covered -- a truncation that reserves three characters and appends four. The agent
    that proposed it marked it optional for exactly that reason, and this keeps that judgement with
    the caller instead of making it silently.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
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


def _pytest_flags() -> list[str]:
    """Flags that isolate a nested run, built from what is actually installed.

    Disabling a plugin BY NAME is not safe: `-p no:xdist` made `pytest_progress` fail validation
    with `unknown hook 'pytest_xdist_node_collection_finished'`, pytest exited 3, and the run
    aborted. Parallelism and coverage are therefore switched off through their own options, which
    only exist when the plugin does -- so both are added conditionally rather than assumed.

    Coverage matters beyond speed: a consuming repo's `--cov-fail-under` would fail every mutant
    run for a reason unrelated to the mutation, making every mutant look killed.
    """
    flags = ["-q", "--no-header", "-p", "no:randomly", "-p", "no:cacheprovider"]
    if importlib.util.find_spec("xdist") is not None:
        flags += ["-n", "0"]
    if importlib.util.find_spec("pytest_cov") is not None:
        flags.append("--no-cov")
    return flags


def _first_failing_file(output: str) -> str | None:
    """The test FILE named by pytest's first failure line, or ``None``.

    Read from the short summary (``FAILED path::test - reason``) rather than from the traceback,
    for the same reason `_killed_by_crash` does: the summary line is a stable shape and a traceback
    is whatever the failing assertion happened to print. With `-x` there is at most one.
    """
    for line in output.splitlines():
        stripped = line.strip()
        for prefix in ("FAILED ", "ERROR "):
            if stripped.startswith(prefix):
                target = stripped[len(prefix) :].split(" ", 1)[0]
                return target.split("::", 1)[0] or None
    return None


def _run_pytest(test_paths: Sequence[Path | str], cwd: Path, timeout: float):
    """Run pytest, or return ``None`` when it did not finish inside *timeout*.

    The timeout is mandatory rather than optional: swapping ``<`` for ``<=`` produces
    non-terminating loops by construction, so an unbounded run hangs forever.
    """
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", *[str(t) for t in test_paths], *_pytest_flags(), "-x"],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            env=_pytest_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None


class _WarmRunner:
    """A persistent pytest process, reused across mutants.

    Measured: 15.8s per mutant as a fresh subprocess, of which pytest reports 0.48s as actual test
    execution -- roughly 97% is interpreter startup and importing the package under test. Warm, with
    a repo-local module purge between runs, the marginal cost measured 1.6-2.7s, a 7-10x
    improvement.

    The two obvious warm designs are silently wrong and were rejected by execution, not argument: no
    purge at all leaves the original module imported and reports a false SURVIVOR against a mutation
    never applied, and ``importlib.reload`` produces a false KILL because an already-imported test
    module still holds the pre-reload class. Only an atomic purge of every repo-local module -- test
    modules included -- is sound. See :mod:`py_ci_shared._mutation_worker`.

    A single worker is used rather than four. Four cold starts measured 39.3s against 19.2s for one:
    on Windows there is no ``fork``, so startup is half-serialised and disk-bound, and four workers
    would each need their own tree copy because they mutate the same file. The measured single-worker
    gain is already 6.9x; the parallel variant's extra 1.2-1.6x is not worth a per-worker copy and a
    crash-recovery protocol.
    """

    def __init__(self, sandbox: Path, timeout: float) -> None:
        self.sandbox = sandbox
        self.timeout = timeout
        self.process: subprocess.Popen | None = None
        #: The file whose failure ended the last run, if the worker reported one.
        self.last_failed: str | None = None
        #: The last run's `t_purge` / `t_pytest` / `t_total`, measured inside the worker.
        self.last_timings: dict[str, float] | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "py_ci_shared._mutation_worker", str(self.sandbox)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(self.sandbox),
            env=_pytest_env(),
            bufsize=1,
        )

    def run(self, test_paths: Sequence[Path | str]) -> int | None:
        """Exit code, or ``None`` when the worker died or timed out.

        ``None`` deliberately does not mean "killed": a dead worker is a harness problem, and the
        caller falls back to a cold subprocess rather than recording a result it cannot trust.

        The timeout is enforced by reading the reply on a helper thread. It was previously stored
        and never applied -- ``readline()`` on a pipe blocks with no deadline, so the sentence above
        was true of the cold path only, and a single non-terminating mutant hung the whole sweep
        with no output. A mutation that turns ``<`` into ``<=`` can produce exactly that by
        construction, which this module's own docstring notes.
        """
        if self.process is None or self.process.poll() is not None:
            return None
        args = [str(t) for t in test_paths] + _pytest_flags() + ["-x"]
        try:
            assert self.process.stdin is not None and self.process.stdout is not None
            self.process.stdin.write(json.dumps({"cmd": "run", "args": args}) + "\n")
            self.process.stdin.flush()
            line = self._readline_within(self.timeout)
        except (OSError, ValueError):
            return None
        if line is None:
            # The worker is wedged on this mutant, not merely slow: kill it so the caller's cold
            # fallback starts from a clean process, and so the next mutant is not read against a
            # reply that belongs to this one.
            self.stop()
            return None
        if not line:
            return None
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            return None
        # Defensive even though the worker now redirects pytest's output: a line that parses as
        # JSON but is not our reply shape must degrade to a cold re-run, never to an exception
        # mid-sweep or -- worse -- to a number read as an exit code.
        if not isinstance(reply, dict) or "rc" not in reply:
            return None
        # Advisory: which file failed first, so the caller can lead with it next time. A worker
        # that does not report it simply leaves the order alone.
        failed = reply.get("failed")
        self.last_failed = failed if isinstance(failed, str) else None
        #: Per-mutant timings from inside the worker, where the cost actually is. `None` from a
        #: worker that does not report them, so an older worker still works.
        self.last_timings = {k: reply[k] for k in ("t_purge", "t_pytest", "t_total") if k in reply} or None
        try:
            return int(reply["rc"])
        except (TypeError, ValueError):
            return None

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.stdin is not None and self.process.poll() is None:
                self.process.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
                self.process.stdin.flush()
            self.process.wait(timeout=10)
        except Exception:  # noqa: BLE001 -- shutdown must never raise over the real result
            self.process.kill()
        finally:
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass

    def _readline_within(self, deadline: float) -> str | None:
        """One reply line, or ``None`` if it does not arrive within *deadline* seconds.

        A pipe read has no deadline of its own, so the wait happens on a helper thread that is
        abandoned if it overruns. Abandoning it is safe precisely because the caller then kills the
        worker: nothing else will ever read that pipe.
        """
        assert self.process is not None and self.process.stdout is not None
        box: list[str] = []
        stdout = self.process.stdout

        def read() -> None:
            try:
                box.append(stdout.readline())
            except (OSError, ValueError):  # pragma: no cover - the pipe closed under us
                pass

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(deadline)
        if reader.is_alive() or not box:
            return None
        return box[0]

    def __enter__(self) -> "_WarmRunner":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def _lead_with(paths: list[Path | str], first: str) -> list[Path | str]:
    """*paths* reordered so the entry matching *first* leads. Unknown names change nothing."""
    wanted = Path(first).as_posix()
    for i, p in enumerate(paths):
        if Path(p).as_posix() == wanted:
            return [paths[i], *paths[:i], *paths[i + 1 :]]
    return paths


#: Exit codes that mean "pytest did not get through the run": 2 interrupted, 3 internal error, 4
#: usage error, 5 nothing collected. On a MUTANT all four say the same thing, because the baseline
#: has already passed with the same paths, so only the mutation can have caused it.
#:
#: 4 and 5 were added after a measured case: emptying the string ``"_count"`` inside a ``__slots__``
#: tuple raises ``TypeError: __slots__ must be identifiers`` while the class body executes, which
#: happens during ``conftest`` import, so pytest exits 4. A refusal discards the result for the
#: WHOLE FILE, so that one obviously-killed mutant cost the other thirty-five in its file an answer.
_MUTATION_BROKE_THE_RUN = frozenset({2, 3, 4, 5})


def _classify_code(code: int, what: str, *, baseline_verified: bool = False) -> bool:
    """True if the tests PASSED. Raises when the exit code means neither pass nor fail.

    *baseline_verified* says the identical command was already observed to exit 0 on the unmutated
    tree, which is what licenses reading a "did not get through the run" code as a kill -- of the
    crash kind, which ``killed_by_crash`` keeps separate from a kill by assertion -- rather than as
    a refusal. On the baseline itself the same codes mean the caller passed paths pytest cannot use,
    and refusing is the only safe answer: the alternative reports every mutant killed and the run as
    a clean bill of health, the failure :mod:`gate_integrity` exists to prevent.

    It replaces a check that sniffed *what* for the words "the unmutated baseline". A guard that
    depends on the wording of a human-readable label is one rename away from silently classifying
    every baseline failure as a kill.
    """
    if code == 0:
        return True
    if code == 1:
        return False
    if baseline_verified and code in _MUTATION_BROKE_THE_RUN:
        return False
    raise MutationHarnessError(
        f"pytest exited {code} on {what}, which is neither pass (0) nor fail (1). "
        "4 = usage error, 5 = nothing collected, 2 = interrupted, 3 = internal error. "
        "Check `test_paths` and that the tests are reachable from `repo_root`."
    )


def _classify(result, what: str, *, baseline_verified: bool = False) -> bool:
    """True if the tests PASSED. Raises when the exit code means neither pass nor fail.

    Same rule as :func:`_classify_code`, for the cold-process path -- which had no mutation-caused
    branch at all, so a mutant that broke the run was a refusal whenever it was re-checked cold.
    """
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    if baseline_verified and result.returncode in _MUTATION_BROKE_THE_RUN:
        return False
    raise MutationHarnessError(
        f"pytest exited {result.returncode} on {what}, which is neither pass (0) nor fail (1). "
        "4 = usage error, 5 = nothing collected, 2 = interrupted, 3 = internal error.\n"
        "Check `test_paths` and that the tests are reachable from `repo_root`.\n"
        f"{result.stdout[-2000:]}"
    )


def _sweep_partition(
    sandbox: Path,
    relative: Path,
    mutants: "list[Mutant]",
    test_paths: "Sequence[Path | str]",
    fallback_test_paths: "Sequence[Path | str]",
    timeout: float,
    use_warm_worker: bool,
) -> "tuple[list[Mutant], list[Mutant], list[Mutant], int, int, dict[str, float]]":
    """One sandbox's share of the mutants.

    Returns ``(survivors, coverage_gaps, inconclusive, run, crashes)``. Every path in and out is a
    value: the caller merges, and nothing here touches state another partition can see. That is
    what makes running several of these at once safe on the harness's side -- whether it is safe on
    the CONSUMER's side is a property of its tests, which is why concurrency is opt-in.
    """
    target = sandbox / relative
    original = io.open(target, encoding="utf-8", newline="").read()
    survivors: list[Mutant] = []
    coverage_gaps: list[Mutant] = []
    inconclusive: list[Mutant] = []
    run = 0
    crashes = 0
    # The restore is in a `finally` because the sandbox may be SHARED across files: an exception
    # mid-sweep used to leave the last mutant on disk, which was harmless while every file got
    # its own throwaway copy and becomes contamination of the next file the moment one does not.
    try:
        return _sweep_mutants(
            sandbox, target, original, mutants, test_paths, fallback_test_paths, timeout, use_warm_worker
        )
    finally:
        io.open(target, "w", encoding="utf-8", newline="").write(original)


def _sweep_mutants(
    sandbox: Path,
    target: Path,
    original: str,
    mutants: "list[Mutant]",
    test_paths: "Sequence[Path | str]",
    fallback_test_paths: "Sequence[Path | str]",
    timeout: float,
    use_warm_worker: bool,
) -> "tuple[list[Mutant], list[Mutant], list[Mutant], int, int, dict[str, float]]":
    """The loop itself, split out so the restore above can wrap it in a `finally`."""
    survivors: list[Mutant] = []
    coverage_gaps: list[Mutant] = []
    inconclusive: list[Mutant] = []
    run = 0
    crashes = 0
    with _WarmRunner(sandbox, timeout) as warm:
        if use_warm_worker:
            warm_baseline = warm.run(test_paths)
            if warm_baseline is None:
                use_warm_worker = False
            elif not _classify_code(warm_baseline, "the unmutated baseline in the warm worker"):
                raise MutationHarnessError(
                    "the tests pass in a fresh process but fail inside the reused worker, so "
                    "every mutant would be recorded as killed for a reason unrelated to the "
                    "mutation. Re-run with `use_warm_worker=False` to get a usable result, and "
                    "treat the difference between the two processes as the bug."
                )
        ordered = list(test_paths)
        # The wider net gets the same treatment as the primary set, and for a bigger prize: it is
        # dozens of files, `-x` stops at the first failure, and the measurement says these re-checks
        # are where a sweep's time goes -- 88% of the wall clock on the worst-covered file.
        ordered_wider = list(fallback_test_paths)
        budget = {"purge": 0.0, "pytest": 0.0, "overhead": 0.0, "recheck": 0.0, "mutants": 0.0}
        for mutant in mutants:
            _mutant_started = time.perf_counter()
            io.open(target, "w", encoding="utf-8", newline="").write(mutant.mutated_file_text)
            code = warm.run(ordered) if use_warm_worker else None
            _judged_at = time.perf_counter()
            if warm.last_timings:
                budget["purge"] += warm.last_timings.get("t_purge", 0.0)
                budget["pytest"] += warm.last_timings.get("t_pytest", 0.0)
                # What the parent spent that the worker did not: the pipe, the JSON, and the two
                # whole-file writes around every mutant.
                budget["overhead"] += max(0.0, (_judged_at - _mutant_started) - warm.last_timings.get("t_total", 0.0))
            if warm.last_failed:
                ordered = _lead_with(ordered, warm.last_failed)
            if code is None:
                result = _run_pytest(test_paths, sandbox, timeout)
                if result is None:
                    inconclusive.append(mutant)
                    io.open(target, "w", encoding="utf-8", newline="").write(original)
                    continue
                passed = _classify(result, f"mutant {mutant}", baseline_verified=True)
            else:
                passed = _classify_code(code, f"mutant {mutant}", baseline_verified=True)
                if code in (2, 3):
                    crashes += 1
                result = None
            run += 1
            if not passed and result is not None and _killed_by_crash(result.stdout):
                crashes += 1
            if passed:
                confirm = _run_pytest(test_paths, sandbox, timeout)
                if confirm is None:
                    inconclusive.append(mutant)
                    io.open(target, "w", encoding="utf-8", newline="").write(original)
                    continue
                if _classify(confirm, f"survivor re-check {mutant}", baseline_verified=True):
                    if fallback_test_paths:
                        # WARM, unlike the confirmation above. That one is cold on purpose -- it
                        # exists to escape state a purge cannot reach, which is why a warm survivor
                        # is a candidate rather than a finding. This one asks a different question:
                        # does a test OUTSIDE the map kill it? That needs a wider selection, not a
                        # fresh process. Measured, it was half of the dominant cost: on the weakly
                        # covered file the two cold runs per survivor were 43 of the 46 seconds a
                        # mutant took.
                        wider_code = warm.run(ordered_wider) if use_warm_worker else None
                        if wider_code is not None:
                            wider_killed = not _classify_code(wider_code, f"fallback re-check {mutant}", baseline_verified=True)
                            wider_killer = warm.last_failed
                        else:
                            # The worker died or was never used: fall back to the cold path rather
                            # than lose the answer.
                            wider = _run_pytest(ordered_wider, sandbox, timeout)
                            wider_killed = wider is not None and not _classify(wider, f"fallback re-check {mutant}", baseline_verified=True)
                            wider_killer = _first_failing_file(wider.stdout) if wider is not None else None
                        if wider_killed:
                            # Record WHICH file killed it. Without this the report says "fix the
                            # map" and leaves the operator to find the one test among dozens --
                            # and adding them all is precisely what the map exists to avoid.
                            killer = wider_killer
                            if killer:
                                object.__setattr__(mutant, "category", f"killed-by:{killer}")
                                # Lead with it next time. Gaps cluster: the same unlisted test
                                # usually kills many of them, and on the file that produced 65 gaps
                                # every one named the same killer.
                                ordered_wider = _lead_with(ordered_wider, killer)
                            coverage_gaps.append(mutant)
                            io.open(target, "w", encoding="utf-8", newline="").write(original)
                            continue
                    survivors.append(mutant)
            io.open(target, "w", encoding="utf-8", newline="").write(original)
            budget["mutants"] += 1
            # Everything after the verdict is a cold re-check: the confirmation and the wider net.
            budget["recheck"] += max(0.0, time.perf_counter() - _judged_at)
    return survivors, coverage_gaps, inconclusive, run, crashes, budget



def find_surviving_mutants(
    path: Path | str,
    test_paths: Sequence[Path | str],
    repo_root: Path | str,
    lines: Iterable[range] | range | None = None,
    limit: int | None = None,
    timeout: float = 300.0,
    cache_path: Path | str | None = None,
    use_cache: bool = True,
    use_warm_worker: bool = True,
    jobs: int = 1,
    _sandbox: Path | None = None,
    extra_fingerprint_paths: Sequence[Path | str] = (),
    fallback_test_paths: Sequence[Path | str] = (),
) -> MutationRun:
    """Mutants the tests did NOT catch.

    *path* and *test_paths* are relative to *repo_root*. The repository is COPIED to a temporary
    directory and every mutation happens there, so nothing this function does can damage the working
    tree -- including being killed outright, which is how the previous in-place design was found to
    be unsafe.

    Raises :class:`MutationHarnessError` rather than returning a misleading empty result when the
    harness cannot do its job: an unmutated baseline that does not pass, or a pytest exit code that
    means something other than pass or fail.
    """
    repo_root = Path(repo_root).resolve()
    relative = Path(path)

    cache_file = Path(cache_path) if cache_path else None
    # Anything that changes WHICH mutants run belongs in the key. Omitting these made raising
    # `limit` after a truncated sweep a silent no-op whenever a cache file was in play.
    key = fingerprint(
        repo_root,
        relative,
        test_paths,
        extra_fingerprint_paths,
        scope=(
            [[r.start, r.stop] for r in lines] if lines else None,
            limit,
            [str(t) for t in (fallback_test_paths or ())],
        ),
    )
    if use_cache and cache_file and cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}
        entry = cached.get(relative.as_posix())
        if entry and entry.get("fingerprint") == key:
            return MutationRun(
                survivors=[
                    Mutant(
                        path=Path(s["path"]),
                        line=s["line"],
                        column=s["column"],
                        description=s["description"],
                        original_span=s["original_span"],
                        mutated_span=s["mutated_span"],
                        category=s.get("category", ""),
                        context=s.get("context", ""),
                    )
                    for s in entry["survivors"]
                ],
                coverage_gaps=[_mutant_from_json(m) for m in entry.get("coverage_gaps", [])],
                inconclusive=[_mutant_from_json(m) for m in entry.get("inconclusive", [])],
                killed_by_crash=entry.get("killed_by_crash", 0),
                mutants_run=entry["mutants_run"],
                killed=entry["killed"],
                truncated=entry["truncated"],
                candidates_total=entry["candidates_total"],
                sampled_containers={k: tuple(v) for k, v in entry.get("sampled_containers", {}).items()},
                from_cache=True,
            )

    # A caller-owned sandbox (from `sweep_files`) is reused as-is: the copy, the cold
    # baseline and the worker start are the fixed costs a multi-file sweep should pay once.
    # `_sweep_partition` restores the target in a `finally`, so a shared tree is clean for
    # the next file even when a sweep raises.
    borrowed = _sandbox is not None
    sandbox_parent = Path(tempfile.mkdtemp(prefix="mutation_teeth_")) if not borrowed else None
    sandbox = _sandbox if borrowed else sandbox_parent / repo_root.name
    try:
        if not borrowed:
            shutil.copytree(repo_root, sandbox, ignore=_COPY_IGNORE, symlinks=False)
        target = sandbox / relative
        if not target.is_file():
            raise MutationHarnessError(
                f"{relative} does not exist under {repo_root}. "
                "`path` must be relative to `repo_root`, not absolute and not relative to the cwd."
            )

        baseline = _run_pytest(test_paths, sandbox, timeout)
        if baseline is None:
            raise MutationHarnessError(
                f"the unmutated baseline did not finish within {timeout}s, so nothing can be concluded. "
                "Raise `timeout`, or narrow `test_paths`."
            )
        if not _classify(baseline, "the unmutated baseline"):
            raise MutationHarnessError(
                "the unmutated baseline does not pass, so no mutant result would mean anything.\n"
                "Fix the failing tests first, then re-run.\n"
                f"{baseline.stdout[-2000:]}"
            )

        # The wider net decides verdicts too -- a failure in it RECLASSIFIES a survivor as a
        # coverage-map gap -- so it needs the same unmutated check the primary set gets. It was not
        # getting one, and the consequence was measured rather than imagined: a test that reads two
        # files from a SIBLING project raises FileNotFoundError inside the sandbox on every mutant,
        # pytest exits non-zero, and 65 real survivors in one module were reported as "killed by a
        # test the map does not list". That module then read as having no survivors at all, which is
        # the direction nobody investigates.
        #
        # A net that cannot run is not evidence, so it is dropped for this file and said out loud.
        wider_baseline_note = ""
        if fallback_test_paths:
            wider_baseline = _run_pytest(fallback_test_paths, sandbox, timeout)
            if wider_baseline is None or not _classify(wider_baseline, "the unmutated wider net"):
                broken = _first_failing_file(wider_baseline.stdout) if wider_baseline else None
                wider_baseline_note = (
                    "the wider net does not pass unmutated"
                    + (f" ({broken} fails in the sandbox)" if broken else "")
                    + " -- it cannot tell a coverage-map gap from a survivor, so it was NOT used"
                )
                fallback_test_paths = ()

        mutants, candidates_total, sampled = generate_mutants(target, lines=lines, limit=limit)
        for mutant in mutants:
            object.__setattr__(mutant, "path", relative)
        # Two candidates can produce a byte-identical file -- an operator landing on a span a
        # container sampler already covers, most often. Running the twin is pure cost, but its
        # verdict must be FANNED to every colliding mutant rather than recorded once: each carries
        # its own baseline key, and a silently unrun twin turns an accepted entry stale.
        twins: dict[str, list[Mutant]] = {}
        for mutant in mutants:
            twins.setdefault(mutant.mutated_file_text, []).append(mutant)
        duplicates = sum(len(g) - 1 for g in twins.values())
        mutants = [g[0] for g in twins.values()]

        # One partition per worker, each with its own copy of the tree. `jobs=1` is the
        # default and runs exactly the path that existed before: a consumer whose tests
        # share a port, a database or a fixed temp path gets FALSE KILLS from concurrency,
        # and the harness cannot see that condition from outside. The speedup reported for
        # this was measured on a loaded machine and is deliberately not claimed here.
        partitions = [mutants[i::jobs] for i in range(jobs)] if jobs > 1 else [mutants]
        partitions = [p for p in partitions if p]
        sandboxes = [sandbox]
        for index in range(1, len(partitions)):
            extra = sandbox_parent / f"{repo_root.name}_{index}"
            shutil.copytree(repo_root, extra, ignore=_COPY_IGNORE, symlinks=False)
            sandboxes.append(extra)
        results: list[tuple] = [()] * len(partitions)  # each: survivors, gaps, inconclusive, run, crashes, budget
        errors: list[BaseException] = []

        def _one(slot: int) -> None:
            try:
                results[slot] = _sweep_partition(
                    sandboxes[slot],
                    relative,
                    partitions[slot],
                    test_paths,
                    fallback_test_paths,
                    timeout,
                    use_warm_worker,
                )
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
                errors.append(exc)

        if len(partitions) == 1:
            _one(0)
        else:
            threads = [threading.Thread(target=_one, args=(i,)) for i in range(len(partitions))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        if errors:
            # A harness failure in any partition is a harness failure, full stop. Merging the
            # partitions that happened to finish would report a subset as if it were the whole.
            raise errors[0]

        survivors = [m for r in results for m in r[0]]
        coverage_gaps = [m for r in results for m in r[1]]
        inconclusive = [m for r in results for m in r[2]]
        run = sum(r[3] for r in results)
        crashes = sum(r[4] for r in results)
        budget = {k: sum(r[5].get(k, 0.0) for r in results) for k in ("purge", "pytest", "overhead", "recheck", "mutants")}
        # Source order, not completion order: a survivor list that reorders itself between
        # runs is a diff nobody can read.
        order = {id(m): i for i, m in enumerate(mutants)}
        survivors.sort(key=lambda m: order[id(m)])
        coverage_gaps.sort(key=lambda m: order[id(m)])
        inconclusive.sort(key=lambda m: order[id(m)])
        outcome = MutationRun(
            survivors=survivors,
            mutants_run=run,
            killed=run - len(survivors),
            # NOT gated on `limit`: container sampling drops candidates too, and gating on the
            # cap left the flag False while the report read as exhaustive.
            # `duplicates` are subtracted because they were never candidates for a SEPARATE run:
            # counting them as omitted would raise the TRUNCATED banner on a complete sweep, the
            # false-alarm direction this module already had to fix once.
            truncated=candidates_total - duplicates > len(mutants),
            candidates_total=candidates_total,
            sampled_containers=sampled,
            killed_by_crash=crashes,
            coverage_gaps=coverage_gaps,
            inconclusive=inconclusive,
            wider_net_note=wider_baseline_note,
            timings={k: round(v, 3) for k, v in budget.items()},
        )
    finally:
        if sandbox_parent is not None:
            shutil.rmtree(sandbox_parent, ignore_errors=True)

    if cache_file:
        # Re-taken AFTER the run. The sweep takes minutes; anything edited during one would
        # otherwise be filed under its NEW fingerprint with a verdict measured on the OLD content,
        # and the next run would replay a clean result for code that was never swept. A changed
        # fingerprint means the result describes nothing that still exists, so it is discarded
        # rather than stored under either key.
        if fingerprint(
            repo_root,
            relative,
            test_paths,
            extra_fingerprint_paths,
            scope=(
                [[r.start, r.stop] for r in lines] if lines else None,
                limit,
                [str(t) for t in (fallback_test_paths or ())],
            ),
        ) != key:
            return outcome
        try:
            existing = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.is_file() else {}
        except (OSError, json.JSONDecodeError):
            existing = {}
        existing[relative.as_posix()] = {
            "fingerprint": key,
            "mutants_run": outcome.mutants_run,
            "killed": outcome.killed,
            "truncated": outcome.truncated,
            "candidates_total": outcome.candidates_total,
            "sampled_containers": {k: list(v) for k, v in outcome.sampled_containers.items()},
            # Every caveat travels with the result. Dropping these made a replay read BETTER than
            # the run it replayed, which is the one direction a cache must never fail in.
            "killed_by_crash": outcome.killed_by_crash,
            "coverage_gaps": [_mutant_json(m) for m in outcome.coverage_gaps],
            "inconclusive": [_mutant_json(m) for m in outcome.inconclusive],
            "survivors": [
                {
                    "path": s.path.as_posix(),
                    "line": s.line,
                    "column": s.column,
                    "description": s.description,
                    "original_span": s.original_span,
                    "mutated_span": s.mutated_span,
                    "category": s.category,
                    "context": s.context,
                }
                for s in outcome.survivors
            ],
        }
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: a process killed mid-write left a truncated file, which the read path treats as
        # an empty cache -- recoverable, but it silently discards every other target's result.
        staging = cache_file.with_suffix(cache_file.suffix + ".tmp")
        staging.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
        staging.replace(cache_file)
    return outcome


def register_refresh_option(parser) -> None:
    """Add ``--refresh-mutation-survivors-baseline`` to a consuming repo's pytest options."""
    try:
        parser.addoption(REFRESH_FLAG, action="store_true", default=False, help="Re-pin the accepted mutation survivors.")
    except ValueError:
        # Already registered by another consumer of this package in the same session.
        pass


def _refresh_requested(request) -> bool:
    """xdist-safe: the flag reaches workers through the parsed config, not ``sys.argv``."""
    try:
        return bool(request.config.getoption(REFRESH_FLAG))
    except ValueError:
        return REFRESH_FLAG in sys.argv


def sweep_files(
    targets: "Sequence[tuple[Path | str, Sequence[Path | str]]]",
    repo_root: Path | str,
    *,
    lines_by_source: "dict[str, Iterable[range]] | None" = None,
    fallback_by_source: "dict[str, Sequence[Path | str]] | None" = None,
    **common,
) -> "dict[str, MutationRun]":
    """Sweep several files, paying the fixed per-sweep costs once.

    *targets* is ``[(source, test_paths), ...]``; *lines_by_source* and *fallback_by_source* carry
    the per-file arguments that differ. Everything in *common* is forwarded to
    :func:`find_surviving_mutants` unchanged, so this adds no behaviour of its own -- the results
    are the same objects, keyed by source, and each file keeps its own fingerprint and cache entry.

    The saving is the tree copy, the cold baseline and the worker start, which
    :func:`find_surviving_mutants` pays once per call and this pays once per sweep. It is a wrapper
    rather than a merge on purpose: merging the fingerprints would make one changed file invalidate
    every verdict, which is the opposite of what the cache exists for.
    """
    repo_root = Path(repo_root).resolve()
    lines_by_source = lines_by_source or {}
    fallback_by_source = fallback_by_source or {}
    out: dict[str, MutationRun] = {}
    shared: Path | None = None
    parent: Path | None = None
    try:
        for source, test_paths in targets:
            key = str(source)
            if shared is None:
                # Lazy: an all-cache-hit sweep copies nothing at all, which is the common case once
                # the cache is warm and the reason the copy is not hoisted out of the loop.
                parent = Path(tempfile.mkdtemp(prefix="mutation_teeth_"))
                shared = parent / repo_root.name
                shutil.copytree(repo_root, shared, ignore=_COPY_IGNORE, symlinks=False)
            out[key] = find_surviving_mutants(
                source,
                test_paths,
                repo_root,
                lines=lines_by_source.get(key),
                fallback_test_paths=fallback_by_source.get(key, ()),
                _sandbox=shared,
                **common,
            )
    finally:
        if parent is not None:
            shutil.rmtree(parent, ignore_errors=True)
    return out


def assert_no_new_surviving_mutant(
    path: Path | str,
    test_paths: Sequence[Path | str],
    repo_root: Path | str,
    baseline,
    request=None,
    **kwargs,
) -> None:
    """The ratchet half: fail on a survivor that is not already accepted.

    *baseline* is a :class:`py_ci_shared.baseline_ratchet.Baseline`; its accepted entries carry a
    mandatory human note, which ``baseline_hygiene`` polices for free. Survivors are keyed on a
    digest of the mutated SPAN rather than a line number, so an edit elsewhere in the file does not
    invalidate every accepted entry.

    Mechanically-noisy survivors (a sampled data-table row, an unobservable constant) are reported
    grouped under their category rather than listed, and never silently dropped: a hidden
    suppression list is the pattern ``gate_integrity`` explicitly argues against.
    """
    outcome = find_surviving_mutants(path, test_paths, repo_root, **kwargs)
    found = {m.key: f"{_UNJUSTIFIED} {m}" for m in outcome.survivors}
    if request is not None and _refresh_requested(request):
        # A refresh writes the KEYS, never the justifications. Regenerating with a plausible
        # placeholder turned "accept every survivor" into one command whose output was green --
        # which is the opposite of what a baseline is for. The marker below fails the very next
        # run until a human replaces it with a real reason.
        baseline.regenerate({k: v for k, v in found.items()})
        raise AssertionError(
            f"{len(found)} survivor(s) written to {baseline.path} WITHOUT justifications.\n"
            f"Each is marked {_UNJUSTIFIED!r} and will keep failing until you replace that marker "
            "with the reason the mutant cannot be observed. A refresh records what was found; it "
            "does not decide that the finding is acceptable."
        )
    if outcome.truncated or outcome.from_cache:
        # Not a failure -- a deliberate `limit` is a legitimate way to run this, and a cache hit is
        # the point of the cache. But on the green path NOTHING was printed, so an incomplete or
        # replayed result was indistinguishable from a complete measured one. A warning lands in
        # pytest's summary without stopping anyone.
        warnings.warn(f"mutation teeth for {path}: {outcome.summary()}", stacklevel=2)
    if outcome.inconclusive:
        raise AssertionError(
            f"{len(outcome.inconclusive)} mutant(s) could not be run to a verdict in "
            f"{path}, so this sweep does not support a pass.\n{outcome.summary()}"
        )
    if outcome.coverage_gaps:
        # NOT a survivor and NOT something to accept: a test that kills this already exists and is
        # missing from the caller's map. Reporting it without failing left the map wrong for as
        # long as anyone tolerated the line, and a cached replay then stopped mentioning it.
        listed = "\n".join(f"  {m}" for m in outcome.coverage_gaps)
        raise AssertionError(
            f"{len(outcome.coverage_gaps)} mutant(s) in {path} are killed by a test that the "
            f"coverage map does not list. Fix the MAP, not the tests:\n{listed}"
        )
    exit_code = baseline.enforce(
        found,
        label=f"mutation teeth: {path}",
        guidance=(
            f"{outcome.summary()}\n"
            "A survivor means the tests do not notice that change. Either add an assertion that "
            "does, or accept it in the baseline with a note saying why it cannot be observed."
        ),
    )
    if exit_code:
        raise AssertionError(f"mutation survivors not accounted for in {baseline.path}\n{outcome.summary()}")


def assert_revert_fails_tests(
    path: Path | str,
    old: str,
    new: str,
    test_paths: Sequence[Path | str],
    repo_root: Path | str,
    timeout: float = 300.0,
) -> None:
    """Reintroduce a defect by hand and require the tests to notice.

    Named for what it does rather than as the ``assert_*`` sibling of :func:`find_surviving_mutants`
    -- that role belongs to :func:`assert_no_new_surviving_mutant`. This is for what mutation
    testing cannot reach: a changed prompt, a reordered call, anything where the defect is prose or
    structure rather than an operator.

    A helper rather than a snippet because the hand-written version used ``str.replace``, which
    silently does nothing when the anchor has drifted: the revert never happened, the tests passed,
    and the pass was read as proof of teeth. Both the presence of *old* and the fact that the text
    changed are checked, and the work happens in a copy so a failure cannot leave the tree modified.
    """
    repo_root = Path(repo_root).resolve()
    relative = Path(path)
    sandbox_parent = Path(tempfile.mkdtemp(prefix="mutation_teeth_"))
    sandbox = sandbox_parent / repo_root.name
    try:
        shutil.copytree(repo_root, sandbox, ignore=_COPY_IGNORE, symlinks=False)
        target = sandbox / relative
        if not target.is_file():
            raise MutationHarnessError(f"{relative} does not exist under {repo_root}")
        original = io.open(target, encoding="utf-8", newline="").read()
        if old not in original:
            raise MutationHarnessError(
                f"revert anchor not found in {relative}: {old[:80]!r}\n"
                "The defect was never reintroduced, so a green run would prove nothing. "
                "Re-copy the anchor from the current file."
            )
        mutated = original.replace(old, new, 1)
        if mutated == original:
            raise MutationHarnessError(f"the revert changed nothing in {relative} -- `old` and `new` are identical")

        baseline = _run_pytest(test_paths, sandbox, timeout)
        if baseline is None:
            raise MutationHarnessError(f"the unmutated baseline did not finish within {timeout}s")
        if not _classify(baseline, "the unmutated baseline"):
            raise MutationHarnessError(
                "the unmutated baseline does not pass, so the teeth check means nothing. "
                f"Fix the failing tests first.\n{baseline.stdout[-2000:]}"
            )

        io.open(target, "w", encoding="utf-8", newline="").write(mutated)
        result = _run_pytest(test_paths, sandbox, timeout)
        if result is None:
            raise MutationHarnessError(f"the mutated run did not finish within {timeout}s")
        if _classify(result, "the mutated run", baseline_verified=True):
            raise AssertionError(
                f"The tests PASSED with the defect reintroduced in {relative}.\n"
                f"Reverted: {old[:100]!r}\n"
                "Most likely they do not assert what they claim to; possibly the reverted line is "
                "not on any path they execute. Either way this check cannot vouch for them.\n\n"
                f"{result.stdout[-2000:]}"
            )
    finally:
        shutil.rmtree(sandbox_parent, ignore_errors=True)
