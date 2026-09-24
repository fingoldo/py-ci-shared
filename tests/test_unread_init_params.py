"""``unread_init_params`` finds the stored-and-ignored constructor parameter and leaves the used ones alone."""

from __future__ import annotations

import re

import pytest

from py_ci_shared.unread_init_params import assert_no_unread_init_params, find_unread_init_params


def _write(tmp_path, name, source):
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def test_a_parameter_that_is_only_stored_is_reported(tmp_path):
    """The shipped shape: accepted, stored, never read."""
    p = _write(
        tmp_path,
        "est.py",
        "class E:\n    def __init__(self, used, dead):\n        self.used = used\n        self.dead = dead\n" "    def fit(self):\n        return self.used\n",
    )
    found = find_unread_init_params([p], tmp_path)
    assert [(u.cls, u.param) for u in found] == [("E", "dead")]


def test_a_parameter_used_inside_init_is_not_reported(tmp_path):
    """Validation, combination or a pass-on inside ``__init__`` is a use."""
    p = _write(tmp_path, "est.py", "class E:\n    def __init__(self, n, k):\n        self.n = int(n)\n        self.size = n * k\n")
    assert find_unread_init_params([p], tmp_path) == []


def test_a_read_through_another_receiver_counts(tmp_path):
    """A wrapper reading ``est.p`` off the estimator it holds is what makes the parameter live."""
    _write(tmp_path, "est.py", "class E:\n    def __init__(self, p):\n        self.p = p\n")
    _write(tmp_path, "wrapper.py", "def run(est):\n    return est.p\n")
    assert find_unread_init_params(sorted(tmp_path.glob("*.py")), tmp_path) == []


def test_a_getattr_or_a_string_key_counts(tmp_path):
    """``getattr(self, 'p')`` and a name listed as a string (get_params, a state key) are reads."""
    _write(tmp_path, "est.py", "class E:\n    def __init__(self, p, q):\n        self.p = p\n        self.q = q\n")
    _write(tmp_path, "use.py", "KEYS = ['q']\n\n\ndef f(o):\n    return getattr(o, 'p')\n")
    assert find_unread_init_params(sorted(tmp_path.glob("*.py")), tmp_path) == []


def test_the_assert_names_the_parameter_and_honours_the_allowlist(tmp_path):
    """The failure names class and parameter; an allowlisted one passes, and a reason is required."""
    p = _write(tmp_path, "est.py", "class E:\n    def __init__(self, dead):\n        self.dead = dead\n")
    with pytest.raises(AssertionError, match=re.escape("E.dead")):
        assert_no_unread_init_params([p], tmp_path)
    assert_no_unread_init_params([p], tmp_path, allowlist={"dead": "sklearn meta-parameter, consumed by get_params"})
    with pytest.raises(AssertionError, match="need a reason"):
        assert_no_unread_init_params([p], tmp_path, allowlist={"dead": "  "})


def test_a_stale_allowlist_entry_and_an_empty_scan_are_rejected(tmp_path):
    """An allowlisted parameter that is read after all, and a scan that lost its files, both fail."""
    p = _write(tmp_path, "est.py", "class E:\n    def __init__(self, p):\n        self.p = p\n\n    def f(self):\n        return self.p\n")
    with pytest.raises(AssertionError, match="read after all"):
        assert_no_unread_init_params([p], tmp_path, allowlist={"p": "was dead once"})
    with pytest.raises(AssertionError, match="lost its subject"):
        assert_no_unread_init_params([], tmp_path, min_files=1)


class TestAuditRegressions:
    def test_an_annotated_store_is_a_plain_store(self, tmp_path):
        p = _write(tmp_path, "est.py", "class E:\n    def __init__(self, p: int):\n        self.p: int = p\n")
        assert [u.param for u in find_unread_init_params([p], tmp_path)] == ["p"]
        read = _write(tmp_path, "use.py", "def f(e):\n    return e.p\n")
        assert find_unread_init_params([p, read], tmp_path) == []

    def test_a_chained_store_is_a_plain_store_into_both_attributes(self, tmp_path):
        p = _write(tmp_path, "est.py", "class E:\n    def __init__(self, p):\n        self.a = self.b = p\n")
        assert [u.param for u in find_unread_init_params([p], tmp_path)] == ["p"]
        read_b = _write(tmp_path, "use.py", "def f(e):\n    return e.b\n")
        assert find_unread_init_params([p, read_b], tmp_path) == []

    def test_a_slots_or_all_entry_or_a_docstring_is_not_a_read(self, tmp_path):
        p = _write(
            tmp_path,
            "est.py",
            '"""Module doc mentions alpha."""\n__all__ = ["E", "alpha"]\n\nclass E:\n    """Stores alpha."""\n    __slots__ = ("alpha",)\n\n'
            "    def __init__(self, alpha):\n        self.alpha = alpha\n",
        )
        assert [u.param for u in find_unread_init_params([p], tmp_path)] == ["alpha"]
        keyed = _write(tmp_path, "keys.py", 'PARAMS = ["alpha"]\n')
        assert find_unread_init_params([p, keyed], tmp_path) == []

    def test_a_file_outside_the_repo_root_does_not_raise(self, tmp_path):
        outside = tmp_path / "site-packages" / "est.py"
        outside.parent.mkdir()
        outside.write_text("class E:\n    def __init__(self, p):\n        self.p = p\n", encoding="utf-8")
        (repo := tmp_path / "repo").mkdir()
        (unread,) = find_unread_init_params([outside], repo)
        assert unread.path.endswith("site-packages/est.py")

    def test_bom_and_unparsable_files(self, tmp_path):
        bom = tmp_path / "bom.py"
        bom.write_bytes(b"\xef\xbb\xbfclass E:\n    def __init__(self, p):\n        self.p = p\n")
        assert [u.param for u in find_unread_init_params([bom], tmp_path)] == ["p"]
        bad = _write(tmp_path, "bad.py", "def f(e):\n    return e.p\ndef (:\n")
        with pytest.raises(AssertionError, match=re.escape("bad.py")):
            find_unread_init_params([bom, bad], tmp_path)
        assert [u.param for u in find_unread_init_params([bom, bad], tmp_path, allow_unparsed=True)] == ["p"]
        with pytest.raises(AssertionError, match="could not be read or parsed"):
            assert_no_unread_init_params([bom, bad], tmp_path, allowlist={"p": "sklearn clone"})
