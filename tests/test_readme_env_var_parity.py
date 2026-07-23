"""Unit tests for the README env-var documentation parity check.

Real scratch source files + README, same no-mocking convention as this
package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import orjson
import pytest

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
    return _write(tmp_path, "README.md", f"""
# Project

## Environment variables

| Name | Description |
|---|---|
{rows}
""")


def test_find_env_vars_read_both_forms(tmp_path):
    f = _write(tmp_path, "a.py", """
import os

def f():
    x = os.environ.get("MY_VAR")
    y = os.getenv("OTHER_VAR")
    return x, y
""")
    assert find_env_vars_read([f]) == {"MY_VAR", "OTHER_VAR"}


def test_find_env_vars_read_loop_over_literal_tuple(tmp_path):
    """for name in (LITERAL, ...): os.environ.get(name) -- a bare for-loop,
    iterable is a literal tuple inline (not a separately-assigned name)."""
    f = _write(tmp_path, "a.py", """
import os

def f():
    for name in ("KEY_A", "KEY_B"):
        if os.environ.get(name):
            return name
""")
    assert find_env_vars_read([f]) == {"KEY_A", "KEY_B"}


def test_find_env_vars_read_listcomp_over_named_tuple(tmp_path):
    """The real llm_bench shape this was written for: a module-level
    tuple assigned to a name, then consumed via a list-comprehension
    generator (not a plain for-statement) that calls os.environ.get on
    the comprehension's loop variable."""
    f = _write(tmp_path, "a.py", """
import os

KEY_NAMES = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")

def f():
    present = [name for name in KEY_NAMES if os.environ.get(name)]
    return present
""")
    assert find_env_vars_read([f]) == {"OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}


def test_find_env_vars_read_getenv_loop_form(tmp_path):
    """Same loop-variable shape via os.getenv rather than os.environ.get."""
    f = _write(tmp_path, "a.py", """
import os

NAMES = ("FOO", "BAR")

def f():
    for n in NAMES:
        os.getenv(n)
""")
    assert find_env_vars_read([f]) == {"FOO", "BAR"}


def test_find_env_vars_read_non_literal_iterable_not_hallucinated(tmp_path):
    """A loop over a runtime-computed iterable (not a literal tuple/list/set,
    and not a name resolvable to one) can't be resolved statically -- must
    stay silent rather than guessing, and must not crash."""
    f = _write(tmp_path, "a.py", """
import os

def f(names):
    for name in names:
        os.environ.get(name)
""")
    assert find_env_vars_read([f]) == set()


def test_find_readme_documented_vars_multi_name_cell(tmp_path):
    readme = _write(tmp_path, "README.md", """
## Environment variables

| Name | Description |
|---|---|
| `GIT_SHA` / `COMMIT_SHA` | the build sha |
""")
    assert find_readme_documented_vars(readme) == {"GIT_SHA", "COMMIT_SHA"}


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


class TestAssertNoNewUndocumentedEnvVars:
    def test_first_run_seeds_baseline_and_skips(self, tmp_path):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars([f], readme, baseline)
        assert orjson.loads(baseline.read_bytes()) == ["LEGACY_VAR"]

    def test_seeds_cleanly_when_readme_has_no_env_var_section_at_all(self, tmp_path):
        """A repo adopting this check may have NO env-var table yet -- unlike
        the hard-assert variant, this must not raise ValueError; every var
        read is simply grandfathered on first run."""
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write(tmp_path, "README.md", "# Project\nNo env var section here.\n")
        baseline = tmp_path / "_baseline.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars([f], readme, baseline)
        assert orjson.loads(baseline.read_bytes()) == ["LEGACY_VAR"]

    def test_grandfathered_var_does_not_fail_after_seeding(self, tmp_path):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars([f], readme, baseline)
        assert_no_new_undocumented_env_vars([f], readme, baseline)  # must not raise

    def test_new_undocumented_var_fails(self, tmp_path):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars([f], readme, baseline)

        _write(tmp_path, "b.py", 'import os\nos.environ.get("NEW_VAR")\n')
        with pytest.raises(pytest.fail.Exception, match="NEW_VAR"):
            assert_no_new_undocumented_env_vars([f, tmp_path / "b.py"], readme, baseline)

    def test_refresh_flag_reseeds(self, tmp_path, monkeypatch):
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        baseline.write_text("[]", encoding="utf-8")

        monkeypatch.setattr(sys, "argv", [*sys.argv, REFRESH_FLAG])
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars([f], readme, baseline)
        assert orjson.loads(baseline.read_bytes()) == ["LEGACY_VAR"]

    def test_documenting_a_grandfathered_var_shrinks_baseline_without_failing(self, tmp_path):
        """A var that gets documented (fixed) after being grandfathered must
        not fail -- only NEW undocumented vars are gated."""
        f = _write(tmp_path, "a.py", 'import os\nos.environ.get("LEGACY_VAR")\nos.environ.get("OTHER_LEGACY")\n')
        readme = _write_readme(tmp_path)
        baseline = tmp_path / "_baseline.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_undocumented_env_vars([f], readme, baseline)

        readme2 = _write_readme(tmp_path, "LEGACY_VAR")  # now documented
        assert_no_new_undocumented_env_vars([f], readme2, baseline)  # must not raise


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
