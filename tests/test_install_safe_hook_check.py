"""47-T3 (refuse to patch a hook whose interpreter cannot run the wrapper), 47-T4 (`--check` and a manual hook entry)."""

from __future__ import annotations

import subprocess
import sys

from py_ci_shared import install_safe_hook as mod
from py_ci_shared import safe_precommit

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
MISSING = "/nonexistent-py-ci-shared-test/python3"

_TEMPLATE = f"""#!/bin/sh
INSTALL_PYTHON='{MISSING}'
ARGS=(hook-impl --config=.pre-commit-config.yaml --hook-type=pre-commit)
if [ -x "$INSTALL_PYTHON" ]; then
    exec "$INSTALL_PYTHON" -mpre_commit "${{ARGS[@]}}"
fi
"""


def _hooks(tmp_path, monkeypatch, text=_TEMPLATE):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text(text, encoding="utf-8")
    monkeypatch.setattr(mod, "_hooks_dir", lambda: hooks)
    return hooks / "pre-commit"


def test_the_probe_names_an_interpreter_that_cannot_import_the_wrapper(tmp_path, monkeypatch):
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"No module named 'py_ci_shared'"))
    problem = mod._interpreter_problem(_TEMPLATE.replace(MISSING, str(py)))
    assert problem and str(py) in problem and "pre-commit install -f" in problem


def test_a_missing_interpreter_is_left_to_pre_commit_fallback():
    assert mod._interpreter_problem(_TEMPLATE) is None


def test_main_refuses_to_patch_when_the_probe_fails(tmp_path, monkeypatch):
    hook = _hooks(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_interpreter_problem", lambda text: "the hook's interpreter cannot run the wrapper")
    assert mod.main([]) == 1
    assert "-mpre_commit" in hook.read_text(encoding="utf-8")


def test_check_reports_an_unpatched_hook(tmp_path, monkeypatch, capsys):
    _hooks(tmp_path, monkeypatch)
    assert mod.main(["--check"]) == 1
    assert "pre-commit" in capsys.readouterr().out


def test_check_passes_once_the_hook_is_patched(tmp_path, monkeypatch):
    _hooks(tmp_path, monkeypatch)
    assert mod.main([]) == 0
    assert mod.main(["--check"]) == 0


def test_the_wrapper_names_the_missing_pre_commit(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pre_commit.main", None)
    monkeypatch.setattr(safe_precommit, "patch_stash_restore", lambda: False)
    assert safe_precommit.main([]) == 1
    assert "pre-commit is not installed in" in capsys.readouterr().err


def test_the_hook_manifest_declares_the_check_as_manual_only():
    text = (REPO_ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    block = text[text.index("id: py-ci-shared-hooks-patched") :]
    block = block[: block.find("\n- id:", 1) if "\n- id:" in block[1:] else len(block)]
    assert "install_safe_hook --check" in block and "stages: [manual]" in block
