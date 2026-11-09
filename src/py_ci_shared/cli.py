"""``py-ci-shared``: one entry point for every gate and tool in the package.

Subcommands::

    py-ci-shared list [--markdown]            every registered module (the README catalogue with --markdown)
    py-ci-shared run <gate> [<gate> ...]      run gates enabled in [tool.py_ci_shared] of the repo
                                              (``naive-utcnow`` and ``naive_utcnow`` name the same gate)
    py-ci-shared run-all                      run every enabled gate
    py-ci-shared refresh <gate>|all           rewrite the baselines of the named gates, then run them
                                              (shrink-only; --grow lets the refresh add entries)
    py-ci-shared config-path <name>           print the installed path of a shipped config (ruff-base, ruff-tests)
    py-ci-shared tool <module> [args ...]     run a module's own command line (``main``), e.g. ``tool worktree_hygiene``
    py-ci-shared new-gate <name> [--kind gate|library] [--summary TEXT] [--repo DIR]
                                              scaffold a gate in a py-ci-shared checkout (module, tests, canary, registry)
    py-ci-shared version

Exit codes: 0 all passed, 1 a gate failed (or ran over budget with ``budget = "fail"``), 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import logging
import os
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from . import __version__, registry
from ._core.config import ConfigError, RepoConfig, find_repo_root, load_config
from ._core.outcome import CLI_ENV_VAR
from ._core.runner import ERROR, FAILED, PASSED, REFRESHED_STATUS, SKIPPED, GateResult, budget_verdict, run_gate

__all__ = ["CONFIG_NAMES", "build_parser", "config_path", "main"]

logger = logging.getLogger(__name__)

CONFIG_NAMES = {"ruff-base": "ruff-base.toml", "ruff-tests": "ruff-tests.toml"}


def config_path(name: str) -> Path:
    """The installed path of a config shipped as package data (``ruff-base`` or ``ruff-tests``)."""
    filename = CONFIG_NAMES.get(name, name)
    if filename not in CONFIG_NAMES.values():
        raise KeyError(f"unknown config {name!r}; choose one of {sorted(CONFIG_NAMES)}")
    return Path(__file__).resolve().parent / "configs" / filename


def _print(text: str = "", *, err: bool = False) -> None:
    """Write one line, never failing on a character the console codepage lacks.

    A finding about ``src/数据.py`` on a cp1251 console raised ``UnicodeEncodeError`` here: the finding, the remaining
    gates and the summary were lost, and the exit code was the traceback's 1, even for an ERROR gate whose contract is 2
    (audit 2026-10-03 K-1). Unencodable characters are written as backslash escapes instead."""
    stream = sys.stderr if err else sys.stdout
    line = text + "\n"
    try:
        stream.write(line)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(line.encode(encoding, "backslashreplace").decode(encoding, "replace"))


def _load(repo: Optional[str]) -> RepoConfig:
    root = Path(repo).resolve() if repo else find_repo_root()
    config = load_config(root)
    if config is None:
        raise ConfigError(f"{root / 'pyproject.toml'} has no [tool.py_ci_shared] table; see README 'Configuring gates'")
    return config


def _announce_over_budget(overs: "list[str]", mode: str) -> None:
    """Make budget overruns impossible to miss under ``budget = "warn"`` without changing the exit code (35-F4).

    One summary line on stdout after the totals, a GitHub ``::warning::`` annotation per gate (it shows on the run page and the PR, not only in a log nobody opens) and, when the runner
    provides ``GITHUB_STEP_SUMMARY``, a block in the job summary. ``budget = "fail"`` already fails the run, so only the summary line is added there.
    """
    _print(f'BUDGET {len(overs)} gate(s) over budget (budget = "{mode}"); ' + ("exit code 1." if mode == "fail" else "exit code unchanged."))
    if mode == "fail":
        return
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for over in overs:
        if in_actions:
            _print(f"::warning title=py-ci-shared gate over budget::{over}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("### Gates over budget\n\n" + "".join(f"- {over}\n" for over in overs))
        except OSError as exc:  # a summary that cannot be written must not fail the run
            _print(f"BUDGET could not write the job summary: {exc}", err=True)


_MARKS = {PASSED: "PASS", FAILED: "FAIL", SKIPPED: "SKIP", ERROR: "ERROR", REFRESHED_STATUS: "REFRESH"}


def _indent(text: str) -> str:
    return textwrap.indent(text, "      ", lambda _line: True)


def _print_result(result: GateResult) -> None:
    _print(f"{_MARKS[result.status]:5} {result.name} ({result.seconds:.2f}s)")
    if result.message:
        _print(_indent(result.message))
    if result.note:
        _print(_indent("note: " + result.note))
        if os.environ.get("GITHUB_ACTIONS") == "true":
            _print(f"::warning title=py-ci-shared {result.name}::{result.note}")
    if result.status == SKIPPED and os.environ.get("GITHUB_ACTIONS") == "true":
        _print(f"::warning title=py-ci-shared gate skipped::{result.name} {result.message}")
    if result.status in (FAILED, ERROR) and os.environ.get("GITHUB_ACTIONS") == "true":
        first = (result.message or "").strip().splitlines()[:1]
        _print(f"::error title=py-ci-shared {result.name} {_MARKS[result.status]}::{first[0] if first else result.name}")


def _summary_line(results: Sequence[GateResult]) -> str:
    """``N passed, N failed, N skipped, N error``; ``, N refreshed`` and ``(N cached)`` are added only when there are some, so the greppable head is unchanged."""
    counts = {s: sum(r.status == s for r in results) for s in (PASSED, FAILED, SKIPPED, ERROR)}
    line = ", ".join(f"{n} {s}" for s, n in counts.items())
    refreshed = sum(r.status == REFRESHED_STATUS for r in results)
    cached = sum(r.cached for r in results)
    return line + (f", {refreshed} refreshed" if refreshed else "") + (f" ({cached} cached)" if cached else "")


def _report(results: Sequence[GateResult], mode: str) -> int:
    code = 0
    overs: "list[str]" = []
    for result in results:
        _print_result(result)
        over = budget_verdict(result, mode)
        if over:
            overs.append(over)
            _print(f"{'BUDGET':5} {over}")
            if mode == "fail":
                code = max(code, 1)
        if result.status == FAILED:
            code = max(code, 1)
        elif result.status == ERROR:
            code = 2
    _print(_summary_line(results))
    if overs:
        _announce_over_budget(overs, mode)
    return code


def _cmd_run(args: argparse.Namespace, *, everything: bool, refresh: bool = False) -> int:
    grow = bool(getattr(args, "grow", False))
    config = _load(args.repo)
    if everything:
        runs = list(config.gates)
    else:
        names = list(args.gates)
        if refresh and names == ["all"]:
            runs = list(config.gates)
        else:
            runs = [config.gate(n) for n in names]
    if not runs:
        raise ConfigError("no gates enabled in [tool.py_ci_shared]")
    previous = os.environ.get(CLI_ENV_VAR)
    os.environ[CLI_ENV_VAR] = "1"
    try:
        results = [run_gate(config, r, refresh=refresh, grow=grow) for r in runs]
    finally:
        if previous is None:
            os.environ.pop(CLI_ENV_VAR, None)
        else:
            os.environ[CLI_ENV_VAR] = previous
    return _report(results, config.budget)


def _cmd_list(args: argparse.Namespace) -> int:
    if args.markdown:
        _print(registry.render_catalogue())
        return 0
    for spec in registry.GATES:
        entry = spec.default_entry or ("main" if spec.cli else "-")
        _print(f"{spec.name:36} {spec.kind:8} {spec.since:8} {entry}")
    return 0


def _cmd_tool(args: argparse.Namespace) -> int:
    try:
        spec = registry.by_name(args.module)
    except KeyError as exc:  # the one lookup that is a usage error; a KeyError raised inside a gate must keep its traceback
        raise ConfigError(str(exc.args[0]) if exc.args else args.module) from exc
    if not spec.cli:
        raise ConfigError(f"{spec.name} has no command line; `py-ci-shared list` marks the ones that do")
    main = importlib.import_module(spec.module).main
    params = list(inspect.signature(main).parameters)
    if params and params[0] in ("argv", "args"):
        result = main(list(args.args))
    else:  # a main that reads sys.argv itself
        saved = sys.argv
        sys.argv = [spec.name, *args.args]
        try:
            result = main()
        finally:
            sys.argv = saved
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    raise ConfigError(
        f"{spec.name}.main returned {type(result).__name__}, not an exit code (None or int); fix the tool, a findings list read as success hides every finding"
    )


def _cmd_new_gate(args: argparse.Namespace) -> int:
    from ._scaffold import ScaffoldError, find_checkout, new_gate

    try:
        root = find_checkout(Path(args.repo) if args.repo else Path.cwd())
        done = new_gate(root, args.name, kind=args.kind, summary=args.summary or "")
    except ScaffoldError as exc:
        logger.debug("new-gate refused: %s: %s", type(exc).__name__, exc)
        _print(f"py-ci-shared new-gate: {exc}", err=True)
        return 2
    for rel in done.created:
        _print(f"created {rel}")
    for rel in done.updated:
        _print(f"updated {rel}")
    _print("Every generated test and the canary fail until written; fill them in, then run tests/test_package_inventory.py and tests/test_gate_teeth.py.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The argument parser of the ``py-ci-shared`` command with its list, run, run-all, refresh, config-path, tool, new-gate and version subcommands."""
    parser = argparse.ArgumentParser(prog="py-ci-shared", description="Run py-ci-shared gates and tools.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list", help="list every registered module")
    p.add_argument("--markdown", action="store_true", help="print the README gate catalogue")
    for name, helptext in (("run", "run the named enabled gates"), ("refresh", "rewrite the named gates' baselines ('all' for every one)")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("gates", nargs="+")
        p.add_argument("--repo", help="repo root (default: nearest pyproject.toml)")
        if name == "refresh":
            p.add_argument("--grow", action="store_true", help="let the refresh ADD entries and raise counts, not only drop stale ones")
    p = sub.add_parser("run-all", help="run every gate enabled in [tool.py_ci_shared]")
    p.add_argument("--repo", help="repo root (default: nearest pyproject.toml)")
    p = sub.add_parser("config-path", help="print the installed path of a shipped config")
    p.add_argument("name", choices=sorted(CONFIG_NAMES))
    p = sub.add_parser("tool", help="run a module's own command line")
    p.add_argument("module")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p = sub.add_parser("new-gate", help="scaffold a new gate or library in a py-ci-shared checkout")
    p.add_argument("name", help="module name, e.g. unsorted_dict_keys")
    p.add_argument("--kind", choices=("gate", "library"), default="gate", help="gate: find_* and assert_*; library: find_* only")
    p.add_argument("--summary", help="one line for the registry and the README catalogue")
    p.add_argument("--repo", help="the py-ci-shared checkout (default: nearest one above the current directory)")
    sub.add_parser("version", help="print the package version")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point of the ``py-ci-shared`` console script."""
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "run":
            return _cmd_run(args, everything=False)
        if args.command == "run-all":
            return _cmd_run(args, everything=True)
        if args.command == "refresh":
            return _cmd_run(args, everything=False, refresh=True)
        if args.command == "config-path":
            try:
                _print(str(config_path(args.name)))
            except KeyError as exc:
                raise ConfigError(str(exc.args[0]) if exc.args else args.name) from exc
            return 0
        if args.command == "tool":
            return _cmd_tool(args)
        if args.command == "new-gate":
            return _cmd_new_gate(args)
        _print(__version__)
        return 0
    except ConfigError as exc:
        logger.debug("usage or configuration error, exit 2: %s: %s", type(exc).__name__, exc)
        _print(f"py-ci-shared: {exc.args[0] if exc.args else exc}", err=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
