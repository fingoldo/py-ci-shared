"""Scanner tests for unguarded_optional_test_import: a test module that imports an optional dependency without importorskip."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.code_audit import scan_unguarded_optional_test_imports

from ._helpers import _write


def test_module_level_lightning_import_without_guard_is_flagged(tmp_path: Path):
    _write(
        tmp_path,
        "test_neural.py",
        """
import lightning
from mylib import thing

def test_it():
    assert thing
""",
    )
    findings = scan_unguarded_optional_test_imports(tmp_path)
    assert [f.check for f in findings] == ["unguarded_optional_test_import"]
    assert findings[0].line == 1


def test_importorskip_before_the_import_is_clean(tmp_path: Path):
    _write(
        tmp_path,
        "test_neural.py",
        """
import pytest
pytest.importorskip("lightning")
from lightning.pytorch import Trainer
""",
    )
    assert scan_unguarded_optional_test_imports(tmp_path) == []


def test_guard_after_the_import_does_not_help(tmp_path: Path):
    _write(
        tmp_path,
        "test_late.py",
        """
import lightning
import pytest
pytest.importorskip("lightning")
""",
    )
    assert len(scan_unguarded_optional_test_imports(tmp_path)) == 1


def test_import_inside_a_test_function_is_not_module_level(tmp_path: Path):
    _write(
        tmp_path,
        "test_lazy.py",
        """
def test_it():
    import lightning
    assert lightning
""",
    )
    assert scan_unguarded_optional_test_imports(tmp_path) == []


def test_non_test_module_is_not_checked(tmp_path: Path):
    _write(
        tmp_path,
        "trainer.py",
        """
import lightning
""",
    )
    assert scan_unguarded_optional_test_imports(tmp_path) == []
