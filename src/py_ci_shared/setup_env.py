#!/usr/bin/env python
"""One-time per-machine setup: persist ``PY_CI_SHARED_DIR`` at the OS/user level so ruff's
``extend = "$PY_CI_SHARED_DIR/configs/ruff-base.toml"`` (see README's "Using the shared ruff
config") resolves identically in a shell, in CI, AND in a GUI-launched editor. A shell-only
``export``/``$env:`` does NOT reach an app launched from the Dock/Start Menu rather than a
terminal (most noticeable on macOS) -- that editor would then silently lint against the unmerged
base config, a smaller effective ruleset than CI enforces, with no error to flag the mismatch.

Cross-platform: Windows (``setx``, user-level registry, no admin needed), macOS (``launchctl
setenv`` for this session's already-running GUI apps, plus a LaunchAgent plist so it also applies
after the next login/reboot), Linux (``~/.config/environment.d/*.conf``, the systemd-recommended
mechanism picked up by systemd user sessions at login). Also appends an idempotent export line to
the detected POSIX shell profile(s), for shell-only workflows that don't go through the OS-level
mechanism at all.

Deliberately NOT run automatically at ``pip install`` time -- a package silently mutating the
registry / LaunchAgents / systemd config as an install side effect is exactly the pattern that
gets a package flagged as a supply-chain risk, and it wouldn't even take effect in the *current*
process anyway (all of these mechanisms need a new shell/session to pick up). Run explicitly,
once, after cloning:

    pip install -e /path/to/py-ci-shared
    python -m py_ci_shared.setup_env

Idempotent -- safe to re-run (e.g. after moving the clone to a new path).
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

_VAR = "PY_CI_SHARED_DIR"


def _repo_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    if not (root / "configs" / "ruff-base.toml").exists():
        raise FileNotFoundError(
            f"{root} has no configs/ruff-base.toml -- this only works from a real clone (an editable " "`pip install -e .`), not a built/non-editable install."
        )
    return root


def _set_windows(value: str) -> None:
    subprocess.run(["setx", _VAR, value], check=True, capture_output=True, text=True)
    print(f"Windows: set {_VAR} via setx (user-level registry).")


def _set_macos(value: str) -> None:
    subprocess.run(["launchctl", "setenv", _VAR, value], check=True, capture_output=True, text=True)
    print(f"macOS: set {_VAR} for this session's already-running GUI apps via `launchctl setenv`.")

    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "dev.py-ci-shared.setenv.plist"
    plist_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        "    <string>dev.py-ci-shared.setenv</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        "        <string>/bin/launchctl</string>\n"
        "        <string>setenv</string>\n"
        f"        <string>{_VAR}</string>\n"
        f"        <string>{value}</string>\n"
        "    </array>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "</dict>\n"
        "</plist>\n",
        encoding="utf-8",
    )
    # unload-then-load so a re-run with a changed `value` actually takes: `load` on an
    # already-loaded label is a silent no-op, leaving the OLD path in the running agent.
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
    try:
        subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=True, capture_output=True, text=True)
        print(f"macOS: installed a LaunchAgent at {plist_path} so this persists across logins/reboots.")
    except subprocess.CalledProcessError as exc:
        print(
            f"macOS: wrote {plist_path} but `launchctl load` failed ({exc.stderr.strip() if exc.stderr else exc}) -- "
            f"it will still apply on the next login; load it manually with `launchctl load -w {plist_path}`.",
            file=sys.stderr,
        )


def _set_linux(value: str) -> None:
    env_dir = Path.home() / ".config" / "environment.d"
    env_dir.mkdir(parents=True, exist_ok=True)
    conf_path = env_dir / "50-py-ci-shared.conf"
    conf_path.write_text(f"{_VAR}={value}\n", encoding="utf-8")
    print(f"Linux: wrote {conf_path} (systemd user-session mechanism -- picked up automatically at the " "next login on systemd-based desktops).")


def _append_to_shell_profile(value: str) -> None:
    for rc_name in (".zshrc", ".bashrc"):
        rc_path = Path.home() / rc_name
        if not rc_path.exists():
            continue
        text = rc_path.read_text(encoding="utf-8")
        if _VAR in text:
            continue
        with rc_path.open("a", encoding="utf-8") as fh:
            fh.write(f'\n# added by py_ci_shared.setup_env\nexport {_VAR}="{value}"\n')
        print(f"Appended {_VAR} export to {rc_path} (covers shell-only workflows too).")


def main(argv: list[str] | None = None) -> int:
    try:
        value = str(_repo_root())
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    system = platform.system()
    try:
        if system == "Windows":
            _set_windows(value)
        elif system == "Darwin":
            _set_macos(value)
            _append_to_shell_profile(value)
        elif system == "Linux":
            _set_linux(value)
            _append_to_shell_profile(value)
        else:
            print(f"Unrecognized platform {system!r} -- set {_VAR}={value} manually.", file=sys.stderr)
            return 1
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"Failed to persist {_VAR}: {exc}", file=sys.stderr)
        return 1

    print(f"\n{_VAR}={value}")
    print("Restart your terminal/IDE for the new value to be picked up everywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
