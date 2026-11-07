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
    p = _write(
        tmp_path,
        "m.py",
        "import os\n"
        "if not os.environ.get('P_A'):\n"
        "    pass\n"
        "b = os.environ.get('P_B', '').strip().lower() in ('1', 'true')\n"
        "if os.getenv('P_C') == '1':\n"
        "    pass\n"
        "if os.environ['P_D']:\n"
        "    pass\n",
    )
    found = [(r.var, r.shape) for r in find_hand_parsed_env_flags([p], tmp_path, ("P_",))]
    assert found == [
        ("P_A", "not <read>"),
        ("P_B", "membership in a literal collection"),
        ("P_C", "compared with '1'"),
        ("P_D", "truthiness in a condition"),
    ]


def test_value_reads_are_not_reported(tmp_path):
    """A number, a path or a backend name read from the environment is not a boolean flag."""
    p = _write(
        tmp_path,
        "m.py",
        "import os\n" "n = int(os.environ.get('P_ROWS', '5'))\n" "backend = os.environ.get('P_BACKEND', 'threading')\n" "path = os.getenv('P_DIR')\n",
    )
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


def _reads(tmp_path, body, prefixes=("MY_",)):
    f = tmp_path / "m.py"
    f.write_text(body, encoding="utf-8")
    return [(r.lineno, r.var, r.shape) for r in find_hand_parsed_env_flags([f], tmp_path, prefixes)]


def test_the_two_step_idiom_is_reported_at_the_use(tmp_path):
    body = 'import os\n\n\ndef f():\n    v = os.environ.get("MY_F")\n    if v == "1":\n        return 1\n'
    assert _reads(tmp_path, body) == [(6, "MY_F", "compared with '1'")]


def test_walrus_bool_and_environ_alias_and_constant_name_are_reported(tmp_path):
    body = (
        'import os\nN = "MY_F"\nenv = os.environ\n\n\ndef f():\n    a = bool(os.environ.get("MY_A"))\n    b = (v := os.environ.get("MY_B")) == "1"\n'
        '    c = env.get("MY_C") == "1"\n    d = os.environ.get(N) == "1"\n'
    )
    assert [(ln, var) for ln, var, _ in _reads(tmp_path, body)] == [(7, "MY_A"), (8, "MY_B"), (9, "MY_C"), (10, "MY_F")]


def test_prefix_match_is_case_insensitive(tmp_path):
    body = 'import os\n\nx = os.environ.get("my_flag") == "1"\n'
    assert _reads(tmp_path, body) == [(3, "my_flag", "compared with '1'")]


def test_a_backend_selector_and_a_default_string_are_not_flags(tmp_path):
    body = 'import os\n\na = os.environ.get("MY_DIR") in ("a", "b")\nb = os.environ.get("MY_DIR") or "a"\n'
    assert _reads(tmp_path, body) == []


def test_a_boolean_membership_and_a_reassigned_name_are_handled(tmp_path):
    body = 'import os\n\na = os.environ.get("MY_X", "") in ("1", "true")\nv = os.environ.get("MY_Y")\nv = "1"\nif v == "1":\n    pass\n'
    assert [(ln, var) for ln, var, _ in _reads(tmp_path, body)] == [(3, "MY_X")]


def test_only_boolean_flags_are_judged(tmp_path):
    """Paths, numbers, URLs, hosts, credentials and secrets read in a truth test or a comparison are not boolean flags (37-F8, 37-F32)."""
    p = _write(
        tmp_path,
        "m.py",
        "import os\n"
        "if not os.environ.get('P_MODEL_PATH'):\n"
        "    pass\n"
        "if os.getenv('P_DB_URL'):\n"
        "    pass\n"
        "if not os.environ.get('P_PROXY_USER'):\n"
        "    pass\n"
        "if os.environ.get('P_PROXY_USER') == '':\n"
        "    pass\n"
        "if os.environ.get('P_API_KEY'):\n"
        "    pass\n"
        "if os.environ.get('P_CREDENTIALS') == '':\n"
        "    pass\n"
        "if os.environ.get('P_WORKERS') == '4':\n"
        "    pass\n"
        "if os.environ.get('P_BACKEND') == 'gpu':\n"
        "    pass\n",
    )
    assert find_hand_parsed_env_flags([p], tmp_path, ("P_",)) == []


def test_a_boolean_flag_compared_with_a_spelling_of_on_is_still_judged(tmp_path):
    p = _write(tmp_path, "m.py", "import os\nif os.environ.get('P_VERBOSE') == 'yes':\n    pass\n")
    assert [(r.var, r.shape) for r in find_hand_parsed_env_flags([p], tmp_path, ("P_",))] == [("P_VERBOSE", "compared with 'yes'")]


@pytest.mark.parametrize(
    "body",
    [
        "x = bool(int(os.environ.get('MY_FLAG', '0')))\n",
        "if 'MY_FLAG' in os.environ:\n    pass\n",
        "if 'MY_FLAG' not in os.environ:\n    pass\n",
        "if os.getenv(f'MY_{name}'):\n    pass\n",
    ],
)
def test_more_hand_parse_shapes_are_reported(tmp_path, body):
    assert len(_reads(tmp_path, "import os\nname = 'a'\n" + body)) == 1


def test_membership_of_a_foreign_variable_is_not_reported(tmp_path):
    assert _reads(tmp_path, "import os\nif 'HOME' in os.environ:\n    pass\nx = int(os.environ.get('MY_N', '1'))\n") == []


@pytest.mark.parametrize(
    "source, flagged",
    [
        ("import os\nn = os.environ.get('P_JOBS')\nif n:\n    jobs = int(n)\n", False),
        ("import os\nif os.environ.get('P_RETRIES') == '0':\n    pass\nr = int(os.environ.get('P_RETRIES', '3'))\n", False),
        ("import os\nfrom pathlib import Path\nif os.environ.get('P_CACHE'):\n    p = Path(os.environ['P_CACHE'])\n", False),
        ("import os\nif os.environ.get('P_DEVICE'):\n    d = os.environ['P_DEVICE'].split(',')\n", False),
        ("import os\nif os.environ.get('P_VERB'):\n    pass\nlevel = int(os.environ.get('P_OTHER', '1'))\n", True),
        ("import os\nif os.environ.get('P_FLAG'):\n    pass\n", True),
    ],
)
def test_a_variable_the_file_also_uses_as_a_number_or_path_is_not_a_switch(tmp_path, source, flagged):
    """Audit 41-F6: routing P_RETRIES=3 through a boolean parser would turn it into True."""
    p = _write(tmp_path, "m.py", source)
    assert bool(find_hand_parsed_env_flags([p], tmp_path, ["P_"])) is flagged
