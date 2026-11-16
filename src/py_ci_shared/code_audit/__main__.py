"""Entry point for running the code_audit package as ``python -m py_ci_shared.code_audit``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    from .._core.console import safe_stdio

    safe_stdio()
    raise SystemExit(main())
