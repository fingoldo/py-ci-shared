"""Tests for py_ci_shared.unread_function_params."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.unread_function_params import NAMED_PARAMS, RULE, assert_unread_function_params, find_unread_function_params

BOM = b"\xef\xbb\xbf"
VIOLATION = "def fit(X, y, sample_weight=None):\n    return X + y\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _found(tmp_path: Path, source: str) -> list[str]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.message for f in find_unread_function_params(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_unread_function_params(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 1, RULE)
    assert finding.message == "fit: parameter 'sample_weight' is accepted but never read"
    with pytest.raises(AssertionError, match=r"m\.py:1"):
        assert_unread_function_params(root, use_git=False)


@pytest.mark.parametrize("name", sorted(NAMED_PARAMS))
def test_every_named_parameter_is_covered_and_reading_it_clears_it(tmp_path, name):
    assert _found(tmp_path / "a", f"def f(x, {name}=None):\n    return x\n") == [f"f: parameter '{name}' is accepted but never read"]
    assert _found(tmp_path / "b", f"def f(x, {name}=None):\n    return x, {name}\n") == []


def test_only_the_named_parameters_are_checked(tmp_path):
    assert _found(tmp_path, "def f(x, y, threshold=0.5, _seed=1):\n    return x\n") == []


def test_forwarding_validating_and_augmenting_all_count_as_reads(tmp_path):
    assert _found(tmp_path / "a", "def f(x, seed):\n    return g(x, seed=seed)\n") == []
    assert _found(tmp_path / "b", "def f(x, random_state):\n    random_state = check(random_state)\n    return x\n") == []
    assert _found(tmp_path / "c", "def f(x, seed):\n    seed += 1\n    return x\n") == []
    assert _found(tmp_path / "d", "def f(x, verbose):\n    return f'{verbose}' + x\n") == []
    assert _found(tmp_path / "e", "def f(x, seed):\n    def inner():\n        return seed\n    return inner\n") == []


def test_assigning_to_the_parameter_without_reading_it_is_not_a_read(tmp_path):
    assert len(_found(tmp_path, "def f(x, seed=None):\n    seed = 7\n    return x\n")) == 1


def test_locals_and_vars_count_as_reading_everything(tmp_path):
    assert _found(tmp_path, "def f(x, seed=None, verbose=0):\n    return dict(locals())\n") == []


def test_kwonly_and_posonly_and_async_and_methods_are_covered(tmp_path):
    src = "class C:\n    def m(self, *, n_jobs=1):\n        return 1\n\n    async def a(self, timeout, /):\n        return 2\n"
    assert _found(tmp_path, src) == ["C.m: parameter 'n_jobs' is accepted but never read", "C.a: parameter 'timeout' is accepted but never read"]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("def f(x, seed=None):\n    raise NotImplementedError\n", id="not-implemented"),
        pytest.param("def f(x, seed=None):\n    raise NotImplementedError('subclass')\n", id="not-implemented-with-message"),
        pytest.param("def f(x, seed=None):\n    pass\n", id="pass"),
        pytest.param("def f(x, seed=None):\n    ...\n", id="ellipsis"),
        pytest.param('def f(x, seed=None):\n    """Docstring only."""\n', id="docstring-only"),
        pytest.param("import abc\n\n\nclass A(abc.ABC):\n    @abc.abstractmethod\n    def f(self, seed=None):\n        return 1\n", id="abstractmethod"),
        pytest.param("from typing import overload\n\n\n@overload\ndef f(x, seed: int) -> int: ...\n", id="overload"),
        pytest.param("from typing import Protocol\n\n\nclass P(Protocol):\n    def f(self, seed=None):\n        return 1\n", id="protocol-method"),
        pytest.param(
            "from typing_extensions import override\n\n\nclass B(A):\n    @override\n    def f(self, seed=None):\n        return 1\n", id="override-decorator"
        ),
        pytest.param("class B(A):\n    def f(self, seed=None):\n        return super().f()\n", id="super-call"),
        pytest.param("class B:\n    def __init__(self, seed=None):\n        self.x = 1\n", id="init-is-the-siblings-job"),
    ],
)
def test_negative_control_stubs_overrides_and_interfaces_are_not_reported(tmp_path, src):
    assert _found(tmp_path, src) == []


def test_a_raise_other_than_not_implemented_is_not_a_stub(tmp_path):
    assert len(_found(tmp_path, "def f(x, seed=None):\n    raise ValueError('no')\n")) == 1


def test_the_nested_function_is_judged_by_its_own_parameters(tmp_path):
    src = "def outer(x, seed):\n    def inner(y, seed):\n        return y\n    return inner(x, seed)\n"
    assert _found(tmp_path, src) == ["outer.<locals>.inner: parameter 'seed' is accepted but never read"]


def test_the_suppression_comment_on_the_def_header_silences_it(tmp_path):
    assert _found(tmp_path / "a", "def f(x, seed=None):  # unused-ok: callback signature fixed by the framework\n    return x\n") == []
    assert _found(tmp_path / "b", "def f(\n    x,\n    seed=None,  # unused-ok: fixed signature\n):\n    return x\n") == []
    assert len(_found(tmp_path / "c", "def f(x, seed=None):\n    return x  # unused-ok: this is the body, not the header\n")) == 1


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"m.py": VIOLATION.encode()})
    bom = _corpus(tmp_path / "bom", {"m.py": BOM + VIOLATION.encode()})
    assert find_unread_function_params(bom, use_git=False) == find_unread_function_params(plain, use_git=False) != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_unread_function_params(root, use_git=False)
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        assert_unread_function_params(root, use_git=False)
    assert find_unread_function_params(root, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_unread_function_params(tmp_path, use_git=False)
    root = _corpus(tmp_path / "one", {"a.py": b"x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_unread_function_params(root, min_files=2, use_git=False)


def test_exclude_skips_paths_by_fragment(tmp_path):
    root = _corpus(tmp_path, {"gen/m.py": VIOLATION.encode(), "lib/m.py": VIOLATION.encode()})
    assert [f.path for f in find_unread_function_params(root, use_git=False, exclude=("gen/",))] == ["lib/m.py"]
