"""Tests for ``py_ci_shared.uncalled_functions``.

The check exists because three controls in one downstream repo were written, exported, tested and
never invoked. The interesting behaviour is therefore what does NOT count as a call, and that is
what most of these tests pin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.uncalled_functions import assert_no_new_uncalled_function, find_uncalled_functions


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestWhatCountsAsUncalled:
    def test_a_function_nothing_calls_is_reported(self, tmp_path):
        f = _write(tmp_path, "m.py", "def never_called():\n    return 1\n")
        assert find_uncalled_functions([f], tmp_path) == {"m.py::never_called": "never_called"}

    def test_a_function_called_from_another_module_is_not(self, tmp_path):
        a = _write(tmp_path, "a.py", "def helper():\n    return 1\n")
        b = _write(tmp_path, "b.py", "from a import helper\n\n\ndef go():\n    return helper()\n")
        assert "a.py::helper" not in find_uncalled_functions([a, b], tmp_path)

    def test_a_function_called_only_within_its_own_module_is_not(self, tmp_path):
        """A private helper used by its own file is live. Excluding the defining module from the
        reference scan would report every one of them."""
        f = _write(tmp_path, "m.py", "def _helper():\n    return 1\n\n\ndef public():\n    return _helper()\n")
        assert "m.py::_helper" not in find_uncalled_functions([f], tmp_path)

    def test_a_reference_without_a_call_still_counts(self, tmp_path):
        """`handlers = [f]` is a call site once something iterates it. A false "this is dead" is far
        more expensive than a missed one -- it invites deletion of working code."""
        a = _write(tmp_path, "a.py", "def handler():\n    return 1\n")
        b = _write(tmp_path, "b.py", "from a import handler\n\nHANDLERS = [handler]\n")
        assert "a.py::handler" not in find_uncalled_functions([a, b], tmp_path)

    def test_a_decorator_use_counts(self, tmp_path):
        a = _write(tmp_path, "a.py", "def deco(fn):\n    return fn\n")
        b = _write(tmp_path, "b.py", "from a import deco\n\n\n@deco\ndef thing():\n    return 1\n")
        assert "a.py::deco" not in find_uncalled_functions([a, b], tmp_path)

    def test_a_literal_getattr_counts(self, tmp_path):
        """Dynamic dispatch is a real call site even though no Name node names it."""
        a = _write(tmp_path, "a.py", "def dynamic():\n    return 1\n")
        b = _write(tmp_path, "b.py", "import a\n\nfn = getattr(a, 'dynamic')\n")
        assert "a.py::dynamic" not in find_uncalled_functions([a, b], tmp_path)


class TestTheThreeThingsThatMustNotCountAsACall:
    """The whole point of parsing rather than grepping. Each of these is how a dead control hides,
    and with ``ast`` each falls out for free rather than needing an exception."""

    def test_an_all_entry_does_not_count(self, tmp_path):
        """The commonest disguise: exporting a function makes it look used to a grep and to
        vulture, and means nothing about whether anything calls it."""
        f = _write(tmp_path, "m.py", '__all__ = ["exported"]\n\n\ndef exported():\n    return 1\n')
        assert "m.py::exported" in find_uncalled_functions([f], tmp_path)

    def test_its_own_doctest_does_not_count(self, tmp_path):
        f = _write(
            tmp_path,
            "m.py",
            'def documented():\n    """Do a thing.\n\n    >>> documented()\n    1\n    """\n    return 1\n',
        )
        assert "m.py::documented" in find_uncalled_functions([f], tmp_path)

    def test_a_comment_does_not_count(self, tmp_path):
        f = _write(tmp_path, "m.py", "# TODO: wire up mentioned() somewhere\ndef mentioned():\n    return 1\n")
        assert "m.py::mentioned" in find_uncalled_functions([f], tmp_path)

    def test_an_import_alone_does_not_count(self, tmp_path):
        """Importing something and never calling it is precisely the state this check hunts."""
        a = _write(tmp_path, "a.py", "def imported_only():\n    return 1\n")
        b = _write(tmp_path, "b.py", "from a import imported_only  # noqa: F401\n")
        assert "a.py::imported_only" in find_uncalled_functions([a, b], tmp_path)


class TestAliasedImportsResolve:
    """The first version of this module reported a live function as dead because an alias and its
    original never met -- `redact_secrets as _redact_secrets`, called under the alias."""

    def test_an_alias_that_is_called_counts_as_a_call(self, tmp_path):
        a = _write(tmp_path, "a.py", "def original():\n    return 1\n")
        b = _write(tmp_path, "b.py", "from a import original as _alias\n\n\ndef go():\n    return _alias()\n")
        assert "a.py::original" not in find_uncalled_functions([a, b], tmp_path)

    def test_an_alias_that_is_never_used_does_not(self, tmp_path):
        """Aliasing is not using. Counting the import itself would make the alias a hiding place."""
        a = _write(tmp_path, "a.py", "def original():\n    return 1\n")
        b = _write(tmp_path, "b.py", "from a import original as _alias  # noqa: F401\n")
        assert "a.py::original" in find_uncalled_functions([a, b], tmp_path)


class TestScope:
    def test_methods_are_out_of_scope(self, tmp_path):
        """A method is reached through an instance and its name is often shared across unrelated
        classes, so "is this called" needs type information this check does not have. Reporting one
        would be a guess."""
        f = _write(tmp_path, "m.py", "class C:\n    def method(self):\n        return 1\n")
        assert find_uncalled_functions([f], tmp_path) == {}

    def test_a_syntax_error_is_skipped_rather_than_raised(self, tmp_path):
        """A CI helper must not take the suite down over one unparseable file."""
        bad = _write(tmp_path, "bad.py", "def (:\n")
        good = _write(tmp_path, "good.py", "def orphan():\n    return 1\n")
        assert find_uncalled_functions([bad, good], tmp_path) == {"good.py::orphan": "orphan"}


class TestTheRatchet:
    def test_it_seeds_a_baseline_and_skips(self, tmp_path):
        f = _write(tmp_path, "m.py", "def orphan():\n    return 1\n")
        baseline = tmp_path / "baseline.json"
        with pytest.raises(BaseException) as excinfo:  # pytest.skip raises Skipped
            assert_no_new_uncalled_function([f], tmp_path, baseline)
        assert "Skipped" in type(excinfo.value).__name__
        assert baseline.exists()

    def test_a_baselined_function_does_not_fail(self, tmp_path):
        f = _write(tmp_path, "m.py", "def orphan():\n    return 1\n")
        baseline = tmp_path / "baseline.json"
        baseline.write_text('["m.py::orphan"]', encoding="utf-8")
        assert_no_new_uncalled_function([f], tmp_path, baseline)

    def test_a_new_one_fails(self, tmp_path):
        f = _write(tmp_path, "m.py", "def orphan():\n    return 1\n\n\ndef fresh():\n    return 2\n")
        baseline = tmp_path / "baseline.json"
        baseline.write_text('["m.py::orphan"]', encoding="utf-8")
        with pytest.raises(BaseException) as excinfo:
            assert_no_new_uncalled_function([f], tmp_path, baseline)
        assert "m.py::fresh" in str(excinfo.value)

    def test_a_stale_baseline_entry_fails(self, tmp_path):
        """An entry whose function is now called must be dropped, or the baseline stops meaning
        anything for that name."""
        f = _write(tmp_path, "m.py", "def used():\n    return 1\n\n\ndef go():\n    return used()\n")
        baseline = tmp_path / "baseline.json"
        # `go` is baselined TOO, deliberately: it is itself uncalled here, and leaving it out made
        # the check fail on it as a new finding before ever reaching the stale entry -- the first
        # version of this test asserted the stale message and got the new-finding one.
        baseline.write_text('["m.py::used", "m.py::go"]', encoding="utf-8")
        with pytest.raises(BaseException) as excinfo:
            assert_no_new_uncalled_function([f], tmp_path, baseline)
        assert "no longer uncalled" in str(excinfo.value)

    def test_ignore_takes_bare_names(self, tmp_path):
        f = _write(tmp_path, "m.py", "def public_api():\n    return 1\n")
        baseline = tmp_path / "baseline.json"
        baseline.write_text("[]", encoding="utf-8")
        assert_no_new_uncalled_function([f], tmp_path, baseline, ignore=["public_api"])
