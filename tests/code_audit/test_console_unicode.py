"""Scanner tests for console_unicode, split verbatim out of the former tests/test_code_audit.py."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.code_audit import (
    scan_console_unicode,
)

from ._helpers import _write

# ---- console_unicode --------------------------------------------------------


def test_console_unicode_print_flagged(tmp_path: Path):
    _write(tmp_path, "bad.py", 'print("done → next")\n')
    findings = scan_console_unicode(tmp_path)
    assert len(findings) == 1, findings
    assert findings[0].check == "console_unicode"


def test_console_unicode_logger_call_flagged(tmp_path: Path):
    _write(tmp_path, "bad.py", 'logger.warning("bad ✓ value")\n')
    findings = scan_console_unicode(tmp_path)
    assert len(findings) == 1, findings


def test_console_unicode_ascii_only_not_flagged(tmp_path: Path):
    _write(tmp_path, "ok.py", 'print("done -> next")\n')
    assert scan_console_unicode(tmp_path) == []


def test_console_unicode_non_console_call_not_flagged(tmp_path: Path):
    _write(tmp_path, "ok.py", 'save_to_file("→")\n')
    assert scan_console_unicode(tmp_path) == []


def test_console_unicode_stdout_reconfigure_suppresses_file(tmp_path: Path):
    """A file that already forces UTF-8 stdio at its own entry point can't hit the
    UnicodeEncodeError this scanner exists to catch -- confirmed as this codebase's own
    established fix (dozens of scripts use exactly this idiom)."""
    _write(
        tmp_path,
        "ok.py",
        'import sys\nsys.stdout.reconfigure(encoding="utf-8", errors="replace")\nprint("done → next")\n',
    )
    assert scan_console_unicode(tmp_path) == []


def test_console_unicode_stderr_reconfigure_suppresses_file(tmp_path: Path):
    _write(
        tmp_path,
        "ok.py",
        'import sys\nsys.stderr.reconfigure(encoding="utf-8")\nlogger.warning("bad ✓ value")\n',
    )
    assert scan_console_unicode(tmp_path) == []


def test_console_unicode_reconfigure_without_encoding_kwarg_does_not_suppress(tmp_path: Path):
    """A bare ``sys.stdout.reconfigure()`` (no ``encoding=``, e.g. line-buffering tweaks) doesn't
    change the console encoding -- must not be mistaken for the UTF-8 fix."""
    _write(
        tmp_path,
        "bad.py",
        'import sys\nsys.stdout.reconfigure(line_buffering=True)\nprint("done → next")\n',
    )
    findings = scan_console_unicode(tmp_path)
    assert len(findings) == 1, findings


def test_console_unicode_package_init_reconfigure_suppresses_submodule(tmp_path: Path):
    """A package's own __init__.py reconfiguring stdio protects every module beneath it --
    the guard fires on the package's FIRST import regardless of which submodule/entry point
    actually runs, so a submodule needs no guard of its own."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    _write(pkg, "__init__.py", 'import sys\nsys.stdout.reconfigure(encoding="utf-8")\n')
    _write(pkg, "sub.py", 'def f():\n    print("done → next")\n')
    assert scan_console_unicode(tmp_path) == []


def test_console_unicode_nested_package_without_init_reconfigure_still_flagged(tmp_path: Path):
    """A sibling package with NO reconfiguring __init__.py anywhere in its own chain must still
    be flagged -- the exemption only follows the actual package containment chain, not the
    whole scanned tree."""
    protected = tmp_path / "protected"
    protected.mkdir()
    _write(protected, "__init__.py", 'import sys\nsys.stdout.reconfigure(encoding="utf-8")\n')
    unprotected = tmp_path / "unprotected"
    unprotected.mkdir()
    _write(unprotected, "__init__.py", '"""No reconfigure here."""\n')
    _write(unprotected, "sub.py", 'def f():\n    print("done → next")\n')
    findings = scan_console_unicode(tmp_path)
    assert len(findings) == 1 and "unprotected/sub.py" in findings[0].file, findings


# ---- 40-T6: the reconfigure exemption is structural; print argument forms widened ----


def _flagged_count(tmp_path: Path, body: str) -> int:
    _write(tmp_path, "m.py", body)
    return len(scan_console_unicode(tmp_path))


def test_40_t6_commented_out_reconfigure_does_not_exempt(tmp_path: Path):
    assert _flagged_count(tmp_path, '# sys.stdout.reconfigure(encoding="utf-8")\nprint("done → next")\n') == 1


def test_40_t6_reconfigure_inside_a_string_does_not_exempt(tmp_path: Path):
    assert _flagged_count(tmp_path, 'X = "sys.stdout.reconfigure(encoding)"\nprint("done → next")\n') == 1


def test_40_t6_stderr_reconfigure_does_not_exempt_stdout_print(tmp_path: Path):
    body = 'import sys\nsys.stderr.reconfigure(encoding="utf-8")\nprint("done → next")\n'
    assert _flagged_count(tmp_path, body) == 1


def test_40_t6_never_called_function_reconfigure_does_not_exempt(tmp_path: Path):
    body = 'import sys\ndef unused():\n    sys.stdout.reconfigure(encoding="utf-8")\nprint("done → next")\n'
    assert _flagged_count(tmp_path, body) == 1


def test_40_t6_called_function_reconfigure_exempts(tmp_path: Path):
    body = 'import sys\ndef main():\n    sys.stdout.reconfigure(encoding="utf-8")\nmain()\nprint("done → next")\n'
    assert _flagged_count(tmp_path, body) == 0


def test_40_t6_widened_print_forms_are_flagged(tmp_path: Path):
    forms = [
        "print('→ ' + x)",
        "print('→ %s' % x)",
        "print('→ {}'.format(x))",
        "print(a, sep=' → ')",
        "print(a, end='→')",
        "import sys\nsys.stdout.write('→')",
        "import click\nclick.echo('→')",
        "from rich.console import Console\nConsole().print('→')",
    ]
    for form in forms:
        assert _flagged_count(tmp_path, f"x = a = 1\n{form}\n") == 1, form


def test_40_t6_print_to_stderr_needs_stderr_reconfigure(tmp_path: Path):
    body = 'import sys\nsys.stdout.reconfigure(encoding="utf-8")\nprint("done → next", file=sys.stderr)\n'
    assert _flagged_count(tmp_path, body) == 1
