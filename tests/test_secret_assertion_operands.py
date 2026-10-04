"""``secret_assertion_operands``: assertions that would print an environment value or a DSN when they fail."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from py_ci_shared import secret_assertion_operands as gate
from py_ci_shared._core import UnparsedFilesError

# The Upwork dashboard's test_an_unset_dsn_is_none (audit 16b.2) as it was, and as it was fixed.
DASHBOARD_16B2 = "import probe\n\n\ndef test_an_unset_dsn_is_none():\n    assert probe._dsn() is None, 'a DSN was resolved'\n"
DASHBOARD_16B2_FIXED = (
    "import probe\n\n\ndef test_an_unset_dsn_is_none():\n    resolved = probe._dsn() is not None\n    assert not resolved, 'a DSN was resolved'\n"
)

OFFENDERS = {
    "environ_membership": 'import os\ndef test_a():\n    assert "K" not in os.environ\n',
    "environ_imported_name": 'from os import environ\ndef test_a():\n    assert "K" not in environ\n',
    "environ_subscript": 'import os\ndef test_a():\n    assert os.environ["DATABASE_URL"] == "v"\n',
    "environ_get_of_any_name": 'import os\ndef test_a():\n    assert os.environ.get("HOME") == "v"\n',
    "getenv_under_a_message": 'import os\ndef test_a():\n    assert os.getenv("UPWORK_DB_DSN") is None, "set"\n',
    "environ_get_via_name": 'import os\ndef test_a():\n    dsn = os.environ.get("DATABASE_URL", "").strip()\n    assert dsn == "x"\n',
    "resolver_via_name": "from dashboard import db\ndef test_a():\n    dsn = db.dsn_or_none()\n    assert dsn is None\n",
    "resolver_named_like_one": DASHBOARD_16B2,
    "configured_resolver": "import cfg\ndef test_a():\n    assert cfg.connection_string() == 'x'\n",
    "secret_in_the_message": 'import os\ndef test_a():\n    dsn = os.getenv("API_TOKEN")\n    assert False, f"got {dsn}"\n',
    "unittest_style": 'import os\nclass T:\n    def test_a(self):\n        self.assertEqual(os.environ["API_KEY"], "v")\n',
    "mock_call_assertion": 'import os\ndef test_a(connect):\n    connect.assert_called_once_with(os.environ["DSN"])\n',
    "or_default": 'from dashboard import db\ndef test_a():\n    assert (db.dsn_or_none() or "") == ""\n',
    "module_level_name": 'import os\nDSN = os.environ.get("DSN")\ndef test_a():\n    assert DSN is None\n',
    "dotenv_values_mapping": 'from dotenv import dotenv_values\ndef test_a():\n    values = dotenv_values(".env")\n    assert "K" in values\n',
    "copied_environment": "import os\ndef test_a():\n    env = dict(os.environ)\n    assert env == {}\n",
    "closure": 'import os\ndef test_a():\n    dsn = os.getenv("SECRET")\n    def check():\n        assert dsn\n    check()\n',
    "loop_over_items": 'import os\ndef test_a():\n    for k, v in os.environ.items():\n        assert v != "x"\n',
}

SAFE = {
    "folded_compare": DASHBOARD_16B2_FIXED,
    "folded_membership": 'import os\ndef test_a():\n    loaded = "K" in os.environ\n    assert not loaded\n',
    "predicate_method": 'import os\ndef test_a():\n    ok = os.environ["K"].startswith("postgresql://")\n    assert ok\n',
    "fake_literal_dsn": "def test_a():\n    dsn = 'postgresql://u:fake@h/db'\n    assert dsn == 'postgresql://u:fake@h/db'\n",
    "predicate_resolver": "from dashboard import db\ndef test_a():\n    assert db.dsn_is_configured() is False\n",
    "one_plain_variable": 'import os\ndef test_a():\n    assert os.environ["HOME"] == "v"\n    assert os.getenv("LANG") == "v"\n',
    "assert_raises": 'import os\nclass T:\n    def test_a(self):\n        self.assertRaises(KeyError, os.environ.__getitem__, "K")\n',
}


@pytest.mark.parametrize("name", sorted(OFFENDERS))
def test_each_leaking_shape_is_reported_once(name):
    found = gate.offenders_in_source(OFFENDERS[name], resolvers={"connection_string"} | gate.DEFAULT_RESOLVERS)
    assert len(found) == 1, f"{name}: expected one finding, got {found}"


@pytest.mark.parametrize("name", sorted(SAFE))
def test_the_safe_shapes_are_not_reported(name):
    assert gate.offenders_in_source(SAFE[name]) == []


def test_a_resolver_outside_the_defaults_needs_configuring():
    assert gate.offenders_in_source(OFFENDERS["configured_resolver"]) == []


def _write(d: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(text.encode())
    return d


def test_the_dashboard_case_fails_the_gate_and_its_fix_passes(tmp_path):
    bad = _write(tmp_path / "bad", {"tests/test_probe.py": DASHBOARD_16B2})
    with pytest.raises(AssertionError, match=r"tests/test_probe.py:5: \[secret-assertion-operand\] test_an_unset_dsn_is_none: `probe._dsn\(\)`"):
        gate.assert_no_secret_assertion_operands([bad / "tests"], root=bad)
    good = _write(tmp_path / "good", {"tests/test_probe.py": DASHBOARD_16B2_FIXED})
    gate.assert_no_secret_assertion_operands([good / "tests"], root=good)


def test_an_allowlisted_offender_passes_and_a_stale_or_unreasoned_entry_fails(tmp_path):
    repo = _write(tmp_path, {"tests/test_probe.py": DASHBOARD_16B2})
    allow = tmp_path / "allow.txt"
    allow.write_text("tests/test_probe.py::test_an_unset_dsn_is_none  # the value is a fixture's fake DSN\n", encoding="utf-8")
    gate.assert_no_secret_assertion_operands([repo / "tests"], root=repo, allowlist_path=allow)
    allow.write_text("tests/test_probe.py::test_an_unset_dsn_is_none\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="has no reason"):
        gate.assert_no_secret_assertion_operands([repo / "tests"], root=repo, allowlist_path=allow)
    (repo / "tests" / "test_probe.py").write_bytes(DASHBOARD_16B2_FIXED.encode())
    allow.write_text("tests/test_probe.py::test_an_unset_dsn_is_none  # was needed\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="matches no offender any more"):
        gate.assert_no_secret_assertion_operands([repo / "tests"], root=repo, allowlist_path=allow)


def test_an_unparsable_file_and_an_empty_corpus_fail(tmp_path):
    repo = _write(tmp_path, {"tests/test_ok.py": DASHBOARD_16B2_FIXED, "tests/test_broken.py": "def f(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"test_broken\.py"):
        gate.assert_no_secret_assertion_operands([repo / "tests"], root=repo)
    gate.assert_no_secret_assertion_operands([repo / "tests"], root=repo, allow_unparsed=True)
    (tmp_path / "empty").mkdir()
    with pytest.raises(AssertionError, match="expected at least 1"):
        gate.assert_no_secret_assertion_operands([tmp_path / "empty"], root=tmp_path)


def test_the_finder_reports_what_the_assert_reports(tmp_path):
    repo = _write(tmp_path, {"tests/test_probe.py": DASHBOARD_16B2})
    found = gate.find_secret_assertion_operands([repo / "tests"], root=repo)
    assert [(f.path, f.line, f.key) for f in found] == [("tests/test_probe.py", 5, "tests/test_probe.py::test_an_unset_dsn_is_none")]


def _mutant(old: str, new: str) -> types.ModuleType:
    """The gate's module with *old* replaced by *new*, executed fresh; the substitution must land."""
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, f"NOT APPLIED: {old!r} is not in the gate once"
    module = types.ModuleType("py_ci_shared._secret_assertion_operands_mutant")
    module.__dict__["__package__"] = "py_ci_shared"
    exec(compile(source.replace(old, new), gate.__file__, "exec"), module.__dict__)
    return module


@pytest.mark.parametrize(
    ("old", "new", "case"),
    [
        ("        if _is_environ(node):\n            return True\n", "        if _is_environ(node):\n            return False\n", "environ_membership"),
        ("(name is not None and RESOLVER_NAME.fullmatch(name))", "False", "resolver_named_like_one"),
        ("[node.test] + ([node.msg] if node.msg is not None else [])", "[node.test]", "secret_in_the_message"),
        ('node.func.attr.startswith("assert")', "False", "unittest_style"),
    ],
)
def test_teeth_reverting_a_core_condition_lets_the_case_through(old, new, case):
    assert gate.offenders_in_source(OFFENDERS[case]), "the unmutated gate must report the case"
    assert _mutant(old, new).offenders_in_source(OFFENDERS[case]) == [], "the mutant still reports it, so the condition is not what catches it"
