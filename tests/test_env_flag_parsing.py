"""``env_flag_parsing`` finds the hand-parsed boolean switches and leaves value reads alone."""

from __future__ import annotations

import pytest

from py_ci_shared.env_flag_parsing import assert_env_flags_use_one_parser, find_hand_parsed_env_flags


def _write(tmp_path, name, source):
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def test_each_hand_parse_shape_is_reported(tmp_path):
    """The three spellings that disagree about "on": a truth test, a comparison and a literal membership."""
    p = _write(tmp_path, "m.py",
               "import os\n"
               "if not os.environ.get('P_A'):\n"
               "    pass\n"
               "b = os.environ.get('P_B', '').strip().lower() in ('1', 'true')\n"
               "if os.getenv('P_C') == '1':\n"
               "    pass\n"
               "if os.environ['P_D']:\n"
               "    pass\n")
    found = [(r.var, r.shape) for r in find_hand_parsed_env_flags([p], tmp_path, ("P_",))]
    assert found == [
        ("P_A", "not <read>"),
        ("P_B", "membership in a literal collection"),
        ("P_C", "compared with '1'"),
        ("P_D", "truthiness in a condition"),
    ]


def test_value_reads_are_not_reported(tmp_path):
    """A number, a path or a backend name read from the environment is not a boolean flag."""
    p = _write(tmp_path, "m.py",
               "import os\n"
               "n = int(os.environ.get('P_ROWS', '5'))\n"
               "backend = os.environ.get('P_BACKEND', 'threading')\n"
               "path = os.getenv('P_DIR')\n")
    assert find_hand_parsed_env_flags([p], tmp_path, ("P_",)) == []


def test_only_the_named_prefixes_count(tmp_path):
    """Another project's variables, and unprefixed ones, are somebody else's contract."""
    p = _write(tmp_path, "m.py", "import os\nif not os.environ.get('OTHER_FLAG'):\n    pass\nif not os.environ.get('HOME'):\n    pass\n")
    assert find_hand_parsed_env_flags([p], tmp_path, ("P_",)) == []


def test_a_read_through_the_shared_parser_is_not_reported(tmp_path):
    """The fix itself: the call the check asks for does not trip it."""
    p = _write(tmp_path, "m.py", "from proj.env import env_flag\nif env_flag('P_A'):\n    pass\n")
    assert find_hand_parsed_env_flags([p], tmp_path, ("P_",)) == []


def test_the_assert_names_the_variable_and_honours_the_allowlist(tmp_path):
    """The failure names the variable and the shape; an allowed one passes, and a reason is required."""
    p = _write(tmp_path, "m.py", "import os\nif not os.environ.get('P_A'):\n    pass\n")
    with pytest.raises(AssertionError, match="P_A"):
        assert_env_flags_use_one_parser([p], tmp_path, ("P_",))
    assert_env_flags_use_one_parser([p], tmp_path, ("P_",), allowed={"P_A": "a numeric override; the test is for presence"})
    with pytest.raises(AssertionError, match="need a reason"):
        assert_env_flags_use_one_parser([p], tmp_path, ("P_",), allowed={"P_A": " "})


def test_a_stale_allowlist_entry_and_an_empty_scan_are_rejected(tmp_path):
    """An allowed variable nobody parses by hand any more, and a scan that lost its files, both fail."""
    p = _write(tmp_path, "m.py", "from proj.env import env_flag\nx = env_flag('P_A')\n")
    with pytest.raises(AssertionError, match="no longer read by hand"):
        assert_env_flags_use_one_parser([p], tmp_path, ("P_",), allowed={"P_A": "was hand-parsed once"})
    with pytest.raises(AssertionError, match="lost its subject"):
        assert_env_flags_use_one_parser([], tmp_path, ("P_",), min_files=1)
