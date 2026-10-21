"""Print the pyutilz commit that the calling repository pins, or exit 1 when it pins none or more than one.

The calling repository's own ``pyproject.toml`` is the single source of the pin: its ``[dev]`` extra names
``pyutilz @ git+https://github.com/fingoldo/pyutilz.git@<sha>``. A consumer that installs this action without a
``pyutilz-ref`` input therefore gets the commit it pins, not the commit py-ci-shared pins.
"""

from __future__ import annotations

import pathlib
import re
import sys

PIN = re.compile(r"pyutilz\s*@\s*git\+https://github\.com/fingoldo/pyutilz(?:\.git)?@([0-9a-f]{40})")


def pins(text: str) -> set[str]:
    return set(PIN.findall(text))


def main(argv: list[str]) -> int:
    path = pathlib.Path(argv[1] if len(argv) > 1 else "pyproject.toml")
    found = pins(path.read_text(encoding="utf-8"))
    if len(found) != 1:
        message = f"::error::{path} must pin pyutilz to exactly one commit (found {len(found)}); set the pyutilz-ref input to the commit you mean"
        sys.stderr.write(message + "\n")
        return 1
    sys.stdout.write(next(iter(found)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
