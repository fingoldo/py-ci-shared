"""Shared check: no production module builds a timestamp with `datetime.utcnow()`.

`datetime.utcnow()` is deprecated from Python 3.12 and scheduled for REMOVAL, and it returns a
NAIVE value whose UTC-ness is carried by convention rather than by the object. Both halves matter:
the deprecation is a future breakage, and the naivety is the frame living in a comment instead of in
the expression.

WHY THIS IS AN AST CHECK AND NOT A GREP. production_scrapers tried the substring form twice and
recorded both failures (wave-17, in `test_wave_17_regressions.py`):

* `"datetime.utcnow()" not in src` -- passes for a module that writes no timestamp at all, and says
  nothing about `datetime.now()`, which is naive LOCAL time and the worse hazard.
* widening it to `"utcnow()" not in src` "immediately failed on the module's own COMMENT, which
  explains the fix by naming the function it replaced. That is the substring trap in miniature."

So the package abandoned the sweep and kept one behavioural test for one module -- and on 2026-09-09
it still had two live calls, one of them emitting a DeprecationWarning on every full-suite run.
`dashboard` kept a sweep by skipping lines that `startswith("#")`, which is better and still reads
a docstring's prose as code.

An AST walk has neither problem: a comment is not in the tree, and a docstring is a string.

THE `.date()` CASE IS NOT AN EXCUSE. `utcnow().date()` and `now(UTC).date()` give the same value
today, which is exactly why a value-based test would not catch this one and would not catch the
next. What is wrong is that the frame is stated by convention.

Usage::

    from py_ci_shared.naive_utcnow import assert_no_naive_utcnow

    def test_no_production_code_uses_naive_utcnow():
        assert_no_naive_utcnow(PACKAGE_DIR, skip_dir_names={"tests", "probes"})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

_DEFAULT_SKIP_DIRS = ("__pycache__", ".git", ".venv", "build", "dist", ".mypy_cache", ".ruff_cache", ".pytest_cache")


def _is_utcnow_call(node: ast.AST) -> bool:
    """`<anything>.utcnow()` -- `datetime.utcnow()`, `dt.datetime.utcnow()`, `_dt.datetime.utcnow()`.

    Matched on the ATTRIBUTE rather than on the full dotted path, because the import spelling varies
    across these repos (`import datetime as dt`, `from datetime import datetime`, `import datetime as
    _dt`) and a check that enumerated the spellings would miss the next one.
    """
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "utcnow"


def find_naive_utcnow(root: Path, *, skip_dir_names: Iterable[str] = ()) -> list[str]:
    """`path:line: source` for every real `.utcnow()` CALL under *root*.

    Comments and docstrings cannot match: they are not calls.
    """
    skip = set(_DEFAULT_SKIP_DIRS) | set(skip_dir_names)
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if set(path.relative_to(root).parts) & skip:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):  # pragma: no cover - unreadable file
            continue
        rel = path.relative_to(root).as_posix()
        out.extend(f"{rel}:{getattr(node, chr(108) + chr(105) + chr(110) + chr(101) + chr(110) + chr(111), 0)}: {ast.unparse(node)}" for node in ast.walk(tree) if _is_utcnow_call(node))
    return out


def assert_no_naive_utcnow(root: Path, *, skip_dir_names: Iterable[str] = ()) -> None:
    """Fail if any module under *root* calls `.utcnow()`."""
    import pytest

    offenders = find_naive_utcnow(root, skip_dir_names=skip_dir_names)
    if offenders:
        pytest.fail(
            f"{len(offenders)} call(s) to `datetime.utcnow()`, which is deprecated (removal is "
            "scheduled) and returns a NAIVE value whose UTC-ness lives in a comment rather than in "
            "the expression. Use `datetime.now(datetime.UTC)`; when the output needs a `Z` suffix "
            "use `.strftime('%Y-%m-%dT%H:%M:%SZ')`, because an AWARE `isoformat()` already emits "
            "`+00:00` and appending `Z` produces `...+00:00Z`:\n  " + "\n  ".join(offenders)
        )
