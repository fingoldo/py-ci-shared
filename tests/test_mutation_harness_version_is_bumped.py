"""The mutation harness must bump `HARNESS_VERSION` when its behaviour changes.

`HARNESS_VERSION` is what stops a verdict computed by an older harness from being replayed by a
newer one. It was, until now, a human promise -- and this package ships the gate that enforces
exactly this kind of promise without ever applying it to itself, which audit 21-F6 pointed out.

The gate is content-based rather than semantic, so it cannot know whether a change was cosmetic.
That is the right trade here: the cost of an unnecessary bump is one cache miss, and the cost of a
missed bump is a survivor list that silently describes a set of mutants nobody generates any more.

`_mutation_worker.py` is in the set because the worker decides what a mutant run MEANS -- its purge
and its exit-code plumbing are as much a part of a verdict as the operator table is.
"""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.content_hash_version_bump_gate import assert_version_bumped_with_content
from py_ci_shared.mutation_teeth import HARNESS_VERSION

_SRC = Path(__file__).resolve().parents[1] / "src" / "py_ci_shared"


def test_the_harness_version_tracks_the_harness():
    assert_version_bumped_with_content(
        files=[_SRC / "mutation_teeth.py", _SRC / "_mutation_worker.py"],
        version=HARNESS_VERSION,
        baseline_path=Path(__file__).parent / "_mutation_harness_version_baseline.json",
    )
