"""Tests for py_ci_shared.definition_after_entry_guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.definition_after_entry_guard import assert_definition_after_entry_guard, find_definitions_after_entry_guard

BOM = b"\xef\xbb\xbf"
LATE = "def main():\n    return 0\n\nif __name__ == '__main__':\n    main()\n\ndef _record_cycle_run():\n    pass\n"


def _find(tmp_path: Path, src, name: str = "s.py"):
    (tmp_path / name).write_bytes(src if isinstance(src, bytes) else src.encode("utf-8"))
    return find_definitions_after_entry_guard(tmp_path, use_git=False)


def test_reports_the_seeded_violation(tmp_path):
    found = _find(tmp_path, LATE)
    assert [(f.path, f.line, f.rule) for f in found] == [("s.py", 7, "definition-after-entry-guard")]
    assert found[0].message.startswith("_record_cycle_run is defined after the `if __name__ == '__main__'` entry guard (line 4)")


def test_guard_as_last_statement_is_clean(tmp_path):
    assert _find(tmp_path, "def main():\n    return 0\n\ndef helper():\n    pass\n\nif __name__ == '__main__':\n    main()\n") == []


@pytest.mark.parametrize(
    "guard",
    [
        "if __name__ == '__main__':\n    sys.exit(main())\n",
        "if '__main__' == __name__:\n    asyncio.run(main())\n",
    ],
)
def test_wrapped_and_reversed_guards_are_entries(tmp_path, guard):
    src = "import sys, asyncio\ndef main():\n    return 0\n" + guard + "class Late:\n    pass\nasync def later():\n    pass\n"
    assert [f.message.split()[0] for f in _find(tmp_path, src)] == ["Late", "later"]


def test_sys_path_and_sys_modules_guards_are_not_entries(tmp_path):
    src = "import sys\nif __name__ == '__main__':\n    sys.path.insert(0, '.')\n    sys.modules.setdefault('x', sys.modules['__main__'])\n\ndef later():\n    pass\n"
    assert _find(tmp_path, src) == []
    assert _find(tmp_path, "if __name__ == '__main__':\n    print('hi')\n\ndef later():\n    pass\n") == []


def test_assignments_after_the_guard_and_other_comparisons_are_ignored(tmp_path):
    assert _find(tmp_path, "def main():\n    pass\nif __name__ == '__main__':\n    main()\nX = 1\n") == []
    assert _find(tmp_path, "def main():\n    pass\nif __name__ != '__main__':\n    main()\ndef later():\n    pass\n") == []


def test_bom_and_crlf_are_reported_like_the_plain_file(tmp_path):
    plain = _find(tmp_path / "p", LATE) if (tmp_path / "p").mkdir() is None else None
    (tmp_path / "b").mkdir()
    assert _find(tmp_path / "b", BOM + LATE.encode()) == plain != []
    (tmp_path / "c").mkdir()
    assert [f.line for f in _find(tmp_path / "c", LATE.replace("\n", "\r\n").encode())] == [7]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    (tmp_path / "ok.py").write_text(LATE, encoding="utf-8")
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_definitions_after_entry_guard(tmp_path, use_git=False)
    assert len(find_definitions_after_entry_guard(tmp_path, use_git=False, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_definitions_after_entry_guard(tmp_path, use_git=False)


def test_assert_and_baseline(tmp_path):
    (tmp_path / "s.py").write_text(LATE, encoding="utf-8")
    with pytest.raises(AssertionError, match="_record_cycle_run"):
        assert_definition_after_entry_guard(tmp_path, use_git=False)
    base = tmp_path / "base.json"
    found = find_definitions_after_entry_guard(tmp_path, use_git=False)
    base.write_text(
        json.dumps({"schema": 1, "gate": "definition_after_entry_guard", "entries": {f.key: {"count": 1, "note": "ok"} for f in found}}), encoding="utf-8"
    )
    assert_definition_after_entry_guard(tmp_path, use_git=False, baseline_path=base)
    (tmp_path / "t.py").write_text(LATE, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"t\.py"):
        assert_definition_after_entry_guard(tmp_path, use_git=False, baseline_path=base)
