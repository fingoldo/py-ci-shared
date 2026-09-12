"""`source_text_claims` finds every spelling the four repos' own bans caught, and none of the legitimate reads.

Each positive case is a shape that got past at least one repository's copy of this rule; each negative case is
a read a careless rule would flag, which is how such a rule gets switched off.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.source_text_claims import REFRESH_FLAG, assert_no_new_source_text_claims, find_source_text_claims


def _claims(tmp_path: Path, body: str, **kwargs):
    path = tmp_path / "test_probe.py"
    path.write_text(body, encoding="utf-8")
    return find_source_text_claims(path, **kwargs)


def _lines(tmp_path: Path, body: str, **kwargs) -> list[int]:
    return [c.line for c in _claims(tmp_path, body, **kwargs)]


class TestTheReaders:
    @pytest.mark.parametrize(
        "body",
        [
            "import inspect\ndef test_x():\n    assert 'x' in inspect.getsource(f)\n",
            "from inspect import getsource\ndef test_x():\n    assert 'x' in getsource(f)\n",
            "import inspect as i\ndef test_x():\n    src = i.getsource(f)\n    assert 'x' in src\n",
            "import inspect\ndef test_x():\n    lines, _ = inspect.getsourcelines(f)\n    assert 'x' in ''.join(lines)\n",
            "import ast\ndef test_x():\n    assert 'x' in ast.unparse(tree)\n",
            "import dis as d\ndef test_x():\n    assert 'LOAD' in d.code_info(f)\n",
            "def test_x():\n    assert 'x' in f.__code__.co_names\n",
        ],
        ids=["getsource", "bare getsource", "aliased", "getsourcelines", "unparse", "dis alias", "co_names"],
    )
    def test_each_reader_is_caught(self, tmp_path, body):
        assert _claims(tmp_path, body)


class TestSourceFilesReadTheLongWay:
    def test_a_dunder_file_read(self, tmp_path):
        body = "from pathlib import Path\nimport m\ndef test_x():\n    text = Path(m.__file__).read_text()\n    assert 'x' in text\n"
        assert _lines(tmp_path, body) == [5]

    def test_a_module_constant_holding_the_path(self, tmp_path):
        body = '_SCRIPT = ROOT / "scripts" / "verify.py"\n\ndef test_x():\n    text = _SCRIPT.read_text()\n    assert "x" in text\n'
        assert _lines(tmp_path, body) == [5]

    def test_a_glob_loop_variable(self, tmp_path):
        body = 'def test_x():\n    for path in ROOT.glob("*.py"):\n        text = path.read_text()\n        assert "x" not in text\n'
        assert _lines(tmp_path, body) == [4]

    def test_a_generator_over_a_glob(self, tmp_path):
        """glossum's test_saver_loads_ol_to_en_too joined a module family this way and the loop-only rule missed it."""
        body = 'def test_x():\n    src = "".join(p.read_text() for p in sorted(DIR.glob("tv*.py")))\n    assert "x" in src\n'
        assert _lines(tmp_path, body) == [3]

    def test_a_generator_over_a_non_source_glob(self, tmp_path):
        body = 'def test_x():\n    text = "".join(p.read_text() for p in DIR.glob("*.json"))\n    assert "x" in text\n'
        assert _lines(tmp_path, body) == []

    def test_a_sql_file_counts_as_source(self, tmp_path):
        body = 'def test_x():\n    sql = (SQL_DIR / "q.sql").read_text()\n    assert "ORDER BY" in sql\n'
        assert _lines(tmp_path, body) == [3]

    def test_sql_can_be_excluded(self, tmp_path):
        body = 'def test_x():\n    sql = (SQL_DIR / "q.sql").read_text()\n    assert "ORDER BY" in sql\n'
        assert _lines(tmp_path, body, treat_sql_as_source=False) == []

    def test_open_then_read(self, tmp_path):
        body = 'def test_x():\n    with open("pkg/mod.py") as fh:\n        src = fh.read()\n    assert "x" in src\n'
        assert _lines(tmp_path, body) == [4]


class TestTheTextTravels:
    def test_a_derived_slice(self, tmp_path):
        """Only production_scrapers' own script saw this; the text is still source after the slice."""
        body = 'import inspect\ndef test_x():\n    src = inspect.getsource(m)\n    block = src[src.index("def f"):]\n    assert "return 1" in block\n'
        assert _lines(tmp_path, body) == [5]

    def test_a_reader_helper(self, tmp_path):
        """Only mlframe's scanner followed this; it put 322 assertions one level out of every other rule."""
        body = 'def _read(rel):\n    return (ROOT / rel).read_text()\n\ndef test_x():\n    src = _read("pkg/mod.py")\n    assert "x" in src\n'
        assert _lines(tmp_path, body) == [6]

    def test_a_position_comparison(self, tmp_path):
        body = 'import inspect\ndef test_x():\n    src = inspect.getsource(m)\n    assert src.find("a") < src.find("b")\n'
        assert _lines(tmp_path, body) == [4]

    @pytest.mark.parametrize("check", ["assert m", "assert m is not None", "assert (m := re.search('x', src))"])
    def test_a_regex_match_over_source(self, tmp_path, check):
        """glossum's test_update_includes_source_word_match asserted a bare match object and was not flagged."""
        body = f"import inspect, re\ndef test_x():\n    src = inspect.getsource(mod)\n    m = re.search('x', src)\n    {check}\n"
        assert _lines(tmp_path, body) == [5]

    def test_a_regex_match_over_rendered_output(self, tmp_path):
        body = "import re\ndef test_x():\n    m = re.search('x', render())\n    assert m\n"
        assert _lines(tmp_path, body) == []

    def test_an_if_fail_guard(self, tmp_path):
        body = 'import inspect, pytest\ndef test_x():\n    src = inspect.getsource(m)\n    if "x" not in src:\n        pytest.fail("gone")\n'
        assert _lines(tmp_path, body) == [4]


class TestWhatIsNotAClaim:
    def test_a_name_in_another_function_does_not_leak(self, tmp_path):
        body = 'import inspect\ndef test_a():\n    src = inspect.getsource(m)\n\ndef test_b():\n    src = render()\n    assert "x" in src\n'
        assert _lines(tmp_path, body) == []

    def test_parsing_source_is_how_meta_linters_work(self, tmp_path):
        body = 'import ast\ndef test_x():\n    tree = ast.parse(Path(m.__file__).read_text())\n    assert any(isinstance(n, ast.Try) for n in ast.walk(tree))\n'
        assert _lines(tmp_path, body) == []

    @pytest.mark.parametrize("name", ["README.md", "config.toml", "cache.json", "prompt.txt"])
    def test_reading_a_non_source_file_next_to_dunder_file(self, tmp_path, name):
        body = f'def test_x():\n    text = (Path(__file__).parent / "{name}").read_text()\n    assert "x" in text\n'
        assert _lines(tmp_path, body) == []

    def test_calling_the_bound_name_is_behaviour(self, tmp_path):
        body = 'import inspect\ndef test_x():\n    fn = inspect.getsource\n    assert fn("x") == "y"\n'
        assert _lines(tmp_path, body) == []

    def test_a_reader_without_a_content_check(self, tmp_path):
        body = "import inspect\ndef test_x():\n    assert inspect.getsource(f)\n"
        assert _lines(tmp_path, body) == []


class TestReadMode:
    def test_every_source_read_is_reported_asserted_or_not(self, tmp_path):
        body = 'import inspect\ndef test_x():\n    src = inspect.getsource(m)\n    text = (ROOT / "x.py").read_text()\n    doc = (ROOT / "README.md").read_text()\n'
        assert _lines(tmp_path, body, mode="read") == [3, 4]

    def test_parsing_a_file_whose_path_is_a_parameter(self, tmp_path):
        """dashboard's `_names_in(path)` got its path from a caller's glob loop; only the parse said it was Python."""
        body = 'import ast\ndef _names_in(path):\n    tree = ast.parse(path.read_text(encoding="utf-8"))\n    return tree\n'
        assert _lines(tmp_path, body, mode="read") == [3]

    def test_parsing_a_file_from_a_helper_built_list(self, tmp_path):
        """realtime_applications' worker parity test iterated a list another function built from rglob."""
        body = 'import ast\ndef test_x():\n    for f in _unit_files():\n        tree = ast.parse(f.read_text(encoding="utf-8"))\n'
        assert _lines(tmp_path, body, mode="read") == [4]

    def test_parsing_is_still_not_an_assertion(self, tmp_path):
        body = 'import ast\ndef test_x():\n    tree = ast.parse(path.read_text())\n    assert any(isinstance(n, ast.Try) for n in ast.walk(tree))\n'
        assert _lines(tmp_path, body) == []

    def test_parsing_a_string_literal_is_not_a_read(self, tmp_path):
        body = 'import ast\ndef test_x():\n    tree = ast.parse("x = 1")\n'
        assert _lines(tmp_path, body, mode="read") == []


class TestTheRatchet:
    def _tree(self, tmp_path):
        (tmp_path / "tests").mkdir()
        bad = tmp_path / "tests" / "test_bad.py"
        bad.write_text('import inspect\ndef test_x():\n    assert "x" in inspect.getsource(f)\n', encoding="utf-8")
        good = tmp_path / "tests" / "test_good.py"
        good.write_text("def test_y():\n    assert f() == 1\n", encoding="utf-8")
        return [bad, good]

    def test_a_new_claim_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match=re.escape("test_bad.py::test_x")):
            assert_no_new_source_text_claims(self._tree(tmp_path), tmp_path)

    def test_an_allowlisted_file_passes_and_a_stale_one_fails(self, tmp_path):
        files = self._tree(tmp_path)
        assert_no_new_source_text_claims(files, tmp_path, allowlist={"tests/test_bad.py": "a meta-test that bans a pattern must read source"})
        with pytest.raises(pytest.fail.Exception, match="no longer read source"):
            assert_no_new_source_text_claims(files, tmp_path, allowlist={"tests/test_good.py": "this entry names a file with no claim at all"})

    def test_a_baseline_accepts_debt_and_fails_when_it_drains(self, tmp_path):
        files = self._tree(tmp_path)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(["tests/test_bad.py::test_x::getsource()"]), encoding="utf-8")
        assert_no_new_source_text_claims(files, tmp_path, baseline_path=baseline)
        baseline.write_text(json.dumps(["tests/test_bad.py::test_x::getsource()", "tests/test_gone.py::test_z::getsource()"]), encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=re.escape("test_gone.py")):
            assert_no_new_source_text_claims(files, tmp_path, baseline_path=baseline)

    def test_a_missing_baseline_is_written_and_skips(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_source_text_claims(self._tree(tmp_path), tmp_path, baseline_path=baseline)
        assert json.loads(baseline.read_text(encoding="utf-8")) == ["tests/test_bad.py::test_x::getsource()"]

    def test_the_floor(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_no_new_source_text_claims(self._tree(tmp_path), tmp_path, min_files=5)

    def test_an_allowlist_reason_must_say_something(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="no reason"):
            assert_no_new_source_text_claims(self._tree(tmp_path), tmp_path, allowlist={"tests/test_bad.py": "ok"})

    def test_the_refresh_flag_is_named(self):
        assert REFRESH_FLAG.startswith("--refresh-")
