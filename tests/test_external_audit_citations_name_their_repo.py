"""An audit folder cited from src names the repository that holds it (audit 43-F4): this repository does not contain those folders."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "py_ci_shared"
EXTERNAL = re.compile(r"audits/(full_audit_\d{4}-\d{2}-\d{2}|\d{4}-\d{2}-\d{2}-full-audit)")
NAMED = re.compile(r"\b(mlframe|social|autopsia|glossum|pyutilz|upwork|noema|repository)\b", re.IGNORECASE)


def unnamed_external_citations(text: str) -> list[str]:
    """Lines that cite an external audit folder without saying which repository holds it."""
    return [line.strip() for line in text.splitlines() if EXTERNAL.search(line) and not NAMED.search(line)]


def test_every_external_audit_citation_in_src_names_its_repository() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        offenders += [f"{path.relative_to(REPO).as_posix()}: {line}" for line in unnamed_external_citations(path.read_text(encoding="utf-8"))]
    assert offenders == [], offenders


def test_the_check_flags_an_unnamed_citation() -> None:
    """Negative control (43-F4): the wording the three docstrings had before the fix is flagged."""
    assert unnamed_external_citations("confirmed in the wild (audits/full_audit_2026-07-21/x.md)") != []
    assert unnamed_external_citations("confirmed in the wild (mlframe repository, audits/full_audit_2026-07-21/x.md)") == []
