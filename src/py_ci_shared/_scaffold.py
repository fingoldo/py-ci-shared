"""``py-ci-shared new-gate``: lay out a new gate (or library) in a py-ci-shared checkout the way every gate is laid out.

It writes ``src/py_ci_shared/<name>.py`` (built on ``_core``: ``scan_python``, ``Finding``, ``Baseline``,
``ImportAliases``, a ``find_*``/``assert_*`` pair with a ``min_files`` floor and ``allow_unparsed``),
``tests/test_<name>.py`` (seeded violation, negative control, BOM, unparsable and empty-corpus tests),
``tests/canary/<name>/{violation,clean,bom,unparsable}/`` with a ``CANARIES`` entry in ``tests/test_gate_teeth.py``,
the ``registry.toml`` entry (``since`` = the version in ``pyproject.toml``) and the README catalogue. Every generated
test fails until it is written for real, and so does the canary, so an unfinished gate cannot pass CI. Nothing that
exists is overwritten: the command checks every target first and changes nothing when one exists.
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass
from pathlib import Path

from ._toml_compat import tomllib

__all__ = ["KINDS", "ScaffoldError", "Scaffold", "find_checkout", "new_gate"]

KINDS = ("gate", "library")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_BOM = b"\xef\xbb\xbf"
_CANARIES_END = "    ]\n}\n\n# Registered gates and libraries that are not corpus scanners"
_PLACEHOLDER = b"# TODO: replace with the minimal code the gate must report; make clean/ the nearest correct code.\nx = 1\n"


class ScaffoldError(Exception):
    """A usage error: bad name, not a py-ci-shared checkout, or a target that exists."""


@dataclass(frozen=True)
class Scaffold:
    """What :func:`new_gate` wrote, relative to the checkout root."""

    created: tuple[str, ...]
    updated: tuple[str, ...]


def find_checkout(start: Path) -> Path:
    """The nearest directory at or above *start* holding ``src/py_ci_shared/registry.toml``."""
    for d in (start.resolve(), *start.resolve().parents):
        if (d / "src" / "py_ci_shared" / "registry.toml").is_file():
            return d
    raise ScaffoldError(f"no py-ci-shared checkout (src/py_ci_shared/registry.toml) at or above {start}; pass --repo")


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _write(path: Path, text: str, *, crlf: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def _crlf(path: Path) -> bool:
    """Keep an edited file's line endings (a Windows checkout with autocrlf holds CRLF)."""
    return b"\r\n" in path.read_bytes()


def _toml_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


_MODULE = '''"""{summary}

TODO: say what defect this reports, the shape it matches, and the nearest correct code it must NOT report.

Usage in a consumer's meta test::

    from py_ci_shared.{name} import {entry}

    def test_{name}():
        {entry}("src")
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = [{exports}]

RULE = "{rule}"


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases) -> list[Finding]:
    """The findings in one parsed file. ``aliases.qualified_name(node)`` resolves ``np.x`` to ``numpy.x``."""
    raise NotImplementedError("{name}: write the matcher, then the canary and tests/test_{name}.py")


def find_{name}(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree)))
    return sorted(out, key=lambda f: (f.path, f.line))
'''

_ASSERT = '''

def {entry}(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_{name}(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "TODO: one line saying how to fix the code"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="{name}", refresh_command="PY_CI_SHARED_REFRESH={name}")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{{len(found)}} {rule} finding(s); {{guidance}}:\\n  " + "\\n  ".join(f.render() for f in found))
'''

_TESTS = '''"""Tests for py_ci_shared.{name}. Each one fails until it is written: an unfinished gate must not pass CI."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.{name} import {imports}

BOM = b"\\xef\\xbb\\xbf"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def test_reports_the_seeded_violation(tmp_path):
    pytest.fail("TODO: seed the minimal defect and assert the exact finding (path, line, rule, message)")


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    pytest.fail("TODO: the closest correct shape, which must produce no finding")


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    pytest.fail("TODO: the violation with BOM in front; same findings as without it")


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    pytest.fail("TODO: a file with a syntax error raises UnparsedFilesError naming it; allow_unparsed=True skips it")


def test_an_empty_corpus_fails_the_floor(tmp_path):
    pytest.fail("TODO: a root with no Python files raises EmptyScanError (min_files)")
'''


def _module_text(name: str, kind: str, summary: str) -> str:
    entry = f"assert_{name}"
    exports = ['"RULE"', f'"find_{name}"'] + ([f'"{entry}"'] if kind == "gate" else [])
    fmt = {"name": name, "entry": entry if kind == "gate" else f"find_{name}", "summary": summary, "rule": name.replace("_", "-")}
    text = _MODULE.format(exports=", ".join(sorted(exports)), **fmt)
    if kind == "gate":
        text += _ASSERT.format(**fmt)
    else:
        text = text.replace("from ._core import Baseline, Finding,", "from ._core import Finding,")
    return text


def _canary_line(name: str, kind: str) -> str:
    if kind == "gate":
        return f'        Canary("{name}", lambda d: _gate("{name}").assert_{name}(d, use_git=False)),\n'
    return f'        Canary("{name}", lambda d: _assert_empty(_gate("{name}").find_{name}(d, use_git=False))),\n'


def _registry_block(name: str, kind: str, since: str, summary: str) -> str:
    lines = ["[[gate]]", f"name = {_toml_str(name)}", f"kind = {_toml_str(kind)}"]
    if kind == "gate":
        lines.append(f'entries = ["assert_{name}"]')
    lines += [f"since = {_toml_str(since)}", f"summary = {_toml_str(summary)}"]
    return "\n".join(lines) + "\n"


def _insert_registry(text: str, block: str, name: str) -> str:
    """*block* in name order among the ``[[gate]]`` tables of *text*."""
    head, *tables = text.split("\n[[gate]]\n")
    names = [re.search(r'^name = "([^"]+)"', t, re.MULTILINE) for t in tables]
    at = next((i for i, m in enumerate(names) if m and m.group(1) > name), len(tables))
    tables.insert(at, block[len("[[gate]]\n") :])  # every table ends in one newline; the separator holds the blank line
    return "\n[[gate]]\n".join([head, *tables])


def _version(root: Path) -> str:
    data = tomllib.loads(_read(root / "pyproject.toml"))
    return str(data["project"]["version"])


def _targets(root: Path, name: str) -> list[Path]:
    canary = root / "tests" / "canary" / name
    return [
        root / "src" / "py_ci_shared" / f"{name}.py",
        root / "tests" / f"test_{name}.py",
        canary / "violation" / "seed.py.canary",
        canary / "clean" / "seed.py.canary",
        canary / "bom" / "seed.py.canary",
        canary / "unparsable" / "broken.py.canary",
    ]


def _check(root: Path, name: str, kind: str, summary: str) -> None:
    if not _NAME.match(name) or keyword.iskeyword(name) or name.startswith("_"):
        raise ScaffoldError(f"{name!r} is not a public module name (lowercase letters, digits and underscores)")
    if kind not in KINDS:
        raise ScaffoldError(f"kind must be one of {KINDS}, got {kind!r}")
    if not summary.strip() or "\n" in summary:
        raise ScaffoldError("--summary must be one non-empty line")
    registry_text = _read(root / "src" / "py_ci_shared" / "registry.toml")
    if re.search(rf'^name = "{re.escape(name)}"$', registry_text, re.MULTILINE):
        raise ScaffoldError(f"{name} is already in registry.toml")
    existing = [p for p in _targets(root, name) if p.exists()] + [p for p in [root / "tests" / "canary" / name] if p.exists()]
    if existing:
        raise ScaffoldError("refusing to overwrite: " + ", ".join(p.relative_to(root).as_posix() for p in existing))
    if _CANARIES_END not in _read(root / "tests" / "test_gate_teeth.py"):
        raise ScaffoldError("tests/test_gate_teeth.py has no CANARIES list end to add the entry before")


def _render_readme(root: Path, registry_path: Path) -> str:
    from . import registry

    readme = _read(root / "README.md")
    start, end = readme.find(registry.CATALOGUE_START), readme.find(registry.CATALOGUE_END)
    if start == -1 or end == -1:
        raise ScaffoldError("README.md has no gate catalogue markers")
    block = registry.render_catalogue(gates=registry.load_gates(registry_path))
    return readme[:start] + block + readme[end + len(registry.CATALOGUE_END) :]


def new_gate(root: Path, name: str, *, kind: str = "gate", summary: str = "") -> Scaffold:
    """Scaffold *name* in the checkout at *root*; see the module docstring. Raises :class:`ScaffoldError`."""
    summary = summary or f"TODO: one line saying what {name} checks"
    _check(root, name, kind, summary)
    module, tests, violation, clean, bom, broken = _targets(root, name)
    _write(module, _module_text(name, kind, summary))
    imports = ", ".join(sorted([f"find_{name}"] + ([f"assert_{name}"] if kind == "gate" else [])))
    _write(tests, _TESTS.format(name=name, imports=imports))
    for path in (violation, clean):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_PLACEHOLDER)
    bom.parent.mkdir(parents=True, exist_ok=True)
    bom.write_bytes(_BOM + _PLACEHOLDER)
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"def broken(:\n")
    registry_path = root / "src" / "py_ci_shared" / "registry.toml"
    block = _registry_block(name, kind, _version(root), summary)
    _write(registry_path, _insert_registry(_read(registry_path), block, name), crlf=_crlf(registry_path))
    teeth = root / "tests" / "test_gate_teeth.py"
    _write(teeth, _read(teeth).replace(_CANARIES_END, _canary_line(name, kind) + _CANARIES_END, 1), crlf=_crlf(teeth))
    readme = root / "README.md"
    _write(readme, _render_readme(root, registry_path), crlf=_crlf(readme))
    rel = [p.relative_to(root).as_posix() for p in (module, tests, violation, clean, bom, broken)]
    return Scaffold(created=tuple(rel), updated=("src/py_ci_shared/registry.toml", "tests/test_gate_teeth.py", "README.md"))
