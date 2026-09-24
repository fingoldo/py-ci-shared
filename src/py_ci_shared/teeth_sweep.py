"""Does anything actually FAIL if an audit fix is undone? Mutate, run the suite, restore.

`scripts/verify_audit_dispositions.py` answers "did the code change". This answers the question that
matters more and that no disposition's prose can settle: "would anything notice if it changed back".
Run on one repository's 2026-09-05 round it mutated seventeen fixes and found FIVE with no teeth, two of them P1 --
including one whose test carried a docstring denying it was toothless.

USAGE

    python -m py_ci_shared.teeth_sweep --repo <repo root> --cases <cases.json>
    python -m py_ci_shared.teeth_sweep --repo . --cases cases.json --only HTTP-3 PERF-1

A case file is a JSON list of objects:

    [{"id": "HTTP-3",
      "what": "the redirect pins the exit port again",
      "file": "download_job_attachments.py",
      "old":  "kwargs[\\"proxy\\"] = _core.upwork_proxy.proxy_url()",
      "new":  "kwargs[\\"proxy\\"] = _core.upwork_proxy.proxy_url(0)"}]

Case files live under `scripts/teeth_cases/` and must be added with `git add -f`: `upwork/.gitignore`
ignores `*.json` wholesale, and a sweep whose cases are not committed cannot be re-run by anyone
else, which is most of its value.

`old` must appear EXACTLY ONCE. Line endings in `old`/`new` are normalised to whatever the target
file uses, because half this repo is CRLF and an LF needle silently matches nothing there.

THREE RULES THIS SCRIPT ENCODES, each learned by getting it wrong:

1. A mutation that does not land is NOT a green run. `old` is counted and the substitution is read
   back from disk before pytest is allowed to run; anything else is reported as NOT APPLIED and
   excluded from the verdict, never counted as "has teeth".
2. The whole suite, every time. A gap means NOTHING anywhere notices, so a targeted run cannot
   establish it -- three of the five misses were in files whose own tests were green.
3. The tree is restored from a byte copy taken before the edit, and restored even when pytest
   crashes. A sweep that leaves a defect behind is worse than no sweep. KILLING the process is the
   one case the `finally` cannot cover: the in-flight mutation stays in the working tree. If you
   stop a sweep, check `git status` immediately -- the leftover is one file and `git diff` shows
   exactly the mutation, and the backups directory printed at the end holds the original bytes.
4. THE BASELINE MUST BE GREEN, and the run stops if it is not. Learned on 2026-09-07: a merge from
   another session left six tests failing (it had added tests for a `scraper_bootstrap(sql_file=)`
   parameter that audit ARCH-14 had deleted in the meantime -- a clean merge, a broken result). The
   sweep then reported every one of seven cases as "has teeth", including a mutation that changed
   one word of a markdown file and one that edited `requirements.txt`. Failures that predate the
   mutation prove nothing about it, and subtracting them by name is not safe either: a mutation can
   make a baseline-failing test pass. So the sweep refuses to start.

MUTATE THE SUBJECT, NEVER THE ASSERTION. When a finding's fix IS a test, the case must break the
thing that test watches -- the production code, the guard, the pattern -- and not the test's own
`assert`. Weakening an assertion does not reintroduce a defect; it deletes the test, and of course
the rest of the suite stays green. Three cases in this sweep were drafted the wrong way round
(TEST-6 renamed a parameter in a Protocol STUB rather than the real method, TEST-9 used a no-op
`.replace('%s', '%s', 1)`, TEST-10 loosened the assertion itself); each would have been recorded as
"no teeth" for a case where nothing changed. Two were caught by reading before the run and one by
re-checking a surprising green afterwards.

WHAT A GREEN RUN MEANS, and it is not "the fix is wrong": the fix is unwatched. The usual cause is a
test that stubs the very symbol the fix introduced -- see `new_scraper/CLAUDE.md`. Before believing
it, check that the mutation was aimed at the SUBJECT and that the disposition ever CLAIMED teeth:
CQ-8's green was predicted in writing by its own disposition and is a confirmation, not a gap.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import time
from typing import Any

from ._mutation_runner import run_with_deadline

#: pytest exit codes that are a verdict: 0 all passed, 1 some failed. Anything else (2 interrupted,
#: 3 internal error, 4 usage error, 5 nothing collected) means the suite did not run.
_VERDICT_CODES = (0, 1)

#: Marks a `failed` entry that stands for a run that did not happen, rather than for a test.
_DID_NOT_RUN = "<pytest did not run"


def _apply(path: pathlib.Path, old: str, new: str) -> str | None:
    """Substitute, or return why it could not be done. Never leaves a partial edit.

    The case's own line endings are normalised to LF first and then to the file's, so a CRLF needle
    does not become ``\\r\\r\\n``. The write is verified byte for byte against the expected content:
    checking that the replacement merely APPEARS in the file is vacuous for an empty replacement, or
    for one the file already contained elsewhere.
    """
    try:
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"cannot read the target: {exc}"
    nl = "\r\n" if "\r\n" in raw else "\n"

    def native(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\n", nl)

    needle, repl = native(old), native(new)
    if not needle:
        return "the needle is empty"
    found = raw.count(needle)
    if found != 1:
        return f"needle matched {found} times"
    expected = raw.replace(needle, repl).encode("utf-8")
    if expected == raw.encode("utf-8"):
        return "the substitution changes nothing (old and new are the same)"
    path.write_bytes(expected)
    if path.read_bytes() != expected:
        return "substitution did not survive the write"
    return None


def read_pytest_outcome(stdout: str) -> tuple[str, list[str]]:
    """A pytest run's summary line and the tests it named as not passing.

    Split out of `_run_suite` so the part that decides a sweep's verdict can be tested without
    running a suite. The rule it encodes is the whole correctness of that verdict:

    FAILED *AND* ERROR, and the difference is not cosmetic. A mutation that renames or removes
    something an importer needs makes pytest fail at COLLECTION, reported as::

        ERROR tests/test_x.py::test_y

    with no FAILED line anywhere. Counting only FAILED reports that mutation as SURVIVED -- "this
    fix has no teeth" -- for a fix so thoroughly watched that the suite cannot even load without it.
    The verdict is inverted, and inverted in the direction that costs work: someone is sent to write
    a regression test that already exists.

    Found 2026-09-07 by hand-checking a surprising SURVIVED. Any earlier "no teeth" verdict from
    this tool for a mutation of that shape was wrong and should be re-run rather than trusted.

    `"ERROR "` carries its trailing space on purpose: pytest also emits bare `ERRORS` section
    banners and `ERROR` lines from logging, and neither names a test.
    """
    lines = stdout.splitlines()
    summary = next(
        (ln.strip() for ln in reversed(lines) if " passed" in ln or " failed" in ln or " error" in ln),
        "NO RESULT LINE",
    )
    return summary, [ln.strip() for ln in lines if ln.startswith(("FAILED", "ERROR "))]


def outcome_of(returncode: int, stdout: str, stderr: str = "") -> tuple[str, list[str], bool]:
    """``(summary, failed, ran)`` of one pytest run, decided by its exit code as well as its output.

    Exit 0 and 1 are verdicts. Any other code means the suite did not run -- a usage error from a
    flag for a plugin that is not installed, a conftest that fails to import, nothing collected --
    and reading that run's output as "nothing failed" reported every case as having no teeth.
    An exit 1 that names no test still counts as a failure, under a placeholder name.
    """
    summary, failed = read_pytest_outcome(stdout)
    if returncode not in _VERDICT_CODES:
        tail = " | ".join((stderr or stdout).strip().splitlines()[-3:])
        return f"PYTEST DID NOT RUN (exit {returncode}): {tail}", [f"{_DID_NOT_RUN} (exit {returncode})>"], False
    if returncode == 1 and not failed:
        return summary, [f"<pytest exited 1 without naming a test: {summary}>"], True
    return summary, failed, True


def _suite_command(jobs: int) -> list[str]:
    """The pytest command, with a plugin's flags only when that plugin is installed.

    ``-n`` without pytest-xdist and ``--no-cov`` without pytest-cov are usage errors (exit 4).
    """
    cmd = [sys.executable, "-m", "pytest", "-q"]
    if importlib.util.find_spec("xdist") is not None:
        cmd += ["-n", str(jobs)]
    if importlib.util.find_spec("pytest_cov") is not None:
        cmd.append("--no-cov")
    if importlib.util.find_spec("anyio") is not None:
        cmd += ["-p", "no:anyio"]
    return cmd


def _run_suite(repo: pathlib.Path, jobs: int) -> tuple[str, list[str]]:
    """Run the WHOLE suite and return its summary line plus the names that failed.

    Whole, not targeted: "no teeth" is a claim about the entire suite, and three of the five
    misses this script was written for sat in files whose own tests were green. A run that did not
    happen is returned with a `failed` entry starting with ``<pytest did not run``.
    """
    # A MUTATION CAN HANG THE SUITE, and without a bound it hangs the whole sweep with it.
    # Seen 2026-09-07 in the sibling package: disabling a cache-eviction cadence left a test
    # waiting for an eviction that could no longer happen, and the run sat there. Fifteen
    # minutes is roughly four times the slowest honest run measured here. The whole process
    # tree is killed on a timeout: pytest-xdist's workers outlived a plain kill on Windows.
    proc = run_with_deadline(_suite_command(jobs), cwd=repo, env=None, timeout=900)
    if proc is None:
        return "TIMED OUT -- the mutation hangs the suite; that is a finding, not a pass", ["<suite timed out>"]
    summary, failed, _ran = outcome_of(proc.returncode, proc.stdout, proc.stderr)
    return summary, failed


def _did_not_run(failed: list[str]) -> bool:
    return any(name.startswith(_DID_NOT_RUN) for name in failed)


def _sweep_case(repo: pathlib.Path, case: dict[str, Any], backups: pathlib.Path, jobs: int) -> str:
    """Mutate, run and restore one case. Returns ``"teeth"``, ``"toothless"``, ``"skipped"`` or ``"error"``."""
    target = repo / case["file"]
    print(f"\n=== {case['id']}: {case['what']}", flush=True)
    backup = backups / f"{case['id']}_{target.name}"
    shutil.copy2(target, backup)
    try:
        why = _apply(target, case["old"], case["new"])
        if why is not None:
            print(f"    NOT APPLIED ({why}) -- this case proves nothing and is not a pass")
            return "skipped"
        started = time.monotonic()
        summary, failed = _run_suite(repo, jobs)
        print(f"    {summary}   [{time.monotonic() - started:.0f}s]")
        if _did_not_run(failed):
            print("    the suite did not RUN with this mutation applied -- not a kill and not a pass")
            return "error"
        if failed:
            for name in failed[:3]:
                print(f"      {name[:110]}")
            if len(failed) > 3:
                print(f"      ... and {len(failed) - 3} more")
            return "teeth"
        print("    NO TEETH: the defect is back and the whole suite is green")
        return "toothless"
    finally:
        shutil.copy2(backup, target)


def _case_id(case: object) -> str:
    return str(case.get("id", "?")) if isinstance(case, dict) else "?"


def main(argv: list[str] | None = None) -> int:
    """Mutate each case in turn, run the suite, restore, and report which fixes nothing watches."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", required=True, help="JSON file of mutations")
    ap.add_argument("--repo", required=True, help="repository root the paths in the case file are relative to")
    ap.add_argument("--only", nargs="*", default=None, help="run just these finding ids")
    ap.add_argument("--jobs", type=int, default=4, help="pytest -n (default 4, a quarter of 16 physical cores)")
    args = ap.parse_args(argv)
    repo = pathlib.Path(args.repo).resolve()

    cases = json.loads(pathlib.Path(args.cases).read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only)
        cases = [c for c in cases if _case_id(c) in wanted]
        missing = wanted - {_case_id(c) for c in cases}
        if missing:
            print(f"no such case: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2

    print("=== baseline: the suite must be green before anything is mutated", flush=True)
    started = time.monotonic()
    summary, failed = _run_suite(repo, args.jobs)
    print(f"    {summary}   [{time.monotonic() - started:.0f}s]")
    if failed:
        print(f"\nREFUSING TO SWEEP: {len(failed)} test(s) already fail on the unmutated tree.")
        for name in failed[:10]:
            print(f"  {name[:110]}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more")
        print("\nEvery case would report those same failures and look like it had teeth. Fix the tree first.")
        return 2

    toothless: list[str] = []
    skipped: list[str] = []
    errored: list[str] = []
    backups = pathlib.Path(tempfile.mkdtemp(prefix="teeth_sweep_"))

    for case in cases:
        try:
            outcome = _sweep_case(repo, case, backups, args.jobs)
        except Exception as exc:  # one bad case must not cost every later case its answer
            print(f"    ERROR ({type(exc).__name__}: {exc}) -- this case proves nothing and is not a pass")
            outcome = "error"
        if outcome == "toothless":
            toothless.append(_case_id(case))
        elif outcome == "skipped":
            skipped.append(_case_id(case))
        elif outcome == "error":
            errored.append(_case_id(case))

    print(f"\n{'=' * 70}\n{len(cases)} cases, {len(toothless)} with no teeth, {len(skipped)} not applied, {len(errored)} errored")
    if toothless:
        print("NO TEETH: " + ", ".join(toothless))
    if skipped:
        print("NOT APPLIED (fix the needle and re-run; do NOT read these as passes): " + ", ".join(skipped))
    if errored:
        print("ERRORED (the case could not be run; do NOT read these as passes): " + ", ".join(errored))
    print(f"backups kept at {backups}")
    return 1 if toothless or skipped or errored else 0


if __name__ == "__main__":
    raise SystemExit(main())
