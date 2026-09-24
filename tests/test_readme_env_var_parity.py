"""Unit tests for the README env-var documentation parity check.

Real scratch source files + README, same no-mocking convention as this
package's other tests.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from py_ci_shared._core import Baseline

from py_ci_shared.readme_env_var_parity import (
    REFRESH_FLAG,
    assert_no_new_undocumented_env_vars,
    assert_readme_documents_every_env_var,
    find_env_vars_read,
    find_readme_documented_vars,
    register_refresh_option,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content.lstrip("\n"), encoding="utf-8")
    return p


def _write_readme(tmp_path: Path, *names: str) -> Path:
    rows = "\n".join(f"| `{n}` | some description |" for n in names)
    return _write(
        tmp_path,
        "README.md",
        f"""
# Project

## Environment variables

| Name | Description |
|---|---|
{rows}
""",
    )


def test_find_env_vars_read_both_forms(tmp_path):
    f = _write(
        tmp_path,
        "a.py",
        """
import os

def f():
    x = os.environ.get("MY_VAR")
    y = os.getenv("OTHER_VAR")
    return x, y
""",
    )
    assert find_env_vars_read([f]) == {"MY_VAR", "OTHER_VAR"}


def test_find_env_vars_read_loop_over_literal_tuple(tmp_path):
    """for name in (LITERAL, ...): os.environ.get(name) -- a bare for-loop,
    iterable is a literal tuple inline (not a separately-assigned name)."""
    f = _write(
        tmp_path,
        "a.py",
        """
import os

def f():
    for name in ("KEY_A", "KEY_B"):
        if os.environ.get(name):
            return name
""",
    )
    assert find_env_vars_read([f]) == {"KEY_A", "KEY_B"}


def test_find_env_vars_read_listcomp_over_named_tuple(tmp_path):
    """The real llm_bench shape this was written for: a module-level
    tuple assigned to a name, then consumed via a list-comprehension
    generator (not a plain for-statement) that calls os.environ.get on
    the comprehension's loop variable."""
    f = _write(
        tmp_path,
        "a.py",
        """
import os

KEY_NAMES = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")

def f():
    present = [name for name in KEY_NAMES if os.environ.get(name)]
    return present
""",
    )
    assert find_env_vars_read([f]) == {"OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}


def test_find_env_vars_read_getenv_loop_form(tmp_path):
    """Same loop-variable shape via os.getenv rather than os.environ.get."""
    f = _write(
        tmp_path,
        "a.py",
        """
import os

NAMES = ("FOO", "BAR")

def f():
    for n in NAMES:
        os.getenv(n)
""",
    )
    assert find_env_vars_read([f]) == {"FOO", "BAR"}


def test_find_env_vars_read_non_literal_iterable_not_hallucinated(tmp_path):
    """A loop over a runtime-computed iterable (not a literal tuple/list/set,
    and not a name resolvable to one) can't be resolved statically -- must
    stay silent rather than guessing, and must not crash."""
    f = _write(
        tmp_path,
        "a.py",
        """
import os

def f(names):
    for name in names:
        os.environ.get(name)
""",
    )
    assert find_env_vars_read([f]) == set()


def test_find_readme_documented_vars_multi_name_cell(tmp_path):
    readme = _write(
        tmp_path,
        "README.md",
        """
## Environment variables

| Name | Description |
|---|---|
| `GIT_SHA` / `COMMIT_SHA` | the build sha |
""",
    )
    assert find_readme_documented_vars(readme) == {"GIT_SHA", "COMMIT_SHA"}


def test_find_readme_documented_vars_with_prose_between_heading_and_table(tmp_path):
    """A sentence introducing the table must not hide it.

    The former single-regex bridge matched only when the table began on the second line after the
    heading. Any README that explains its table first parsed as "section not found", the caller
    swallowed that into an empty documented-set, and every variable silently counted as
    undocumented -- the check passed while measuring nothing.
    """
    readme = _write(
        tmp_path,
        "README.md",
        """
## Environment variables

Every environment variable read anywhere in `src/`, generated from the source.
This inventory documents *that* a var is read, not *why* it exists.

| Variable | Default | First read at |
|---|---|---|
| `MLFRAME_FOO` | `'0'` | [a.py](a.py#L1) |
| `MLFRAME_BAR` | — | [b.py](b.py#L2) |

## Some other section

| `NOT_A_VAR` | ignored |
""",
    )
    assert find_readme_documented_vars(readme) == {"MLFRAME_FOO", "MLFRAME_BAR"}


def test_find_readme_documented_vars_missing_heading_raises(tmp_path):
    readme = _write(tmp_path, "README.md", "# Project\nno such section here\n")
    with pytest.raises(ValueError, match="not found"):
        find_readme_documented_vars(readme)


class TestAssertReadmeDocumentsEveryEnvVar:
    def test_passes_when_fully_documented(self, tmp_path):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("MY_VAR")\n')
        readme = _write_readme(tmp_path, "MY_VAR")
        assert_readme_documents_every_env_var([f], readme)

    def test_fails_on_undocumented_var(self, tmp_path):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("MY_VAR")\n')
        readme = _write_readme(tmp_path)
        with pytest.raises(pytest.fail.Exception, match="MY_VAR"):
            assert_readme_documents_every_env_var([f], readme)

    def test_third_party_vars_excluded(self, tmp_path):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("HF_HOME")\n')
        readme = _write_readme(tmp_path)
        assert_readme_documents_every_env_var([f], readme, third_party_vars=frozenset({"HF_HOME"}))


def _baselined(path: Path) -> list[str]:
    counts, _ = Baseline(path).load()
    return sorted(counts)


class TestAssertNoNewUndocumentedEnvVars:
    def _seed(self, files, readme, baseline, monkeypatch):
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", "readme-env-var")
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars(files, readme, baseline)
        monkeypatch.delenv("PY_CI_SHARED_REFRESH")

    def test_a_missing_baseline_fails_instead_of_seeding(self, tmp_path):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_new_undocumented_env_vars([f], readme, baseline)
        assert not baseline.exists()

    def test_a_refresh_seeds_and_skips(self, tmp_path, monkeypatch):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        self._seed([f], readme, baseline, monkeypatch)
        assert _baselined(baseline) == ["LEGACY_VAR"]

    def test_seeds_cleanly_when_readme_has_no_env_var_section_at_all(self, tmp_path, monkeypatch):
        """A repo adopting this check may have NO env-var table yet -- unlike
        the hard-assert variant, this must not raise ValueError; every var
        read is simply grandfathered by the first refresh."""
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write(tmp_path, "README.md", "# Project\nNo env var section here.\n")
        baseline = tmp_path / "_baseline.json"
        self._seed([f], readme, baseline, monkeypatch)
        assert _baselined(baseline) == ["LEGACY_VAR"]

    def test_grandfathered_var_does_not_fail_after_seeding(self, tmp_path, monkeypatch):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        self._seed([f], readme, baseline, monkeypatch)
        assert_no_new_undocumented_env_vars([f], readme, baseline)  # must not raise

    def test_new_undocumented_var_fails(self, tmp_path, monkeypatch):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        self._seed([f], readme, baseline, monkeypatch)

        _write(tmp_path, "b.py", 'import os\nos.environ.get("NEW_VAR")\n')
        with pytest.raises(pytest.fail.Exception, match="NEW_VAR"):
            assert_no_new_undocumented_env_vars([f, tmp_path / "b.py"], readme, baseline)

    def test_refresh_flag_reseeds_via_the_pytest_option(self, tmp_path):
        """The option is read from the pytest config, so it works under xdist and pytest.main()."""
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        baseline.write_text("[]", encoding="utf-8")

        class _Config:
            def getoption(self, name):
                return name == REFRESH_FLAG

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars([f], readme, baseline, request=types.SimpleNamespace(config=_Config()))
        assert _baselined(baseline) == ["LEGACY_VAR"]

    def test_the_legacy_argv_flag_still_reseeds(self, tmp_path, monkeypatch):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        baseline.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [*sys.argv, REFRESH_FLAG])
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars([f], readme, baseline)
        assert _baselined(baseline) == ["LEGACY_VAR"]

    def test_documenting_a_grandfathered_var_is_stale_until_the_baseline_shrinks(self, tmp_path, monkeypatch):
        """The baseline must tighten: once a var is documented its entry would silently excuse a regression."""
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\nos.environ.get("OTHER_LEGACY")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        self._seed([f], readme, baseline, monkeypatch)

        readme2 = _write_readme(tmp_path, "LEGACY_VAR")  # now documented
        with pytest.raises(pytest.fail.Exception, match="no longer found"):
            assert_no_new_undocumented_env_vars([f], readme2, baseline)
        self._seed([f], readme2, baseline, monkeypatch)
        assert _baselined(baseline) == ["OTHER_LEGACY"]
        assert_no_new_undocumented_env_vars([f], readme2, baseline)

    def test_a_legacy_json_list_baseline_is_still_read(self, tmp_path):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        baseline.write_text('["LEGACY_VAR"]', encoding="utf-8")
        assert_no_new_undocumented_env_vars([f], readme, baseline)


class TestReadForms:
    def test_aliases_subscripts_and_keywords(self, tmp_path):
        f = _write(
            tmp_path,
            "a.py",
            """
import os as _os
from os import environ, getenv
from os import environ as env

a = environ.get("FROM_ENVIRON")
b = getenv("FROM_GETENV")
c = _os.environ.get("ALIASED_OS")
d = _os.environ["SUBSCRIPT"]
e = getenv(key="KEYWORD")
f = env.setdefault("SETDEFAULT", "1")
g = "MEMBERSHIP" in _os.environ
_os.environ["WRITTEN_ONLY"] = "1"
""",
        )
        assert find_env_vars_read([f]) == {
            "FROM_ENVIRON",
            "FROM_GETENV",
            "ALIASED_OS",
            "SUBSCRIPT",
            "KEYWORD",
            "SETDEFAULT",
            "MEMBERSHIP",
        }

    def test_an_unrelated_get_or_getenv_is_not_a_read(self, tmp_path):
        f = _write(
            tmp_path,
            "a.py",
            """
config = {}
environ = {}
x = config.get("NOT_ENV")
y = environ.get("LOCAL_DICT")
def getenv(k):
    return k
z = getenv("LOCAL_FUNC")
""",
        )
        assert find_env_vars_read([f]) == set()

    def test_a_bom_file_is_read(self, tmp_path):
        p = tmp_path / "bom.py"
        p.write_bytes(b"\xef\xbb\xbfimport os\nos.getenv('BOMVAR')\n")
        assert find_env_vars_read([p]) == {"BOMVAR"}


class TestUnparsedAndFloor:
    def test_an_unparsable_file_raises_rather_than_contributing_nothing(self, tmp_path):
        bad = _write(tmp_path, "bad.py", 'import os\nos.getenv("HIDDEN"\n')
        with pytest.raises(AssertionError, match=r"bad\.py"):
            find_env_vars_read([bad])
        assert find_env_vars_read([bad], allow_unparsed=True) == set()

    def test_the_asserts_fail_on_an_unparsable_file_and_an_empty_corpus(self, tmp_path):
        bad = _write(tmp_path, "bad.py", 'import os\nos.getenv("HIDDEN"\n')
        readme = _write_readme(tmp_path)
        with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
            assert_readme_documents_every_env_var([bad], readme)
        with pytest.raises(pytest.fail.Exception, match="only 0 file"):
            assert_readme_documents_every_env_var([], readme)
        with pytest.raises(pytest.fail.Exception, match="only 0 file"):
            assert_no_new_undocumented_env_vars([], readme, tmp_path / "b.json")
        good = _write(tmp_path, "good.py", "x = 1\n")
        assert_readme_documents_every_env_var([good], readme)


class TestRegisterRefreshOption:
    def _make_parser(self):
        from _pytest.config.argparsing import Parser

        return Parser()

    def test_registers_without_raising(self):
        parser = self._make_parser()
        register_refresh_option(parser)
        args = parser.parse([REFRESH_FLAG])
        assert args.refresh_readme_env_var_baseline is True

    def test_double_registration_is_a_noop(self):
        parser = self._make_parser()
        register_refresh_option(parser)
        register_refresh_option(parser)


def test_find_env_vars_read_resolves_string_constants(tmp_path):
    f = _write(
        tmp_path,
        "c.py",
        """
import os

_ENV_VAR = "PROJ_ALLOW_UNSAFE"
_OTHER: str = "PROJ_OTHER"
_TWICE = "PROJ_A"
_TWICE = "PROJ_B"

def f():
    return os.environ.get(_ENV_VAR), os.getenv(_OTHER), os.environ.get(_TWICE), os.environ[_ENV_VAR]
""",
    )
    assert find_env_vars_read([f]) == {"PROJ_ALLOW_UNSAFE", "PROJ_OTHER"}


def test_find_env_vars_read_project_reader_funcs(tmp_path):
    f = _write(
        tmp_path,
        "d.py",
        """
from proj.env import env_flag, env_int
from proj import env

_NAME = "PROJ_CONST"

def f():
    return env_flag("PROJ_SWITCH"), env_int("PROJ_N", 5), env.env_float(_NAME, 0.5), unrelated("NOT_A_VAR")
""",
    )
    assert find_env_vars_read([f]) == set()
    assert find_env_vars_read([f], reader_funcs={"env_flag", "env_int", "env_float"}) == {"PROJ_SWITCH", "PROJ_N", "PROJ_CONST"}


def test_find_env_var_reads_first_site_and_default(tmp_path):
    from py_ci_shared.readme_env_var_parity import find_env_var_reads

    f = _write(
        tmp_path,
        "e.py",
        """
import os

def f():
    a = os.environ.get("PROJ_A", "0.05")
    b = env_int("PROJ_B", 32, minimum=2)
    c = os.environ.get("PROJ_A")
    return a, b, c
""",
    )
    reads = find_env_var_reads([f], reader_funcs={"env_int"})
    assert reads["PROJ_A"][1:] == (4, "'0.05'")
    assert reads["PROJ_B"][1:] == (5, "32")
    assert reads["PROJ_A"][0].name == "e.py"


def test_documented_names_may_start_with_underscore(tmp_path):
    readme = _write_readme(tmp_path, "_PRIVATE_SWITCH", "PUBLIC")
    assert find_readme_documented_vars(readme) == {"_PRIVATE_SWITCH", "PUBLIC"}


def test_environment_writes_are_not_reads(tmp_path):
    from py_ci_shared.readme_env_var_parity import find_env_vars_read

    src = tmp_path / "m.py"
    src.write_text(
        "import os\nimport subprocess\n\n"
        'os.environ.setdefault("OMP_NUM_THREADS", "1")\nos.environ.pop("STALE", None)\nos.environ["PYTHONIOENCODING"] = "utf-8"\n'
        'del os.environ["GONE"]\nsubprocess.run(["x"], env={**os.environ, "PGPASSWORD": "p"})\n'
        'a = os.environ.setdefault("READ_BY_SETDEFAULT", "1")\nb = os.environ.pop("READ_BY_POP", None)\nc = os.environ["READ_BY_SUBSCRIPT"]\n',
        encoding="utf-8",
    )
    assert find_env_vars_read([src]) == {"READ_BY_SETDEFAULT", "READ_BY_POP", "READ_BY_SUBSCRIPT"}
