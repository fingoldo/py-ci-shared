"""Data model of :mod:`py_ci_shared.mutation_teeth`: the version, the result types and the source I/O.

Split out of ``mutation_teeth`` so each part stays readable; everything here is re-exported from there.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import shutil
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from ._core import SourceError, read_source

#: Bumped whenever the operator set or the run semantics change, so a cached result computed by an
#: older harness is not reused by a newer one. Without it, adding an operator would silently keep
#: reporting the old survivor list.
HARNESS_VERSION = "14"  # 14: the tracked files walk ASTs with _core.walk (same order as ast.walk, faster)
# 13: a ratchet refresh writes survivors under the shrink-only baseline rule with growth allowed
# 12: the fingerprint walks test dirs with DEFAULT_EXCLUDE (pytest's norecursedirs), so a .venv or
# build/ copy of a test no longer enters it
# 11: `min` describes itself as "min becomes max", `not` removal keeps the next token, emptied bytes stay bytes,
# table sampling applies to every operator, twins share one verdict, warm timeouts are inconclusive and crash kills are counted
# 10: string-literal mutants skip non-string constants, the container path returns a list on a SyntaxError,
# and the scope fingerprint ignores a non-range entry -- each changes WHICH mutants a run generates
# 9: lint and formatting only (ruff 0.16.1 clean-up), bumped because the gate is content-based
# 8: a mutant that stops pytest starting (exit 4/5) is a kill, not a refusal

#: Written in place of a justification by a refresh, and rejected on the next run. A refresh
#: that produced an acceptable-looking note made accepting every survivor a one-command,
#: green-output operation.
_UNJUSTIFIED = "NEEDS-JUSTIFICATION:"

REFRESH_FLAG = "--refresh-mutation-survivors-baseline"

_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".mypy_cache",
    ".hypothesis",
    ".benchmarks",
    ".cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
)

#: Container literals larger than this are SAMPLED rather than exhausted. Measured on four real
#: modules: two lookup tables covering 18% of one file's lines produced 64% of its mutants, and a
#: survivor list where half the entries are dictionary rows is one nobody reads to the end.
_CONTAINER_SAMPLE = 3


class MutationHarnessError(RuntimeError):
    """The harness could not do its job. Never confused with "the mutant survived"."""


@dataclass(frozen=True)
class SourceText:
    """A target file's text plus what is needed to write a mutant back byte-compatibly.

    The text is decoded the way the interpreter decodes it (BOM, PEP 263 cookie, UTF-8) with the BOM
    stripped, because ``ast`` rejects a leading U+FEFF and every AST operator then produced nothing.
    Line endings are kept as they are in the file.
    """

    text: str
    encoding: str = "utf-8"
    bom: bool = False

    def encode(self, text: str) -> bytes:
        return (codecs.BOM_UTF8 if self.bom else b"") + text.encode(self.encoding)


def read_target(path: Union[Path, str]) -> SourceText:
    """Read a mutation target, raising :class:`MutationHarnessError` for anything unreadable."""
    target = Path(path)
    try:
        text = read_source(target)
        raw = target.read_bytes()
    except (SourceError, OSError) as exc:
        raise MutationHarnessError(f"cannot read {target}: {exc}") from exc
    bom = raw.startswith(codecs.BOM_UTF8)
    encoding = "utf-8"
    if target.suffix.lower() in (".py", ".pyi", ".pyw"):
        try:
            detected, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        except SyntaxError:  # pragma: no cover - read_source already rejected the same bytes
            detected = "utf-8"
        encoding = "utf-8" if detected == "utf-8-sig" else detected
    return SourceText(text=text, encoding=encoding, bom=bom)


def write_target(path: Union[Path, str], source: SourceText, text: str) -> None:
    """Write *text* with *source*'s encoding and BOM, bytes exactly, no newline translation."""
    Path(path).write_bytes(source.encode(text))


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
    #: container name -> (rows kept, rows in the table), for every table a candidate in scope was sampled out of.
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
                f"{len(self.inconclusive)} mutants were INCONCLUSIVE (the run timed out); they are " "neither killed nor survived, so this sweep is incomplete"
            )
        if self.wider_net_note:
            # Loud, because its absence silently changes what a survivor MEANS: without the net,
            # every survivor is "no listed test kills this", which is a weaker claim.
            parts.append(f"WIDER NET UNUSED: {self.wider_net_note}")
        if self.coverage_gaps:
            # Reported separately and loudly: these are NOT test gaps. A reader who treats them as
            # survivors writes a test that already exists.
            named = sorted({m.category.split(":", 1)[1] for m in self.coverage_gaps if m.category.startswith("killed-by:")})
            detail = f" -- add {', '.join(named)}" if named else ""
            parts.append(
                f"{len(self.coverage_gaps)} 'survivors' were killed by a test the coverage map does " f"not list -- fix the map, not the tests{detail}"
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
