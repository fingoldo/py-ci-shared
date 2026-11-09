#!/usr/bin/env python
"""Command line drivers for per-module mutation testing.

Two subcommands, both run ``mutation_teeth.find_surviving_mutants`` against a repository checkout:

``lines``
    Re-check only the survivor lines of an earlier log, against a test set. The notes file maps
    ``<line>:<col>`` to a justification; a survivor with a note is recorded as an accepted equivalent in
    the survivors journal, a survivor without one is printed as UNCLASSIFIED and not recorded.

``sweep``
    Run a whole module (every line) with a checkpoint file, so an interrupted run resumes instead of
    starting over.

Usage::

    python -m py_ci_shared.mutation_rerun lines <module> <tests,comma,separated> <survivor_log> <notes.json> [min_line max_line]
    python -m py_ci_shared.mutation_rerun sweep <module> <tests,comma,separated> <checkpoint.json>

Paths are relative to ``--repo`` (default: the current directory). ``--survivors`` names the journal
(default ``tests/test_meta/mutation_survivors.json``). The checkpoint for ``lines`` is the notes file
with the ``.ckpt.jsonl`` suffix, so each notes file gets its own verdict cache.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from .mutation_teeth import find_surviving_mutants

__all__ = ["main", "survivor_lines", "survivor_location"]

DEFAULT_SURVIVORS = "tests/test_meta/mutation_survivors.json"
_LINE_TIMEOUT_S = 600
_SWEEP_TIMEOUT_S = 1800


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def survivor_lines(log_text: str, module_name: str, lo: int = 1, hi: int = 10**9) -> list[int]:
    """Return the sorted distinct line numbers of ``module_name`` survivors in ``log_text`` within ``[lo, hi]``."""
    pattern = re.compile(rf"^(?:SURVIVOR|mutation survivor: ).*{re.escape(module_name)}:(\d+):", re.M)
    return sorted({int(m.group(1)) for m in pattern.finditer(log_text) if lo <= int(m.group(1)) <= hi})


def survivor_location(text: str) -> str:
    """Return the ``line:col`` part of a survivor's text, for matching against the notes keys."""
    return text.split(":", 1)[1].split()[0] if ":" in text else ""


def _run_lines(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    survivors_path = repo / args.survivors
    notes_path = Path(args.notes)
    notes: dict[str, str] = json.loads(notes_path.read_text(encoding="utf-8"))
    module = Path(args.module)
    lines = survivor_lines(Path(args.log).read_text(encoding="utf-8"), module.name, args.min_line, args.max_line)
    _say(f"LINES {len(lines)}")
    if not lines:
        return 0
    run = find_surviving_mutants(
        module,
        [Path(t) for t in args.tests.split(",") if t],
        str(repo),
        lines=[range(n, n + 1) for n in lines],
        timeout=_LINE_TIMEOUT_S,
        use_cache=False,
        limit=600,
        checkpoint_path=notes_path.with_suffix(".ckpt.jsonl"),
    )
    _say(f"RUN mutants={run.mutants_run} killed={run.killed} survived={len(run.survivors)} truncated={run.truncated}")
    accepted: dict[str, str] = {}
    for m in run.survivors:
        text = str(m)
        _say(f"SURVIVOR {text}")
        loc = survivor_location(text)
        if loc in notes:
            accepted[m.key] = notes[loc]
        else:
            _say(f"UNCLASSIFIED {text}")
    existing: dict[str, str] = json.loads(survivors_path.read_text(encoding="utf-8"))
    existing.update(accepted)
    survivors_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _say(f"WROTE {len(accepted)} new of {len(run.survivors)} survivors; total entries {len(existing)}")
    return 0


def _run_sweep(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    module = Path(args.module)
    n_lines = sum(1 for _ in (repo / module).read_text(encoding="utf-8").splitlines())
    tests = [t for t in (Path(p) for p in args.tests.split(",") if p) if (repo / t).exists()]
    run = find_surviving_mutants(
        module,
        tests,
        str(repo),
        lines=[range(1, n_lines + 1)],
        timeout=_SWEEP_TIMEOUT_S,
        use_cache=False,
        limit=600,
        checkpoint_path=Path(args.checkpoint),
    )
    _say(f"RUN mutants={run.mutants_run} killed={run.killed} survived={len(run.survivors)} " f"truncated={run.truncated} candidates={run.candidates_total}")
    for s in run.survivors:
        _say(f"SURVIVOR {s}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.mutation_rerun", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repo", default=".", help="repository root; module and test paths are relative to it")
    sub = parser.add_subparsers(dest="command", required=True)

    lines = sub.add_parser("lines", help="re-check the survivor lines of an earlier log")
    lines.add_argument("module")
    lines.add_argument("tests")
    lines.add_argument("log", help="survivor log of an earlier run")
    lines.add_argument("notes", help="JSON mapping line:col to a justification")
    lines.add_argument("min_line", nargs="?", type=int, default=1)
    lines.add_argument("max_line", nargs="?", type=int, default=10**9)
    lines.add_argument("--survivors", default=DEFAULT_SURVIVORS, help="journal of accepted equivalents")
    lines.set_defaults(func=_run_lines)

    sweep = sub.add_parser("sweep", help="run every line of one module with a checkpoint")
    sweep.add_argument("module")
    sweep.add_argument("tests")
    sweep.add_argument("checkpoint")
    sweep.set_defaults(func=_run_sweep)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse the arguments and run the chosen subcommand; returns the process exit code."""
    args = _parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
