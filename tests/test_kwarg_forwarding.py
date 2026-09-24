"""``kwarg_forwarding`` finds dropped variant options, in-scope arguments not passed, and delegate state not copied."""

from __future__ import annotations

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.kwarg_forwarding import find_available_but_not_passed, find_delegate_state_loss, find_dropped_variant_params


def _write(tmp_path, name, source):
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def test_a_variant_that_drops_its_bases_options_is_flagged(tmp_path):
    """``fit_stacked`` calls ``fit`` without ``val_df``; forwarding it (or **kwargs) clears the finding."""
    bad = _write(
        tmp_path,
        "m.py",
        "class D:\n"
        "    def fit(self, df, val_df=None, time_ordering=None):\n        pass\n"
        "    def fit_stacked(self, df, time_ordering=None):\n        self.fit(df, time_ordering=time_ordering)\n",
    )
    assert [f.name for f in find_dropped_variant_params([bad], tmp_path)] == ["val_df"]
    good = _write(
        tmp_path,
        "m.py",
        "class D:\n" "    def fit(self, df, val_df=None):\n        pass\n" "    def fit_stacked(self, df, **kw):\n        self.fit(df, **kw)\n",
    )
    assert find_dropped_variant_params([good], tmp_path) == []


def test_a_registered_cross_module_variant_is_checked(tmp_path):
    """A variant living in another module is paired with its base through ``delegates``."""
    base = _write(tmp_path, "b.py", "def fit(self, df, val_y=None):\n    pass\n")
    var = _write(tmp_path, "v.py", "def fit_twice(self, df, val_y=None):\n    self.fit(df)\n")
    found = find_dropped_variant_params([base, var], tmp_path, delegates={"v.py::fit_twice": "b.py::fit"})
    assert [f.name for f in found] == ["val_y"]


def test_an_argument_in_scope_but_not_passed_is_flagged(tmp_path):
    """The caller has ``sample_weight`` and calls a function taking it without passing it; positional passing counts."""
    p = _write(
        tmp_path,
        "m.py",
        "def stack(P, y, sample_weight=None):\n    pass\n"
        "def build(P, y, sample_weight=None):\n    return stack(P, y)\n"
        "def build_ok(P, y, sample_weight=None):\n    return stack(P, y, sample_weight)\n",
    )
    assert [(f.scope, f.name) for f in find_available_but_not_passed([p], tmp_path)] == [("build", "sample_weight")]


def test_a_delegate_that_loses_injected_state_is_flagged(tmp_path):
    """State injected from outside and read by fit must be copied onto a same-class delegate before its fit."""
    cls = _write(
        tmp_path,
        "c.py",
        "class Disc:\n"
        "    def fit(self, df):\n        return getattr(self, '_groups', None)\n"
        "    def per_group(self, df):\n        d = Disc()\n        d.fit(df)\n"
        "    def per_group_ok(self, df):\n        d = Disc()\n        d._groups = self._groups\n        d.fit(df)\n",
    )
    suite = _write(tmp_path, "s.py", "def run(disc, g):\n    disc._groups = g\n")
    found = find_delegate_state_loss([cls, suite], tmp_path)
    assert [(f.scope, f.name) for f in found] == [("Disc.per_group", "_groups")]


def test_a_copy_loop_over_constant_names_counts_as_copying(tmp_path):
    """``for a in ("_groups",): setattr(d, a, ...)`` copies the state."""
    cls = _write(
        tmp_path,
        "c.py",
        "class Disc:\n"
        "    def fit(self, df):\n        return getattr(self, '_groups', None)\n"
        "    def per_group(self, df):\n        d = Disc()\n"
        "        for a in ('_groups',):\n            setattr(d, a, getattr(self, a))\n        d.fit(df)\n",
    )
    suite = _write(tmp_path, "s.py", "def run(disc, g):\n    disc._groups = g\n")
    assert find_delegate_state_loss([cls, suite], tmp_path) == []


_DROPS = "def fit(df, val_df=None):\n    return df\n\n\ndef fit_stacked(df):\n    return fit(df)\n"
_FINDERS = (find_dropped_variant_params, find_available_but_not_passed, find_delegate_state_loss)


def test_a_bom_file_is_scanned_like_a_plain_one(tmp_path):
    """A leading BOM must not hide the dropped argument inside the file."""
    plain = find_dropped_variant_params([_write(tmp_path, "a.py", _DROPS)], tmp_path)
    bom = tmp_path / "b.py"
    bom.write_bytes(b"\xef\xbb\xbf" + _DROPS.encode("utf-8"))
    found = find_dropped_variant_params([bom], tmp_path)
    assert [(f.scope, f.name) for f in found] == [(f.scope, f.name) for f in plain] == [("fit_stacked", "val_df")]


@pytest.mark.parametrize("finder", _FINDERS, ids=lambda f: f.__name__)
def test_an_unparsable_file_is_reported_not_skipped(tmp_path, finder):
    good = _write(tmp_path, "good.py", _DROPS)
    bad = _write(tmp_path, "bad.py", "def (:\n")
    with pytest.raises(UnparsedFilesError, match=r"bad\.py"):
        finder([good, bad], tmp_path)
    assert list(map(repr, finder([good, bad], tmp_path, allow_unparsed=True))) == list(map(repr, finder([good], tmp_path)))


def test_allow_unparsed_keeps_the_findings_of_the_files_that_parse(tmp_path):
    good = _write(tmp_path, "good.py", _DROPS)
    bad = _write(tmp_path, "bad.py", "def (:\n")
    assert [f.name for f in find_dropped_variant_params([good, bad], tmp_path, allow_unparsed=True)] == ["val_df"]


@pytest.mark.parametrize("finder", _FINDERS, ids=lambda f: f.__name__)
def test_an_empty_corpus_fails_the_floor(tmp_path, finder):
    with pytest.raises(EmptyScanError):
        finder([], tmp_path)
    assert finder([], tmp_path, min_files=0) == []
