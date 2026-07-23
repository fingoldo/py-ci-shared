"""Shared checks for the "a CHANGELOG bullet promises something, does the promise ever get
kept" consistency pattern.

Generalizes two independently-built checks in this ecosystem:

  * mlframe's ``test_changelog_sensor_cross_walk.py`` -- every ``fix(...)``-tagged bullet in a
    dated audit-cycle section must ALSO mention a regression-test/sensor reference, catching
    "fixed it but forgot the regression test" entries.
  * production_scrapers's 2026-07-23 gap-pass, which found 11 findings that CHANGELOG.md
    explicitly promised would be "flagged for the final disposition report" / "tracked under a
    future leaf" but never actually appeared in the referenced follow-up document -- a class of
    drift nothing automated caught until a dedicated 9-agent verification workflow was run by
    hand, well after the promises were made.

Both are the same shape: a bullet matching a TRIGGER pattern (a fix, a promise-to-follow-up)
must be SATISFIED, either by a pattern match within its own text (a sensor reference, mlframe's
case) or by its distinctive title/phrase showing up in another document entirely (a disposition
report, production_scrapers's case) -- this module supports both satisfaction modes from one
engine so a repo can wire up whichever (or both) apply to its own CHANGELOG convention.

Deliberately dependency-light: ``pytest`` is imported lazily inside the ``assert_*`` function,
matching this package's other modules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple, Optional

# Matches this ecosystem's dominant CHANGELOG bullet convention: `- **Title** rest of line`,
# optionally followed by indented continuation lines (a multi-line bullet body).
DEFAULT_BULLET_PATTERN = re.compile(r"^- \*\*[^*]+\*\*[^\n]*(?:\n[ \t]+[^\n-][^\n]*)*", re.MULTILINE)

# Extracts the bolded title text from a bullet matched by DEFAULT_BULLET_PATTERN.
DEFAULT_TITLE_PATTERN = re.compile(r"^- \*\*([^*]+)\*\*")

# A generic "this bullet promises later follow-up elsewhere" trigger, matching phrasing observed
# in practice across both consuming repos ("flagged for the final disposition report", "tracked
# under the future sql/ leaf", "carried forward to", "noted for a later pass").
DEFAULT_PROMISE_PATTERN = re.compile(
    r"(flagged for (the )?(final )?disposition" r"|tracked under" r"|carried forward to" r"|noted for (a )?(later|future)" r"|will be (addressed|tracked|handled)" r"|future leaf)",
    re.IGNORECASE,
)


class UnsatisfiedBullet(NamedTuple):
    title: str
    excerpt: str


def extract_section(text: str, section_pattern: re.Pattern) -> str:
    """From the first match of ``section_pattern`` in ``text`` down to (but not including) the
    second match, or end-of-text if there's only one. Returns ``""`` if there's no match at all
    (caller should treat that as "convention not yet applicable", not a failure)."""
    matches = list(section_pattern.finditer(text))
    if not matches:
        return ""
    start = matches[0].start()
    end = matches[1].start() if len(matches) > 1 else len(text)
    return text[start:end]


def find_unsatisfied_bullets(
    text: str,
    trigger_pattern: re.Pattern,
    satisfies_pattern: Optional[re.Pattern] = None,
    other_resolution_texts: Iterable[str] = (),
    bullet_pattern: re.Pattern = DEFAULT_BULLET_PATTERN,
    title_pattern: re.Pattern = DEFAULT_TITLE_PATTERN,
    min_title_len: int = 12,
) -> tuple[list[str], list[UnsatisfiedBullet]]:
    """Return ``(triggered_bullets, unsatisfied)``.

    A bullet matching ``trigger_pattern`` is "satisfied" (excluded from ``unsatisfied``) if
    EITHER: ``satisfies_pattern`` matches somewhere within the bullet's own text (mlframe's
    self-contained "fix cites a sensor" mode), OR the bullet's extracted title (via
    ``title_pattern``) appears verbatim as a substring of any of ``other_resolution_texts``
    (production_scrapers's cross-document "promise resolved in another file" mode). Either mode
    can be disabled by passing ``satisfies_pattern=None`` / an empty ``other_resolution_texts``;
    a repo can use one, the other, or both together.

    ``min_title_len`` guards against a spuriously short/generic title (e.g. a 3-character
    fragment from a malformed bullet) matching almost anything in a large resolution document by
    coincidence -- titles shorter than this are never treated as satisfied via the
    cross-document mode (they can still be satisfied via ``satisfies_pattern``).
    """
    bullets = bullet_pattern.findall(text)
    triggered = [b for b in bullets if trigger_pattern.search(b)]
    combined_other = "\n".join(other_resolution_texts)
    unsatisfied: list[UnsatisfiedBullet] = []
    for b in triggered:
        if satisfies_pattern is not None and satisfies_pattern.search(b):
            continue
        m = title_pattern.match(b)
        title = m.group(1).strip() if m else b[:80].strip()
        if combined_other and len(title) >= min_title_len and title in combined_other:
            continue
        unsatisfied.append(UnsatisfiedBullet(title=title, excerpt=b[:200].strip()))
    return triggered, unsatisfied


def assert_changelog_bullets_satisfy_pattern(
    changelog_path: Path,
    trigger_pattern: re.Pattern,
    satisfies_pattern: Optional[re.Pattern] = None,
    *,
    section_pattern: Optional[re.Pattern] = None,
    other_resolution_paths: Iterable[Path] = (),
    bullet_pattern: re.Pattern = DEFAULT_BULLET_PATTERN,
    title_pattern: re.Pattern = DEFAULT_TITLE_PATTERN,
    max_unsatisfied_fraction: float = 0.0,
    label: str = "bullet",
) -> None:
    """Fail if too many ``trigger_pattern``-matching bullets in ``changelog_path`` are
    unsatisfied -- see ``find_unsatisfied_bullets`` for what "satisfied" means.

    ``section_pattern``, if given, scopes the scan to ``extract_section(text, section_pattern)``
    (e.g. mlframe's dated audit-cycle heading) instead of the whole file; a file with no matching
    section is skipped (not failed) -- the convention isn't applicable yet, not violated.

    ``other_resolution_paths`` are read and concatenated as the cross-document satisfaction
    source (e.g. a ``DISPOSITION.md``); missing paths are silently skipped.

    ``max_unsatisfied_fraction`` (default ``0.0``, i.e. strict -- every triggered bullet must be
    satisfied) allows a soft threshold for conventions with a known, accepted rate of doc-only/
    genuinely-unresolvable entries (mlframe's sensor cross-walk uses ``0.15``). The hard-fail only
    fires when the fraction exceeds the threshold AND at least one bullet WAS satisfied (otherwise
    the sample is too small/the convention not yet established to drift-detect on -- mirrors
    mlframe's own ``cited_n > 0`` guard).
    """
    import pytest

    text = changelog_path.read_text(encoding="utf-8")
    if section_pattern is not None:
        text = extract_section(text, section_pattern)
        if not text:
            pytest.skip(f"{changelog_path.name} has no section matching section_pattern; convention not yet applicable")

    other_texts = [p.read_text(encoding="utf-8") for p in other_resolution_paths if p.exists()]
    triggered, unsatisfied = find_unsatisfied_bullets(text, trigger_pattern, satisfies_pattern, other_texts, bullet_pattern, title_pattern)
    if not triggered:
        pytest.skip(f"no {label}-triggering bullets found in scope; convention can't drift")

    satisfied_n = len(triggered) - len(unsatisfied)
    fraction = len(unsatisfied) / len(triggered)
    # The "at least one satisfied bullet" leniency only makes sense for a SOFT threshold
    # (max_unsatisfied_fraction > 0): it exists so a too-small sample doesn't false-fail on
    # statistical noise around a tolerated rate. In strict mode (0.0, the default -- every
    # promise must be kept), that concern doesn't apply: a single unsatisfied bullet in an
    # otherwise-empty sample is a real, not a noisy, violation.
    sample_too_small_to_trust = max_unsatisfied_fraction > 0 and satisfied_n == 0
    if fraction > max_unsatisfied_fraction and not sample_too_small_to_trust:
        titles = "\n  - ".join(u.title for u in unsatisfied)
        pytest.fail(
            f"{len(unsatisfied)}/{len(triggered)} {label}(s) ({fraction:.0%}) have no matching resolution "
            f"(threshold {max_unsatisfied_fraction:.0%}). Either resolve them, or if intentionally still open, "
            f"make sure they're captured in the resolution document(s) checked. Unsatisfied:\n  - " + titles
        )
