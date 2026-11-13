"""Tests for py_ci_shared.cuda_kernel_sources: every source shape the extractor reads, and the ones it must leave alone."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_kernel_sources import find_cuda_kernel_sources, module_str_constant

KERNEL = "__global__ void k(float* x) { x[threadIdx.x] = 1.0f; }"


def _corpus(tmp_path: Path, files: dict) -> Path:
    """Write *files* (relative path to text) under *tmp_path* and return it."""
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _find(root: Path, **kw):
    """The extractor on a plain directory (no git listing)."""
    return find_cuda_kernel_sources(root, use_git=False, **kw)


def _lines(*rows: str) -> str:
    """Source text of one file from its lines."""
    return "\n".join(rows) + "\n"


def test_a_plain_constant_is_found_with_its_path_and_name(tmp_path):
    """The key is the posix path relative to the root and the assigned name; the value is the literal as written."""
    root = _corpus(tmp_path, {"pkg/sub/mod.py": _lines(f'SRC = "{KERNEL}"', 'OTHER = "not a kernel"')})
    assert _find(root) == {"pkg/sub/mod.py::SRC": KERNEL}


def test_wrapped_concatenated_and_annotated_values_are_found(tmp_path):
    """``render(...)``, ``.replace(...)``, a sum of literals and an annotated assignment all give their kernel literal."""
    text = _lines(
        f'A = render("{KERNEL}", REAL="float")',
        f'B = "{KERNEL}".replace("float", "double")',
        f'C = "// head" + "{KERNEL}"',
        f'D: str = "{KERNEL}"',
    )
    assert sorted(_find(_corpus(tmp_path, {"m.py": text}))) == ["m.py::A", "m.py::B", "m.py::C", "m.py::D"]


def test_an_fstring_keeps_its_text_and_marks_each_interpolation(tmp_path):
    """The text around an interpolation stays, the interpolation becomes ``{__FSTRING__}``."""
    text = _lines("def build(t):", '    SRC = f"__global__ void k_{t}(float* x) {{ x[0] = 1; }}"', "    return SRC")
    found = _find(_corpus(tmp_path, {"m.py": text}))
    assert found["m.py::SRC"].startswith("__global__ void k_{__FSTRING__}(float* x)")
    # the constant segments of the f-string are literals of the same assignment too (``SRC#2``): the keys of a consumer's tables were built on this walk
    assert all(key.startswith("m.py::SRC") for key in found)


def test_assignments_inside_functions_and_attributes_are_found(tmp_path):
    """A source built at call time and ``self.SRC = ...`` (keyed by the attribute name) are not lost."""
    text = _lines("def make():", f'    LOCAL = "{KERNEL}"', "    return LOCAL", "", "class K:", "    def __init__(self):", f'        self.SRC = "{KERNEL}"')
    assert sorted(_find(_corpus(tmp_path, {"m.py": text}))) == ["m.py::LOCAL", "m.py::SRC"]


def test_several_kernel_literals_of_one_assignment_each_get_a_key(tmp_path):
    """A dict of kernels or a sum of sources keeps every literal: ``NAME``, ``NAME#2``, ``NAME#3``."""
    other = "__global__ void j() {}"
    third = "__global__ void i() {}"
    text = _lines(f'KERNELS = {{"a": "{KERNEL}", "b": "{other}", "c": "{third}"}}')
    assert _find(_corpus(tmp_path, {"m.py": text})) == {"m.py::KERNELS": KERNEL, "m.py::KERNELS#2": other, "m.py::KERNELS#3": third}


def test_the_same_name_twice_in_a_file_keeps_the_first_source(tmp_path):
    """``setdefault`` semantics: a later assignment of the same name does not replace the first."""
    first, second = "__global__ void a() {}", "__global__ void b() {}"
    text = _lines(f'SRC = "{first}"', f'SRC = "{second}"')
    assert _find(_corpus(tmp_path, {"m.py": text})) == {"m.py::SRC": first}


def test_negative_control_text_without_a_kernel_or_built_from_non_literals_is_not_reported(tmp_path):
    """A docstring that names ``__global__`` is not an assignment; a source assembled only from variables has no literal to read."""
    text = _lines('"""the word __global__ in a docstring"""', "PARTS = [str(x) for x in range(3)]", 'SRC = "".join(PARTS)', 'NOTE = "plain text"')
    assert _find(_corpus(tmp_path, {"m.py": text})) == {}


def test_excluded_path_parts_are_out_of_scope(tmp_path):
    """A file under ``_benchmarks`` is skipped; the same source elsewhere is found."""
    text = _lines(f'SRC = "{KERNEL}"')
    root = _corpus(tmp_path, {"pkg/_benchmarks/b.py": text, "pkg/real.py": text})
    assert sorted(_find(root, exclude=("_benchmarks",))) == ["pkg/real.py::SRC"]
    assert sorted(_find(root)) == ["pkg/_benchmarks/b.py::SRC", "pkg/real.py::SRC"]


def test_key_base_sets_the_prefix_of_the_keys(tmp_path):
    """Keys can be relative to the parent of the scanned package (``pkg/...``), as a consumer's tables expect."""
    root = _corpus(tmp_path, {"src/pkg/m.py": _lines(f'SRC = "{KERNEL}"')})
    assert sorted(_find(root / "src" / "pkg", key_base=root / "src")) == ["pkg/m.py::SRC"]


def test_a_bom_prefixed_file_is_read_like_the_plain_one(tmp_path):
    """A UTF-8 BOM in front of the file does not hide the kernel."""
    (tmp_path / "m.py").write_bytes(chr(0xFEFF).encode("utf-8") + _lines(f'SRC = "{KERNEL}"').encode())
    assert _find(tmp_path) == {"m.py::SRC": KERNEL}


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    """A syntax error is an error naming the file, never a silently kernel-free file; ``allow_unparsed`` skips it."""
    root = _corpus(tmp_path, {"good.py": _lines(f'SRC = "{KERNEL}"'), "broken.py": _lines("def f(:")})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == {"good.py::SRC": KERNEL}


def test_an_empty_corpus_fails_the_floor(tmp_path):
    """Fewer parsed files than ``min_files`` is an error: a wrong root must not read as 'no kernels'."""
    with pytest.raises(EmptyScanError):
        _find(tmp_path, min_files=1)


def test_module_str_constant_reads_plain_and_annotated_constants(tmp_path):
    """A module-level string by name, assigned plainly or with an annotation."""
    file = tmp_path / "m.py"
    file.write_text(_lines('HELPERS = "__device__ int h() { return 1; }"', 'TYPED: str = "typed"', 'USED = HELPERS + "x"'), encoding="utf-8")
    assert module_str_constant(file, "HELPERS") == "__device__ int h() { return 1; }"
    assert module_str_constant(file, "TYPED") == "typed"


@pytest.mark.parametrize("name", ["USED", "MISSING"])
def test_module_str_constant_names_the_file_and_constant_it_cannot_read(tmp_path, name):
    """A computed value and an absent name both raise ``KeyError`` with ``file::name``."""
    file = tmp_path / "m.py"
    file.write_text(_lines('HELPERS = "x"', 'USED = HELPERS + "y"'), encoding="utf-8")
    with pytest.raises(KeyError, match=name):
        module_str_constant(file, name)
