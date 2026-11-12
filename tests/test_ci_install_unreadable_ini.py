"""A pytest.ini that cannot be decoded is reported, not read as "no testpaths" (audit 40-F3)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "test_ci_install_covers_conftest.py"
_SPEC = importlib.util.spec_from_file_location("ci_install_builder", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
builder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(builder)

from py_ci_shared.ci_install_covers_conftest import find_ci_install_gaps


def test_an_undecodable_pytest_ini_is_named_as_unparsed(tmp_path: Path) -> None:
    """Negative control (40-F3): the old code treated the unreadable ini as having no testpaths and reported nothing about it."""
    root = builder._repo(
        tmp_path,
        {
            ".github/workflows/ci.yml": builder._job("pip install -e .", test="pytest"),  # no paths: the ini's testpaths decide the targets
            "pyproject.toml": "[project]\nname = 'x'\n",
        },
    )
    (root / "pytest.ini").write_bytes(b"\x80\x81[pytest]\n\x82\n")  # not UTF-8 and no BOM: undecodable
    rendered = find_ci_install_gaps(root)
    assert any("pytest.ini" in line and "unparsable" in line for line in rendered), rendered
