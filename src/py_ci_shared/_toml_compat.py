"""``tomllib`` on the interpreters that have it, ``tomli`` on the ones that do not.

``tomllib`` entered the standard library in 3.11, and this package supports 3.9 upward -- so five checkers
that read a ``pyproject.toml`` raised ``ModuleNotFoundError: No module named 'tomllib'`` on 3.9 and 3.10.
The failure is silent in the worst way: the gates that consume them are meant to FAIL a build, so on those
interpreters they failed for a reason that has nothing to do with what they check, and any consumer whose
matrix includes 3.9 or 3.10 saw a red shard instead of a real verdict.

``tomli`` is the same parser -- ``tomllib`` was adopted from it -- so the fallback reads identically rather
than approximating.
"""

from __future__ import annotations

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- exercised only on 3.9 / 3.10
    import tomli as tomllib  # type: ignore[no-redef]

__all__ = ["tomllib"]
