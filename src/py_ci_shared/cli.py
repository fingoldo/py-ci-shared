"""``py-ci-shared``: one entry point for every gate and tool in the package.

Subcommands::

    py-ci-shared list [--markdown]            every registered module (the README catalogue with --markdown)
    py-ci-shared run <gate> [<gate> ...]      run gates enabled in [tool.py_ci_shared] of the repo
    py-ci-shared run-all                      run every enabled gate
    py-ci-shared refresh <gate>|all           rewrite the baselines of the named gates, then run them
    py-ci-shared config-path <name>           print the installed path of a shipped config (ruff-base, ruff-tests)
    py-ci-shared tool <module> [args ...]     run a module's own command line (``main``), e.g. ``tool worktree_hygiene``
    py-ci-shared version

Exit codes: 0 all passed, 1 a gate failed (or ran over budget with ``budget = "fail"``), 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from . import __version__, registry
from ._core.config import ConfigError, RepoConfig, find_repo_root, load_config
from ._core.runner import ERROR, FAILED, PASSED, SKIPPED, GateResult, budget_verdict, run_gate

CONFIG_NAMES = {"ruff-base": "ruff-base.toml", "ruff-tests": "ruff-tests.toml"}


def config_path(name: str) -> Path:
    """The installed path of a config shipped as package data (``ruff-base`` or ``ruff-tests``)."""
    filename = CONFIG_NAMES.get(name, name)
    if filename not in CONFIG_NAMES.values():
        raise KeyError(f"unknown config {name!r}; choose one of {sorted(CONFIG_NAMES)}")
    return Path(__file__).resolve().parent / "configs" / filename


def _print(text: str = "", *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(text + "\n")


def _load(repo: Optional[str]) -> RepoConfig:
    root = Path(repo).resolve() if repo else find_repo_root()
    config = load_config(root)
    if config is None:
        raise ConfigError(f"{root / 'pyproject.toml'} has no [tool.py_ci_shared] table; see README 'Configuring gates'")
    return config


def _report(results: Sequence[GateResult], mode: str) -> int:
    code = 0
    for result in results:
        mark = {PASSED: "PASS", FAILED: "FAIL", SKIPPED: "SKIP", ERROR: "ERROR"}[result.status]
        _print(f"{mark:5} {result.name} ({result.seconds:.2f}s)")
        if result.message:
            _print("      " + result.message.replace("\n", "\n      "))
        over = budget_verdict(result, mode)
        if over:
            _print(f"{'BUDGET':5} {over}", err=True)
            if mode == "fail":
                code = max(code, 1)
        if result.status == FAILED:
            code = max(code, 1)
        elif result.status == ERROR:
            code = 2
    counts = {s: sum(r.status == s for r in results) for s in (PASSED, FAILED, SKIPPED, ERROR)}
    _print(", ".join(f"{n} {s}" for s, n in counts.items()))
    return code


def _cmd_run(args: argparse.Namespace, *, everything: bool, refresh: bool = False) -> int:
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
    return _report([run_gate(config, r, refresh=refresh) for r in runs], config.budget)


def _cmd_list(args: argparse.Namespace) -> int:
    if args.markdown:
        _print(registry.render_catalogue())
        return 0
    for spec in registry.GATES:
        entry = spec.default_entry or ("main" if spec.cli else "-")
        _print(f"{spec.name:36} {spec.kind:8} {spec.since:8} {entry}")
    return 0


def _cmd_tool(args: argparse.Namespace) -> int:
    spec = registry.by_name(args.module)
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
    return result if isinstance(result, int) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="py-ci-shared", description="Run py-ci-shared gates and tools.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list", help="list every registered module")
    p.add_argument("--markdown", action="store_true", help="print the README gate catalogue")
    for name, helptext in (("run", "run the named enabled gates"), ("refresh", "rewrite the named gates' baselines ('all' for every one)")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("gates", nargs="+")
        p.add_argument("--repo", help="repo root (default: nearest pyproject.toml)")
    p = sub.add_parser("run-all", help="run every gate enabled in [tool.py_ci_shared]")
    p.add_argument("--repo", help="repo root (default: nearest pyproject.toml)")
    p = sub.add_parser("config-path", help="print the installed path of a shipped config")
    p.add_argument("name", choices=sorted(CONFIG_NAMES))
    p = sub.add_parser("tool", help="run a module's own command line")
    p.add_argument("module")
    p.add_argument("args", nargs=argparse.REMAINDER)
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
            _print(str(config_path(args.name)))
            return 0
        if args.command == "tool":
            return _cmd_tool(args)
        _print(__version__)
        return 0
    except (ConfigError, KeyError) as exc:
        _print(f"py-ci-shared: {exc.args[0] if exc.args else exc}", err=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
