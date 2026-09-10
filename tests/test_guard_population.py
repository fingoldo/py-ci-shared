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

    def test_a_violation_search_is_not_a_population(self, tmp_path):
        # `grep -rn 'forbidden'` finding nothing is the guard PASSING. Reading that as an empty
        # population reported two healthy guards as broken.
        tool, root = _repo(
            tmp_path,
            "#!/bin/sh\nroot=\"$PWD\"\ngrep -rn 'Supabase.instance.client' \"$root/lib\" --include='*.dart'\n",
            "lib/a.dart",
        )
        assert find_guards_with_empty_population(tool, root) == []

    def test_a_file_listing_grep_is_still_a_population(self, tmp_path):
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rl 'NeverAppears' lib\n", "lib/a.dart")
        assert len(find_guards_with_empty_population(tool, root)) == 1

    def test_a_multi_line_command_substitution_is_not_prepended(self, tmp_path):
        # `hits="$(` opens a substitution the next lines finish. Prepended as an assignment it gave
        # `hits="$(; find ...`, a syntax error that was reported as a broken guard.
        body = "#!/bin/sh\nhits=\"$(\n  find lib -name '*.dart' -print0 \\\n  | xargs -0 cat\n)\"\n"
        tool, root = _repo(tmp_path, body, "lib/a.dart")
        assert find_guards_with_empty_population(tool, root) == []

    def test_a_pipe_and_parentheses_inside_a_quoted_pattern_belong_to_it(self, tmp_path):
        # The selection used to be cut at the `|` inside the quotes, leaving `grep -rlE 'class (Foo`.
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rlE 'class (Foo|Bar)' lib\n", "lib/a.dart")
        assert find_guards_with_empty_population(tool, root) == []

    def test_the_same_quoted_pattern_still_reports_an_empty_population(self, tmp_path):
        # Teeth for the case above: the command genuinely RAN, so a pattern that selects nothing is
        # flagged as matching nothing rather than as a command that could not run.
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rlE 'class (Nope|Never)' lib\n", "lib/a.dart")
        problems = find_guards_with_empty_population(tool, root)
        assert len(problems) == 1
        assert "matches nothing" in problems[0]

    def test_a_selection_inside_command_substitution_stops_at_its_close(self, tmp_path):
        body = "#!/bin/sh\nfor f in $(find lib -name '*.dart'); do echo \"$f\"; done\n"
        tool, root = _repo(tmp_path, body, "lib/a.dart")
        assert find_guards_with_empty_population(tool, root) == []

    def test_a_quote_closing_on_a_later_line_is_not_a_guard_problem(self, tmp_path):
        # Not replayable from one line; that is the parser's limit, not the guard's fault.
        tool, root = _repo(tmp_path, "#!/bin/sh\ngrep -rl 'first\nsecond' lib\n", "lib/a.dart")
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
        with pytest.raises(pytest.fail.Exception, match=r"check-x\.sh"):
            assert_guards_examine_something(tool, root)
