"""A module that never names a known dataclass is not walked, and one that does still gets its wrong ``getattr`` literal flagged."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.code_audit import scan_getattr_literal_on_known_dataclass


def _tree(root: Path) -> None:
    (root / "shapes.py").write_text("from dataclasses import dataclass\n\n\n@dataclass\nclass Box:\n    width: int = 1\n", encoding="utf-8")
    (root / "reader.py").write_text('from shapes import Box\n\n\ndef read(box: Box):\n    return getattr(box, "height", 0)\n', encoding="utf-8")
    (root / "unrelated.py").write_text('def read(thing):\n    thing = object()\n    return getattr(thing, "height", 0)\n', encoding="utf-8")


def test_only_the_module_that_names_the_dataclass_reports_and_the_skipped_one_adds_nothing(tmp_path):
    """The reader of a ``Box`` is flagged for a field it lacks; the module without the name contributes no finding and no error."""
    _tree(tmp_path)
    findings = scan_getattr_literal_on_known_dataclass(tmp_path)
    assert [(f.file, f.line) for f in findings] == [("reader.py", 5)]
