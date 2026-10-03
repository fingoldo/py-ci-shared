"""Consumer workflows: no moving ``*-latest`` runner label, and pinned actions have an update channel.

``ubuntu-latest`` moves when GitHub re-points it (22.04 to 24.04 changed the default Python, compilers and system
libraries under every job at once, on a day nobody chose). py-ci-shared's own workflows pin ``ubuntu-24.04``; the
consumers held 82 moving labels in 2026-10. A ``uses: owner/action@<sha>`` pin never moves either, which is the point,
but without a Dependabot (or Renovate) ``github-actions`` entry it also never moves when the action's Node runtime is
retired. Checking each action's ``runs.using`` would need a network fetch per action; the update channel fixes the
cause instead.

Findings (one line each, regex over the YAML text, comments ignored):

* ``moving-runner-label``: ``ubuntu-latest``/``windows-latest``/``macos-latest`` anywhere in a workflow (``runs-on``
  or a matrix value), unless the line carries ``# moving-label-ok: <reason>``;
* ``no-actions-update-channel``: the repo pins ``uses:`` to a SHA or tag and has no ``github-actions`` ecosystem in
  ``.github/dependabot.yml`` (or a Renovate config).

Autofix (``python -m py_ci_shared.workflow_runner_labels --fix <repo>``): rewrites each moving label to
:data:`PINNED_LABELS` (what the ``-latest`` labels pointed at when this was written; pass ``--label ubuntu=ubuntu-22.04``
to choose) and writes or extends ``.github/dependabot.yml`` with a weekly ``github-actions`` entry. Review the diff:
a job that relied on what ``-latest`` happened to be may need its own label.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import CoreError, Finding, read_source

__all__ = [
    "MARKER",
    "PINNED_LABELS",
    "assert_workflow_runner_labels_pinned",
    "find_workflow_runner_label_problems",
    "fix_workflow_runner_labels",
    "main",
]

MARKER = "moving-label-ok"
#: What each moving label resolved to on GitHub-hosted runners in 2026-10.
PINNED_LABELS: dict[str, str] = {"ubuntu": "ubuntu-24.04", "windows": "windows-2025", "macos": "macos-15"}
_LATEST = re.compile(r"\b(ubuntu|windows|macos)-latest\b")
_USES = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^\s'\"#]+)@([^\s'\"#]+)")
_DEPENDABOT = (".github/dependabot.yml", ".github/dependabot.yaml")
_RENOVATE = ("renovate.json", "renovate.json5", ".github/renovate.json", ".github/renovate.json5", ".renovaterc", ".renovaterc.json")
_DEPENDABOT_ENTRY = """  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
"""

PathLike = Union[str, Path]


def _workflows(root: Path) -> list[Path]:
    wf = root / ".github" / "workflows"
    return sorted(p for p in wf.glob("*") if p.suffix in (".yml", ".yaml") and p.is_file()) if wf.is_dir() else []


def _code(line: str) -> str:
    """The line without its YAML comment (a ``#`` at the start or after whitespace; not inside a quoted string)."""
    quote = None
    for i, ch in enumerate(line):
        if ch in "'\"":
            quote = None if quote == ch else (quote or ch)
        elif ch == "#" and quote is None and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def _has_update_channel(root: Path) -> bool:
    for rel in _DEPENDABOT:
        p = root / rel
        if p.is_file() and re.search(r"package-ecosystem:\s*['\"]?github-actions", read_source(p)):
            return True
    return any((root / rel).is_file() for rel in _RENOVATE)


def find_workflow_runner_label_problems(repo_root: PathLike, *, min_files: int = 0, allow_unparsed: bool = False) -> list[Finding]:
    """Every moving runner label, plus one repo finding when pinned actions have no update channel.

    Raises ``CoreError`` with fewer than *min_files* workflow files (0, the finder's default, disables the floor; the
    assert defaults to 1). Workflows are read as text,
    so *allow_unparsed* only lets an undecodable file be skipped instead of raising."""
    root = Path(repo_root)
    files = _workflows(root)
    if len(files) < min_files:
        raise CoreError(f"workflow_runner_labels: {len(files)} workflow file(s) under {root / '.github/workflows'}; expected at least {min_files}")
    out: list[Finding] = []
    pinned = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            text = read_source(path)
        except CoreError:
            if allow_unparsed:
                continue
            raise
        for no, line in enumerate(text.splitlines(), start=1):
            code = _code(line)
            if _USES.match(code) and not code.split("uses:", 1)[1].strip().strip("'\"").startswith("./"):
                pinned += 1
            if MARKER in line[len(code) :]:
                continue
            out.extend(
                Finding(rel, no, "moving-runner-label", f"`{m.group(0)}` moves when GitHub re-points it; pin `{PINNED_LABELS[m.group(1)]}`")
                for m in _LATEST.finditer(code)
            )
    if pinned and not _has_update_channel(root):
        out.append(
            Finding(
                ".github/dependabot.yml",
                1,
                "no-actions-update-channel",
                f"{pinned} pinned `uses:` and no github-actions update channel: a pin never moves off a retired action runtime",
            )
        )
    return out


def assert_workflow_runner_labels_pinned(repo_root: PathLike, *, min_files: int = 1, allow_unparsed: bool = False) -> None:
    found = find_workflow_runner_label_problems(repo_root, min_files=min_files, allow_unparsed=allow_unparsed)
    if found:
        raise AssertionError(
            f"{len(found)} workflow runner/update-channel problem(s); `python -m py_ci_shared.workflow_runner_labels --fix .` "
            "rewrites them:\n  " + "\n  ".join(f.render() for f in found)
        )


def _write_like(path: Path, text: str) -> None:
    """Write *text* keeping the file's CRLF line endings when it had them."""
    crlf = path.is_file() and b"\r\n" in path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def fix_workflow_runner_labels(repo_root: PathLike, *, labels: Optional[Mapping[str, str]] = None) -> list[str]:
    """Rewrite moving labels and add the github-actions update entry; the files changed (repo-relative)."""
    root = Path(repo_root)
    mapping = {**PINNED_LABELS, **dict(labels or {})}
    changed: list[str] = []
    for path in _workflows(root):
        lines = read_source(path).replace("\r\n", "\n").split("\n")
        new = []
        for line in lines:
            code = _code(line)
            if MARKER not in line[len(code) :]:
                code = _LATEST.sub(lambda m: mapping[m.group(1)], code)
            new.append(code + line[len(_code(line)) :])
        if new != lines:
            _write_like(path, "\n".join(new))
            changed.append(path.relative_to(root).as_posix())
    if any(f.rule == "no-actions-update-channel" for f in find_workflow_runner_label_problems(root, min_files=0)):
        target = next((root / rel for rel in _DEPENDABOT if (root / rel).is_file()), root / _DEPENDABOT[0])
        if target.is_file():
            text = read_source(target).replace("\r\n", "\n")
            top = [line.split(":", 1)[0] for line in text.split("\n") if line and not line[0].isspace() and not line.startswith("#")]
            if not top or top[-1] != "updates":
                raise CoreError(f"{target}: `updates:` is not the last top-level key; add the github-actions entry by hand")
            text = text.rstrip("\n") + "\n" + _DEPENDABOT_ENTRY
        else:
            text = "version: 2\nupdates:\n" + _DEPENDABOT_ENTRY
        _write_like(target, text)
        changed.append(target.relative_to(root).as_posix())
    return changed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m py_ci_shared.workflow_runner_labels", description=__doc__.split("\n", 1)[0])
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--fix", action="store_true", help="rewrite the labels and add the update channel")
    parser.add_argument("--label", action="append", default=[], help="override a pin, e.g. ubuntu=ubuntu-22.04")
    args = parser.parse_args(argv)
    try:
        labels = dict(item.split("=", 1) for item in args.label)
        if args.fix:
            for rel in fix_workflow_runner_labels(args.repo, labels=labels):
                sys.stdout.write(f"fixed {rel}" + "\n")
        found = find_workflow_runner_label_problems(args.repo, min_files=0)
    except (CoreError, ValueError) as exc:
        sys.stderr.write(f"workflow_runner_labels: {exc}" + "\n")
        return 1
    for f in found:
        sys.stdout.write(f.render() + "\n")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
