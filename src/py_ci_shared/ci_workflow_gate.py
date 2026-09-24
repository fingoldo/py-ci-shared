"""Shared check: every ``continue-on-error: true`` in a CI workflow file is
either genuinely advisory (an explicitly reviewed, allowlisted step/job) or
a gate-defeating accident.

Generalizes three related 2026-07-21 audit findings (O-10/O-11/O-17): a
lint/security-gate CI job carried ``continue-on-error: true`` on a step
that was supposed to BLOCK on failure, silently turning a "blocking" gate
into a no-op -- the job still shows green in the PR check list even when
the gate step itself failed. Grep can find the literal string, but can't
tell a *deliberately* advisory step (an existing debt tracked as a
follow-up, e.g. realtime_applications's own ``ci.yml`` "Run mypy" step)
from an *accidental* one -- that judgment call is exactly what an
explicit, by-name allowlist should capture and force a human decision on,
the same pattern used throughout this package's other checks (e.g.
``_KNOWN_INDIRECT_READERS``-style whitelists in consuming repos).

Deliberately line-based/regex, not a YAML parser: matches this package's
established convention (``sql_lint.py``, ``code_audit``'s scanners are all
regex/text-based), and pulling in PyYAML for this one check isn't worth a
new dependency.

Usage (in a consuming repo's test suite)::

    from pathlib import Path
    from py_ci_shared.ci_workflow_gate import assert_continue_on_error_is_reviewed

    def test_ci_continue_on_error_steps_are_reviewed():
        assert_continue_on_error_is_reviewed(
            Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml",
            reviewed_advisory_steps={"Run ruff", "Run black --check", "Run bandit security scan", "Run mypy"},
        )

Deliberately dependency-light: ``pytest`` is imported lazily inside the
assert function, matching this package's other modules.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ._core import read_source

# Matches both step-level (`  - name: "..."`) and job-level (`  name: ...`)
# YAML name fields, with or without quotes. Kept for callers; the scan reads names by indentation.
_NAME_RE = re.compile(r"^\s*(?:-\s*)?name:\s*[\"']?(.*?)[\"']?\s*$")
# Only a literal `true` (bare or quoted, any case) -- a templated value like `${{ inputs.advisory }}`
# (py-ci-shared's own reusable workflows use this to make it a per-consumer
# choice) is deliberately NOT flagged here, since there's no static answer
# to review until the consuming repo's own ci.yml resolves the expression.
_CONTINUE_ON_ERROR_RE = re.compile(r"^\s*(?:-\s+)?continue-on-error:\s*([\"']?)true\1\s*$", re.IGNORECASE)
# A trailing `  # explanation` comment on either a `name:` or a
# `continue-on-error:` line (this repo's own workflows are full of them,
# e.g. llm_bench's ci.yml) would otherwise desync a captured step name from
# its allowlist entry, or make `_CONTINUE_ON_ERROR_RE`'s end-anchor fail to
# match `continue-on-error: true  # TODO` at all -- silently hiding a real
# gate-defeat from the scanner. Stripped from both lines before matching.
_TRAILING_COMMENT_RE = re.compile(r"\s+#.*$")
_KEY_VALUE_RE = re.compile(r"""^(?P<key>[A-Za-z0-9_.-]+|"[^"]*"|'[^']*')\s*:(?:\s+(?P<value>.*))?$""")
_JOBS_RE = re.compile(r"^jobs\s*:\s*$")


def _strip_comment(line: str) -> str:
    """*line* without a trailing YAML comment: a ``#`` at the start or after whitespace, outside quotes."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and (i == 0 or line[i - 1] in " \t:-[{,"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _key_value(body: str) -> "Optional[tuple[str, str]]":
    m = _KEY_VALUE_RE.match(body)
    if not m:
        return None
    return _unquote(m.group("key")), _unquote(m.group("value") or "")


def _is_true(value: str) -> bool:
    return value.strip().lower() == "true"


class _Scope:
    """A job or a step being read: its name and the line of a literal ``continue-on-error: true`` in it, if any."""

    def __init__(self, ident: Optional[str], key_col: Optional[int]) -> None:
        self.ident = ident
        self.key_col = key_col
        self.name: Optional[str] = None
        self.coe_line: Optional[int] = None


def find_continue_on_error_steps(workflow_path: Path) -> list[str]:
    """Return the name of every step/job in ``workflow_path`` carrying a
    literal ``continue-on-error: true`` (bare or quoted, also written on the
    step's ``-`` line), in file order.

    Names are read by YAML indentation, not by "the nearest ``name:`` above":
    a step's name is the ``name`` key of THAT step (wherever it sits in the
    step), a job-level ``continue-on-error`` belongs to that job's ``name``
    (or its id), and a ``name:`` nested under ``with:`` (an action input) is
    never a step name. A step without a name falls back to its own job's
    ``name``; with neither it is returned as ``"<unnamed step, line N>"`` so it
    can't silently vanish from review. A file without a top-level ``jobs:``
    (a composite action) is read for ``steps:`` lists anywhere.
    """
    lines = [_strip_comment(raw) for raw in read_source(workflow_path).splitlines()]
    has_jobs = any(_JOBS_RE.match(line) for line in lines)
    found: list[tuple[int, str]] = []
    in_jobs = not has_jobs
    job_indent: Optional[int] = None
    job: Optional[_Scope] = _Scope(None, None) if not has_jobs else None
    steps_parent: Optional[int] = None
    item_indent: Optional[int] = None
    step: Optional[_Scope] = None

    def close_step() -> None:
        nonlocal step
        if step is not None and step.coe_line is not None:
            job_name = job.name if job is not None else None
            found.append((step.coe_line, step.name or job_name or f"<unnamed step, line {step.coe_line}>"))
        step = None

    def close_job() -> None:
        nonlocal job, steps_parent, item_indent
        close_step()
        if job is not None and job.coe_line is not None:
            found.append((job.coe_line, job.name or job.ident or f"<unnamed job, line {job.coe_line}>"))
        job = None
        steps_parent = item_indent = None

    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()
        if has_jobs and indent == 0:
            close_job()
            in_jobs = bool(_JOBS_RE.match(content))
            job_indent = None
            continue
        if not in_jobs:
            continue
        if steps_parent is not None and indent <= steps_parent:
            close_step()
            steps_parent = item_indent = None
        if has_jobs:
            if job_indent is None:
                job_indent = indent
            if indent <= job_indent:
                close_job()
                kv = _key_value(content)
                job = _Scope(kv[0] if kv else content.rstrip(":"), None)
                continue
            if job is not None and job.key_col is None:
                job.key_col = indent
        is_item = content == "-" or content.startswith("- ")
        body = content[1:].lstrip() if is_item else content
        key_col = indent + (len(content) - len(body)) if is_item else indent
        if steps_parent is not None and is_item and (item_indent is None or indent == item_indent):
            close_step()
            item_indent = indent
            step = _Scope(None, key_col)
        kv = _key_value(body)
        if kv is None:
            continue
        key, value = kv
        if steps_parent is None and key == "steps" and not value:
            steps_parent = indent
            continue
        if step is not None and key_col == step.key_col:
            if key == "name":
                step.name = value
            elif key == "continue-on-error" and _is_true(value):
                step.coe_line = lineno
            continue
        if has_jobs and job is not None and not is_item and indent == job.key_col and steps_parent is None:
            if key == "name":
                job.name = value
            elif key == "continue-on-error" and _is_true(value):
                job.coe_line = lineno
    close_job()
    return [name for _, name in sorted(found)]


def assert_continue_on_error_is_reviewed(workflow_path: Path, reviewed_advisory_steps: set[str]) -> None:
    """Fail if any ``continue-on-error: true`` step/job in ``workflow_path``
    isn't in ``reviewed_advisory_steps``. Forces a human decision on every
    NEW occurrence -- either rename/extend the allowlist to confirm "yes,
    this one's deliberately advisory", or remove the ``continue-on-error``
    line -- rather than letting a copy-pasted or refactored gate step
    silently regain (or lose) blocking status unnoticed.
    """
    import pytest

    steps = find_continue_on_error_steps(workflow_path)
    unreviewed = [s for s in steps if s not in reviewed_advisory_steps]
    if unreviewed:
        pytest.fail(
            f"{len(unreviewed)} `continue-on-error: true` step(s)/job(s) in {workflow_path} "
            f"aren't in the reviewed-advisory allowlist -- confirm each is genuinely meant to "
            f"never block the gate, then add it to `reviewed_advisory_steps` by name (or remove "
            f"`continue-on-error` if it was accidental):\n  " + "\n  ".join(unreviewed)
        )
