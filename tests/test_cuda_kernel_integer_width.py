"""Tests for py_ci_shared.cuda_kernel_integer_width."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_kernel_integer_width import RULE_LONG, RULE_PRODUCT, assert_cuda_kernel_integer_width, find_cuda_kernel_integer_width

BOM = b"\xef\xbb\xbf"


def _find(root, **kw):
    return find_cuda_kernel_integer_width(root, use_git=False, **kw)


def _assert(root, **kw):
    return assert_cuda_kernel_integer_width(root, use_git=False, **kw)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _mod(kernel_body: str, *, name: str = "K_SRC") -> bytes:
    return f'{name} = """\n__global__ void k(const float* X, int n) {{\n{kernel_body}\n}}\n"""\n'.encode()


def _found(tmp_path: Path, body: str, **kw):
    return _find(_corpus(tmp_path, {"m.py": _mod(body, **kw)}))


def test_reports_the_seeded_violation(tmp_path):
    """`(long)i * n` in a kernel constant is reported at the line of the `long`, with the rule and the fragment."""
    found = _found(tmp_path, "    int i = 0;\n    const float* row = X + (long)i * n;")
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 4, RULE_LONG)]
    assert "(long)i * n" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    """`long long`, `unsigned long long`, `long double`, a comment, a literal product, the grid idiom and a 64-bit cast."""
    body = "\n".join(
        [
            "    long long a = (long long)n * 2;",
            "    unsigned long long b = 0;",
            "    long double c = 1.0;",
            "    int d = 1; // a long comment",
            "    /* long block */",
            "    int e = 4 * 8;",
            "    int i = blockIdx.x * blockDim.x + threadIdx.x;",
            "    int f = (int)((long long)n * n);",
        ]
    )
    assert _found(tmp_path, body) == []


def test_unsigned_long_and_a_bare_declaration_are_reported(tmp_path):
    """`unsigned long` is as narrow as `long`; so is a plain `long off`."""
    found = _found(tmp_path, "    unsigned long a = 0;\n    long off = 1;")
    assert [(f.line, f.rule) for f in found] == [(3, RULE_LONG), (4, RULE_LONG)]


def test_a_long_inside_a_longer_identifier_is_not_a_type(tmp_path):
    assert _found(tmp_path, "    int longest = 1; int along = 2; int long_name = 3;") == []


def test_the_int_product_of_two_sizes_is_advisory_and_reported(tmp_path):
    found = _found(tmp_path, "    int idx = n_rows * n_cols;")
    assert [(f.line, f.rule) for f in found] == [(3, RULE_PRODUCT)]
    assert "int idx" in found[0].message


@pytest.mark.parametrize("expr", ["2 * n", "n * 4", "n * 2.0f", "(long long)a * b", "blockIdx.x * blockDim.x", "a + b"])
def test_an_int_product_with_a_literal_a_wide_cast_or_the_grid_idiom_is_not_reported(tmp_path, expr):
    assert _found(tmp_path, f"    int idx = {expr};") == []


@pytest.mark.parametrize("expr", ["a * b", "(a + 1) * b", "rows[i] * c", "a * (b + 1)", "x.y * z.w", "blockIdx.x * n"])
def test_an_int_product_of_two_non_literal_operands_is_reported(tmp_path, expr):
    assert [f.rule for f in _found(tmp_path, f"    int idx = {expr};")] == [RULE_PRODUCT]


def test_only_index_named_variables_and_products_outside_subscripts_are_reported(tmp_path):
    body = "\n".join(
        [
            "    int joint_size = nbx * nby;",
            "    int nid = topk[q * K + t];",
            "    int off = rows[i] * stride_n;",
            "    int a_pos = 3, b_row = r * c, tile = lanes * warps;",
        ]
    )
    found = _found(tmp_path, body)
    assert [(f.line, f.rule) for f in found] == [(5, RULE_PRODUCT), (6, RULE_PRODUCT)]
    assert "int off = rows[i] * stride_n" in found[0].message and "int b_row = r * c" in found[1].message


def test_a_dereference_or_pointer_declaration_is_not_a_product(tmp_path):
    assert _found(tmp_path, "    int v = *p;\n    int w = (*p) + 1;\n    int q = a ** 2;") == []


def test_cupy_kernel_classes_are_scanned_whatever_the_constant_is_called(tmp_path):
    """A source passed to `cupy.RawKernel` counts, by name or inline, through an import alias; so does ElementwiseKernel's body."""
    src = (
        "import cupy as cp\nfrom cupy import ElementwiseKernel as EK\n"
        'SOURCE_TEXT = "__global__ void a(long n) {}"\n'
        'k1 = cp.RawKernel(SOURCE_TEXT, "a")\n'
        'k2 = cp.RawModule(code="__global__ void b(long n) {}")\n'
        'k3 = EK("float32 x", "float32 y", "long off = i;", "k3")\n'
    )
    found = _find(_corpus(tmp_path, {"m.py": src.encode()}))
    assert sorted((f.line, f.rule) for f in found) == [(3, RULE_LONG), (5, RULE_LONG), (6, RULE_LONG)]


def test_a_non_cupy_call_or_an_unnamed_string_without_global_is_not_kernel_source(tmp_path):
    src = 'import other\nnote = "the long road"\nk = other.RawKernel("long x;", "a")\nSTMT = "long text"\nBODY_CODE = "no kernel here: long"\n'
    assert _find(_corpus(tmp_path, {"m.py": src.encode()})) == []


def test_a_string_with_global_is_kernel_source_under_any_name_and_a_docstring_is_not(tmp_path):
    src = 'def f():\n    """Mentions __global__ and long."""\n    return "__global__ void a(long n) {}"\n'
    assert [(f.line, f.rule) for f in _find(_corpus(tmp_path, {"m.py": src.encode()}))] == [(3, RULE_LONG)]


def test_fstring_and_concatenated_sources_are_scanned(tmp_path):
    src = (
        'K_SRC = f"""__global__ void k({"{"}dtype{"}"} * x) {{\n    long i = 0;\n}}"""\n'
        'A_CODE = "__global__ void a() {\\n" + "  long j = 0;\\n" + "}"\n'
        'B_CODE = ("__global__ void b() {"\n    "  unsigned long j = 0;"\n    "}")\n'
    )
    found = _find(_corpus(tmp_path, {"m.py": src.encode()}))
    assert [(f.line, f.rule) for f in found] == [(2, RULE_LONG), (5, RULE_LONG), (5, RULE_LONG)]


def test_a_c_comment_mentioning_long_keeps_line_numbers_exact(tmp_path):
    found = _found(tmp_path, "    /* a long\n       comment */\n    long x = 0;")
    assert [(f.line, f.rule) for f in found] == [(5, RULE_LONG)]


def test_the_in_kernel_marker_on_the_line_or_the_line_above_suppresses_it(tmp_path):
    body = "    long a = 0; // width-ok: warp lane id, never above 31\n    // width-ok: bounded by the grid\n    long b = 0;\n    long c = 0;"
    assert [(f.line) for f in _found(tmp_path, body)] == [6]


def test_the_python_marker_on_the_statement_suppresses_it(tmp_path):
    first = b'K_SRC = """__global__ void k() { long a = 0; }"""  # width-ok: 32-bit by design\n'
    last = b'K_SRC = """__global__ void k() {\n long a = 0;\n}"""  # width-ok: 32-bit by design\n'
    other = b'K_SRC = """__global__ void k() {\n long a = 0;\n}"""  # width-ok without colon\n'
    assert _find(_corpus(tmp_path / "a", {"m.py": first})) == []
    assert _find(_corpus(tmp_path / "b", {"m.py": last})) == []
    assert len(_find(_corpus(tmp_path / "c", {"m.py": other}))) == 1


def test_a_marker_covers_only_its_own_line(tmp_path):
    body = "    long a = 0; // width-ok: a\n    int gap = 0;\n    long b = 0;"
    assert [f.line for f in _found(tmp_path, body)] == [5]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _mod("    long a = 0;")
    a = _find(_corpus(tmp_path / "a", {"m.py": plain}))
    b = _find(_corpus(tmp_path / "b", {"m.py": BOM + plain}))
    assert [(f.path, f.line, f.rule, f.message) for f in a] == [(f.path, f.line, f.rule, f.message) for f in b] != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": _mod("    long a = 0;")})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        _find(root)
    assert len(_find(root, allow_unparsed=True)) == 1


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        _find(tmp_path)
    root = _corpus(tmp_path, {"a.py": b"x = 1\n"})
    _find(root, min_files=1)
    with pytest.raises(EmptyScanError):
        _find(root, min_files=2)


def test_the_assert_fails_on_long_but_not_on_the_advisory_unless_asked(tmp_path):
    advisory = _corpus(tmp_path / "adv", {"m.py": _mod("    int idx = a * b;")})
    _assert(advisory)
    with pytest.raises(AssertionError, match="cuda-int-index-product"):
        _assert(advisory, include_advisory=True)
    hard = _corpus(tmp_path / "hard", {"m.py": _mod("    long a = 0;")})
    with pytest.raises(AssertionError, match=r"m\.py:3: \[cuda-platform-long\]"):
        _assert(hard)


def test_the_assert_honours_a_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": _mod("    long a = 0;")})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):  # a refresh writes the baseline and skips the test that asked for it
        _assert(root, baseline_path=baseline, refresh=True)
    _assert(root, baseline_path=baseline)
    (root / "n.py").write_bytes(_mod("    long b = 0;"))
    with pytest.raises(pytest.fail.Exception):
        _assert(root, baseline_path=baseline)
