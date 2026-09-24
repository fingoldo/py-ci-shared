#!/usr/bin/env python
"""Drop-in ``pre-commit`` replacement that survives concurrent-session stash-restore races.

``pre_commit.staged_files_only`` stashes a repo's unstaged tracked-file changes to a patch file
before running hooks (so hooks see only staged content), then tries to restore that patch
afterward. In a repo with multiple concurrent git sessions (parallel agents, multiple terminals),
another session can modify the SAME unstaged files while ours are stashed; the restore-patch then
fails to apply (``git apply: patch does not apply``) even after pre-commit's own one retry
(checkout-and-reapply), and the un-caught ``CalledProcessError`` aborts the entire commit -- even
though every real hook (mypy, tests, lint...) already ran and passed. The files being COMMITTED
are unaffected by this failure; only some OTHER file's unstaged edits (not part of this commit,
usually another session's own working copy) couldn't be silently restored.

This module monkeypatches ``pre_commit.staged_files_only._unstaged_changes_cleared`` so that final
failure is non-fatal: it's logged at ERROR level and printed to stderr as a banner with the exact
``git apply`` command (the affected unstaged changes are never silently dropped -- the patch file
stays on disk, and a copy is kept under ``.git/safe-precommit-unrestored/``, where pre-commit's own
cache cleanup cannot remove it), and the commit proceeds instead of aborting.

Usage
-----
Either invoke directly instead of ``pre-commit``::

    safe-precommit run --hook-stage pre-commit
    python -m py_ci_shared.safe_precommit run --hook-stage pre-commit

or (recommended, fully automatic) point the generated git hook at this module -- see
``py_ci_shared.install_safe_hook``, which rewrites ``.git/hooks/pre-commit`` to invoke
``python -m py_ci_shared.safe_precommit hook-impl ...`` instead of ``python -mpre_commit
hook-impl ...``, so plain ``git commit`` gets the patched behavior with no one needing to
remember a wrapper command.

Version coupling: the patched function is a maintained copy of ``pre_commit``'s own
``_unstaged_changes_cleared`` (currently tracking pre-commit's ``staged_files_only.py``), not a
generic wrapper -- a ``pre-commit`` upgrade that changes that function's internals will not break
this (the patch only replaces the module attribute; unrelated pre-commit internals are untouched),
but WILL silently stop reflecting any behavior change pre-commit makes to that function until this
copy is updated to match. ``_verify_patch_target()`` below does a best-effort staleness check at
import time so drift is visible (a log warning), not silently wrong.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable, Generator
from types import ModuleType

#: Where an unrestorable patch is copied, relative to the repository's git dir.
UNRESTORED_DIR = "safe-precommit-unrestored"

_REQUIRED_ATTRIBUTES = ("cmd_output", "cmd_output_b", "_CHECKOUT_CMD", "_git_apply", "CalledProcessError", "FatalError", "logger")


def _preserve_unrestored_patch(sfo: ModuleType, patch_filename: str) -> "str | None":
    """Copy *patch_filename* into ``<git dir>/safe-precommit-unrestored/``; the copy's path, or None if that failed."""
    try:
        git_dir = sfo.cmd_output("git", "rev-parse", "--absolute-git-dir")[1].strip()
        target_dir = os.path.join(git_dir, UNRESTORED_DIR)
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, os.path.basename(patch_filename))
        shutil.copy2(patch_filename, target)
        return target
    except Exception:  # the loud report below must happen even when the copy cannot be made
        return None


def _report_unrestored(sfo: ModuleType, patch_filename: str) -> None:
    kept = _preserve_unrestored_patch(sfo, patch_filename)
    copies = f"{patch_filename}" + (f" and {kept}" if kept else "")
    sfo.logger.error(
        "Could not restore unstaged changes from %s (conflicted with concurrent edits from another session). "
        "The patch is PRESERVED at %s -- inspect it and `git apply` it if those changes are still needed. "
        "Proceeding with the commit.",
        patch_filename,
        copies,
    )
    banner = "=" * 78
    sys.stderr.write(
        f"\n{banner}\nsafe-precommit: UNSTAGED CHANGES WERE NOT RESTORED to the working tree.\n"
        f'They are saved in: {copies}\nRestore with:      git apply --3way "{kept or patch_filename}"\n{banner}\n\n'
    )
    sys.stderr.flush()


def _patched_unstaged_changes_cleared(sfo: ModuleType) -> Callable[[str], contextlib.AbstractContextManager[None]]:
    """Build the patched ``_unstaged_changes_cleared`` context manager bound to ``sfo``'s own state.

    ``sfo`` is the live ``pre_commit.staged_files_only`` module -- reusing its already-imported
    ``git``/``FatalError``/``CalledProcessError``/``cmd_output``/``cmd_output_b``/``_CHECKOUT_CMD``/
    ``_git_apply``/``logger`` keeps this copy consistent with whatever pre-commit itself resolved,
    rather than re-importing (and potentially diverging on) those names ourselves.
    """

    @contextlib.contextmanager
    def _unstaged_changes_cleared(patch_dir: str) -> Generator[None]:
        tree = sfo.cmd_output("git", "write-tree")[1].strip()
        diff_cmd = (
            "git",
            "diff-index",
            "--ignore-submodules",
            "--binary",
            "--exit-code",
            "--no-color",
            "--no-ext-diff",
            tree,
            "--",
        )
        retcode, diff_stdout, diff_stderr = sfo.cmd_output_b(*diff_cmd, check=False)
        if retcode == 0:
            yield
        elif retcode == 1 and not diff_stdout.strip():
            yield
        elif retcode == 1 and diff_stdout.strip():
            patch_filename = f"patch{int(time.time())}-{os.getpid()}"
            patch_filename = os.path.join(patch_dir, patch_filename)
            sfo.logger.warning("Unstaged files detected.")
            sfo.logger.info("Stashing unstaged files to %s.", patch_filename)
            os.makedirs(patch_dir, exist_ok=True)
            with open(patch_filename, "wb") as patch_file:
                patch_file.write(diff_stdout)

            no_checkout_env = dict(os.environ, _PRE_COMMIT_SKIP_POST_CHECKOUT="1")
            try:
                sfo.cmd_output_b(*sfo._CHECKOUT_CMD, env=no_checkout_env)
                yield
            finally:
                # No bare `return`/`break` in this finally: doing so would silently swallow any
                # exception that propagated from the `yield` above (e.g. a real hook failure) --
                # only the restore outcome is decided here, never whether the block itself failed.
                restored = False
                try:
                    sfo._git_apply(patch_filename)
                    restored = True
                except sfo.CalledProcessError:
                    sfo.logger.warning("Stashed changes conflicted with hook auto-fixes... Rolling back fixes...")
                    sfo.cmd_output_b(*sfo._CHECKOUT_CMD, env=no_checkout_env)
                    try:
                        sfo._git_apply(patch_filename)
                        restored = True
                    except sfo.CalledProcessError:
                        pass
                if restored:
                    sfo.logger.info("Restored changes from %s.", patch_filename)
                else:
                    # PATCHED tail: the original raises here and aborts the whole commit, even
                    # though every real hook already ran. The staged changes we're committing are
                    # unaffected -- only some OTHER unstaged edit (commonly a concurrent session's
                    # own working copy) couldn't be silently restored. Surface it loudly (ERROR log
                    # plus a stderr banner), keep the patch file and a copy under the git dir so
                    # nothing is lost, but don't raise.
                    _report_unrestored(sfo, patch_filename)
        else:  # pragma: win32 no cover
            e = sfo.CalledProcessError(retcode, diff_cmd, b"", diff_stderr)
            raise sfo.FatalError(
                f"pre-commit failed to diff -- perhaps due to permissions?\n\n{e}",
            )

    return _unstaged_changes_cleared


def _verify_patch_target(sfo) -> list[str]:
    """Best-effort staleness check: warn (never raise) if the live function's required attributes
    have drifted from what this copy expects, so a future pre-commit upgrade fails loudly via a
    log line instead of silently patching the wrong thing. Returns the missing attribute names."""
    missing = [name for name in _REQUIRED_ATTRIBUTES if not hasattr(sfo, name)]
    if missing:
        logging.getLogger("pre_commit").warning(
            "safe_precommit: pre_commit.staged_files_only is missing expected attribute(s) %s -- "
            "the stash-race patch may be stale for this pre-commit version and was NOT applied.",
            missing,
        )
    return missing


def patch_stash_restore() -> bool:
    """Monkeypatch ``pre_commit.staged_files_only._unstaged_changes_cleared`` in place.

    Returns True if the patch was applied, False if it was skipped (pre-commit not importable, or
    the staleness check found the target function's dependencies missing).
    """
    try:
        import pre_commit.staged_files_only as sfo
    except ImportError:
        return False
    if _verify_patch_target(sfo):
        return False  # the copy would call an attribute this pre-commit version does not have
    sfo._unstaged_changes_cleared = _patched_unstaged_changes_cleared(sfo)
    return True


def main(argv: list[str] | None = None) -> int:
    patch_stash_restore()
    from pre_commit.main import main as pc_main

    return int(pc_main(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
