"""`source_text_ban.offending_lines` catches the four evasions, and stays quiet on the rest.

Each positive case below is a real shape that got past a project's own copy of this rule; each
negative case is a read that a stricter rule would have flagged, which is how a check like this gets
switched off.
"""

from __future__ import annotations

from py_ci_shared.source_text_ban import offending_lines


def _probe(tmp_path, body: str):
    path = tmp_path / "test_probe.py"
    path.write_text(body, encoding="utf-8")
    return offending_lines(path)


class TestTheFourEvasions:
    def test_getsource_is_caught(self, tmp_path):
        assert _probe(tmp_path, "src = inspect.getsource(backfill._reader_loop)\n")

    def test_a_dunder_file_read_is_caught(self, tmp_path):
        assert _probe(tmp_path, 'source = Path(module.__file__).read_text(encoding="utf-8")\n')

    def test_a_path_built_from_a_module_constant_is_caught(self, tmp_path):
        """The third evasion: the `.py` is on the BINDING line, and the read is somewhere else."""
        found = _probe(
            tmp_path,
            '_SCRIPT = _ROOT / "scripts" / "verify_sql.py"\n\n\ndef test_x():\n    text = _SCRIPT.read_text(encoding="utf-8")\n',
        )
        assert [n for n, _ in found] == [5], "the offending line is the READ, not the binding"

    def test_a_glob_loop_variable_is_caught(self, tmp_path):
        """The fourth: `for path in DIR.glob("*.py")` parses a whole package, and every earlier
        version of this rule saw nothing at all."""
        assert _probe(tmp_path, 'for path in sorted(_VIEWS.glob("*.py")):\n    tree = ast.parse(path.read_text(encoding="utf-8"))\n')

    def test_an_rglob_loop_variable_is_caught_too(self, tmp_path):
        assert _probe(tmp_path, 'for f in ROOT.rglob("*.py"):\n    tree = ast.parse(f.read_text(encoding="utf-8"))\n')


class TestTheTextTravelsUnderANewName:
    def test_parsing_a_variable_read_from_a_py_file_is_caught(self, tmp_path):
        """`ast.parse(source)` carries no path of its own, so the READ is what has to be seen."""
        found = _probe(
            tmp_path,
            'for py_file in ROOT.glob("*.py"):\n    source = py_file.read_text(encoding="utf-8")\n    tree = ast.parse(source)\n',
        )
        # Both the read and the parse are reported: the text is Python source under either name, and
        # naming both lines is what tells the author where the claim actually is.
        assert [n for n, _ in found] == [2, 3]

    def test_a_name_holding_unrelated_text_is_left_alone(self, tmp_path):
        """Teeth for the rule above: `source` is only Python source because the line that filled it
        said so. A name filled from a `.sql` file and parsed is somebody else's bug, not this one."""
        assert not _probe(tmp_path, 'source = (SQL_DIR / "schema.sql").read_text(encoding="utf-8")\nparsed = parse_sql(source)\n')


class TestWhatIsDeliberatelyNotBanned:
    """A rule that fires on these is a rule someone turns off, and then the real one is gone."""

    def test_reading_a_sql_file(self, tmp_path):
        assert not _probe(tmp_path, 'text = (SQL_DIR / "schema.sql").read_text(encoding="utf-8")\n')

    def test_reading_a_readme_next_to_dunder_file(self, tmp_path):
        """`Path(__file__).parent / "README.MD"` carries the token but not the subject."""
        assert not _probe(tmp_path, 'readme = (Path(__file__).resolve().parent / "README.MD").read_text(encoding="utf-8")\n')

    def test_reading_a_deployment_toml(self, tmp_path):
        assert not _probe(tmp_path, 'text = (Path(__file__).resolve().parents[1] / "config.toml").read_text(encoding="utf-8")\n')

    def test_reading_a_prompt_text_file(self, tmp_path):
        assert not _probe(tmp_path, 'PROMPT = (Path(__file__).parent.parent / "prompts" / "system_v3.txt").read_text(encoding="utf-8")\n')

    def test_a_comment_mentioning_the_pattern(self, tmp_path):
        assert not _probe(tmp_path, "# this used to be inspect.getsource(loader), which passed against dead code\n")

    def test_a_docstring_line_starting_with_a_quote(self, tmp_path):
        assert not _probe(tmp_path, '"""was: source = Path(m.__file__).read_text()"""\n')


class TestTheCallerCanAddItsOwnForms:
    def test_extra_patterns_are_honoured(self, tmp_path):
        import re

        path = tmp_path / "test_probe.py"
        path.write_text("value = dis.get_instructions(fn)\n", encoding="utf-8")
        assert not offending_lines(path)
        assert offending_lines(path, extra_patterns=(re.compile(r"\bdis\.get_instructions\s*\("),))
