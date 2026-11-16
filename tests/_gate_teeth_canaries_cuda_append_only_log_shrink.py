"""Canary fragment of append_only_log_shrink (audit 50-T12). The loader globs ``_gate_teeth_canaries_cuda_*.py``, so this
file is named for that glob even though the gate is not a CUDA gate; each fragment defines one CANARY.

The subject is a git history, which a file fixture cannot hold: each ``step<N>/`` directory of a case is one commit, in
name order, of ``log.jsonl``; a ``message.txt`` in a step is that commit's message. The run builds the repository under
the materialized corpus and asserts the gate over it. No empty case (an empty history is a clean pass) and no
unparsable case (the gate reads blobs, not syntax)."""

from __future__ import annotations

from pathlib import Path

from _git_repo_helper import make_repo
from _gate_teeth_canaries import Canary, _gate
from py_ci_shared._core.git import run_git


def _shrink_canary(d: Path) -> None:
    steps = sorted(p for p in d.iterdir() if p.is_dir() and p.name.startswith("step"))
    repo = d / "repo"
    for n, step in enumerate(steps):
        text = (step / "log.jsonl").read_bytes()
        if n == 0:
            make_repo(repo, {"log.jsonl": text.decode("utf-8")})
            continue
        (repo / "log.jsonl").write_bytes(text)
        message = (step / "message.txt").read_text(encoding="utf-8") if (step / "message.txt").is_file() else f"append step {n}"
        run_git(repo, "add", "-A", check=True)
        run_git(repo, "commit", "-q", "-m", message, check=True)
    _gate("append_only_log_shrink").assert_append_only_log_shrink(repo, paths=("log.jsonl",))


CANARY = Canary(
    "append_only_log_shrink",
    _shrink_canary,
    token="log.jsonl",
    tokens=("log.jsonl",),
    parses=False,
    empty=False,
)
