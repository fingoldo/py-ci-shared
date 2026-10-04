"""Tests for py_ci_shared.unsafe_deserialization."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.unsafe_deserialization import RULE, assert_unsafe_deserialization, find_unsafe_deserialization

BOM = b"\xef\xbb\xbf"
VIOLATION = "import torch\n\n\ndef load(path):\n    return torch.load(path)\n"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _found(tmp_path: Path, source: str) -> list[str]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.message for f in find_unsafe_deserialization(root, use_git=False)]


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_unsafe_deserialization(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 5, RULE)
    assert "weights_only=True" in finding.message
    with pytest.raises(AssertionError, match=r"m\.py:5"):
        assert_unsafe_deserialization(root, use_git=False)


WIDE = (
    "import importlib\nimport pickle\n\n\nclass U(pickle.Unpickler):\n    def find_class(self, module, name):\n"
    '        if module.startswith("sklearn."):\n            return getattr(importlib.import_module(module), name)\n'
    "        raise pickle.UnpicklingError(name)\n"
)
PER_NAME = (
    "import pickle\n\nALLOWED = {('numpy', 'ndarray'), ('builtins', 'set')}\n\n\nclass U(pickle.Unpickler):\n    def find_class(self, module, name):\n"
    "        if (module, name) in ALLOWED:\n            return super().find_class(module, name)\n        raise pickle.UnpicklingError(name)\n"
)


def test_a_whole_module_allowlist_in_find_class_is_reported(tmp_path):
    assert _found(tmp_path, WIDE) == ["U: find_class admits whole modules (a test on the module alone, no per-name allowlist)"]


def test_a_per_name_allowlist_is_clean_even_with_a_harmless_builtin(tmp_path):
    assert _found(tmp_path, PER_NAME) == []


@pytest.mark.parametrize(
    "allowed, fragment",
    [
        pytest.param("{('builtins', 'eval')}", "builtins.eval", id="builtins-eval-pair"),
        pytest.param("{('os', 'system')}", "os.system", id="os-system-pair"),
        pytest.param("{('functools', 'partial')}", "functools.partial", id="functools-partial"),
        pytest.param("{'subprocess'}", "subprocess", id="module-alone"),
    ],
)
def test_a_gadget_module_in_the_allowlist_is_reported(tmp_path, allowed, fragment):
    src = PER_NAME.replace("{('numpy', 'ndarray'), ('builtins', 'set')}", allowed)
    messages = _found(tmp_path, src)
    assert len(messages) == 1 and fragment in messages[0] and "gadget" in messages[0]


def test_a_deny_list_that_raises_on_gadgets_is_not_an_admission(tmp_path):
    src = PER_NAME.replace(
        "        if (module, name) in ALLOWED:",
        '        if module == "os":\n            raise pickle.UnpicklingError(name)\n        if (module, name) in ALLOWED:',
    )
    assert _found(tmp_path, src) == []


def test_a_deny_list_with_no_allowlist_is_still_whole_module_admission(tmp_path):
    src = (
        "import importlib\nimport pickle\n\n\nclass U(pickle.Unpickler):\n    def find_class(self, module, name):\n"
        '        if module in ("os", "subprocess"):\n            raise pickle.UnpicklingError(name)\n        return getattr(importlib.import_module(module), name)\n'
    )
    assert _found(tmp_path, src) == ["U: find_class admits whole modules (a test on the module alone, no per-name allowlist)"]


def test_a_find_class_that_always_refuses_is_clean(tmp_path):
    src = "import pickle\n\n\nclass U(pickle.Unpickler):\n    def find_class(self, module, name):\n        raise pickle.UnpicklingError(name)\n"
    assert _found(tmp_path, src) == []


def test_torch_load_needs_weights_only_true(tmp_path):
    base = "import torch\n\n\ndef f(p, flag):\n    return torch.load(p%s)\n"
    assert len(_found(tmp_path / "a", base % "")) == 1
    assert len(_found(tmp_path / "b", base % ", weights_only=False")) == 1
    assert _found(tmp_path / "c", base % ", weights_only=True") == []
    assert _found(tmp_path / "d", base % ", weights_only=flag") == []


def test_numpy_load_with_allow_pickle_true_is_reported_but_not_false_or_default(tmp_path):
    base = "import numpy as np\n\n\ndef f(p):\n    return np.load(p%s)\n"
    assert len(_found(tmp_path / "a", base % ", allow_pickle=True")) == 1
    assert _found(tmp_path / "b", base % ", allow_pickle=False") == []
    assert _found(tmp_path / "c", base % "") == []


def test_aliased_imports_are_resolved(tmp_path):
    assert len(_found(tmp_path, "from torch import load as tl\n\n\ndef f(p):\n    return tl(p)\n")) == 1


def test_yaml_load_needs_a_safe_loader(tmp_path):
    base = "import yaml\n\n\ndef f(s):\n    return yaml.%s\n"
    assert len(_found(tmp_path / "a", base % "load(s)")) == 1
    assert len(_found(tmp_path / "b", base % "load(s, Loader=yaml.Loader)")) == 1
    assert len(_found(tmp_path / "c", base % "unsafe_load(s)")) == 1
    assert _found(tmp_path / "d", base % "load(s, Loader=yaml.SafeLoader)") == []
    assert _found(tmp_path / "e", base % "load(s, yaml.CSafeLoader)") == []
    assert _found(tmp_path / "f", base % "safe_load(s)") == []


UNVERIFIED = "import hashlib\nimport pickle\n\n\ndef load(p, expected):\n%s    return pickle.loads(p)\n"


def test_an_unverified_pickle_load_is_reported_and_a_preceding_hash_check_clears_it(tmp_path):
    assert len(_found(tmp_path / "a", UNVERIFIED % "")) == 1
    assert _found(tmp_path / "b", UNVERIFIED % "    assert hashlib.sha256(p).hexdigest() == expected\n") == []
    assert _found(tmp_path / "c", UNVERIFIED % "    _verify_signature(p, expected)\n") == []
    # a check that comes AFTER the load verifies nothing
    late = "import hashlib\nimport pickle\n\n\ndef load(p, expected):\n    obj = pickle.loads(p)\n    assert hashlib.sha256(p).hexdigest() == expected\n    return obj\n"
    assert len(_found(tmp_path / "d", late)) == 1


def test_a_round_trip_of_bytes_produced_in_the_same_function_is_not_untrusted_input(tmp_path):
    produced = "import pickle\n\n\ndef test_roundtrip(obj):\n    blob = pickle.dumps(obj)\n    return pickle.loads(blob)\n"
    assert _found(tmp_path / "a", produced) == []
    written = "import joblib\n\n\ndef f(obj, p):\n    joblib.dump(obj, p)\n    return joblib.load(p)\n"
    assert _found(tmp_path / "b", written) == []
    after = "import pickle\n\n\ndef f(blob, obj):\n    out = pickle.loads(blob)\n    pickle.dumps(obj)\n    return out\n"
    assert len(_found(tmp_path / "c", after)) == 1


def test_a_check_in_the_enclosing_function_covers_a_nested_loader(tmp_path):
    src = "import hmac\nimport pickle\n\n\ndef outer(p, mac):\n    hmac.compare_digest(mac, mac)\n\n    def inner():\n        return pickle.loads(p)\n\n    return inner()\n"
    assert _found(tmp_path, src) == []


def test_the_methods_of_a_restricted_unpickler_are_exempt_from_the_unverified_rule(tmp_path):
    assert _found(tmp_path, PER_NAME + "\n    def load_bytes(self, b):\n        return pickle.loads(b)\n") == []


def test_joblib_dill_and_cloudpickle_loads_are_covered(tmp_path):
    src = "import joblib\nimport dill\nimport cloudpickle\n\n\ndef f(p):\n    return joblib.load(p), dill.loads(p), cloudpickle.loads(p)\n"
    assert len(_found(tmp_path, src)) == 3


def test_the_suppression_comment_silences_the_statement_only(tmp_path):
    suppressed = VIOLATION.replace("torch.load(path)", "torch.load(path)  # deserialize-ok: checkpoint is produced by this repo's CI")
    assert _found(tmp_path / "a", suppressed) == []
    elsewhere = "import torch\n\n\ndef load(path):  # deserialize-ok: not this line\n    return torch.load(path)\n"
    assert len(_found(tmp_path / "b", elsewhere)) == 1
    on_class = WIDE.replace("class U(pickle.Unpickler):", "class U(pickle.Unpickler):  # deserialize-ok: closed module set")
    assert _found(tmp_path / "c", on_class) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"m.py": VIOLATION.encode()})
    bom = _corpus(tmp_path / "bom", {"m.py": BOM + VIOLATION.encode()})
    assert find_unsafe_deserialization(bom, use_git=False) == find_unsafe_deserialization(plain, use_git=False) != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"broken.py": b"def broken(:\n", "ok.py": b"x = 1\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_unsafe_deserialization(root, use_git=False)
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        assert_unsafe_deserialization(root, use_git=False)
    assert find_unsafe_deserialization(root, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_unsafe_deserialization(tmp_path, use_git=False)
    root = _corpus(tmp_path / "one", {"a.py": b"x = 1\n"})
    with pytest.raises(EmptyScanError):
        find_unsafe_deserialization(root, min_files=2, use_git=False)


def test_exclude_skips_paths_by_fragment(tmp_path):
    root = _corpus(tmp_path, {"vendored/m.py": VIOLATION.encode(), "lib/m.py": VIOLATION.encode()})
    assert [f.path for f in find_unsafe_deserialization(root, use_git=False, exclude=("vendored/",))] == ["lib/m.py"]
