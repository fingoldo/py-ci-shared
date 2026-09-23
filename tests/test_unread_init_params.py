"""``unread_init_params`` finds the stored-and-ignored constructor parameter and leaves the used ones alone."""

from __future__ import annotations

import pytest

from py_ci_shared.unread_init_params import assert_no_unread_init_params, find_unread_init_params


def _write(tmp_path, name, source):
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def test_a_parameter_that_is_only_stored_is_reported(tmp_path):
    """The shipped shape: accepted, stored, never read."""
    p = _write(tmp_path, "est.py", "class E:\n    def __init__(self, used, dead):\n        self.used = used\n        self.dead = dead\n"
                                   "    def fit(self):\n        return self.used\n")
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
    assert find_unread_init_params(sorted(tmp_path.glob('*.py')), tmp_path) == []


def test_a_getattr_or_a_string_key_counts(tmp_path):
    """``getattr(self, 'p')`` and a name listed as a string (get_params, a state key) are reads."""
    _write(tmp_path, "est.py", "class E:\n    def __init__(self, p, q):\n        self.p = p\n        self.q = q\n")
    _write(tmp_path, "use.py", "KEYS = ['q']\n\n\ndef f(o):\n    return getattr(o, 'p')\n")
    assert find_unread_init_params(sorted(tmp_path.glob('*.py')), tmp_path) == []


def test_the_assert_names_the_parameter_and_honours_the_allowlist(tmp_path):
    """The failure names class and parameter; an allowlisted one passes, and a reason is required."""
    p = _write(tmp_path, "est.py", "class E:\n    def __init__(self, dead):\n        self.dead = dead\n")
    with pytest.raises(AssertionError, match="E.dead"):
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
