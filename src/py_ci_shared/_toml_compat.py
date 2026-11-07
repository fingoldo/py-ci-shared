"""Old private name of :mod:`py_ci_shared.toml_compat`, kept so imports of ``py_ci_shared._toml_compat`` keep working."""

from __future__ import annotations

from .toml_compat import tomllib

__all__ = ["tomllib"]
