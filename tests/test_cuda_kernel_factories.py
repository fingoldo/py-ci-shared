"""Tests for py_ci_shared.cuda_kernel_factories."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_kernel_factories import RULE, assert_cuda_kernel_factories, find_cuda_kernel_factories

BOM = b"\xef\xbb\xbf"

FACTORY = b"def kernel(cp, real):\n    return cp.RawKernel('', 'k')\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _find(root, **kw):
    return find_cuda_kernel_factories(root, use_git=False, **kw)


def test_reports_the_seeded_violation(tmp_path):
    """A ``kernel(cp, real)`` factory that the registry does not list is reported at its ``def`` line, with the rule and the function name."""
    root = _corpus(tmp_path, {"pkg/lof.py": b"import numpy as np\n\n\n" + FACTORY})
    found = _find(root)
    assert [(f.path, f.line, f.rule) for f in found] == [("pkg/lof.py", 4, RULE)]
    assert "kernel(cp, real" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """Other names, other first parameters, a method and a nested function are not factories; a factory the registry lists is not reported."""
    body = (
        b"def kernel(x, y):\n    return x\n\n\n"
        b"def kernel_size(cp):\n    return 1\n\n\n"
        b"def other(cp, real):\n    return cp\n\n\n"
        b"class A:\n    def kernel(self, cp, real):\n        return 1\n\n\n"
        b"def outer():\n    def kernel(cp, real):\n        return 1\n    return kernel\n"
    )
    assert _find(_corpus(tmp_path / "a", {"pkg/m.py": body})) == []
    listed = _corpus(tmp_path / "b", {"pkg/lof.py": FACTORY})
    assert _find(listed, registered={("pkg.lof", "kernel")}) == []


def test_a_registered_module_may_carry_the_package_prefix_the_root_lacks(tmp_path):
    """The registry names ``mlframe.sub.mod`` while the scan root is the package itself: the file ``sub/mod.py`` matches by its tail, and only for the registered function name."""
    root = _corpus(tmp_path, {"sub/mod.py": FACTORY + b"\n\ndef kernel_rows(cp, real):\n    return 1\n"})
    assert [f.message.split("(")[0] for f in _find(root, registered={("mlframe.sub.mod", "kernel")})] == ["the kernel factory kernel_rows"]
    assert _find(root, registered={("mlframe.sub.mod", "kernel"), ("mlframe.sub.mod", "kernel_rows")}) == []
    assert len(_find(root, registered={("mlframe.other.mod", "kernel"), ("mlframe.other.mod", "kernel_rows")})) == 2


def test_a_package_init_is_matched_by_its_package_name(tmp_path):
    """``pkg/__init__.py`` is the module ``pkg``."""
    root = _corpus(tmp_path, {"pkg/__init__.py": FACTORY})
    assert _find(root, registered={("pkg", "kernel")}) == []
    assert len(_find(root)) == 1


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    """The BOM must not hide the factory."""
    plain = _find(_corpus(tmp_path / "a", {"m.py": FACTORY}))
    bom = _find(_corpus(tmp_path / "b", {"m.py": BOM + FACTORY}))
    assert [(f.line, f.rule) for f in bom] == [(f.line, f.rule) for f in plain] == [(1, RULE)]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    """A file with a syntax error raises ``UnparsedFilesError`` naming it; ``allow_unparsed=True`` skips it."""
    root = _corpus(tmp_path, {"good.py": b"x = 1\n", "broken.py": b"def kernel(cp, real:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    """A root with no Python files raises ``EmptyScanError``."""
    with pytest.raises(EmptyScanError):
        _find(_corpus(tmp_path, {"notes.txt": b"x"}), min_files=1)


def test_the_assert_names_every_unregistered_factory(tmp_path):
    """``assert_cuda_kernel_factories`` raises with the findings and passes once they are registered."""
    root = _corpus(tmp_path, {"pkg/lof.py": FACTORY})
    with pytest.raises(AssertionError, match=r"unregistered kernel factory"):
        assert_cuda_kernel_factories(root, use_git=False)
    assert_cuda_kernel_factories(root, registered={("pkg.lof", "kernel")}, use_git=False)
