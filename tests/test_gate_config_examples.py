"""The `[tool.py_ci_shared]` examples in gate docstrings never enable a gate that also has a `[gates.X]` table (audit 37-F28)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import py_ci_shared
from py_ci_shared._core.config import ConfigError, load_config

_ENABLE = re.compile(r"enable\s*=\s*\[([^\]]*)\]")
_TABLE = re.compile(r"\[tool\.py_ci_shared\.gates\.([A-Za-z0-9_-]+)\]")


def test_a_gate_with_a_gates_table_is_not_also_in_enable_and_the_error_says_how_to_fix_it(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.py_ci_shared]\nenable = ["function_length"]\n\n[tool.py_ci_shared.gates.function_length]\nfiles = ["src/**/*.py"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"remove the name from `enable`"):
        load_config(tmp_path)


def test_no_docstring_example_enables_a_gate_and_also_gives_it_a_gates_table():
    package = Path(py_ci_shared.__file__).parent
    offenders = []
    for path in sorted(package.glob("*.py")):
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        enabled = {n.strip().strip("\"'") for m in _ENABLE.finditer(doc) for n in m.group(1).split(",") if n.strip()}
        tabled = set(_TABLE.findall(doc))
        both = sorted(enabled & tabled)
        if both:
            offenders.append(f"{path.name}: {both}")
    assert offenders == []
