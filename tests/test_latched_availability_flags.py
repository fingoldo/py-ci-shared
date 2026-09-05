"""The latched-availability-flag check must catch a memoised probe and leave per-call verdicts alone."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.latched_availability_flags import (
    assert_no_latched_availability_flags,
    find_latched_availability_flags,
)

NEWLINE = chr(10)


def _module(tmp_path: Path, lines, name: str = "m.py") -> Path:
    """Write one module and return the directory holding it."""
    (tmp_path / name).write_bytes((NEWLINE.join(lines) + NEWLINE).encode("utf-8"))
    return tmp_path


def _probe(declaration: str, handler: str = "except Exception as e:", assignment: str = "        _GPU_AVAILABLE = False"):
    """A memoised availability probe, parameterised on the parts that decide whether it latches."""
    return [
        declaration,
        "",
        "",
        "def is_gpu_available():",
        "    global _GPU_AVAILABLE",
        "    if _GPU_AVAILABLE is not None:",
        "        return _GPU_AVAILABLE",
        "    try:",
        "        import cupy",
        "        _GPU_AVAILABLE = True",
        "    " + handler,
        assignment,
        "    return _GPU_AVAILABLE",
    ]


def test_an_annotated_module_global_is_reported(tmp_path: Path):
    """The annotated form is what these flags actually use; missing it made an early draft report nothing."""
    root = _module(tmp_path, _probe("_GPU_AVAILABLE: bool | None = None"))
    found = find_latched_availability_flags([root])
    assert len(found) == 1, [str(f) for f in found]
    assert found[0].flag == "_GPU_AVAILABLE"
    assert found[0].function == "is_gpu_available"


def test_a_plain_module_global_is_reported(tmp_path: Path):
    """The unannotated declaration must be seen too."""
    root = _module(tmp_path, _probe("_GPU_AVAILABLE = None"))
    assert len(find_latched_availability_flags([root])) == 1


def test_a_bare_except_is_reported(tmp_path: Path):
    """`except:` is at least as broad as `except Exception:`."""
    root = _module(tmp_path, _probe("_GPU_AVAILABLE = None", handler="except:"))
    assert len(find_latched_availability_flags([root])) == 1


def test_a_tuple_handler_containing_exception_is_reported(tmp_path: Path):
    """Listing Exception alongside a narrow type is still broad."""
    root = _module(tmp_path, _probe("_GPU_AVAILABLE = None", handler="except (ValueError, Exception) as e:"))
    assert len(find_latched_availability_flags([root])) == 1


def test_a_narrow_handler_is_not_reported(tmp_path: Path):
    """ImportError is the one genuine absence, and caching it for the process is correct."""
    root = _module(tmp_path, _probe("_GPU_AVAILABLE = None", handler="except ImportError as e:"))
    assert find_latched_availability_flags([root]) == []


def test_a_local_verdict_is_not_reported(tmp_path: Path):
    """A per-call flag is harmless, and on a real repository this distinction removes most of the noise."""
    root = _module(
        tmp_path,
        [
            "def check():",
            "    ok = True",
            "    try:",
            "        import cupy",
            "    except Exception:",
            "        ok = False",
            "    return ok",
        ],
    )
    assert find_latched_availability_flags([root]) == []


def test_a_global_that_is_not_module_level_is_not_reported(tmp_path: Path):
    """`global` on a name never bound at module scope is not a memoised probe result."""
    root = _module(
        tmp_path,
        [
            "def check():",
            "    global _SOMETHING_AVAILABLE",
            "    try:",
            "        import cupy",
            "    except Exception:",
            "        _SOMETHING_AVAILABLE = False",
            "    return False",
        ],
    )
    assert find_latched_availability_flags([root]) == []


def test_a_name_that_does_not_read_as_a_verdict_is_not_reported(tmp_path: Path):
    """A log-once flag pinned in a handler is not an availability latch."""
    root = _module(
        tmp_path,
        [
            "_WARN_EMITTED = False",
            "",
            "",
            "def warn_once():",
            "    global _WARN_EMITTED",
            "    try:",
            "        import cupy",
            "    except Exception:",
            "        _WARN_EMITTED = True",
            "    return _WARN_EMITTED",
        ],
    )
    assert find_latched_availability_flags([root]) == []


def test_a_non_boolean_assignment_is_not_reported(tmp_path: Path):
    """Caching a value is a different question from pinning a yes/no verdict."""
    root = _module(
        tmp_path,
        [
            "_DEVICE_AVAILABLE = None",
            "",
            "",
            "def probe():",
            "    global _DEVICE_AVAILABLE",
            "    try:",
            "        import cupy",
            "    except Exception:",
            "        _DEVICE_AVAILABLE = 'unknown'",
            "    return _DEVICE_AVAILABLE",
        ],
    )
    assert find_latched_availability_flags([root]) == []


def test_the_assertion_names_the_flag_and_the_remedy(tmp_path: Path):
    """The message has to say what to do; the fix is not obvious from the symptom."""
    root = _module(tmp_path, _probe("_GPU_AVAILABLE: bool | None = None"))
    with pytest.raises(AssertionError) as exc:
        assert_no_latched_availability_flags([root])
    message = str(exc.value)
    assert "_GPU_AVAILABLE" in message
    assert "ImportError" in message


def test_an_allowed_flag_is_not_reported(tmp_path: Path):
    """A circuit breaker a caller re-arms is a deliberate latch, not a defect."""
    root = _module(tmp_path, _probe("_GPU_AVAILABLE: bool | None = None"))
    assert_no_latched_availability_flags([root], allow=["_GPU_AVAILABLE"])


def test_a_file_that_does_not_parse_is_skipped_rather_than_raising(tmp_path: Path):
    """A scanner that dies on one unparseable file takes the whole gate down with it."""
    _module(tmp_path, ["def f(:"], name="broken.py")
    _module(tmp_path, _probe("_GPU_AVAILABLE = None"), name="good.py")
    found = find_latched_availability_flags([tmp_path])
    assert [f.path.name for f in found] == ["good.py"]
