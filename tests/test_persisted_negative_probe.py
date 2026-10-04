"""Tests for py_ci_shared.persisted_negative_probe."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.persisted_negative_probe import RULE, assert_persisted_negative_probe, find_persisted_negative_probe

BOM = b"\xef\xbb\xbf"

VIOLATION = (
    "import json\nimport cupy\n\n\ndef fingerprint(path):\n    try:\n        n = cupy.cuda.runtime.getDeviceCount()\n"
    "    except Exception:\n        path.write_text(json.dumps({'gpu': 'no-gpu'}))\n        n = 0\n    return n\n"
)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _find(root, **kw):
    return find_persisted_negative_probe(root, use_git=False, **kw)


def _lines(tmp_path: Path, source: str) -> list[int]:
    return [f.line for f in _find(_corpus(tmp_path, {"m.py": source.encode()}))]


def _handler(body: str, *, probe: str = "cupy.cuda.runtime.getDeviceCount()", exc: str = "Exception", head: str = "import cupy, json, pickle, yaml\n") -> str:
    """A function probing hardware with *body* as its except handler (handler line is 6 + the header lines)."""
    return f"{head}def f(path, cache):\n    try:\n        x = {probe}\n    except {exc}:\n        " + body.replace("\n", "\n        ") + "\n    return 1\n"


def test_reports_the_seeded_violation(tmp_path):
    found = _find(_corpus(tmp_path, {"m.py": VIOLATION.encode()}))
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 8, RULE)]
    assert ".write_text" in found[0].message and "'no-gpu'" in found[0].message and "line 9" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """The failure is logged and returned; only a positive probe is persisted."""
    src = (
        "import json, logging\nimport cupy\n\n\ndef f(path):\n    try:\n        n = cupy.cuda.runtime.getDeviceCount()\n"
        "    except Exception:\n        logging.warning('probe failed')\n        return 'no-gpu'\n    path.write_text(json.dumps({'n': n}))\n    return n\n"
    )
    assert _lines(tmp_path, src) == []


@pytest.mark.parametrize(
    "body,shape",
    [
        pytest.param("path.write_text('no-gpu')", ".write_text", id="write_text"),
        pytest.param("path.write_bytes(b'no_gpu')", ".write_bytes", id="write_bytes-needs-str-marker"),
        pytest.param("json.dump({'gpu': False}, open(path, 'w'))", "json.dump", id="json.dump-false"),
        pytest.param("pickle.dump(None, open(path, 'wb'))", "pickle.dump", id="pickle-none"),
        pytest.param("yaml.safe_dump({'gpu': 'unavailable'}, open(path, 'w'))", "yaml.safe_dump", id="yaml"),
        pytest.param("with open(path, 'w') as fh:\n    fh.write('cpu')", "open(..., 'w').write", id="open-w"),
        pytest.param("with open(path, mode='a') as fh:\n    fh.write('0 devices')", "open(..., 'w').write", id="open-mode-kw"),
        pytest.param("cache.set('hw', 'none')", ".set", id="cache.set"),
        pytest.param("cache.put('hw', {'gpus': 0, 'kind': 'CPU-only'})", ".put", id="cache.put"),
        pytest.param("self_store = cache\nself_store.save('hw', False)", ".save", id="store.save"),
    ],
)
def test_every_persisting_shape_with_a_negative_marker_is_reported(tmp_path, body, shape):
    found = _find(_corpus(tmp_path, {"m.py": _handler(body).encode()}))
    assert len(found) == 1 and shape in found[0].message, found


def test_a_marker_reaches_the_write_through_a_local_the_handler_bound(tmp_path):
    assert len(_find(_corpus(tmp_path, {"m.py": _handler("verdict = {'gpu': 'no-gpu'}\npath.write_text(str(verdict))").encode()}))) == 1


def test_a_write_after_the_try_of_a_name_the_handler_bound_negative_is_reported(tmp_path):
    src = (
        "import json\nimport cupy\n\n\ndef f(path):\n    try:\n        gpu = cupy.cuda.runtime.getDeviceCount()\n"
        "    except Exception:\n        gpu = 'no-gpu'\n    path.write_text(json.dumps({'gpu': gpu}))\n"
    )
    found = _find(_corpus(tmp_path, {"m.py": src.encode()}))
    assert [(f.line, "gpu = " in f.message) for f in found] == [(8, True)]


def test_a_later_write_of_an_unrelated_name_is_not_reported(tmp_path):
    src = (
        "import cupy\n\n\ndef f(path):\n    try:\n        gpu = cupy.cuda.runtime.getDeviceCount()\n"
        "    except Exception:\n        gpu = 'no-gpu'\n    path.write_text('hello')\n"
    )
    assert _lines(tmp_path, src) == []


@pytest.mark.parametrize(
    "body",
    [
        "path.write_text('error: busy')",
        "path.write_text(str(1))",
        "log.warning('no-gpu')",
        "raise",
        "n = 0",
        "x = {'gpu': 'no-gpu'}",
        "print('cpu')",
    ],
)
def test_a_handler_that_persists_no_negative_verdict_is_clean(tmp_path, body):
    assert _lines(tmp_path, _handler(body)) == []


@pytest.mark.parametrize("exc", ["ImportError", "ModuleNotFoundError", "(ImportError, ModuleNotFoundError)"])
def test_a_genuine_absence_is_not_a_transient_failure(tmp_path, exc):
    assert _lines(tmp_path, _handler("path.write_text('no-gpu')", exc=exc)) == []


@pytest.mark.parametrize("exc", ["", " Exception", "RuntimeError", "OSError", "(ImportError, RuntimeError)", "BaseException"])
def test_any_other_handler_is_broad_enough(tmp_path, exc):
    assert len(_find(_corpus(tmp_path, {"m.py": _handler("path.write_text('no-gpu')", exc=exc.strip()).encode()}))) == 1


@pytest.mark.parametrize(
    "probe",
    ["torch.cuda.device_count()", "pynvml.nvmlInit()", "numba.cuda.get_current_device()", "free_mem_info()", "detect_gpu()", "dev.mem_info", "nvml.read()"],
)
def test_hardware_probes_of_every_family_make_the_try_a_probe(tmp_path, probe):
    src = _handler("path.write_text('no-gpu')", probe=probe, head="import torch, pynvml, numba.cuda\n")
    assert len(_find(_corpus(tmp_path, {"m.py": src.encode()}))) == 1


def test_a_try_that_probes_nothing_is_not_a_probe(tmp_path):
    assert _lines(tmp_path, _handler("path.write_text('no-gpu')", probe="json.loads(path.read_text())")) == []


def test_an_alias_of_cupy_is_still_a_probe(tmp_path):
    src = _handler("path.write_text('no-gpu')", probe="xp.cuda.Device(0).mem_info", head="import cupy as xp\n")
    assert len(_find(_corpus(tmp_path, {"m.py": src.encode()}))) == 1


def test_an_import_of_cupy_under_a_broad_handler_is_a_probe(tmp_path):
    src = "def f(path):\n    try:\n        import cupy\n    except Exception:\n        path.write_text('no-gpu')\n"
    assert _lines(tmp_path, src) == [4]


def test_the_marker_on_the_except_line_or_the_write_suppresses_it(tmp_path):
    on_except = VIOLATION.replace("except Exception:", "except Exception:  # probe-ok: user-level opt-out, expiry in the reader")
    on_write = VIOLATION.replace("'no-gpu'}))", "'no-gpu'}))  # probe-ok: expires in an hour")
    assert _lines(tmp_path / "a", on_except) == []
    assert _lines(tmp_path / "b", on_write) == []
    assert _lines(tmp_path / "c", VIOLATION.replace("except Exception:", "except Exception:  # probe-fine")) == [8]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    a = _find(_corpus(tmp_path / "a", {"m.py": VIOLATION.encode()}))
    b = _find(_corpus(tmp_path / "b", {"m.py": BOM + VIOLATION.encode()}))
    assert [(f.line, f.message) for f in a] == [(f.line, f.message) for f in b] != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": VIOLATION.encode()})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert len(_find(root, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path)
    with pytest.raises(EmptyScanError):
        _find(_corpus(tmp_path, {"a.py": b"x = 1\n"}), min_files=2)


def test_the_assert_names_the_site_and_passes_a_clean_tree(tmp_path):
    with pytest.raises(AssertionError, match=r"m\.py:8: \[persisted-negative-probe\]"):
        assert_persisted_negative_probe(_corpus(tmp_path / "bad", {"m.py": VIOLATION.encode()}), use_git=False)
    assert_persisted_negative_probe(_corpus(tmp_path / "ok", {"m.py": b"x = 1\n"}), use_git=False)
