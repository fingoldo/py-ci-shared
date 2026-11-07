"""The one place tests find a POSIX bash. On Windows, ``bash`` on PATH may be System32's WSL launcher, which runs nothing."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest


def _works(exe: str) -> bool:
    try:
        proc = subprocess.run([exe, "-c", "echo ok"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "ok"


def _candidates() -> list:
    found = []
    on_path = shutil.which("bash")
    if on_path:
        found.append(on_path)
    git = shutil.which("git")
    if git and sys.platform == "win32":
        for parent in list(Path(git).resolve().parents)[:3]:
            found += [str(parent / "bin" / "bash.exe"), str(parent / "usr" / "bin" / "bash.exe")]
        found.append(r"C:\Program Files\Git\bin\bash.exe")
    return found


def find_bash() -> Optional[str]:
    """A bash that really runs ``echo ok``; None when there is none."""
    for exe in _candidates():
        if (Path(exe).is_file() or exe == shutil.which("bash")) and _works(exe):
            return exe
    return None


def working_bash() -> str:
    """The bash path; skips on Windows without one, fails loudly elsewhere."""
    exe = find_bash()
    if exe is None:
        if sys.platform == "win32":
            pytest.skip("no working POSIX bash on this Windows host (System32 bash.exe is the WSL launcher)")
        raise AssertionError("bash is missing or broken on a POSIX host")
    return exe
