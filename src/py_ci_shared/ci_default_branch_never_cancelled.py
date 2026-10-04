"""A workflow that runs on a push to the default branch must never let a newer push cancel it.

``concurrency: {group: ..., cancel-in-progress: true}`` is the usual way to stop superseded pull-request runs from piling
up. On a workflow that ALSO fires on ``push`` to the default branch it does something else: every merge cancels the run of
the merge before it, so the default branch never gets a completed verdict. In the shipped case every one of the last 14
completed ``master`` runs of the main CI workflow had status ``cancelled``; the badge was grey, a red commit could not be
told from a green one, and nothing was ever reported to the coverage service because the upload job never started::

    on: {push: {branches: [master]}, pull_request: {}}
    concurrency:
      group: ci-${{ github.ref }}
      cancel-in-progress: true          # <- cancels the previous master run on every push

The safe shapes cancel pull requests only::

    cancel-in-progress: ${{ github.event_name == 'pull_request' }}
    cancel-in-progress: ${{ github.ref != 'refs/heads/master' }}

What is read: every ``.github/workflows/*.yml``/``*.yaml`` under the repo root (YAML parsed, never regex), at the workflow
level and in each job's own ``concurrency``. A workflow is in scope when its ``on:`` has ``push`` and the push filter lets
a default branch through (no ``branches``/``branches-ignore`` filter, a ``branches`` list matching ``main``/``master``
or a ``branches-ignore`` that does not exclude them; a tags-only push is not a branch push). ``cancel-in-progress`` is
reported when it is the literal ``true`` or an expression that is true, or that this reader cannot prove false, for a
push to a default branch. The expression is evaluated with the GitHub operators (``==``, ``!=``, ``&&``, ``||``, ``!``,
``startsWith``/``endsWith``/``contains``) against ``github.event_name == 'push'``, ``github.ref == 'refs/heads/<branch>'``
and an empty ``github.head_ref``; ``inputs``, ``vars``, ``env`` and ``matrix`` are unknown, so an expression built from
them is reported, with the reason. Three things make a ``true`` harmless and are not reported:

* the ``group`` is unique per run for a push (``${{ github.run_id }}``, ``${{ github.head_ref || github.run_id }}``,
  ``${{ github.sha }}``), so there is never a run in the same group to cancel;
* a job's own ``if:`` is false for a push (``if: github.event_name == 'pull_request'``);
* the line carries ``# cancel-ok: <reason>``.

``default_branches`` names the default branch (default ``main`` and ``master``; narrow it to the repo's own so that
``github.ref != 'refs/heads/master'`` is judged against ``master`` alone). ``exclude`` lists workflow file names (or path
fragments) to skip, for utility workflows that deliberately cancel.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from ._core import Baseline, EmptyScanError, Finding, SourceError, UnparsedFilesError, read_source
from .ci_install_covers_conftest import _LineLoader

__all__ = [
    "DEFAULT_BRANCHES",
    "RULE",
    "assert_ci_default_branch_never_cancelled",
    "find_ci_default_branch_never_cancelled",
]

RULE = "ci-default-branch-never-cancelled"
RULE_UNPARSED = "unparsed-file"
DEFAULT_BRANCHES = ("main", "master")
_OK_COMMENT = re.compile(r"#\s*cancel-ok:\s*\S")
_EXPR = re.compile(r"\$\{\{((?:(?!\}\}).)*)\}\}", re.DOTALL)


# --------------------------------------------------------------------------------------------------------------------
# a small evaluator for GitHub Actions expressions


class _Unknown:
    """A value this reader cannot know (``inputs.*``, ``vars.*``, an unlisted ``github.*`` field)."""

    def __repr__(self) -> str:
        return "unknown"


UNKNOWN = _Unknown()
_UNIQUE = "\x00unique-per-run\x00"  # what ``github.run_id`` and friends evaluate to: never equal for two runs

_TOKEN = re.compile(
    r"""\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>-?\d+(?:\.\d+)?)|(?P<op>==|!=|<=|>=|&&|\|\||[<>!(),])"""
    r"""|(?P<name>[A-Za-z_][\w-]*(?:(?:\.|\[\s*'[^']*'\s*\]|\[\s*\d+\s*\]|\.\*)[\w-]*)*))"""
)


def _truthy(value: Any) -> Any:
    if isinstance(value, _Unknown):
        return value
    return not (value is None or value is False or value == "" or (value == 0 and not isinstance(value, str)))


def _equal(a: Any, b: Any) -> Any:
    if isinstance(a, _Unknown) or isinstance(b, _Unknown):
        return UNKNOWN
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    if a is None and b in (None, "", 0, False):
        return True
    if b is None and a in ("", 0, False):
        return True
    return a == b


class _Eval:
    """Recursive-descent evaluation of one expression against a fixed context; any syntax it cannot read is unknown."""

    def __init__(self, text: str, context: dict[str, Any]) -> None:
        self.text = text
        self.context = context
        self.pos = 0
        self.tokens: list[tuple[str, str]] = []

    def run(self) -> Any:
        position = 0
        while position < len(self.text.rstrip()):
            match = _TOKEN.match(self.text, position)
            if not match or match.end() == position:
                return UNKNOWN
            position = match.end()
            kind = match.lastgroup or ""
            self.tokens.append((kind, match.group(kind)))
        try:
            value = self.disjunction()
        except (IndexError, ValueError):
            return UNKNOWN
        return value if self.pos == len(self.tokens) else UNKNOWN

    def peek(self) -> Optional[tuple[str, str]]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def disjunction(self) -> Any:
        value = self.conjunction()
        while self.peek() == ("op", "||"):
            self.take()
            right = self.conjunction()
            truth = _truthy(value)
            value = value if truth is True else right if truth is False else (right if _truthy(right) is True else UNKNOWN)
        return value

    def conjunction(self) -> Any:
        value = self.comparison()
        while self.peek() == ("op", "&&"):
            self.take()
            right = self.comparison()
            truth = _truthy(value)
            value = value if truth is False else right if truth is True else (False if _truthy(right) is False else UNKNOWN)
        return value

    def comparison(self) -> Any:
        left = self.unary()
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] in ("==", "!=", "<", ">", "<=", ">="):
            self.take()
            right = self.unary()
            if token[1] in ("==", "!="):
                eq = _equal(left, right)
                return eq if isinstance(eq, _Unknown) else (eq if token[1] == "==" else not eq)
            return UNKNOWN
        return left

    def unary(self) -> Any:
        if self.peek() == ("op", "!"):
            self.take()
            truth = _truthy(self.unary())
            return truth if isinstance(truth, _Unknown) else not truth
        return self.atom()

    def atom(self) -> Any:
        kind, value = self.take()
        if kind == "op" and value == "(":
            inner = self.disjunction()
            self.take()
            return inner
        if kind == "str":
            return value[1:-1].replace("''", "'")
        if kind == "num":
            return float(value)
        if kind != "name":
            raise ValueError(value)
        if self.peek() == ("op", "("):
            self.take()
            args: list[Any] = []
            while self.peek() != ("op", ")"):
                args.append(self.disjunction())
                if self.peek() == ("op", ","):
                    self.take()
            self.take()
            return _call(value.lower(), args)
        lowered = value.lower()
        if lowered in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[lowered]
        return _lookup(self.context, lowered)


def _call(name: str, args: list[Any]) -> Any:
    if name in ("startswith", "endswith", "contains") and len(args) == 2:
        a, b = args
        if isinstance(a, _Unknown) or isinstance(b, _Unknown):
            return UNKNOWN
        a_text, b_text = str(a if a is not None else "").lower(), str(b if b is not None else "").lower()
        return a_text.startswith(b_text) if name == "startswith" else a_text.endswith(b_text) if name == "endswith" else b_text in a_text
    if name in ("always", "success") and not args:
        return True
    return UNKNOWN


def _lookup(context: dict[str, Any], dotted: str) -> Any:
    if dotted in context:
        return context[dotted]
    parts = dotted.split(".")
    for end in range(len(parts) - 1, 0, -1):  # github.event.pull_request.number: the nearest known prefix decides
        prefix = ".".join(parts[:end])
        if prefix in context and context[prefix] is None:
            return None
    return UNKNOWN


def _push_context(branch: str) -> dict[str, Any]:
    """What a push to *branch* looks like to an expression."""
    return {
        "github.event_name": "push",
        "github.ref": f"refs/heads/{branch}",
        "github.ref_name": branch,
        "github.ref_type": "branch",
        "github.base_ref": "",
        "github.head_ref": "",
        "github.workflow": "workflow",
        "github.job": "job",
        "github.repository": "owner/repo",
        "github.event.repository.default_branch": branch,
        "github.event.pull_request": None,
        "github.event.number": None,
        "github.run_id": _UNIQUE,
        "github.run_number": _UNIQUE,
        "github.run_attempt": _UNIQUE,
        "github.sha": _UNIQUE,
        "github.event.head_commit.id": _UNIQUE,
        "github.event.after": _UNIQUE,
    }


def _evaluate(value: Any, context: dict[str, Any]) -> Any:
    """A workflow value as GitHub would resolve it: a ``${{ }}`` expression evaluated, a mixed string interpolated."""
    if not isinstance(value, str):
        return value
    whole = _EXPR.fullmatch(value.strip())
    if whole:
        return _Eval(whole.group(1), context).run()
    if "${{" not in value:
        return value
    unknown = False

    def part(match: re.Match[str]) -> str:
        nonlocal unknown
        result = _Eval(match.group(1), context).run()
        if isinstance(result, _Unknown):
            unknown = True
            return ""
        return "" if result is None else str(result)

    text = _EXPR.sub(part, value)
    return UNKNOWN if unknown else text


def _condition(value: Any, context: dict[str, Any]) -> Any:
    """A job's ``if:``: an expression with or without the ``${{ }}`` wrapper."""
    if not isinstance(value, str):
        return value
    whole = _EXPR.fullmatch(value.strip())
    return _Eval(whole.group(1) if whole else value, context).run()


# --------------------------------------------------------------------------------------------------------------------
# triggers


def _branch_pattern(pattern: str) -> "re.Pattern[str]":
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) if ch != "[" else "[")
        i += 1
    return re.compile("".join(out) + r"\Z")


def _listed(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else [str(value)] if isinstance(value, str) else []


def _on(doc: dict[Any, Any]) -> Any:
    """The ``on:`` value; YAML 1.1 reads the bare key ``on`` as the boolean True."""
    return doc.get("on", doc.get(True))


def _pushed_branches(doc: dict[Any, Any], candidates: Sequence[str]) -> list[str]:
    """The default branches a push can reach under the workflow's ``on.push`` filters (empty: no branch push)."""
    on = _on(doc)
    if isinstance(on, str):
        return list(candidates) if on == "push" else []
    if isinstance(on, list):
        return list(candidates) if "push" in [str(x) for x in on] else []
    if not isinstance(on, dict) or "push" not in on:
        return []
    push = on["push"]
    if not isinstance(push, dict):
        return list(candidates)
    include, ignore = _listed(push.get("branches")), _listed(push.get("branches-ignore"))
    if not include and not ignore:
        return [] if push.get("tags") or push.get("tags-ignore") else list(candidates)
    reached = []
    for name in candidates:
        verdict = not include
        for pattern in include:
            negate = pattern.startswith("!")
            if _branch_pattern(pattern.lstrip("!")).match(name):
                verdict = not negate
        if verdict and not any(_branch_pattern(p).match(name) for p in ignore):
            reached.append(name)
    return reached


def _mentioned(text: str, candidates: Sequence[str]) -> list[str]:
    """The candidate default branches the workflow names (``refs/heads/master``, ``ref_name == 'master'``, ``branches: [master]``)."""
    return [c for c in candidates if re.search(rf"(?<![\w.-]){re.escape(c)}(?![\w./-])", text)]


# --------------------------------------------------------------------------------------------------------------------
# the check


@dataclass
class _Site:
    where: str
    concurrency: Any
    lines: dict[str, int]
    job_if: Any = None


def _cancel_line(block: Any) -> int:
    lines = getattr(block, "key_lines", {})
    return int(lines.get("cancel-in-progress", getattr(block, "line", 1)))


def _check_site(site: _Site, branches: Sequence[str], text_lines: list[str], rel: str) -> Optional[Finding]:
    block = site.concurrency
    if not isinstance(block, dict) or "cancel-in-progress" not in block:
        return None
    line = _cancel_line(block)
    if 0 < line <= len(text_lines) and _OK_COMMENT.search(text_lines[line - 1]):
        return None
    raw = block["cancel-in-progress"]
    for branch in branches:
        context = _push_context(branch)
        if site.job_if is not None and _truthy(_condition(site.job_if, context)) is False:
            continue
        if isinstance(raw, str) and "${{" not in raw:
            cancel: Any = raw.strip().lower() == "true"
        else:
            cancel = _evaluate(raw, context)
        verdict = _truthy(cancel)
        if verdict is False:
            continue
        group = _evaluate(block.get("group", ""), context)
        if isinstance(group, str) and _UNIQUE in group:
            continue
        shown = raw if isinstance(raw, str) else str(raw).lower()
        reason = "is true" if verdict is True else "cannot be proven false (it reads a value this check does not know)"
        message = (
            f"{site.where} sets cancel-in-progress: {shown}, which {reason} for a push to {branch!r}: every merge cancels the run of "
            f"the one before it, so {branch} never gets a completed verdict. Cancel pull requests only: "
            f"cancel-in-progress: ${{{{ github.event_name == 'pull_request' }}}}"
        )
        return Finding(rel, line, RULE, message, key=f"{RULE}::{rel}::{site.where}")
    return None


def _workflow_files(root: Path) -> list[Path]:
    folder = root / ".github" / "workflows"
    return sorted(p for p in folder.glob("*") if p.suffix in (".yml", ".yaml") and p.is_file()) if folder.is_dir() else []


def _problem(rel: str, message: str, line: int = 1) -> Finding:
    return Finding(rel, line, RULE_UNPARSED, message)


def _load(path: Path, rel: str) -> tuple[Optional[tuple[str, dict[Any, Any]]], Optional[Finding]]:
    """``((text, document), None)`` for a readable workflow, ``(None, problem)`` otherwise."""
    try:
        text = read_source(path)
        doc = yaml.load(text, Loader=_LineLoader)  # nosec B506 -- a SafeLoader subclass that only adds line numbers
    except SourceError as exc:
        return None, _problem(rel, f"{exc.kind}: {exc.message}", exc.line or 1)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        first = str(exc).splitlines()[0] if str(exc) else ""
        return None, _problem(rel, f"unparsable: {type(exc).__name__}: {first}", (mark.line + 1) if mark is not None else 1)
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        return None, _problem(rel, "unparsable: no `jobs:` mapping")
    return (text, doc), None


def _workflow_findings(rel: str, text: str, doc: dict[Any, Any], default_branches: Sequence[str]) -> list[Finding]:
    reached = _pushed_branches(doc, tuple(default_branches))
    if not reached:
        return []
    named = _mentioned(text, default_branches)
    branches = [b for b in reached if b in named] or reached
    lines = text.splitlines()
    sites = [_Site(f"workflow {rel}", doc.get("concurrency"), {})]
    for job_id, job in doc["jobs"].items():
        if isinstance(job, dict):
            sites.append(_Site(f"workflow {rel}, job {job_id!r}", job.get("concurrency"), {}, job.get("if")))
    found = (_check_site(site, branches, lines, rel) for site in sites)
    return [f for f in found if f is not None]


def _scan(root: Path, default_branches: Sequence[str], exclude: Iterable[str]) -> tuple[list[Finding], list[Finding], int]:
    """``(findings, unreadable workflows, workflows read)``."""
    skipped = tuple(exclude)
    findings: list[Finding] = []
    problems: list[Finding] = []
    read = 0
    for path in _workflow_files(root):
        rel = path.relative_to(root).as_posix()
        if any(fragment in rel for fragment in skipped):
            continue
        loaded, problem = _load(path, rel)
        if problem is not None or loaded is None:
            problems.extend([problem] if problem is not None else [])
            continue
        read += 1
        findings.extend(_workflow_findings(rel, loaded[0], loaded[1], default_branches))
    return sorted(findings, key=lambda f: (f.path, f.line)), problems, read


def find_ci_default_branch_never_cancelled(
    root: Union[str, Path],
    *,
    default_branches: Sequence[str] = DEFAULT_BRANCHES,
    exclude: Iterable[str] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> list[Finding]:
    """Every workflow or job under ``<root>/.github/workflows`` that a push to the default branch can cancel, by path and line.

    ``min_files`` is the floor on workflow files read (``EmptyScanError`` below it); a workflow that is not valid YAML
    raises ``UnparsedFilesError`` unless *allow_unparsed*: a workflow this cannot read is not one that passes.
    """
    findings, problems, read = _scan(Path(root), default_branches, exclude)
    if problems and not allow_unparsed:
        raise UnparsedFilesError(
            f"{len(problems)} workflow file(s) could not be read or parsed, so they were not checked:\n  " + "\n  ".join(p.render() for p in problems)
        )
    if read < min_files:
        raise EmptyScanError(f"only {read} workflow file(s) read under {Path(root) / '.github' / 'workflows'}; expected at least {min_files}")
    return findings


def assert_ci_default_branch_never_cancelled(
    root: Union[str, Path],
    *,
    default_branches: Sequence[str] = DEFAULT_BRANCHES,
    exclude: Iterable[str] = (),
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Fail on any workflow a push to the default branch can cancel, or with *baseline_path* on any the baseline does not accept."""
    found = find_ci_default_branch_never_cancelled(root, default_branches=default_branches, exclude=exclude, min_files=min_files, allow_unparsed=allow_unparsed)
    guidance = "restrict cancel-in-progress to pull requests (`${{ github.event_name == 'pull_request' }}`) or mark the line `# cancel-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="ci_default_branch_never_cancelled", refresh_command="PY_CI_SHARED_REFRESH=ci_default_branch_never_cancelled")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} workflow(s) a push to the default branch can cancel; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
