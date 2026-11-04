"""A library function that turns a missing seed into an OS-entropy generator.

``if rng is None: rng = np.random.default_rng()`` makes the next fit pick different folds, features or tie-breaks
from the previous one; the bug cannot be reproduced, and seed sweeps still look reproducible because tests pass an
``rng`` and never reach the entropy branch. mlframe hand-fixed it in 589478c49, c4128eac5, afe506200 and ea5f723c3
and still had five more (a stability vote choosing CV folds, a tuning rule, swap noise, a CV shuffle seed drawn
from entropy).

What is reported: inside a function, a call of ``numpy.random.default_rng``, ``numpy.random.RandomState``,
``random.Random`` or ``numpy.random.seed``/``random.seed`` with no argument at all (or the literal ``None``).
Calls with any seed expression (``default_rng(seed)``, ``default_rng(SeedSequence(...))``) are clean, as are
module-level calls. The preferred form derives the generator from a repo-level default seed.

A reviewed entropy source is ``# rng-entropy-ok: <reason>`` on the call line or the line above it, an *allowed*
entry ``{"path::function": reason}``, or a function name matching one of the *allowed_functions* fnmatch patterns.
Files whose relative path contains one of the *exclude* fragments are skipped.

Usage in a consumer's meta test::

    from py_ci_shared.unseeded_rng_fallback import assert_unseeded_rng_fallback

    def test_unseeded_rng_fallback():
        assert_unseeded_rng_fallback("src", baseline_path="tests/baselines/unseeded_rng_fallback.json")
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from ._core import walk as shared_walk

__all__ = ["DEFAULT_CONSTRUCTORS", "RULE", "SUPPRESSION", "assert_unseeded_rng_fallback", "find_unseeded_rng_fallback"]

RULE = "unseeded-rng-fallback"
SUPPRESSION = "# rng-entropy-ok:"

DEFAULT_CONSTRUCTORS = frozenset(
    {
        "numpy.random.default_rng",
        "numpy.random.RandomState",
        "numpy.random.seed",
        "random.Random",
        "random.seed",
    }
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _entropy_call(call: ast.Call) -> bool:
    """No argument at all, or a lone literal ``None`` (positional or ``seed=None``)."""
    if any(isinstance(a, ast.Starred) for a in call.args) or any(kw.arg is None for kw in call.keywords):
        return False
    args: list[ast.expr] = list(call.args) + [kw.value for kw in call.keywords]
    if not args:
        return True
    return len(args) == 1 and isinstance(args[0], ast.Constant) and args[0].value is None


def _marked(lines: list[str], line: int) -> bool:
    return any(1 <= n <= len(lines) and SUPPRESSION in lines[n - 1] for n in (line, line - 1))


def _findings_in(
    tree: ast.Module,
    rel: str,
    lines: list[str],
    constructors: Collection[str],
    allowed: Mapping[str, str],
    allowed_functions: Collection[str],
) -> list[Finding]:
    aliases = ImportAliases.from_tree(tree)
    out: list[Finding] = []
    seen: set[int] = set()
    for func in (n for n in shared_walk(tree) if isinstance(n, _FUNCTIONS)):
        if f"{rel}::{func.name}" in allowed or any(fnmatchcase(func.name, p) for p in allowed_functions):
            continue
        for call in (n for n in shared_walk(func) if isinstance(n, ast.Call)):
            qualified = aliases.qualified_name(call)
            if qualified not in constructors or not _entropy_call(call) or id(call) in seen:
                continue
            seen.add(id(call))
            if _marked(lines, call.lineno):
                continue
            out.append(
                Finding(
                    rel,
                    call.lineno,
                    RULE,
                    f"'{func.name}' calls {qualified}() with no seed: every run draws OS entropy; derive it from the caller's seed or a repo default seed",
                )
            )
    return out


def find_unseeded_rng_fallback(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    constructors: Collection[str] = DEFAULT_CONSTRUCTORS,
    allowed: Optional[Mapping[str, str]] = None,
    allowed_functions: Collection[str] = (),
    exclude: Iterable[str] = (),
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    skipped = tuple(exclude)
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        if any(fragment in parsed.rel for fragment in skipped):
            continue
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines(), constructors, allowed or {}, allowed_functions))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_unseeded_rng_fallback(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    constructors: Collection[str] = DEFAULT_CONSTRUCTORS,
    allowed: Optional[Mapping[str, str]] = None,
    allowed_functions: Collection[str] = (),
    exclude: Iterable[str] = (),
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_unseeded_rng_fallback(
        root,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
        constructors=constructors,
        allowed=allowed,
        allowed_functions=allowed_functions,
        exclude=exclude,
    )
    guidance = "derive the generator from the caller's seed or a repo default seed, or `# rng-entropy-ok: <reason>` where entropy is the point"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="unseeded_rng_fallback", refresh_command="PY_CI_SHARED_REFRESH=unseeded_rng_fallback")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} unseeded-rng-fallback finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
