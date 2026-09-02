"""Unit tests for the guard-population check. Real scratch guard scripts and real subprocess runs,
same no-mocking convention as this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.guard_population import (
    assert_guards_examine_something,
    find_guards_with_empty_population,
)


def _repo(tmp_path: Path, guard_body: str, *files: str) -> tuple[Path, Path]:
    tool = tmp_path / "tool"
    tool.mkdir()
    (tool / "check-x.sh").write_text(guard_body, encoding="utf-8")
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("class Foo {}\n", encoding="utf-8")
    return tool, tmp_path


class TestFindGuardsWithEmptyPopulation:
    def test_guard_selecting_nothing_is_flagged(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rl 'NeverAppears' lib\necho ok\n", "lib/a.dart")
        problems = find_guards_with_empty_population(tool, root)
        assert len(problems) == 1
        assert "matches nothing" in problems[0]

    def test_guard_selecting_files_passes(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rl 'class' lib\necho ok\n", "lib/a.dart")
        assert find_guards_with_empty_population(tool, root) == []

    def test_guard_with_a_self_assert_is_exempt(self, tmp_path):
        body = "#!/bin/sh\ngrep -rl 'NeverAppears' lib\nif [ -z \"$files\" ]; then echo 'ERROR: examining nothing'; exit 1; fi\n"
        tool, root = _repo(tmp_path, body, "lib/a.dart")
        assert find_guards_with_empty_population(tool, root) == []

    def test_find_selecting_nothing_is_flagged(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\nfind lib -name '*.kt'\n", "lib/a.dart")
        assert len(find_guards_with_empty_population(tool, root)) == 1

    def test_find_selecting_files_passes(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\nfind lib -name '*.dart'\n", "lib/a.dart")
        assert find_guards_with_empty_population(tool, root) == []

    def test_guard_with_no_population_command_is_skipped(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\npython tool/other.py\n", "lib/a.dart")
        assert find_guards_with_empty_population(tool, root) == []

    def test_skip_list_is_honoured(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rl 'Nope' lib\n", "lib/a.dart")
        assert find_guards_with_empty_population(tool, root, skip=["check-x.sh"]) == []

    def test_empty_tool_dir_reports_examining_nothing(self, tmp_path):
        tool = tmp_path / "tool"
        tool.mkdir()
        problems = find_guards_with_empty_population(tool, tmp_path)
        assert len(problems) == 1
        assert "examined nothing itself" in problems[0]


class TestAssert:
    def test_assert_passes(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rl 'class' lib\n", "lib/a.dart")
        assert_guards_examine_something(tool, root)

    def test_assert_fails_naming_the_guard(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rl 'Nope' lib\n", "lib/a.dart")
        with pytest.raises(pytest.fail.Exception, match="check-x.sh"):
            assert_guards_examine_something(tool, root)
