"""52-T2: ``py-ci-shared run-all`` warns when the imported package is not the checkout it is run in."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared import cli


def _checkout(tmp_path: Path) -> Path:
    package = tmp_path / "src" / "py_ci_shared"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = '1.0'\n", encoding="utf-8")
    return tmp_path


def test_package_imported_from_an_install_elsewhere_is_reported(tmp_path):
    root = _checkout(tmp_path / "checkout")
    installed = tmp_path / "site-packages" / "py_ci_shared" / "__init__.py"
    message = cli._install_mismatch(root, str(installed))
    assert message is not None
    assert "site-packages" in message
    assert "pip install -e ." in message


def test_package_imported_from_the_checkout_is_silent(tmp_path):
    root = _checkout(tmp_path / "checkout")
    assert cli._install_mismatch(root, str(root / "src" / "py_ci_shared" / "__init__.py")) is None


def test_repo_that_is_not_py_ci_shared_is_silent(tmp_path):
    other = tmp_path / "consumer"
    other.mkdir()
    assert cli._install_mismatch(other, str(tmp_path / "site-packages" / "py_ci_shared" / "__init__.py")) is None
