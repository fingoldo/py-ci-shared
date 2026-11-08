"""Helpers shared by the tests/test_mutation_teeth*.py files."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.mutation_teeth import generate_mutants


def _write(tmp_path: Path, text: str, name: str = "subject.py") -> Path:
    path = tmp_path / name
    open(path, "w", encoding="utf-8", newline="").write(text)
    return path


def _descriptions(path: Path) -> list[str]:
    mutants, _total, _sampled = generate_mutants(path)
    return [m.description for m in mutants]
