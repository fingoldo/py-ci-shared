"""The install-pyutilz action resolves the calling repository's pyutilz pin; it fails rather than guess when that pin is absent or split."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("resolve_pin", REPO / ".github" / "actions" / "install-pyutilz" / "resolve_pin.py")
resolve_pin = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(resolve_pin)

SHA_A = "a" * 40
SHA_B = "b" * 40


def test_one_pin_is_returned():
    text = f'dev = ["pyutilz @ git+https://github.com/fingoldo/pyutilz.git@{SHA_A}"]'
    assert resolve_pin.pins(text) == {SHA_A}


def test_the_same_pin_twice_is_still_one_commit():
    text = f'a = "pyutilz @ git+https://github.com/fingoldo/pyutilz@{SHA_A}"\nb = "pyutilz @ git+https://github.com/fingoldo/pyutilz.git@{SHA_A}"'
    assert resolve_pin.pins(text) == {SHA_A}


def test_two_different_pins_are_refused(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        f'x = "pyutilz @ git+https://github.com/fingoldo/pyutilz.git@{SHA_A}"\n' f'y = "pyutilz @ git+https://github.com/fingoldo/pyutilz.git@{SHA_B}"\n',
        encoding="utf-8",
    )
    assert resolve_pin.main(["resolve_pin.py", str(pyproject)]) == 1
    assert "exactly one commit" in capsys.readouterr().err


def test_no_pin_is_refused(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert resolve_pin.main(["resolve_pin.py", str(pyproject)]) == 1
    assert "found 0" in capsys.readouterr().err


@pytest.mark.parametrize("ref", ["main", "v1.2.3", "abc123"])
def test_a_branch_or_tag_is_not_a_pin(ref):
    text = f'"pyutilz @ git+https://github.com/fingoldo/pyutilz.git@{ref}"'
    assert resolve_pin.pins(text) == set()
