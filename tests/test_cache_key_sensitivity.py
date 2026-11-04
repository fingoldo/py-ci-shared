"""Tests for py_ci_shared.cache_key_sensitivity."""

from __future__ import annotations

import pytest

from py_ci_shared.cache_key_sensitivity import assert_key_sensitive

BASE = {"X": (1, 2, 3), "y": (0, 1, 0), "verbose": 0}


def good_key(X, y, verbose):  # unused-ok: the key function must accept every BASE kwarg
    return hash((tuple(X), tuple(y)))


def ignores_y(X, y, verbose):  # unused-ok: the key function must accept every BASE kwarg
    return hash((tuple(X),))


def includes_verbose(X, y, verbose):
    return hash((tuple(X), tuple(y), verbose))


def test_a_key_that_hashes_the_content_and_ignores_verbosity_passes():
    assert_key_sensitive(good_key, BASE, sensitive={"X": (9, 9, 9), "y": (1, 1, 1)}, insensitive={"verbose": 5})


def test_a_key_that_ignores_the_target_fails_the_sensitive_check():
    with pytest.raises(AssertionError, match="'y' changes the result but not the key"):
        assert_key_sensitive(ignores_y, BASE, sensitive={"X": (9, 9, 9), "y": (1, 1, 1)}, insensitive={"verbose": 5})


def test_a_key_that_includes_verbosity_fails_the_insensitive_check():
    with pytest.raises(AssertionError, match="'verbose' must not affect the result but changes the key"):
        assert_key_sensitive(includes_verbose, BASE, sensitive={"X": (9, 9, 9), "y": (1, 1, 1)}, insensitive={"verbose": 5})


def test_an_id_based_key_is_caught_by_the_content_variation():
    def id_key(X, y, verbose):  # unused-ok: the key function must accept every BASE kwarg
        return id(X)

    with pytest.raises(AssertionError, match=r"'X' changes the result|not deterministic|'y' changes"):
        assert_key_sensitive(id_key, BASE, sensitive={"X": [1, 2, 3], "y": (1, 1, 1)}, insensitive={"verbose": 5})


def test_a_parameter_in_neither_table_is_a_population_failure():
    with pytest.raises(AssertionError, match=r"neither sensitive nor insensitive: \['verbose'\]"):
        assert_key_sensitive(good_key, BASE, sensitive={"X": (9, 9, 9), "y": (1, 1, 1)})


def test_a_nondeterministic_key_fails():
    counter = iter(range(100))

    def drifting(X, y, verbose):  # unused-ok: the key function must accept every BASE kwarg
        return next(counter)

    with pytest.raises(AssertionError, match="not deterministic"):
        assert_key_sensitive(drifting, BASE, sensitive={"X": (9,), "y": (1,)}, insensitive={"verbose": 1})


def test_declaring_a_name_both_ways_or_outside_base_is_a_usage_error():
    with pytest.raises(ValueError, match="both sensitive and insensitive"):
        assert_key_sensitive(good_key, BASE, sensitive={"X": (1,)}, insensitive={"X": (2,)})
    with pytest.raises(ValueError, match="not in base"):
        assert_key_sensitive(good_key, BASE, sensitive={"zzz": 1})


def test_var_keyword_and_self_are_not_reflected_over():
    class K:
        def key(self, X, **extra):
            return hash((tuple(X), tuple(sorted(extra.items()))))

    assert_key_sensitive(K().key, {"X": (1,), "n": 1}, sensitive={"X": (2,), "n": 2})
