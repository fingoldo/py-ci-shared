"""Line endings of what is COMMITTED: bare CR, mixed CRLF/LF, and CRLF in a text blob.

``src/pyutilz/dev/code_audit/constructor_param_overwritten.py`` was committed in pyutilz fa1aab0 (2026-09-03) with 306
bare-CR line ends, 18 CRLF and 0 LF, and stayed that way for 23 days (fixed in 60c59c3); black could not format it.
mlframe needed three "restore LF" commits on one day (7e2c512e8, 572848918, 58af3ebe0). ``lf_file_writes`` checks code
that WRITES files; nothing checked what is in the index. Two traps make the worktree the wrong place to look: on
Windows ``core.autocrlf`` turns every LF blob into CRLF on checkout (a worktree byte scan reported 2448 of glossum's
2494 files as CRLF, all artefacts), and pre-commit's ``mixed-line-ending`` hook does not see a CR-only file as mixed.

So the gate reads the index blobs (``git ls-files -s`` + ``git cat-file --batch``) and counts the endings from the
bytes. git's own verdict (``git ls-files --eol``) cannot be the filter: git calls a file with lone CRs binary, and the
seed file reads as ``i/-text``. A blob git calls binary is checked when it holds no NUL byte and decodes as UTF-8, so
images and archives stay out and a CR-damaged source file does not. ``.gitattributes`` is honoured: ``-text`` (or
``binary``) skips a file, which is how a fixture that must keep CRLF opts out. ``eol=crlf`` does not: it only sets
what a checkout writes, and git itself stores such a file with LF, so a CRLF blob under it is a finding too. One
finding per file, with the three counts; its baseline key is the rule and path, so a count change does not re-key it.

Usage in a consumer's meta test::

    from py_ci_shared.committed_line_endings import assert_committed_line_endings

    def test_committed_files_have_lf_line_endings():
        assert_committed_line_endings(REPO_ROOT)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, CorpusError, EmptyScanError, Finding
from ._core.git import GitError, git_text, run_git

__all__ = ["RULE_BARE_CR", "RULE_CRLF", "RULE_MIXED", "Endings", "assert_committed_line_endings", "find_committed_line_endings", "index_endings"]

RULE_BARE_CR = "committed-bare-cr"
RULE_MIXED = "committed-mixed-endings"
RULE_CRLF = "committed-crlf"

PathLike = Union[str, Path]
_SKIPPED_MODES = frozenset({"160000", "120000"})  # a submodule commit, a symlink


@dataclass(frozen=True)
class Endings:
    """Line-end counts of one index blob."""

    path: str
    crlf: int
    lf: int
    bare_cr: int


def _git(root: Path, *args: str, stdin: Optional[bytes] = None) -> bytes:
    try:
        proc = run_git(root, *args, stdin=stdin)
    except GitError as exc:
        raise CorpusError(f"cannot run git in {root}: {exc}") from exc
    if proc.returncode != 0:
        raise CorpusError(f"git {' '.join(args[:2])} failed in {root}: {git_text(proc.stderr).strip()}")
    return proc.stdout


def _records(raw: bytes) -> Iterator[tuple[str, str]]:
    """``(head, path)`` per NUL-terminated ``<head>\\t<path>`` record."""
    for record in raw.split(b"\0"):
        if b"\t" in record:
            head, path = record.split(b"\t", 1)
            yield head.decode("utf-8", "replace"), path.decode("utf-8", "surrogateescape")


def _text_files(root: Path) -> dict[str, bool]:
    """Index path -> whether git calls it binary, for every file not marked ``-text``/``binary``."""
    out: dict[str, bool] = {}
    for head, path in _records(_git(root, "ls-files", "-z", "--eol")):
        fields = head.split()
        index = next((f[2:] for f in fields if f.startswith("i/")), "")
        attr = " ".join(f[5:] if f.startswith("attr/") else f for f in fields if not f.startswith(("i/", "w/")))
        attrs = attr.split()
        if "-text" in attrs or "binary" in attrs:
            continue
        out[path] = index == "-text"
    return out


def _blobs(root: Path) -> dict[str, str]:
    """Index path -> blob id (stage 0, or the first stage of a conflicted path); submodules and symlinks left out."""
    out: dict[str, str] = {}
    for head, path in _records(_git(root, "ls-files", "-s", "-z")):
        mode, sha, _stage = head.split()
        if mode not in _SKIPPED_MODES:
            out.setdefault(path, sha)
    return out


def _cat_blobs(root: Path, shas: list[str]) -> dict[str, bytes]:
    """Every blob in one ``git cat-file --batch`` process."""
    if not shas:
        return {}
    raw = _git(root, "cat-file", "--batch", stdin=("\n".join(shas) + "\n").encode("ascii"))
    out: dict[str, bytes] = {}
    at = 0
    while at < len(raw):
        nl = raw.index(b"\n", at)
        header = raw[at:nl].decode("ascii", "replace").split()
        at = nl + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        out[header[0]] = raw[at : at + size]
        at += size + 1  # the blob is followed by one newline
    return out


def _decodes_as_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def index_endings(root: PathLike) -> list[Endings]:
    """Line-end counts of every text file in the index of the git repo at *root*, sorted by path."""
    base = Path(root)
    if not base.is_dir():
        raise CorpusError(f"corpus root does not exist: {base}")
    binary = _text_files(base)  # path -> git calls it binary
    ids = {path: sha for path, sha in _blobs(base).items() if path in binary}
    blobs = _cat_blobs(base, sorted(set(ids.values())))
    out = []
    for path in sorted(ids):
        data = blobs.get(ids[path], b"")
        if binary[path] and not _decodes_as_text(data):
            continue
        crlf = data.count(b"\r\n")
        out.append(Endings(path, crlf, data.count(b"\n") - crlf, data.count(b"\r") - crlf))
    return out


def _finding(e: Endings) -> Optional[Finding]:
    counts = f"{e.bare_cr} bare CR, {e.crlf} CRLF, {e.lf} LF"
    if e.bare_cr:
        rule, why = RULE_BARE_CR, "a bare CR ends a line for some tools and not others (black rejects the file)"
    elif e.crlf and e.lf:
        rule, why = RULE_MIXED, "mixed line endings"
    elif e.crlf:
        rule, why = RULE_CRLF, "CRLF committed in a text blob (core.autocrlf or a text attribute would have stored LF)"
    else:
        return None
    fix = "convert it to LF and commit it, or mark the path -text in .gitattributes when it must keep its bytes"
    return Finding(e.path, 1, rule, f"{why}; the committed blob has {counts}. {fix}", key=f"{rule}::{e.path}")


def find_committed_line_endings(root: PathLike, *, min_files: int = 1) -> list[Finding]:
    """One finding per committed text file with a bare CR, mixed endings, or CRLF.

    Raises ``CorpusError`` outside a git repository and ``EmptyScanError`` when fewer than *min_files* text files are
    in the index: a gate that reads nothing must not pass."""
    endings = index_endings(root)
    if len(endings) < min_files:
        raise EmptyScanError(f"only {len(endings)} committed text file(s) under {root}; expected at least {min_files}")
    return [f for f in (_finding(e) for e in endings) if f is not None]


def assert_committed_line_endings(root: PathLike, *, baseline_path: Optional[PathLike] = None, refresh: bool = False, min_files: int = 1) -> None:
    """Fail on any finding, or with *baseline_path* on one the (shrink-only) baseline does not accept."""
    found = find_committed_line_endings(root, min_files=min_files)
    guidance = "commit the file with LF line endings (a -text .gitattributes line for a file that must keep other bytes)"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="committed_line_endings", refresh_command="PY_CI_SHARED_REFRESH=committed_line_endings")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} committed file(s) with wrong line endings; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
