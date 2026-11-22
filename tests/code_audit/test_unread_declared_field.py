"""Scanner tests for unread_declared_field: a public dataclass field whose name is never read anywhere in the tree."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.code_audit import scan_unread_declared_fields

from ._helpers import _write


def test_declared_field_never_read_is_flagged(tmp_path: Path):
    _write(
        tmp_path,
        "spec.py",
        """
from dataclasses import dataclass

@dataclass
class CeilingTarget:
    tolerance: float = 0.005
    label: str = "x"
""",
    )
    _write(
        tmp_path,
        "use.py",
        """
def caveat(target):
    return target.label
""",
    )
    findings = scan_unread_declared_fields(tmp_path)
    assert [f.check for f in findings] == ["unread_declared_field"]
    assert "tolerance" in findings[0].detail
    assert findings[0].line == 5


def test_field_read_anywhere_in_the_tree_is_clean(tmp_path: Path):
    _write(
        tmp_path,
        "spec.py",
        """
from dataclasses import dataclass

@dataclass
class CeilingTarget:
    tolerance: float = 0.005
""",
    )
    _write(
        tmp_path,
        "gen.py",
        """
def caveat(target):
    return 0.01 if abs(x) > target.calibrate_to.tolerance else 0
""",
    )
    assert scan_unread_declared_fields(tmp_path) == []


def test_generic_reader_in_tree_suppresses_the_rule(tmp_path: Path):
    _write(
        tmp_path,
        "spec.py",
        """
from dataclasses import dataclass, asdict

@dataclass
class Spec:
    unused_knob: int = 1
""",
    )
    _write(
        tmp_path,
        "hash.py",
        """
from dataclasses import asdict

def content_hash(spec):
    return repr(asdict(spec))
""",
    )
    assert scan_unread_declared_fields(tmp_path) == []


def test_classvar_private_and_undecorated_classes_are_not_fields(tmp_path: Path):
    _write(
        tmp_path,
        "m.py",
        """
from dataclasses import dataclass
from typing import ClassVar

@dataclass
class A:
    LIMIT: ClassVar[int] = 3
    _private: int = 0

class Plain:
    never_read: int = 0
""",
    )
    assert scan_unread_declared_fields(tmp_path) == []


def test_audit_exempt_marker_with_reason_suppresses_one_field(tmp_path: Path):
    _write(
        tmp_path,
        "spec.py",
        """
from dataclasses import dataclass

@dataclass
class Spec:
    reserved: int = 0  # audit-exempt: unread_declared_field -- kept for the on-disk format of v1 results
""",
    )
    assert scan_unread_declared_fields(tmp_path) == []
