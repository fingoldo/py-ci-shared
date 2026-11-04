"""Gate unit tests assert exact findings, not 'some finding contains a substring' (audit 33-F31).

``assert any(token in v for v in findings.values())`` passes with duplicate, extra or misplaced findings. The scanner tests
use ``assert sum(1 for v in ... if token in v) == 1`` (exactly one, no duplicates) or an equality on the full finding list.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Files already converted. Add a gate's test file here once its weak assertions are gone; the ban then keeps it that way.
STRICT_FILES = (
    "test_dart_scanners.py",
    "test_swallowed_exceptions_gaps.py",
    "test_unsafe_deserialization_gaps.py",
    "test_text_ratchet_gaps.py",
    "test_core_scoped_imports.py",
)
WEAK = re.compile(r"^\s*assert any\(|^\s*assert (?:found|findings)\s*$", re.MULTILINE)


def test_converted_gate_tests_have_no_any_substring_or_bare_truthiness_assertions():
    here = Path(__file__).parent
    offenders = [
        f"{name}:{text.count(chr(10), 0, m.start()) + 1}"
        for name in STRICT_FILES
        for text in [(here / name).read_text(encoding="utf-8")]
        for m in WEAK.finditer(text)
    ]
    assert offenders == []


def test_the_ban_pattern_catches_both_weak_shapes():
    assert WEAK.search("    assert any('x' in v for v in d.values())\n")
    assert WEAK.search("    assert findings\n")
    assert not WEAK.search("    assert sum(1 for v in d.values() if 'x' in v) == 1\n")
