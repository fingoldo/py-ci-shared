"""Tests for ``py_ci_shared.uncalled_functions``.

The check exists because three controls in one downstream repo were written, exported, tested and
never invoked. The interesting behaviour is therefore what does NOT count as a call, and that is
what most of these tests pin.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from py_ci_shared._core import UnparsedFilesError
from py_ci_shared.uncalled_functions import assert_no_new_uncalled_function, find_uncalled_functions


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


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

    def test_a_call_through_a_re_export_alias_chain_counts(self, tmp_path):
        """``_impl._helper`` re-exported by ``shared`` as ``helper`` and imported by its consumer as ``_probe``:
        the only call site names neither the definition's name nor a same-file alias of it."""
        impl = _write(tmp_path, "_impl.py", "def _helper():\n    return 1\n")
        shared = _write(tmp_path, "shared.py", "from _impl import _helper as helper  # noqa: F401\n")
        user = _write(tmp_path, "user.py", "from shared import helper as _probe\n\n\ndef go():\n    return _probe()\n")
        assert "_impl.py::_helper" not in find_uncalled_functions([impl, shared, user], tmp_path)

    def test_a_re_exported_function_nobody_calls_is_still_reported(self, tmp_path):
        impl = _write(tmp_path, "_impl.py", "def _helper():\n    return 1\n")
        shared = _write(tmp_path, "shared.py", "from _impl import _helper as helper  # noqa: F401\n")
        assert "_impl.py::_helper" in find_uncalled_functions([impl, shared], tmp_path)

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

    def test_a_syntax_error_raises_unless_the_caller_allows_it(self, tmp_path):
        """An unparsable file's call sites are unknown, so every function it calls would read as dead."""
        bad = _write(tmp_path, "bad.py", "from good import orphan\norphan()\ndef (:\n")
        good = _write(tmp_path, "good.py", "def orphan():\n    return 1\n")
        with pytest.raises(UnparsedFilesError, match=re.escape("bad.py")):
            find_uncalled_functions([bad, good], tmp_path)
        assert find_uncalled_functions([bad, good], tmp_path, allow_unparsed=True) == {"good.py::orphan": "orphan"}


class TestTheRatchet:
    def test_a_missing_baseline_fails_and_is_written_only_on_refresh(self, tmp_path, monkeypatch):
        """Seeding on a missing file turned a deleted or mistyped baseline into a permanent pass."""
        monkeypatch.delenv("PY_CI_SHARED_REFRESH", raising=False)
        f = _write(tmp_path, "m.py", "def orphan():\n    return 1\n")
        baseline = tmp_path / "baseline.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_new_uncalled_function([f], tmp_path, baseline)
        assert not baseline.exists()
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_uncalled_function([f], tmp_path, baseline, refresh=True)
        assert baseline.exists()
        assert_no_new_uncalled_function([f], tmp_path, baseline)

    def test_refresh_via_env_var_as_under_xdist(self, tmp_path, monkeypatch):
        f = _write(tmp_path, "m.py", "def orphan():\n    return 1\n")
        baseline = tmp_path / "baseline.json"
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", "uncalled-functions")
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_uncalled_function([f], tmp_path, baseline)
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


class TestAuditRegressions:
    def test_self_recursion_is_not_a_call(self, tmp_path):
        f = _write(tmp_path, "m.py", "def countdown(n):\n    return countdown(n - 1) if n else 0\n")
        assert find_uncalled_functions([f], tmp_path) == {"m.py::countdown": "countdown"}
        g = _write(tmp_path, "n.py", "from m import countdown\n\ndef main():\n    return countdown(3)\n\nmain()\n")
        assert find_uncalled_functions([f, g], tmp_path) == {}

    def test_a_same_name_local_is_not_a_call(self, tmp_path):
        f = _write(
            tmp_path,
            "m.py",
            "def helper():\n    return 1\n\ndef other(helper=None):\n    return helper\n\ndef third():\n    helper = 2\n    return helper\n\nother()\nthird()\n",
        )
        assert find_uncalled_functions([f], tmp_path) == {"m.py::helper": "helper"}
        g = _write(tmp_path, "n.py", "def helper():\n    return 1\n\ndef user():\n    def inner():\n        return helper()\n    return inner()\n\nuser()\n")
        assert find_uncalled_functions([g], tmp_path) == {}

    def test_a_function_imported_inside_the_calling_function_is_called(self, tmp_path):
        """A lazy import binds the name locally, but to the function itself: it is a reference, not a shadow.

        Reading it as shadowing reported every lazily imported function as dead -- 49 in one consuming project,
        where ``from pkg.ids import make_id`` inside a CLI command is the normal way to keep start-up cheap.
        """
        f = _write(tmp_path, "ids.py", "def make_id():\n    return 1\n")
        g = _write(tmp_path, "cli.py", "def command():\n    from ids import make_id\n    return make_id()\n\ncommand()\n")
        assert find_uncalled_functions([f, g], tmp_path) == {}

    def test_a_function_imported_under_an_alias_inside_a_function_is_called(self, tmp_path):
        f = _write(tmp_path, "ids.py", "def make_id():\n    return 1\n")
        g = _write(tmp_path, "cli.py", "def command():\n    from ids import make_id as mk\n    return mk()\n\ncommand()\n")
        assert find_uncalled_functions([f, g], tmp_path) == {}

    def test_defs_under_module_if_and_try_are_judged(self, tmp_path):
        f = _write(
            tmp_path,
            "m.py",
            "import sys\nif sys.platform == 'win32':\n    def win_only():\n        return 1\nelse:\n    def posix_only():\n        return 2\n"
            "try:\n    import fast\nexcept ImportError:\n    def fallback():\n        return 3\n\nposix_only()\n",
        )
        assert find_uncalled_functions([f], tmp_path) == {"m.py::win_only": "win_only", "m.py::fallback": "fallback"}

    def test_the_file_floor(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        baseline.write_text("[]", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_no_new_uncalled_function([], tmp_path, baseline)

    def test_a_bom_file_is_parsed_and_an_unparsable_one_fails_the_entry_point(self, tmp_path):
        bom = tmp_path / "bom.py"
        bom.write_bytes(b"\xef\xbb\xbfdef orphan():\n    return 1\n")
        assert find_uncalled_functions([bom], tmp_path) == {"bom.py::orphan": "orphan"}
        bad = _write(tmp_path, "bad.py", "def (:\n")
        baseline = tmp_path / "baseline.json"
        baseline.write_text('["bom.py::orphan"]', encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
            assert_no_new_uncalled_function([bom, bad], tmp_path, baseline)


class TestGuardedLocalImport:
    def test_a_lazily_imported_function_with_an_import_error_fallback_is_called(self, tmp_path):
        """``try: from a import f`` / ``except ImportError: f = None`` then ``f()``: the fallback assignment must not turn
        the imported name into a local shadow."""
        a = _write(tmp_path, "a.py", "def f():\n    return 1\n")
        b = _write(
            tmp_path,
            "b.py",
            "def g():\n    try:\n        from a import f\n    except ImportError:\n        f = None\n    if f is not None:\n        return f()\n",
        )
        assert "a.py::f" not in find_uncalled_functions([a, b], tmp_path)
