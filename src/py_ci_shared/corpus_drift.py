"""Per-gate finding counts over real consumer repos, and the drift between two nights of them.

Canary corpora are a few lines each; false positives that only real code triggers slip past them. This tool runs
every ``find_*`` of every registered ``gate``/``library`` module whose required parameters are corpus-shaped (a repo
root, a list of roots, the tracked Python files, the tests directory) over each repo and records the counts:

    python -m py_ci_shared.corpus_drift snapshot --repos-file repos.toml --output tonight.json
    python -m py_ci_shared.corpus_drift compare last-night.json tonight.json --output drift.md

``compare`` exits 1 when a finder's count JUMPS (grows by more than ``--pct`` (default 0.20) AND by more than
``--absolute`` (default 5)), goes BLIND (drops to zero from non-zero) or starts to ERROR where it counted before. A
missing previous snapshot is the first night: it reports that and exits 0. Finders that need inputs a corpus cannot
supply (live models, config maps, precomputed claims) are listed in :data:`NON_CORPUS` with the reason, and a test
fails when a new finder is neither bindable nor listed there.
"""

from __future__ import annotations

import argparse
import datetime
import importlib
import inspect
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from . import registry
from ._core import iter_files

__all__ = [
    "BINDINGS",
    "FAILING_KINDS",
    "NON_CORPUS",
    "Drift",
    "bind",
    "compare",
    "count_of",
    "finders",
    "main",
    "render_table",
    "snapshot",
    "unbound_finders",
]

SCHEMA = 1
FAILING_KINDS = ("jump", "blind", "errored")

# Finders (``module`` for all of its finders, or ``module.find_x``) a repo checkout cannot feed, with the reason.
NON_CORPUS: dict[str, str] = {
    "alembic_concurrently": "takes an Alembic versions directory; the corpus repos have no fixed migrations path",
    "arb_checks": "reads Flutter .arb catalogues keyed by locale, with a template locale the caller names",
    "audit_disposition_parity": "needs the repo's audit directory and verdict vocabulary",
    "audit_path_references": "needs the repo's own and foreign audit round directories",
    "baseline_hygiene": "reads one baseline file and its current violations",
    "changelog_promise_parity": "needs caller-supplied trigger patterns over a CHANGELOG text",
    "ci_test_dir_reachability": "needs the workflows directory and an intentionally-unreached list",
    "ci_workflow_gate": "takes one workflow file",
    "ci_workflow_paths": "needs the workflows directory",
    "ci_workflow_timeout_gate": "takes one workflow file",
    "config_getattr_default_parity": "needs live pydantic schema classes",
    "dataclass_case_completeness": "takes the dataclass files of one module family and a name pattern",
    "disposition_test_references": "needs the audit files of the repo",
    "docs_inventory_parity": "needs the docs files, pyproject path and bullet patterns of the repo",
    "edge_function_hygiene": "takes a Supabase functions directory",
    "effect_assertion_parity": "needs a production-to-test import map",
    "entry_points_resolvable": "takes a pyproject path and imports the entry points",
    "env_flag_parsing": "needs the repo's env-var prefixes",
    "gate_config_honesty": "needs parsed pre-commit and workflow commands",
    "gate_integrity": "needs the pre-commit config, workflows and declared narrowings",
    "git_dependency_pins": "takes a pyproject path",
    "guard_population": "runs shell guard scripts",
    "hook_hygiene": "needs the git hooks directory",
    "import_layering": "needs caller-supplied layer rules",
    "index_coverage": "compares SQL index definitions against a live catalogue",
    "llm_call_archive_gate": "needs provider classes, archive names and wrapping factories",
    "marker_runner_coverage": "needs a marker name and runner commands",
    "mutation_teeth": "runs mutants against tests; minutes per file, not a count",
    "optional_truthiness": "takes one file path, not a corpus",
    "phantom_code_references": "needs the declared-name set of the repo",
    "phantom_markdown_links": "takes Markdown files, not the Python corpus",
    "private_imports": "needs the package name and its source directory",
    "prose_numeric_claims": "takes caller-built claims or prose paths",
    "pydantic_field_bounds": "needs live pydantic model classes",
    "readme_env_var_parity.find_readme_documented_vars": "takes a README path, not a corpus",
    "repo_hygiene.find_missing_required_files": "needs the repo's list of required files",
    "repo_hygiene.find_unguarded_numeric_gates": "takes a workflows directory",
    "resource_release_paths": "takes one file and a release method",
    "source_text_claims": "takes one test file path, not a corpus",
    "spec_bound_doubles": "takes one file with its entry points and name hints",
    "sql_function_privileges": "takes a SQL migrations directory",
    "stale_comment_age": "ages comments with git blame; a shallow clone has no history",
    "test_partition_reachability": "needs runner text, tag files and playwright configs",
    "unresolved_imports": "needs a module index and the repo's resolvable prefixes",
    "version_tag_currency": "compares manifests against the git tags of another repo",
}


def _py_files(repo: Path) -> list[Path]:
    return iter_files(repo, ("*.py",), include_untracked=False)


def _tests_dir(repo: Path) -> Optional[Path]:
    d = repo / "tests"
    return d if d.is_dir() else None


# Required-parameter name -> how to build it from a repo root; None from a builder means "not applicable here".
BINDINGS: dict[str, Callable[[Path], Any]] = {
    "root": lambda r: r,
    "repo_root": lambda r: r,
    "src_root": lambda r: r,
    "package_root": lambda r: r,
    "roots": lambda r: [r],
    "files": _py_files,
    "test_files": lambda r: [p for p in _py_files(r) if "tests" in p.relative_to(r).parts] or None,
    "tests_root": _tests_dir,
    "tests_dir": _tests_dir,
}


def _required(fn: Callable[..., Any]) -> list[str]:
    params = inspect.signature(fn).parameters.values()
    return [p.name for p in params if p.default is p.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]


def finders() -> list[tuple[str, Callable[..., Any]]]:
    """``(module.find_x, fn)`` for every ``find_*`` defined in a registered gate or library module, in registry order."""
    out: list[tuple[str, Callable[..., Any]]] = []
    for spec in registry.GATES:
        if spec.kind not in ("gate", "library"):
            continue
        module = importlib.import_module(spec.module)
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("find_") and fn.__module__ == module.__name__:
                out.append((f"{spec.name}.{name}", fn))
    return out


def _listed(key: str) -> bool:
    return key in NON_CORPUS or key.split(".", 1)[0] in NON_CORPUS


def unbound_finders() -> list[str]:
    """Finders that are neither bindable from a repo root nor listed in :data:`NON_CORPUS`."""
    return [key for key, fn in finders() if not _listed(key) and any(n not in BINDINGS for n in _required(fn))]


def runnable_finders() -> list[tuple[str, Callable[..., Any]]]:
    return [(key, fn) for key, fn in finders() if not _listed(key) and all(n in BINDINGS for n in _required(fn))]


def bind(fn: Callable[..., Any], repo: Path) -> Optional[dict[str, Any]]:
    """Keyword arguments for *fn* over *repo*, or None when an input does not exist there (no tests directory)."""
    kwargs: dict[str, Any] = {}
    for name in _required(fn):
        value = BINDINGS[name](repo)
        if value is None:
            return None
        kwargs[name] = value
    if "allow_unparsed" in inspect.signature(fn).parameters:
        kwargs["allow_unparsed"] = True  # a real repo may hold one unparsable file; count what does parse
    return kwargs


def count_of(result: Any) -> int:
    """A finder's result as a count: a list/dict/set's length, the first element of a ``(findings, extra)`` pair."""
    if result is None:
        return 0
    if isinstance(result, bool):
        return int(result)
    if isinstance(result, int):
        return result
    if isinstance(result, tuple) and result and isinstance(result[0], (list, dict, set, tuple)):
        return len(result[0])
    return len(result)


def _commit(repo: Path) -> Optional[str]:
    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _scan_repo(repo: Path, run: Sequence[tuple[str, Callable[..., Any]]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    seconds: dict[str, float] = {}
    for key, fn in run:
        kwargs = bind(fn, repo)
        if kwargs is None:
            continue
        start = time.perf_counter()
        try:
            counts[key] = count_of(fn(**kwargs))
        except Exception as exc:  # a finder that raises on real code is a result to record, not a crash of the night
            errors[key] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}"
        seconds[key] = round(time.perf_counter() - start, 3)
    return {"commit": _commit(repo), "counts": counts, "errors": errors, "seconds": seconds}


def snapshot(repos: Sequence[tuple[str, Path]], *, run: Optional[Sequence[tuple[str, Callable[..., Any]]]] = None) -> dict[str, Any]:
    """The counts of every runnable finder (or of *run*) over each ``(name, root)`` repo."""
    todo = list(run) if run is not None else runnable_finders()
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return {"schema": SCHEMA, "created": stamp, "repos": {name: _scan_repo(root, todo) for name, root in repos}}


@dataclass(frozen=True)
class Drift:
    """One finder's change on one repo between two snapshots."""

    repo: str
    finder: str
    before: Optional[int]
    after: Optional[int]
    kind: str  # jump | blind | errored | new | gone | recovered
    detail: str = ""

    @property
    def failing(self) -> bool:
        return self.kind in FAILING_KINDS


def _pair(prev: dict[str, Any], cur: dict[str, Any], repo: str, key: str, pct: float, absolute: int) -> Optional[Drift]:
    before, after = prev["counts"].get(key), cur["counts"].get(key)
    was_err, now_err = prev["errors"].get(key), cur["errors"].get(key)
    if now_err is not None:
        return Drift(repo, key, before, None, "errored", now_err) if before is not None else None
    if after is None:
        return Drift(repo, key, before, None, "gone") if before is not None else None
    if before is None:
        return Drift(repo, key, None, after, "recovered" if was_err else "new", was_err or "")
    if before > 0 and after == 0:
        return Drift(repo, key, before, after, "blind", "dropped to zero: the finder may no longer reach the code")
    growth = after - before
    if growth > absolute and after > before * (1 + pct):
        return Drift(repo, key, before, after, "jump", f"+{growth} ({'new' if before == 0 else f'+{growth / before:.0%}'})")
    return None


def compare(prev: dict[str, Any], cur: dict[str, Any], *, pct: float = 0.20, absolute: int = 5) -> list[Drift]:
    """Every notable change from *prev* to *cur*; see :data:`FAILING_KINDS` for the ones that fail the night."""
    out: list[Drift] = []
    for repo, now in sorted(cur.get("repos", {}).items()):
        then = prev.get("repos", {}).get(repo)
        if then is None:
            continue
        keys = sorted(set(then["counts"]) | set(then["errors"]) | set(now["counts"]) | set(now["errors"]))
        for key in keys:
            drift = _pair(then, now, repo, key, pct, absolute)
            if drift is not None:
                out.append(drift)
    return out


def _n(value: Optional[int]) -> str:
    return "-" if value is None else str(value)


def render_table(drifts: Sequence[Drift], *, pct: float, absolute: int) -> str:
    """Markdown: the failing changes first, then the informational ones."""
    lines = ["# Corpus drift", "", f"Fails on: a jump of more than {pct:.0%} and more than {absolute} findings, a drop to zero, a new error.", ""]
    if not drifts:
        return "\n".join([*lines, "No change past the thresholds.", ""])
    lines += ["| verdict | repo | finder | before | after | detail |", "|---|---|---|---|---|---|"]
    for d in sorted(drifts, key=lambda d: (not d.failing, d.repo, d.finder)):
        verdict = f"**{d.kind.upper()}**" if d.failing else d.kind
        lines.append(f"| {verdict} | {d.repo} | `{d.finder}` | {_n(d.before)} | {_n(d.after)} | {d.detail.replace('|', '/')} |")
    return "\n".join([*lines, ""])


def _cmd_snapshot(args: argparse.Namespace) -> int:
    from .adoption_matrix import load_repo_list

    repos = load_repo_list(args.repos_file)
    if not repos:
        sys.stderr.write(f"corpus_drift: {args.repos_file} lists no repos\n")
        return 2
    data = snapshot(repos)
    Path(args.output).write_bytes((json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    for name, repo in data["repos"].items():
        sys.stdout.write(f"{name}: {len(repo['counts'])} finders counted, {len(repo['errors'])} errored, {sum(repo['counts'].values())} findings\n")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    previous = Path(args.previous)
    if not previous.is_file():
        text = "# Corpus drift\n\nNo previous snapshot: this run is the baseline for the next one.\n"
        drifts: list[Drift] = []
    else:
        prev = json.loads(previous.read_bytes().decode("utf-8"))
        cur = json.loads(Path(args.current).read_bytes().decode("utf-8"))
        drifts = compare(prev, cur, pct=args.pct, absolute=args.absolute)
        text = render_table(drifts, pct=args.pct, absolute=args.absolute)
    if args.output:
        Path(args.output).write_bytes(text.encode("utf-8"))
    sys.stdout.write(text)
    failing = [d for d in drifts if d.failing]
    if failing:
        sys.stderr.write(f"corpus_drift: {len(failing)} finder(s) jumped, went blind or started to error\n")
    return 1 if failing else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``snapshot`` writes tonight's counts; ``compare`` fails on a jump, a drop to zero or a new error."""
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.corpus_drift", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot", help="count every runnable finder over the repos of a repos file")
    p.add_argument("--repos-file", required=True, help="TOML repo list (`[[repo]]` with path and name)")
    p.add_argument("--output", required=True, help="the JSON snapshot to write")
    p = sub.add_parser("compare", help="compare two snapshots")
    p.add_argument("previous", help="the last snapshot (a missing file means this is the first run)")
    p.add_argument("current", help="tonight's snapshot")
    p.add_argument("--pct", type=float, default=0.20, help="relative growth that, with --absolute, is a jump (default 0.20)")
    p.add_argument("--absolute", type=int, default=5, help="absolute growth that, with --pct, is a jump (default 5)")
    p.add_argument("--output", help="also write the markdown table here")
    args = parser.parse_args(argv)
    return _cmd_snapshot(args) if args.command == "snapshot" else _cmd_compare(args)


if __name__ == "__main__":
    raise SystemExit(main())
