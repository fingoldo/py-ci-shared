"""Shared check: a finding marked RESOLVED names artefacts that exist.

An audit disposition is a sentence somebody wrote while the fix was still in flight. Nobody
re-walks a 160-row tracker afterwards, so the prose and the tree drift apart silently and the
tracker then reads as coverage. In the 2026-09-02 round eight dispositions across two repositories
recorded RESOLVED while describing changes nobody had made:

* a CI step "removed" that still pointed at a directory which had left the repo months earlier;
* ``permissions: contents: read`` "added to both workflows", present in neither;
* a pre-commit hook that "re-stages only what was already staged" and still ran ``git add -u``;
* size caps "added" to an edge function deployed with JWT verification off;
* ``migration 039`` named by a disposition, with no such file in ``supabase/migrations/``;
* ``tool/check-live-rpc-exists.py`` named by another, which did not exist either.

Every one was found later by a gate. This is the gate that finds them at the source, and it is
cheap: a disposition that claims a file, a script or a migration is making a checkable statement
about the tree, so check it.

What it does NOT try to do is judge whether the fix is correct - only whether the things it names
are there. A disposition that says "the callback upserts" is prose this cannot verify; one that
says ``migration 039`` or ``tool/check-x.py`` is a claim with a filename in it.

Deliberately regex over Markdown, no parser and no dependency. The finding-id and disposition
shapes are passed in, because every project writes its audit files slightly differently.

Usage::

    from py_ci_shared.audit_disposition_parity import assert_dispositions_name_real_artefacts

    def test_resolved_findings_name_things_that_exist():
        assert_dispositions_name_real_artefacts(
            REPO / "audits" / "implemented" / "2026-09-02", REPO
        )
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

# `- Disposition: RESOLVED - ...` / `Disposition: PARTIAL - ...`
_DISPOSITION_RE = re.compile(
    r"^-?\s*Disposition(?: of this (?:item|report))?:\s*(?P<verdict>[A-Z][A-Z' ]+?)\b\s*[-:]?\s*(?P<text>.*)$"
)
# A backticked token that looks like a repository path: it carries a separator and an extension.
_BACKTICK_PATH_RE = re.compile(r"`([\w./\-]*/[\w./\-]+\.[\w]{1,6})`")
# A bare path written without backticks, which audit prose does constantly.
_BARE_PATH_RE = re.compile(r"(?<![\w`/])((?:lib|tool|test|web|e2e|scripts|deploy|supabase|\.github|\.githooks)/[\w./\-]+\.[\w]{1,6})")
# `migration 039`, `migration 034`, `migrations 034-039`
_MIGRATION_RE = re.compile(r"\bmigrations?\s+(\d{3})(?:\s*[-–]\s*(\d{3}))?", re.IGNORECASE)
# Verdicts that assert something was done. DEFERRED and WON'T FIX assert the opposite.
_ASSERTIVE_VERDICTS = frozenset({"RESOLVED", "PARTIAL", "DOC", "FIXED", "DONE", "IMPLEMENTED"})


def _artefacts(text: str) -> set[str]:
    """Paths a disposition claims exist."""
    found = set(_BACKTICK_PATH_RE.findall(text))
    found.update(_BARE_PATH_RE.findall(text))
    # Strip a trailing `:123` line reference and any sentence punctuation that stuck.
    return {re.sub(r":\d+(?:-\d+)?$", "", p).rstrip(".,;)") for p in found}


def _migration_numbers(text: str) -> set[str]:
    numbers: set[str] = set()
    for start, end in _MIGRATION_RE.findall(text):
        if end:
            numbers.update(f"{n:03d}" for n in range(int(start), int(end) + 1))
        else:
            numbers.add(start)
    return numbers


def find_unsupported_dispositions(
    audit_dir: Path,
    repo_root: Path,
    *,
    migrations_dir: "Path | None" = None,
    ignore_paths: Iterable[str] = (),
    verdicts: Sequence[str] = tuple(sorted(_ASSERTIVE_VERDICTS)),
) -> list[str]:
    """Return one problem string per disposition that claims a file or migration which is absent.

    ``ignore_paths`` lists artefacts a disposition may legitimately name without them existing
    here: a path in another repository, or one a CORRECTION line is quoting precisely because it
    was missing.
    """
    assertive = {v.upper() for v in verdicts}
    ignored = set(ignore_paths)
    migrations_dir = migrations_dir or (repo_root / "supabase" / "migrations")
    migration_prefixes = (
        {p.name[:3] for p in migrations_dir.glob("*.sql")} if migrations_dir.is_dir() else set()
    )

    files = sorted(audit_dir.glob("*.md")) if audit_dir.is_dir() else []
    if not files:
        return [
            f"{audit_dir}: no audit files found - this check examined nothing, which reads as a pass."
        ]

    problems: list[str] = []
    # Two different counters: a file whose findings are ALL deferred is normal, while a file where
    # no line matches the disposition shape at all means this check is reading the wrong format
    # and would pass everything.
    seen_any = 0
    checked = 0
    for path in files:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            match = _DISPOSITION_RE.match(line.strip())
            if not match:
                continue
            seen_any += 1
            verdict = match.group("verdict").strip().upper()
            if verdict not in assertive:
                continue
            text = match.group("text")
            checked += 1

            for artefact in sorted(_artefacts(text)):
                if artefact in ignored or (repo_root / artefact).exists():
                    continue
                problems.append(
                    f"{path.name}:{lineno}: disposition says {verdict} and names `{artefact}`, "
                    f"which does not exist. Either the change was not made, or the disposition "
                    f"names the wrong path."
                )
            for number in sorted(_migration_numbers(text)):
                if number in migration_prefixes:
                    continue
                problems.append(
                    f"{path.name}:{lineno}: disposition says {verdict} and names migration "
                    f"{number}, and no {number}*.sql exists under "
                    f"{migrations_dir.name}/."
                )
    if seen_any == 0:
        problems.append(
            f"{audit_dir}: no line matched the disposition shape, so this check examined nothing. "
            f"Either the audit format differs from `Disposition: <VERDICT> - ...` or it is "
            f"pointed at the wrong directory."
        )
    return problems


def assert_dispositions_name_real_artefacts(
    audit_dir: Path,
    repo_root: Path,
    *,
    migrations_dir: "Path | None" = None,
    ignore_paths: Iterable[str] = (),
) -> None:
    """Fail when a RESOLVED/PARTIAL disposition names a file or migration that is not there."""
    import pytest

    problems = find_unsupported_dispositions(
        audit_dir, repo_root, migrations_dir=migrations_dir, ignore_paths=ignore_paths
    )
    if problems:
        pytest.fail(
            f"{len(problems)} disposition(s) claim something that is not in the tree:\n  "
            + "\n  ".join(problems)
        )
