"""Tests for py_ci_shared.cuda_device_idioms (literal device ids, cupy small powers, direct axis-0 device sorts)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_device_idioms import (
    RULE_DEVICE_ID,
    RULE_DIRECT_SORT,
    RULE_SMALL_POWER,
    assert_cuda_device_idioms,
    find_cuda_device_idioms,
)

CANARY_ROOT = Path(__file__).parent / "canary" / "cuda_device_idioms"


def _canary(kind: str) -> Path:
    """A fresh directory holding the ``.canary`` fixtures of *kind* under their ``.py`` names (the scanner reads ``*.py`` only)."""
    dest = Path(tempfile.mkdtemp())
    for src in (CANARY_ROOT / kind).glob("*.canary"):
        (dest / src.name[: -len(".canary")]).write_bytes(src.read_bytes())
    return dest


def _find(root, **kw):
    return find_cuda_device_idioms(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _found(tmp_path: Path, body: str):
    text = "import cupy as cp\n\n\ndef run(x, vec, dev):\n" + "".join("    " + line + "\n" for line in body.splitlines())
    return _find(_corpus(tmp_path, {"m.py": text.encode()}))


def test_canary_violation_fires_every_rule():
    rules = {f.rule for f in _find(_canary("violation"))}
    assert rules == {RULE_DEVICE_ID, RULE_SMALL_POWER, RULE_DIRECT_SORT}


def test_canary_clean_is_silent():
    assert _find(_canary("clean")) == []


def test_canary_bom_still_parses_and_fires():
    # The BOM canary is the violation canary with a BOM prefix: it must fire the same rules.
    assert {f.rule for f in _find(_canary("bom"))} == {RULE_DEVICE_ID, RULE_SMALL_POWER, RULE_DIRECT_SORT}


def test_canary_unparsable_is_a_finding_not_a_pass():
    with pytest.raises(UnparsedFilesError):
        _find(_canary("unparsable"), min_files=0)


@pytest.mark.parametrize(
    "line",
    [
        "gpu_capability_summary(0)",
        "cp.cuda.Device(1)",
        "cp.cuda.runtime.getDeviceProperties(0)",
        "cp.cuda.runtime.setDevice(0)",
        "cuda.select_device(0)",
    ],
)
def test_literal_device_id_is_reported(tmp_path, line):
    assert [f.rule for f in _found(tmp_path, line)] == [RULE_DEVICE_ID]


def test_device_from_a_name_or_the_current_device_is_not_reported(tmp_path):
    body = "gpu_capability_summary(launch_device_id())\ncp.cuda.Device(dev).use()\ncp.cuda.get_current_device()"
    assert _found(tmp_path, body) == []


def test_device_marker_with_reason_suppresses(tmp_path):
    assert _found(tmp_path, "cp.cuda.Device(0).use()  # device-ok: single-GPU bench script") == []


def test_device_marker_without_reason_does_not_suppress(tmp_path):
    assert [f.rule for f in _found(tmp_path, "cp.cuda.Device(0).use()  # device-ok:")] == [RULE_DEVICE_ID]


@pytest.mark.parametrize("expr", ["cp.power(x, 2)", "cp.power(x, 3)", "cp.power(x, -1)", "cp.power(x, -2)", "xp.power(x, exponent=2)"])
def test_small_integer_power_is_reported(tmp_path, expr):
    assert [f.rule for f in _found(tmp_path, expr)] == [RULE_SMALL_POWER]


@pytest.mark.parametrize("expr", ["cp.power(x, 0.5)", "cp.power(x, dev)", "np.power(x, 2)", "x ** 2", "cp.power(x, 4)"])
def test_other_powers_are_not_reported(tmp_path, expr):
    assert _found(tmp_path, expr) == []


def test_small_power_marker_suppresses(tmp_path):
    assert _found(tmp_path, "cp.power(x, 2)  # power-ok: the exact rounding of the reference is the test") == []


@pytest.mark.parametrize("expr", ["cp.argsort(x, axis=0)", "cupy.sort(x, axis=0)", "xp.sort(x, axis=0)"])
def test_direct_axis0_device_sort_is_reported(tmp_path, expr):
    assert [f.rule for f in _found(tmp_path, expr)] == [RULE_DIRECT_SORT]


@pytest.mark.parametrize("expr", ["cp.sort(vec)", "cp.sort(x, axis=1)", "device_argsort(x, axis=0)", "np.sort(x, axis=0)"])
def test_sorts_that_are_not_direct_axis0_device_sorts_are_not_reported(tmp_path, expr):
    assert _found(tmp_path, expr) == []


def test_sort_marker_suppresses(tmp_path):
    assert _found(tmp_path, "cp.argsort(x, axis=0)  # sort-ok: 3 rows, below the wrapper threshold") == []


def test_wrapper_definition_inside_device_sort_module_is_exempt(tmp_path):
    text = "import cupy as cp\n\n\ndef device_argsort(a, axis):\n    return cp.argsort(a, axis=0)\n"
    assert _find(_corpus(tmp_path, {"m.py": text.encode()})) == []


def test_empty_corpus_is_an_error(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=5)


def test_assert_fails_with_the_rule_ids(tmp_path):
    with pytest.raises(AssertionError, match="cuda-cupy-small-power"):
        assert_cuda_device_idioms(_corpus(tmp_path, {"m.py": b"import cupy as cp\ny = cp.power(x, 2)\n"}), use_git=False)
