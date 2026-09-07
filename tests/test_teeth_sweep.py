"""`teeth_sweep._apply` must refuse to report a substitution that did not happen.

That is the module's whole safety property. A mutation that does not land leaves the code unchanged,
the suite green, and the case recorded as "has teeth" -- the opposite of the truth. Three separate
cases in one audit round were drafted that way: one matched a multi-line signature with a single-line
needle, one used a literal no-op, and one matched an LF needle against a CRLF file.
"""

from __future__ import annotations

from py_ci_shared.teeth_sweep import _apply


def test_a_unique_needle_is_substituted(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("x = 1\ny = 2\n", encoding="utf-8")

    assert _apply(p, "x = 1", "x = 99") is None
    assert "x = 99" in p.read_text(encoding="utf-8")


def test_a_needle_that_matches_nothing_is_reported_not_applied(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("x = 1\n", encoding="utf-8")

    why = _apply(p, "z = 3", "z = 4")

    assert why is not None and "matched 0" in why
    assert p.read_text(encoding="utf-8") == "x = 1\n", "a refused case must leave the file untouched"


def test_an_ambiguous_needle_is_refused(tmp_path):
    """Two matches means the case does not say which site it meant. Substituting the first is a
    guess, and a guess that silently mutates the wrong line reports about the wrong code."""
    p = tmp_path / "m.py"
    p.write_text("import orjson\nimport orjson\n", encoding="utf-8")

    why = _apply(p, "import orjson", "import json")

    assert why is not None and "matched 2" in why


def test_line_endings_are_normalised_to_the_target_file(tmp_path):
    """THE ONE THAT COST A CYCLE. Half of some repositories is CRLF, and an LF needle silently
    matches nothing there -- so the defect is never reintroduced, the suite stays green, and the
    reader concludes the TEST is toothless when the mutation never happened."""
    p = tmp_path / "crlf.py"
    p.write_bytes(b"def f():\r\n    return 1\r\n")

    assert _apply(p, "def f():\n    return 1", "def f():\n    return 2") is None
    assert b"return 2" in p.read_bytes()
    assert b"\r\n" in p.read_bytes(), "the file's own line endings must survive the substitution"
