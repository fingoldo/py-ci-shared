"""A warm pytest worker for :mod:`py_ci_shared.mutation_teeth`. Not a public API.

Measured on the repo this was written for: a mutant costs 15.8s as a fresh subprocess, of which
pytest reports **0.48s** as actual test execution. Collection alone -- ``--co``, running nothing --
is 15.2s. So roughly 97% of every mutant is the interpreter starting and the package under test
importing, identical every single time. At 40 mutants that is eleven minutes to evaluate nineteen
seconds of assertions.

Keeping the interpreter warm is therefore the whole game. Two obvious ways to do it are both
silently WRONG, and both were proved so by execution rather than argued:

* **Re-running ``pytest.main()`` without touching ``sys.modules``** leaves the ORIGINAL module
  object imported. The mutated file on disk is never read, the tests pass, and the harness reports
  a false SURVIVOR -- it accuses a test of being toothless against a mutation that was never
  applied.
* **``importlib.reload(target)``** is worse: a mutation to one function made an unrelated test fail,
  because the already-imported test module still held a reference to the pre-reload class. A false
  KILL, which is the direction that hides a real defect.

What works is purging every repo-local module -- production AND test -- atomically between runs,
while leaving the third-party stack (pytest, its plugins, the standard library) warm. Measured at
1.6-2.7s marginal per mutant, a 7-10x improvement, with no identity split because nothing local
survives the purge to hold a stale reference.

A residual risk remains and is handled by the caller rather than denied here: a third-party
registry (a plugin's cache, a metaclass registry inside an installed package) can retain state
across runs in a way a purge does not reach. :mod:`mutation_teeth` therefore re-verifies every
reported SURVIVOR in a cold subprocess before believing it -- survivors are rare, so the cost is
small, and a false survivor is the outcome that wastes a human's time.

Protocol: one JSON object per line on stdin, one on stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _purge_local_modules(root: Path) -> int:
    """Drop every imported module whose file lives under *root*.

    Atomically, in one pass: purging production modules but leaving test modules imported is
    exactly the half-measure that produced the false-kill above, because the surviving test module
    holds references into the dropped one.
    """
    doomed = []
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if not file:
            continue
        try:
            if Path(file).resolve().is_relative_to(root):
                doomed.append(name)
        except (OSError, ValueError):
            continue
    for name in doomed:
        sys.modules.pop(name, None)
    return len(doomed)


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root))
    import pytest  # imported once, deliberately: this is the cost the worker exists to amortise

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"bad request: {exc}"}), flush=True)
            continue
        if request.get("cmd") == "stop":
            return 0
        try:
            purged = _purge_local_modules(root)
            code = int(pytest.main(list(request["args"])))
            print(json.dumps({"rc": code, "purged": purged}), flush=True)
        except BaseException as exc:  # noqa: BLE001 -- a worker crash must be reported, not raised
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
