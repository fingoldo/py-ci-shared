"""Read-only report: which consumer baselines hold stale entries that a strict ratchet turns red (audit 42-F4, 42-F5).

``baseline_ratchet.Baseline.enforce`` and ``_core.BaselineOutcome.ok`` used to accept an accepted-but-no-longer-found entry
(and a count above what is still found); both now fail, so the first bump of a consumer's py-ci-shared pin onto that release
goes red wherever such an entry sits. This tool says where, before the bump.

For each consumer checkout it extracts ``HEAD`` with ``git archive`` into a temporary directory (the checkout is never
written, and uncommitted edits are not seen), runs the pytest files there that import ``py_ci_shared`` with this package on
``PYTHONPATH`` and a probe plugin loaded, and collects every enforcement that would newly fail for staleness alone:

* ``ratchet``: ``baseline_ratchet.Baseline.enforce`` returned 1 although nothing is new (it used to return 0).
* ``core``: a ``_core.Baseline.enforce`` outcome whose ``.ok`` is False only because of stale entries or surplus counts, read
  by a caller that tests ``.ok`` (``mutation_teeth``, ``unexecuted_function_bodies``). Gates ending in ``raise_for_pytest()``
  already failed on stale entries and are not listed.

A consumer whose tests cannot run here (missing dependencies, a timeout) is reported as such with the rows seen so far:
that is "not measured", never "clean". The mutation baselines (``mutation_teeth``) need the multi-hour mutation run and are
out of reach of this sweep; their files are counted so the gap is visible.

Usage::

    py-ci-shared tool stale_baseline_sweep ../pyutilz ../mlframe --output sweep.md
    python -m py_ci_shared.stale_baseline_sweep REPO... [--python PY] [--timeout SECONDS] [--fail-on-stale]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ._core.git import GitError, git_text, run_git

logger = logging.getLogger(__name__)

__all__ = ["OUT_ENV", "RepoResult", "StaleRow", "install_probes", "main", "pytest_configure", "render_markdown", "sweep_repo"]

OUT_ENV = "PY_CI_SHARED_STALE_SWEEP_OUT"
SRC_ROOT = Path(__file__).resolve().parents[1]
_KEYS_SHOWN = 5


@dataclass(frozen=True)
class StaleRow:
    """One baseline that would newly fail: *stale* entries (surplus counts included), *api* ``ratchet`` or ``core``."""

    api: str
    gate: str
    baseline: str
    stale: int
    keys: tuple[str, ...] = ()


@dataclass
class RepoResult:
    repo: str
    commit: str = ""
    test_files: int = 0
    pytest_summary: str = ""
    problem: str = ""
    mutation_baselines: int = 0
    rows: list[StaleRow] = field(default_factory=list)

    @property
    def stale_total(self) -> int:
        return sum(row.stale for row in self.rows)


# ---- the probes, loaded in the consumer's pytest process ----


def _append(out_path: str, record: dict[str, Any]) -> None:
    with open(out_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _probe_core(out_path: str) -> None:
    from ._core import baseline as core

    original_enforce = core.Baseline.enforce
    original_ok = core.BaselineOutcome.__dict__["ok"].fget

    def enforce(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        outcome = original_enforce(self, *args, **kwargs)
        outcome._sweep_meta = (self.gate, str(self.path))  # type: ignore[attr-defined]
        return outcome

    def ok(self):  # type: ignore[no-untyped-def]
        value = original_ok(self)
        meta = getattr(self, "_sweep_meta", None)
        if meta and self.stale and not self.blocking:
            _append(out_path, {"api": "core", "gate": meta[0], "baseline": meta[1], "stale": len(self.stale), "keys": sorted(set(self.stale))})
        return value

    core.Baseline.enforce = enforce  # type: ignore[method-assign]
    core.BaselineOutcome.ok = property(ok)  # type: ignore[assignment,method-assign]


def _probe_ratchet(out_path: str) -> None:
    from . import baseline_ratchet

    original = baseline_ratchet.Baseline.enforce

    def enforce(self, found, **kwargs):  # type: ignore[no-untyped-def]
        code = original(self, found, **kwargs)
        accepted = self.load()
        stale = sorted(key for key in accepted if key not in found)
        if code == 1 and stale and all(key in accepted for key in found):
            _append(out_path, {"api": "ratchet", "gate": self.name, "baseline": self.path, "stale": len(stale), "keys": stale})
        return code

    baseline_ratchet.Baseline.enforce = enforce  # type: ignore[method-assign]


def install_probes(out_path: str) -> None:
    """Wrap both ``Baseline.enforce`` implementations so a staleness-only failure is appended to *out_path* as one JSON line."""
    _probe_core(out_path)
    _probe_ratchet(out_path)


def pytest_configure(config: Any) -> None:
    """Pytest plugin hook (``-p py_ci_shared.stale_baseline_sweep``): install the probes when the output variable is set."""
    out_path = os.environ.get(OUT_ENV)
    if out_path:
        install_probes(out_path)


# ---- the sweep ----


def _snapshot(repo: Path, dest: Path) -> str:
    """Extract ``HEAD`` of *repo* into *dest* (git reads the object database only); return the short commit."""
    commit = git_text(run_git(repo, "rev-parse", "--short", "HEAD", check=True).stdout).strip()
    archive = dest / "snapshot.zip"
    run_git(repo, "archive", "--format=zip", "-o", str(archive), "HEAD", check=True, timeout=600)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(dest / "tree")
    archive.unlink()
    return commit


def _gate_test_files(tree: Path) -> list[str]:
    found = []
    for path in sorted(tree.rglob("test_*.py")):
        if any(part.startswith(".") or part in ("node_modules", "site-packages") for part in path.relative_to(tree).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("stale sweep: could not read %s (%s); it is not run", path, type(exc).__name__)
            continue
        if "py_ci_shared" in text and "mutation" not in path.name:
            found.append(path.relative_to(tree).as_posix())
    return found


def _pytest_env(tree: Path, out_path: Path) -> dict[str, str]:
    parts = [str(SRC_ROOT), str(tree), str(tree / "src"), os.environ.get("PYTHONPATH", "")]
    return dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in parts if p), PYTHONDONTWRITEBYTECODE="1", **{OUT_ENV: str(out_path)})


def _run_pytest(tree: Path, files: Sequence[str], out_path: Path, *, python: str, timeout: float) -> tuple[str, str]:
    """``(summary line, problem)``; *problem* is empty unless pytest did not run to a verdict."""
    argv = [python, "-m", "pytest", "-p", "py_ci_shared.stale_baseline_sweep", "-p", "no:cacheprovider", "-o", "addopts=", "-q", "--no-header", *files]
    try:
        proc = subprocess.run(argv, cwd=tree, env=_pytest_env(tree, out_path), capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return "", f"pytest timed out after {timeout:g}s; the rows are partial"
    except OSError as exc:
        return "", f"could not start {python}: {exc}"
    lines = [line for line in proc.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    summary = lines[-1] if lines else ""
    tail = " | ".join(proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:])
    problem = (
        ""
        if proc.returncode in (0, 1)
        else f"pytest exited {proc.returncode} ({summary or 'no output'}{'; stderr: ' + tail if tail else ''}); the rows may be partial"
    )
    return summary, problem


def _read_rows(out_path: Path) -> list[StaleRow]:
    if not out_path.is_file():
        return []
    seen: dict[tuple[str, str, str], StaleRow] = {}
    for line in out_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        row = StaleRow(record["api"], record["gate"], record["baseline"], int(record["stale"]), tuple(record["keys"]))
        seen[(row.api, row.gate, row.baseline)] = row
    return sorted(seen.values(), key=lambda r: (r.gate, r.baseline))


def _relative(row: StaleRow, tree: Path) -> StaleRow:
    try:
        shown = Path(row.baseline).resolve().relative_to(tree.resolve()).as_posix()
    except (ValueError, OSError):
        shown = row.baseline.replace("\\", "/")
    return StaleRow(row.api, row.gate, shown, row.stale, row.keys)


def _count_mutation_baselines(tree: Path) -> int:
    return sum(1 for path in tree.rglob("*.json") if "mutation" in path.name and "baseline" in path.as_posix())


def sweep_repo(repo: Path, *, python: str = sys.executable, timeout: float = 1800.0) -> RepoResult:
    """Sweep one consumer checkout; never writes to it."""
    result = RepoResult(repo=repo.name)
    with tempfile.TemporaryDirectory(prefix="stale-sweep-") as scratch:
        dest = Path(scratch)
        try:
            result.commit = _snapshot(repo, dest)
        except (GitError, OSError, zipfile.BadZipFile) as exc:
            result.problem = f"could not snapshot HEAD: {exc}"
            return result
        tree = dest / "tree"
        files = _gate_test_files(tree)
        result.test_files = len(files)
        result.mutation_baselines = _count_mutation_baselines(tree)
        if not files:
            result.problem = "no test file imports py_ci_shared"
            return result
        out_path = dest / "stale.jsonl"
        result.pytest_summary, result.problem = _run_pytest(tree, files, out_path, python=python, timeout=timeout)
        result.rows = [_relative(row, tree) for row in _read_rows(out_path)]
    return result


def render_markdown(results: Sequence[RepoResult]) -> str:
    lines = ["# Stale baseline sweep", "", "| repo | commit | test files | would be red | stale entries | note |", "|---|---|---|---|---|---|"]
    for r in results:
        note = r.problem or r.pytest_summary
        if r.mutation_baselines:
            note += f" ({r.mutation_baselines} mutation baseline file(s) not measurable here)"
        red = "yes" if r.rows else ("not measured" if r.problem else "no")
        lines.append(f"| {r.repo} | {r.commit} | {r.test_files} | {red} | {r.stale_total} | {note.strip()} |")
    for r in results:
        if not r.rows:
            continue
        lines += ["", f"## {r.repo}", "", "| gate | api | baseline | stale | first keys |", "|---|---|---|---|---|"]
        for row in r.rows:
            keys = "; ".join(row.keys[:_KEYS_SHOWN]) + (" ..." if len(row.keys) > _KEYS_SHOWN else "")
            lines.append(f"| {row.gate} | {row.api} | {row.baseline} | {row.stale} | {keys.replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="stale_baseline_sweep", description=__doc__.splitlines()[0])
    parser.add_argument("repos", nargs="+", type=Path, help="consumer checkouts (read-only: HEAD is extracted with git archive)")
    parser.add_argument("--python", default=sys.executable, help="interpreter that runs the consumers' pytest (default: this one)")
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds allowed to one consumer's pytest run")
    parser.add_argument("--output", type=Path, help="also write the markdown report here")
    parser.add_argument("--fail-on-stale", action="store_true", help="exit 1 when any consumer would go red")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    results = [sweep_repo(repo, python=args.python, timeout=args.timeout) for repo in args.repos]
    text = render_markdown(results)
    sys.stdout.write(text)
    if args.output:
        args.output.write_bytes(text.encode("utf-8"))
    return 1 if args.fail_on_stale and any(r.rows for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
