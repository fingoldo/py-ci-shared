"""Tests for py_ci_shared.cuda_optout_allocations, with a fake cupy so no device is needed."""

from __future__ import annotations

import os
import sys
import types

import pytest

from py_ci_shared.cuda_optout_allocations import NoDeviceBackendError, assert_no_device_allocations, assert_optout_env_honoured, count_device_allocations


@pytest.fixture
def fake_cupy(monkeypatch):
    state = {"alloc": lambda size: ("mem", size)}
    cuda = types.SimpleNamespace(get_allocator=lambda: state["alloc"], set_allocator=lambda f: state.__setitem__("alloc", f))
    monkeypatch.setitem(sys.modules, "cupy", types.SimpleNamespace(cuda=cuda))
    monkeypatch.setitem(sys.modules, "numba", None)  # `from numba import cuda` raises ImportError
    return state


def _probe_alloc(state):
    def _ensure_gpu_runtime_configured():
        state["alloc"](16)

    _ensure_gpu_runtime_configured()


def test_counts_allocations_and_restores_the_allocator(fake_cupy):
    original = fake_cupy["alloc"]
    with count_device_allocations() as calls:
        assert fake_cupy["alloc"](64) == ("mem", 64)
    assert calls == [64] and fake_cupy["alloc"] is original


def test_probe_frames_are_not_counted(fake_cupy):
    with count_device_allocations(probe_frames=("_ensure_gpu_runtime_configured",)) as calls:
        _probe_alloc(fake_cupy)
        fake_cupy["alloc"](8)
    assert calls == [8]


def test_allocator_restored_on_error(fake_cupy):
    original = fake_cupy["alloc"]
    with pytest.raises(KeyError):
        with count_device_allocations():
            raise KeyError("boom")
    assert fake_cupy["alloc"] is original


def test_assert_no_device_allocations_passes_and_fails(fake_cupy):
    assert assert_no_device_allocations(lambda: 7) == 7
    with pytest.raises(AssertionError, match=r"1 device allocation.*gate it"):
        assert_no_device_allocations(lambda: fake_cupy["alloc"](32), advice="gate it")
    assert_no_device_allocations(lambda: _probe_alloc(fake_cupy), probe_frames=("_ensure_gpu_runtime_configured",))


def test_no_backend_raises_a_named_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cupy", None)
    monkeypatch.setitem(sys.modules, "numba", None)
    with pytest.raises(NoDeviceBackendError):
        assert_no_device_allocations(lambda: None)


def _policy():
    if os.environ.get("MYPKG_DISABLE_GPU") == "1":
        return True
    return os.environ.get("CUDA_VISIBLE_DEVICES") == ""


def test_env_honoured_passes_for_a_correct_policy_and_restores_environ(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3")
    monkeypatch.delenv("MYPKG_DISABLE_GPU", raising=False)
    assert_optout_env_honoured(_policy, flag_var="MYPKG_DISABLE_GPU")
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "3" and "MYPKG_DISABLE_GPU" not in os.environ


@pytest.mark.parametrize(
    "policy,message",
    [
        (lambda: os.environ.get("MYPKG_DISABLE_GPU") == "1", "CUDA_VISIBLE_DEVICES"),
        (lambda: True, "NOT be read"),
        (lambda: os.environ.get("CUDA_VISIBLE_DEVICES") in ("", "0") or os.environ.get("MYPKG_DISABLE_GPU") == "1", "NOT be read"),
        (lambda: "CUDA_VISIBLE_DEVICES" not in os.environ or _policy(), "unset"),
        (lambda: False, "MYPKG_DISABLE_GPU=1"),
    ],
)
def test_env_honoured_catches_each_broken_policy(policy, message):
    with pytest.raises(AssertionError, match=message):
        assert_optout_env_honoured(policy, flag_var="MYPKG_DISABLE_GPU")
