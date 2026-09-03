"""Unit tests for the documented-inventory parity checks. Each case reproduces one
concrete documentation-audit finding shape on a scratch repo."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.docs_inventory_parity import (
    find_aggregate_group_drift,
    find_extras_documentation_drift,
    find_phantom_doc_paths,
    find_undeclared_markers,
    find_undocumented_modules,
    resolve_extras_group,
)

_BULLET = r"pip install mypkg\[(\w+)\]\s*#\s*(.*)"

_PYPROJECT = """
[project.optional-dependencies]
web = ["selenium>=4.0", "requests>=2.28", "grequests>=0.7"]
llm = ["anthropic>=0.40", "httpx>=0.25"]
all = ["mypkg[web,llm]"]
"""


def _repo(tmp_path: Path, readme: str, pyproject: str = _PYPROJECT) -> tuple[Path, Path]:
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    return tmp_path / "pyproject.toml", tmp_path / "README.md"


class TestExtrasDocumentationDrift:
    def test_a_bullet_omitting_a_member_is_reported(self, tmp_path):
        """The '[web] description omits the group's two heaviest members' shape."""
        pyproject, readme = _repo(tmp_path, "pip install mypkg[web]  # selenium + requests\npip install mypkg[llm]  # anthropic + httpx\n")
        problems = find_extras_documentation_drift(pyproject, readme, _BULLET, undocumented_groups=["all"])
        assert len(problems) == 1 and "omits ['grequests']" in problems[0]

    def test_a_bullet_naming_a_non_member_is_reported(self, tmp_path):
        """The 'lists three packages that are now core' shape."""
        pyproject, readme = _repo(tmp_path, "pip install mypkg[web]  # selenium + requests + grequests + anthropic\npip install mypkg[llm]  # anthropic + httpx\n")
        problems = find_extras_documentation_drift(pyproject, readme, _BULLET, undocumented_groups=["all"])
        assert len(problems) == 1 and "names ['anthropic']" in problems[0]

    def test_an_undocumented_group_is_reported(self, tmp_path):
        pyproject, readme = _repo(tmp_path, "pip install mypkg[web]  # selenium + requests + grequests\n")
        problems = find_extras_documentation_drift(pyproject, readme, _BULLET, undocumented_groups=["all"])
        assert any("[llm] is declared in pyproject.toml but documented nowhere" in p for p in problems)

    def test_an_accurate_install_block_passes(self, tmp_path):
        pyproject, readme = _repo(tmp_path, "pip install mypkg[web]  # selenium + requests + grequests\npip install mypkg[llm]  # anthropic + httpx\n")
        assert find_extras_documentation_drift(pyproject, readme, _BULLET, undocumented_groups=["all"]) == []

    def test_ignored_packages_need_no_prose_mention(self, tmp_path):
        pyproject, readme = _repo(
            tmp_path,
            "pip install mypkg[web]  # selenium + requests\npip install mypkg[llm]  # anthropic + httpx\n",
        )
        assert find_extras_documentation_drift(pyproject, readme, _BULLET, ignore_packages=["grequests"], undocumented_groups=["all"]) == []


class TestResolveExtrasGroup:
    def test_a_self_referential_group_resolves_transitively(self, tmp_path):
        pyproject, _ = _repo(tmp_path, "")
        import tomllib

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        assert resolve_extras_group(data["project"]["optional-dependencies"], "all") == {"selenium", "requests", "grequests", "anthropic", "httpx"}


class TestAggregateGroupDrift:
    def test_a_stale_composition_sentence_is_reported(self, tmp_path):
        """The '[all] silently omits three groups' shape, from the prose side."""
        pyproject, readme = _repo(tmp_path, "`[all]` = `web`.\n")
        problems = find_aggregate_group_drift(pyproject, readme, r"`\[(all)\]` = `([\w,]+)`")
        assert len(problems) == 1 and "['llm', 'web']" in problems[0]

    def test_an_accurate_composition_sentence_passes(self, tmp_path):
        pyproject, readme = _repo(tmp_path, "`[all]` = `web,llm`.\n")
        assert find_aggregate_group_drift(pyproject, readme, r"`\[(all)\]` = `([\w,]+)`") == []


class TestPhantomDocPaths:
    def test_a_moved_path_is_reported(self, tmp_path):
        """CONTRIBUTING pointed at `tests/benchmark_*.py`; benchmarks had moved."""
        doc = tmp_path / "CONTRIBUTING.md"
        doc.write_text("Benchmarks live in `tests/benchmark_x.py`.\n", encoding="utf-8")
        assert len(find_phantom_doc_paths([doc], tmp_path)) == 1

    def test_a_path_relative_to_a_search_root_resolves(self, tmp_path):
        (tmp_path / "src" / "pkg" / "web").mkdir(parents=True)
        (tmp_path / "src" / "pkg" / "web" / "browser.py").write_text("", encoding="utf-8")
        doc = tmp_path / "CONTRIBUTING.md"
        doc.write_text("See `web/browser.py`.\n", encoding="utf-8")
        assert find_phantom_doc_paths([doc], tmp_path, search_roots=[tmp_path / "src" / "pkg"]) == []

    def test_a_changelog_is_checked_only_where_it_describes_the_tree_that_exists(self, tmp_path):
        """An old entry names the tree AS IT WAS; the only way to satisfy the check is to lie."""
        doc = tmp_path / "CHANGELOG.md"
        doc.write_text(
            "\n".join(
                [
                    "# Changelog",
                    "",
                    "## v2.0",
                    "",
                    "Moved everything into `lib/current/thing.dart`.",
                    "",
                    "## v1.0",
                    "",
                    "Added `lib/gone/old.dart`, which a later release deleted.",
                ]
            ),
            encoding="utf-8",
        )

        # Without the option, the release that DID add that file fails forever.
        assert len(find_phantom_doc_paths([doc], tmp_path)) == 2

        # With it, only the newest section - the one describing today - is held to today's tree.
        only_current = find_phantom_doc_paths([doc], tmp_path, recent_sections={"CHANGELOG.md": 1})
        assert len(only_current) == 1
        assert "lib/current/thing.dart" in only_current[0]

    def test_the_section_limit_applies_by_filename_not_to_every_doc(self, tmp_path):
        """A README's sections are all current; trimming them would quietly stop checking it."""
        readme = tmp_path / "README.md"
        readme.write_text(
            "\n".join(
                [
                    "# R",
                    "",
                    "## First",
                    "",
                    "See `lib/a/gone.dart`.",
                    "",
                    "## Second",
                    "",
                    "And `lib/b/gone.dart`.",
                ]
            ),
            encoding="utf-8",
        )
        assert len(find_phantom_doc_paths([readme], tmp_path, recent_sections={"CHANGELOG.md": 1})) == 2

    def test_an_ignored_directory_covers_what_is_inside_it(self, tmp_path):
        """`lib/shared/` in the list read as one exact path, so everything under it still reported."""
        doc = tmp_path / "CHANGELOG.md"
        doc.write_text("Moved `lib/shared/access/gate.dart` and `lib/shared/`.\n", encoding="utf-8")
        assert len(find_phantom_doc_paths([doc], tmp_path)) == 2
        assert find_phantom_doc_paths([doc], tmp_path, ignore=("lib/shared/",)) == []

    def test_an_ignore_entry_without_a_trailing_slash_still_matches_only_itself(self, tmp_path):
        """A file named in the list must not silently exempt every path that starts with its name."""
        doc = tmp_path / "README.md"
        doc.write_text("See `tool/gen.py` and `tool/gen.py.bak`.\n", encoding="utf-8")
        remaining = find_phantom_doc_paths([doc], tmp_path, ignore=("tool/gen.py",))
        assert len(remaining) == 1
        assert "tool/gen.py.bak" in remaining[0]

    def test_a_slashed_english_phrase_is_not_a_path(self, tmp_path):
        doc = tmp_path / "README.md"
        doc.write_text("every import site is `try/except`-guarded and `and/or` safe.\n", encoding="utf-8")
        assert find_phantom_doc_paths([doc], tmp_path) == []


class TestUndeclaredMarkers:
    def test_a_documented_undeclared_marker_is_reported(self, tmp_path):
        """Under --strict-markers this is a collection ERROR for the contributor."""
        (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers = ["slow: slow tests"]\n', encoding="utf-8")
        doc = tmp_path / "CONTRIBUTING.md"
        doc.write_text("Tag integration tests with @pytest.mark.integration.\n", encoding="utf-8")
        problems = find_undeclared_markers([doc], tmp_path / "pyproject.toml")
        assert len(problems) == 1 and "integration" in problems[0]

    def test_declared_and_builtin_markers_pass(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers = ["slow: slow tests"]\n', encoding="utf-8")
        doc = tmp_path / "CONTRIBUTING.md"
        doc.write_text("Use @pytest.mark.slow, or @pytest.mark.parametrize.\n", encoding="utf-8")
        assert find_undeclared_markers([doc], tmp_path / "pyproject.toml") == []


class TestUndocumentedModules:
    def _package(self, tmp_path: Path) -> Path:
        package = tmp_path / "pkg"
        (package / "stats").mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "stats" / "__init__.py").write_text("", encoding="utf-8")
        (package / "stats" / "normality.py").write_text("", encoding="utf-8")
        (package / "_private.py").write_text("", encoding="utf-8")
        return package

    def test_a_whole_undocumented_subpackage_is_reported(self, tmp_path):
        package = self._package(tmp_path)
        doc = tmp_path / "README.md"
        doc.write_text("This package does things.\n", encoding="utf-8")
        assert find_undocumented_modules(package, [doc]) == ["pkg.stats", "pkg.stats.normality"]

    def test_a_documented_parent_package_covers_its_submodules(self, tmp_path):
        package = self._package(tmp_path)
        doc = tmp_path / "README.md"
        doc.write_text("See `pkg.stats` for normality testing.\n", encoding="utf-8")
        assert find_undocumented_modules(package, [doc]) == []

    def test_private_modules_are_never_required_to_be_documented(self, tmp_path):
        package = self._package(tmp_path)
        doc = tmp_path / "README.md"
        doc.write_text("See `pkg.stats`.\n", encoding="utf-8")
        assert "pkg._private" not in find_undocumented_modules(package, [doc])
