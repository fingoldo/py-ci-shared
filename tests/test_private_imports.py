"""Unit tests for the shared private-cross-package-import check, on real scratch packages."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.private_imports import assert_no_private_cross_package_imports, find_private_cross_package_imports, owning_package


def _pkg(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / "src" / "pkg" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


def test_owning_package_stops_at_the_first_underscore_segment():
    assert owning_package("pkg.a._b.c") == "pkg.a"
    assert owning_package("pkg.a.b") is None
    assert owning_package("pkg._core") == "pkg"


def test_a_reach_across_packages_is_flagged_and_a_sibling_is_not(tmp_path):
    root = _pkg(
        tmp_path,
        {
            "metrics/_core.py": "X = 1\n",
            "metrics/public.py": "from pkg.metrics._core import X\n",  # sibling: allowed
            "metrics/deep/user.py": "from pkg.metrics._core import X\n",  # below the owner: allowed
            "evaluation/boot.py": "from pkg.metrics._core import X\nimport pkg.metrics._core\n",  # foreign: flagged
        },
    )

    found = find_private_cross_package_imports(root / "src" / "pkg", "pkg", root)

    assert found == {("src/pkg/evaluation/boot.py", "pkg.metrics._core")}


def test_test_adjacent_code_and_other_packages_are_exempt(tmp_path):
    root = _pkg(
        tmp_path,
        {
            "metrics/_core.py": "X = 1\n",
            "_benchmarks/b.py": "from pkg.metrics._core import X\n",
            "evaluation/_profile_run.py": "from pkg.metrics._core import X\n",
            "evaluation/other.py": "from numpy._core import multiarray\n",
        },
    )

    assert find_private_cross_package_imports(root / "src" / "pkg", "pkg", root) == set()


def test_the_allowlist_admits_a_known_reach_and_fails_when_it_goes_stale(tmp_path):
    root = _pkg(tmp_path, {"metrics/_core.py": "X = 1\n", "evaluation/boot.py": "from pkg.metrics._core import X\n"})
    entry = ("src/pkg/evaluation/boot.py", "pkg.metrics._core")

    assert_no_private_cross_package_imports(root / "src" / "pkg", "pkg", root, allowlist={entry})
    with pytest.raises(pytest.fail.Exception, match="no longer occur"):
        assert_no_private_cross_package_imports(root / "src" / "pkg", "pkg", root, allowlist={entry, ("src/pkg/gone.py", "pkg.x._y")})
    with pytest.raises(pytest.fail.Exception, match="underscore module"):
        assert_no_private_cross_package_imports(root / "src" / "pkg", "pkg", root)
