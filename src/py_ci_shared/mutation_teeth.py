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
import shutil
import subprocess
import sys
import tempfile
import threading
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
    "register_refresh_option",
]

#: Bumped whenever the operator set or the run semantics change, so a cached result computed by an
#: older harness is not reused by a newer one. Without it, adding an operator would silently keep
#: reporting the old survivor list.
HARNESS_VERSION = "3"  # 3: closure resolves `from .mod import name`; the key covers scope

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
    from_cache: bool = False

    def summary(self) -> str:
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
        if self.coverage_gaps:
            # Reported separately and loudly: these are NOT test gaps. A reader who treats them as
            # survivors writes a test that already exists.
            parts.append(
                f"{len(self.coverage_gaps)} 'survivors' were killed by a test the coverage map does "
                "not list -- fix the map, not the tests"
            )
        if self.truncated:
            parts.append(f"TRUNCATED: {self.candidates_total} candidates existed, {self.mutants_run} were run")
        for name, (shown, total) in sorted(self.sampled_containers.items()):
            parts.append(f"sampled {shown} of {total} entries in {name}")
        if self.from_cache:
            parts.append("(cached: nothing in the import closure changed)")
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
}
_NAME_SWAP = {
    "and": ("or", "and becomes or"),
    "or": ("and", "or becomes and"),
    "True": ("False", "True becomes False"),
    "False": ("True", "False becomes True"),
    "is": ("is not", "is becomes is not"),
    "in": ("not in", "in becomes not in"),
}


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
            if (s := span(node.annotation)) is not None:
                out.append(s)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in [*args.args, *args.posonlyargs, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is not None and (s := span(arg.annotation)) is not None:
                    out.append(s)
            if (s := span(node.returns)) is not None:
                out.append(s)
    return out


def _container_members(source: str) -> dict[int, str]:
    """Absolute start index of each element of a large module-level container -> container name.

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
    out: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.Dict, ast.Set, ast.List, ast.Tuple)):
            continue
        name = next((t.id for t in node.targets if isinstance(t, ast.Name)), "<container>")
        elements: list[ast.AST] = []
        if isinstance(node.value, ast.Dict):
            elements = [k for k in node.value.keys if k is not None] + list(node.value.values)
        else:
            elements = list(node.value.elts)
        if len(elements) <= _CONTAINER_SAMPLE:
            continue
        keep = {(seed + i) % len(elements) for i in range(_CONTAINER_SAMPLE)}
        for index, element in enumerate(elements):
            if index in keep or getattr(element, "lineno", None) is None:
                continue
            out[_abs_index(source, starts, element.lineno, element.col_offset)] = name
    return out


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
        category = f"noise:table-sample:{sampled_out[abs_start]}" if abs_start in sampled_out else ""

        def emit(end: int, replacement: str, description: str) -> None:
            out.append((abs_start, end, row, replacement, description, category))

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
    candidates = _token_candidates(source, skip_coupled_constants) + _statement_call_candidates(source)
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
        total += 1
        if category.startswith("noise:table-sample:"):
            name = category.split(":", 2)[2]
            shown, seen = sampled.get(name, (0, 0))
            sampled[name] = (shown, seen + 1)
            continue
        mutated = source[:abs_start] + replacement + source[abs_end:]
        if mutated == source:
            continue
        try:
            ast.parse(mutated)
        except SyntaxError:
            continue  # a mutant that does not compile tests nothing
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
        for candidate in (repo_root.joinpath(*parts).with_suffix(".py"), repo_root.joinpath(*parts, "__init__.py")):
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


def _pytest_env() -> dict[str, str]:
    """Environment for a nested pytest run.

    ``PYTEST_ADDOPTS`` is cleared because a consuming repo's ``--cov-fail-under`` would fail every
    mutant run for the wrong reason and make every mutant look killed -- the same exit-code
    overloading this module was rewritten to abolish.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
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


def _classify_code(code: int, what: str) -> bool:
    """True if the tests PASSED. Raises when the exit code means neither pass nor fail."""
    if code == 0:
        return True
    if code == 1:
        return False
    raise MutationHarnessError(
        f"pytest exited {code} on {what}, which is neither pass (0) nor fail (1). "
        "4 = usage error, 5 = nothing collected, 2 = interrupted, 3 = internal error. "
        "Check `test_paths` and that the tests are reachable from `repo_root`."
    )


def _classify(result, what: str) -> bool:
    """True if the tests PASSED. Raises when the exit code means neither pass nor fail."""
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise MutationHarnessError(
        f"pytest exited {result.returncode} on {what}, which is neither pass (0) nor fail (1). "
        "4 = usage error, 5 = nothing collected, 2 = interrupted, 3 = internal error.\n"
        "Check `test_paths` and that the tests are reachable from `repo_root`.\n"
        f"{result.stdout[-2000:]}"
    )


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

    sandbox_parent = Path(tempfile.mkdtemp(prefix="mutation_teeth_"))
    sandbox = sandbox_parent / repo_root.name
    try:
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

        mutants, candidates_total, sampled = generate_mutants(target, lines=lines, limit=limit)
        for mutant in mutants:
            object.__setattr__(mutant, "path", relative)

        original = io.open(target, encoding="utf-8", newline="").read()
        survivors: list[Mutant] = []
        coverage_gaps: list[Mutant] = []
        inconclusive: list[Mutant] = []
        run = 0
        crashes = 0
        with _WarmRunner(sandbox, timeout) as warm:
            for mutant in mutants:
                io.open(target, "w", encoding="utf-8", newline="").write(mutant.mutated_file_text)
                code = warm.run(test_paths) if use_warm_worker else None
                if code is None:
                    # The worker died, timed out, or was never used. A cold run is slower but
                    # always available, so a worker problem degrades performance rather than
                    # silently dropping a mutant from the denominator.
                    result = _run_pytest(test_paths, sandbox, timeout)
                    if result is None:
                        # Not a survivor, and not a kill either. Dropping it silently shrank the
                        # denominator while the report still read as complete.
                        inconclusive.append(mutant)
                        io.open(target, "w", encoding="utf-8", newline="").write(original)
                        continue
                    passed = _classify(result, f"mutant {mutant}")
                else:
                    passed = _classify_code(code, f"mutant {mutant}")
                    result = None  # the warm worker returns a code, not output, so a
                    # crash cannot be distinguished from an assertion failure on this
                    # path. The count is therefore a lower bound, which the summary says.
                run += 1
                if not passed and result is not None and _killed_by_crash(result.stdout):
                    crashes += 1
                if passed:
                    # Re-verify in a COLD process before believing it. A purge cannot reach state
                    # held inside an installed third-party package, so a warm survivor is a
                    # candidate, not a finding. Survivors are rare, so this costs little, and a
                    # false survivor is the outcome that wastes a human's afternoon.
                    confirm = _run_pytest(test_paths, sandbox, timeout)
                    if confirm is None:
                        # `killed` is `run - len(survivors)`, so falling through here recorded a
                        # mutant nobody managed to run as one the tests caught -- the unsafe
                        # direction, and the only re-check on this path that failed that way.
                        inconclusive.append(mutant)
                        io.open(target, "w", encoding="utf-8", newline="").write(original)
                        continue
                    if _classify(confirm, f"survivor re-check {mutant}"):
                        # Before believing it, ask the wider set. "No test kills this" and "no
                        # LISTED test kills this" are different findings, and only the first is
                        # about the tests. Run only for survivors, which are rare -- listing the
                        # wider set as primary would make every mutant cost minutes.
                        if fallback_test_paths:
                            wider = _run_pytest(fallback_test_paths, sandbox, timeout)
                            if wider is not None and not _classify(wider, f"fallback re-check {mutant}"):
                                coverage_gaps.append(mutant)
                                io.open(target, "w", encoding="utf-8", newline="").write(original)
                                continue
                        survivors.append(mutant)
                io.open(target, "w", encoding="utf-8", newline="").write(original)

        outcome = MutationRun(
            survivors=survivors,
            mutants_run=run,
            killed=run - len(survivors),
            # NOT gated on `limit`: container sampling drops candidates too, and gating on the
            # cap left the flag False while the report read as exhaustive.
            truncated=candidates_total > len(mutants),
            candidates_total=candidates_total,
            sampled_containers=sampled,
            killed_by_crash=crashes,
            coverage_gaps=coverage_gaps,
            inconclusive=inconclusive,
        )
    finally:
        shutil.rmtree(sandbox_parent, ignore_errors=True)

    if cache_file:
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
    found = {m.key: f"{m} -- accepted because ..." for m in outcome.survivors}
    if request is not None and _refresh_requested(request):
        baseline.regenerate(found)
        return
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
        if _classify(result, "the mutated run"):
            raise AssertionError(
                f"The tests PASSED with the defect reintroduced in {relative}.\n"
                f"Reverted: {old[:100]!r}\n"
                "Most likely they do not assert what they claim to; possibly the reverted line is "
                "not on any path they execute. Either way this check cannot vouch for them.\n\n"
                f"{result.stdout[-2000:]}"
            )
    finally:
        shutil.rmtree(sandbox_parent, ignore_errors=True)
