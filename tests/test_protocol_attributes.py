"""Tests for py_ci_shared.protocol_attributes: the run-time protocol check on the dashboard's load_top_jobs shape."""

from __future__ import annotations

import functools

import pytest

import py_ci_shared.protocol_attributes as helper
from py_ci_shared.protocol_attributes import assert_satisfies_protocol, missing_protocol_attributes
from py_ci_shared.wrapper_protocol_parity import CACHED_LOADER


def _ttl_cached(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return fn(*a, **k)

    wrapper.cache_clear = lambda: None  # type: ignore[attr-defined]
    wrapper.refresh = lambda *a, **k: fn(*a, **k)  # type: ignore[attr-defined]
    return wrapper


def test_assert_satisfies_protocol_names_the_missing_attribute():
    inner = _ttl_cached(lambda: 1)

    def load_top_jobs():
        return inner()

    load_top_jobs.cache_clear = inner.cache_clear  # type: ignore[attr-defined]
    with pytest.raises(AssertionError, match=r"^warm target load_top_jobs lacks refresh;"):
        assert_satisfies_protocol(load_top_jobs, {"refresh", "cache_clear"}, what="warm target load_top_jobs")
    # against the inner: `has_value` is not required, the inner has none
    assert missing_protocol_attributes(load_top_jobs, CACHED_LOADER, inner=inner) == ["refresh"]
    load_top_jobs.refresh = functools.partial(load_top_jobs)  # type: ignore[attr-defined]
    assert_satisfies_protocol(load_top_jobs, CACHED_LOADER, inner=inner)
    load_top_jobs.refresh = None  # type: ignore[attr-defined]  # present but not callable is missing
    assert missing_protocol_attributes(load_top_jobs, {"refresh"}) == ["refresh"]


def test_teeth_the_runtime_helper_depends_on_its_check(monkeypatch):
    monkeypatch.setattr(helper, "missing_protocol_attributes", lambda obj, names, inner=None: [])
    assert_satisfies_protocol(object(), {"refresh"})  # with the check gutted the gap passes: the real one above fails it
