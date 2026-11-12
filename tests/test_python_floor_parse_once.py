"""The floor checks parse each file once, not once per check (audit 48-F14)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "test_python_floor_compatibility.py"
_SPEC = importlib.util.spec_from_file_location("python_floor_compatibility", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
floor = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(floor)

SAMPLE = _PATH


def test_a_file_is_parsed_once_however_many_checks_read_it() -> None:
    """Negative control (48-F14): before the change each check parsed the file itself, with no cache to count."""
    floor._tree.cache_clear()
    first = floor._tree(SAMPLE)
    second = floor._tree(SAMPLE)
    assert first is second
    assert floor._tree.cache_info().misses == 1


def test_the_tree_form_gives_the_same_offenders_as_the_source_form() -> None:
    source = "import sys\ndef f():\n    import tomllib\n"
    assert floor.unguarded_in_tree(floor.ast.parse(source)) == floor.unguarded_tomllib_imports(source) == [3]
