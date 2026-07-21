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

# Matches both step-level (`  - name: "..."`) and job-level (`  name: ...`)
# YAML name fields, with or without quotes.
_NAME_RE = re.compile(r"^\s*(?:-\s*)?name:\s*[\"']?(.*?)[\"']?\s*$")
# Only a literal `true` -- a templated value like `${{ inputs.advisory }}`
# (py-ci-shared's own reusable workflows use this to make it a per-consumer
# choice) is deliberately NOT flagged here, since there's no static answer
# to review until the consuming repo's own ci.yml resolves the expression.
_CONTINUE_ON_ERROR_RE = re.compile(r"^\s*continue-on-error:\s*true\s*$", re.IGNORECASE)


def find_continue_on_error_steps(workflow_path: Path) -> list[str]:
    """Return the name of every step/job in ``workflow_path`` carrying a
    literal ``continue-on-error: true``, associated via the nearest
    preceding ``name:`` (or ``- name:``) line above it -- the same
    sequential reading order a human reviewing the YAML would use.

    A ``continue-on-error: true`` line with no preceding ``name:`` at all
    (e.g. a job-level key with no ``name:`` field) is returned as
    ``"<unnamed step, line N>"`` so it can't silently vanish from review.
    """
    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    found: list[str] = []
    current_name: str | None = None
    for i, line in enumerate(lines, start=1):
        m = _NAME_RE.match(line)
        if m:
            current_name = m.group(1)
            continue
        if _CONTINUE_ON_ERROR_RE.match(line):
            found.append(current_name if current_name is not None else f"<unnamed step, line {i}>")
    return found


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
