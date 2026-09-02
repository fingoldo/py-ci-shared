"""Shared check: a counted fact stated in prose is computed from the repo, not typed by hand.

The single most reliably recurring documentation defect across three audit waves of one
consuming repo -- present in every wave, every time -- was a hand-typed count that had
drifted: "24 backward-compat aliases" against a 27-entry map, "1900+ tests" understating
the suite by about 1600, a meta-test file count stale within the same session that wrote
it, a documented meta-suite runtime six times faster than the measured one. Each is
trivially computable. None was computed.

Two rules:

* **Parity.** A registered claim's regex must match its file (a claim whose anchor text was
  reworded has stopped being checked, which is its own finding), and the captured number
  must equal what the callable computes. A number that is right today becomes
  self-verifying; a number that is wrong fails immediately with both values named.
* **Dating.** A volatile number that CANNOT be computed -- a measured runtime, a coverage
  percentage, a benchmark speedup -- must carry an ``as of YYYY-MM-DD``-style qualifier
  near it, so a reader can weigh it instead of trusting it. This rule is advisory by
  construction: prose legitimately quotes historical numbers, and the qualifier is a
  convention rather than something the repo can prove.

Usage (in a consuming repo's test suite)::

    from py_ci_shared.prose_numeric_claims import NumericClaim, assert_numeric_claims_match

    CLAIMS = [
        NumericClaim(README, r"across (\\d+) providers", lambda: len(PROVIDERS), "LLM provider count"),
    ]

    def test_prose_numeric_claims():
        assert_numeric_claims_match(CLAIMS)

``pytest`` is imported lazily, matching this package's other modules.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# `as of 2026-09-02`, `(measured 2026-09-02)`, `on 2026-09-02` -- any ISO date near the claim.
_DATE_QUALIFIER_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
# The volatile shapes worth demanding a date for: a percentage, or a duration.
_VOLATILE_CLAIM_RES = (
    re.compile(r"(?<![\w.])\d{1,3}(?:\.\d+)?\s?%"),
    re.compile(r"(?<![\w.])[~<>]?\s?\d+(?:\.\d+)?\s?(?:s|sec|secs|seconds|min|mins|minutes|ms)\b"),
)


@dataclass(frozen=True)
class NumericClaim:
    """One counted fact asserted in prose, paired with the code that computes the truth.

    Args:
        path: the prose file the claim lives in.
        pattern: a regex with exactly ONE capture group, capturing the number. It must match
            the file: a claim whose anchor text was reworded away is reported, because a
            silently-unmatched claim is an unchecked claim.
        compute: returns the true value. Kept as a callable so an expensive truth (a pytest
            collection count) can be supplied only where it is affordable to run.
        description: what the number means, quoted back in the failure message.
        tolerance: allowed absolute difference. 0 for exact counts; non-zero for a number
            the prose deliberately rounds.
        allow_multiple: True when the same claim is repeated in the file and every occurrence
            must match (the repeated-claim case is how one copy goes stale unnoticed).
    """

    path: Path
    pattern: str
    compute: Callable[[], float]
    description: str
    tolerance: float = 0.0
    allow_multiple: bool = True


def find_stale_claims(claims: Iterable[NumericClaim]) -> list[str]:
    """Return one message per claim whose anchor is missing or whose number is wrong."""
    problems: list[str] = []
    for claim in claims:
        if not claim.path.is_file():
            problems.append(f"{claim.path}: file does not exist, so the claim {claim.description!r} cannot be checked")
            continue
        text = claim.path.read_text(encoding="utf-8")
        matches = list(re.finditer(claim.pattern, text))
        if not matches:
            problems.append(
                f"{claim.path.name}: the claim {claim.description!r} no longer matches its anchor "
                f"pattern {claim.pattern!r} -- the prose was reworded and the number stopped being "
                f"checked. Update the pattern or drop the claim deliberately."
            )
            continue
        if not claim.allow_multiple and len(matches) > 1:
            problems.append(f"{claim.path.name}: the claim {claim.description!r} matched {len(matches)} times but is declared single-occurrence")
            continue
        truth = claim.compute()
        for match in matches:
            stated = float(match.group(1).replace(",", ""))
            if abs(stated - truth) > claim.tolerance:
                line = text[: match.start()].count("\n") + 1
                problems.append(f"{claim.path.name}:{line}: {claim.description} states {match.group(1)} but the repo has {truth:g}")
    return problems


def assert_numeric_claims_match(claims: Iterable[NumericClaim]) -> None:
    """Fail if any registered prose number disagrees with the repo it describes."""
    import pytest

    problems = find_stale_claims(claims)
    if problems:
        pytest.fail(f"{len(problems)} prose numeric claim(s) are stale or unanchored:\n  " + "\n  ".join(problems))


def find_undated_volatile_claims(paths: Iterable[Path], covered_patterns: Sequence[str] = (), context_lines: int = 3) -> list[str]:
    """Return one message per percentage/duration figure with no nearby ISO date.

    ``covered_patterns`` names the regexes already policed by :func:`find_stale_claims` --
    a computed claim needs no date, since it can never go stale. ``context_lines`` is how
    far above/below the figure a qualifying date may sit, which is what lets one dated
    paragraph cover the several measurements it introduces.
    """
    findings: list[str] = []
    covered = [re.compile(p) for p in covered_patterns]
    for path in paths:
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if any(pattern.search(line) for pattern in covered):
                continue
            for volatile_re in _VOLATILE_CLAIM_RES:
                match = volatile_re.search(line)
                if match is None:
                    continue
                window = "\n".join(lines[max(0, index - context_lines) : index + context_lines + 1])
                if not _DATE_QUALIFIER_RE.search(window):
                    findings.append(f"{path.name}:{index + 1}: undated volatile figure {match.group(0).strip()!r} -- add an 'as of YYYY-MM-DD' qualifier or make it computed")
                break
    return findings
