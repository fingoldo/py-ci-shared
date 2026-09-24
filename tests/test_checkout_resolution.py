"""Unit tests for the shared checkout-resolution check: real packages on sys.path, real directory copies."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.checkout_resolution import assert_modules_resolve_to_checkout, module_resolution_problems, resolved_in_a_copy


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A tiny src-layout repo whose package is importable, and forgotten again afterwards."""
    root = tmp_path / "repo"
    pkg = root / "src" / "zz_probe_pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(root / "src"))
    yield root
    for name in [m for m in sys.modules if m == "zz_probe_pkg" or m.startswith("zz_probe_pkg.")]:
        del sys.modules[name]


def test_a_package_inside_the_checkout_passes(repo):
    assert_modules_resolve_to_checkout(repo, ["zz_probe_pkg", "zz_probe_pkg.sub"])


def test_a_package_from_elsewhere_is_reported(repo, tmp_path):
    other = tmp_path / "other_checkout"
    other.mkdir()

    problems = module_resolution_problems(other, ["zz_probe_pkg"])

    assert len(problems) == 1 and "outside" in problems[0]


def test_a_namespace_portion_is_reported(tmp_path, monkeypatch):
    """The autopsia failure: a directory named like the package, no __init__.py, imports with no __file__."""
    (tmp_path / "zz_ns_pkg").mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        problems = module_resolution_problems(tmp_path, ["zz_ns_pkg"])
    finally:
        sys.modules.pop("zz_ns_pkg", None)

    assert len(problems) == 1 and "namespace portion" in problems[0]


def test_an_empty_module_list_checks_nothing_and_says_so(repo):
    with pytest.raises(pytest.fail.Exception, match="nothing was checked"):
        assert_modules_resolve_to_checkout(repo, [])


def test_a_copy_that_puts_its_own_src_first_imports_itself(tmp_path):
    """The probe end to end: a copy with a rootdir conftest that prepends its src imports the copy."""
    root = tmp_path / "orig"
    (root / "src" / "zz_copy_pkg").mkdir(parents=True)
    (root / "src" / "zz_copy_pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / 'src'))\n",
        encoding="utf-8",
    )

    resolved = resolved_in_a_copy(root, "zz_copy_pkg", tmp_path / "work", timeout=120)

    assert resolved and str((tmp_path / "work" / "copy").resolve()) in str(Path(resolved).resolve())


def _orig(tmp_path: Path, name: str, *, prepend_src: bool = True) -> Path:
    root = tmp_path / "orig"
    (root / "src" / name).mkdir(parents=True)
    (root / "src" / name / "__init__.py").write_text("", encoding="utf-8")
    if prepend_src:
        (root / "conftest.py").write_text(
            "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / 'src'))\n",
            encoding="utf-8",
        )
    return root


def test_a_second_call_with_the_same_workdir_takes_a_fresh_copy(tmp_path):
    root = _orig(tmp_path, "zz_copy_pkg2")
    work = tmp_path / "work"
    first = resolved_in_a_copy(root, "zz_copy_pkg2", work, timeout=120)
    (work / "copy" / "stale_marker.txt").write_text("x", encoding="utf-8")
    second = resolved_in_a_copy(root, "zz_copy_pkg2", work, timeout=120)
    assert first and first == second
    assert not (work / "copy" / "stale_marker.txt").exists()


def test_a_sibling_directory_sharing_the_prefix_is_not_the_copy():
    from py_ci_shared.checkout_resolution import _is_within

    base = Path("/w")
    assert _is_within(base / "copy" / "src" / "p" / "__init__.py", base / "copy")
    assert not _is_within(base / "copy2" / "src" / "p" / "__init__.py", base / "copy")


def test_a_probe_that_fails_is_reported_with_its_output(tmp_path):
    from py_ci_shared.checkout_resolution import assert_a_copy_imports_itself

    root = _orig(tmp_path, "zz_copy_pkg3")
    with pytest.raises(pytest.fail.Exception, match=r"pytest exit [1-9]") as exc:
        assert_a_copy_imports_itself(root, "zz_absent_pkg", tmp_path / "work", timeout=120)
    assert "ModuleNotFoundError" in str(exc.value) or "No module named" in str(exc.value)
    good = _orig(tmp_path / "g", "zz_copy_pkg4")
    assert_a_copy_imports_itself(good, "zz_copy_pkg4", tmp_path / "g" / "work", timeout=120)
