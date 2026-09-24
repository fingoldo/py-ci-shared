"""Unit tests for the shared private-cross-package-import check, on real scratch packages."""

from __future__ import annotations

from pathlib import Path

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


class TestAuditRegressions:
    def test_a_plain_import_alone_is_flagged(self, tmp_path):
        """SUITE-15 mutant P7: dropping the `ast.Import` branch survived because every fixture also had a
        from-import. Here the plain import is the ONLY reach."""
        root = _pkg(tmp_path, {"metrics/_core.py": "X = 1\n", "evaluation/boot.py": "import pkg.metrics._core\n"})
        assert find_private_cross_package_imports(root / "src" / "pkg", "pkg", root) == {("src/pkg/evaluation/boot.py", "pkg.metrics._core")}
        (root / "src" / "pkg" / "evaluation" / "boot.py").write_text("import pkg.metrics\n", encoding="utf-8")
        assert find_private_cross_package_imports(root / "src" / "pkg", "pkg", root) == set()

    def test_a_private_name_from_a_public_module_is_flagged(self, tmp_path):
        """MP-26: `from pkg.metrics import _core` names the private module in the alias, not in `module`."""
        root = _pkg(tmp_path, {"metrics/_core.py": "X = 1\n", "evaluation/boot.py": "from pkg.metrics import _core\n"})
        assert find_private_cross_package_imports(root / "src" / "pkg", "pkg", root) == {("src/pkg/evaluation/boot.py", "pkg.metrics._core")}
        (root / "src" / "pkg" / "evaluation" / "boot.py").write_text("from pkg.metrics import public, __version__\n", encoding="utf-8")
        assert find_private_cross_package_imports(root / "src" / "pkg", "pkg", root) == set()

    @pytest.mark.parametrize(
        "rel, body",
        [
            ("evaluation/boot.py", "from ..metrics._core import X\n"),
            ("evaluation/boot.py", "from ..metrics import _core\n"),
            ("evaluation/__init__.py", "from ..metrics._core import X\n"),
            ("evaluation/deep/boot.py", "from ...metrics._core import X\n"),
        ],
    )
    def test_relative_imports_are_resolved(self, tmp_path, rel, body):
        """MP-26: relative imports (level > 0) were skipped outright."""
        root = _pkg(tmp_path, {"metrics/_core.py": "X = 1\n", rel: body})
        assert find_private_cross_package_imports(root / "src" / "pkg", "pkg", root) == {(f"src/pkg/{rel}", "pkg.metrics._core")}

    def test_a_relative_sibling_import_is_allowed(self, tmp_path):
        root = _pkg(tmp_path, {"metrics/_core.py": "X = 1\n", "metrics/public.py": "from ._core import X\nfrom . import _core\n"})
        assert find_private_cross_package_imports(root / "src" / "pkg", "pkg", root) == set()
        # control: the same file one package over is a reach
        root2 = _pkg(tmp_path / "b", {"metrics/_core.py": "X = 1\n", "other/public.py": "from ..metrics._core import X\n"})
        assert find_private_cross_package_imports(root2 / "src" / "pkg", "pkg", root2) == {("src/pkg/other/public.py", "pkg.metrics._core")}

    def test_src_outside_repo_root_does_not_raise(self, tmp_path):
        """MP-27: `relative_to(repo_root)` raised ValueError; the importer is now keyed by its absolute path."""
        root = _pkg(tmp_path / "elsewhere", {"metrics/_core.py": "X = 1\n", "evaluation/boot.py": "import pkg.metrics._core\n"})
        repo = tmp_path / "repo"
        repo.mkdir()
        found = find_private_cross_package_imports(root / "src" / "pkg", "pkg", repo)
        assert len(found) == 1
        ((path, module),) = found
        assert path.endswith("elsewhere/src/pkg/evaluation/boot.py") and Path(path).is_absolute()
        assert module == "pkg.metrics._core"

    def test_unparsable_and_bom_files(self, tmp_path):
        root = _pkg(tmp_path, {"metrics/_core.py": "X = 1\n", "evaluation/broken.py": "def (:\n"})
        (root / "src" / "pkg" / "evaluation" / "bom.py").write_bytes(b"\xef\xbb\xbfimport pkg.metrics._core\n")
        found = find_private_cross_package_imports(root / "src" / "pkg", "pkg", root)
        assert found == {("src/pkg/evaluation/bom.py", "pkg.metrics._core"), ("src/pkg/evaluation/broken.py", "<unparsed>")}
        with pytest.raises(pytest.fail.Exception, match=r"could not be parsed[\s\S]*broken\.py:1"):
            assert_no_private_cross_package_imports(root / "src" / "pkg", "pkg", root, allowlist={("src/pkg/evaluation/bom.py", "pkg.metrics._core")})
        (root / "src" / "pkg" / "evaluation" / "broken.py").write_text("x = 1\n", encoding="utf-8")
        assert_no_private_cross_package_imports(root / "src" / "pkg", "pkg", root, allowlist={("src/pkg/evaluation/bom.py", "pkg.metrics._core")})

    def test_the_floor_and_a_missing_root(self, tmp_path):
        from py_ci_shared._core import CorpusError

        (tmp_path / "src" / "pkg").mkdir(parents=True)
        with pytest.raises(pytest.fail.Exception, match="only 0 file"):
            assert_no_private_cross_package_imports(tmp_path / "src" / "pkg", "pkg", tmp_path)
        with pytest.raises(CorpusError):
            assert_no_private_cross_package_imports(tmp_path / "missing", "pkg", tmp_path)
        (tmp_path / "src" / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
        assert_no_private_cross_package_imports(tmp_path / "src" / "pkg", "pkg", tmp_path)


def test_a_private_helper_of_a_module_is_shared_with_its_siblings_not_with_other_packages(tmp_path):
    root = _pkg(
        tmp_path,
        {
            "__init__.py": "",
            "db/__init__.py": "",
            "db/models.py": "def _helper():\n    return 1\n",
            "db/queries.py": "from pkg.db.models import _helper\nfrom .models import _helper as h\n",
            "db/sub/deep.py": "from pkg.db.models import _helper\n",
            "web/views.py": "from pkg.db.models import _helper\n",
        },
    )
    assert find_private_cross_package_imports(root / "src" / "pkg", "pkg", root) == {("src/pkg/web/views.py", "pkg.db.models._helper")}
