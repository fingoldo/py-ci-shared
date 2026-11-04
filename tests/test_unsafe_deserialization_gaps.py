"""unsafe_deserialization: verification must concern the loaded value (33-F6) and the sink table is wider (33-F7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.unsafe_deserialization import find_unsafe_deserialization


def _lines(tmp_path: Path, src: str) -> list[int]:
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    return [f.line for f in find_unsafe_deserialization(tmp_path, use_git=False)]


def test_an_unrelated_hash_before_the_load_does_not_count_as_verification(tmp_path):
    src = 'import hashlib\nimport pickle\n\n\ndef f(p, b):\n    hashlib.sha256(b"unrelated").hexdigest()\n    return pickle.loads(b)\n'
    assert _lines(tmp_path, src) == [7]


def test_a_hash_of_the_payload_still_verifies(tmp_path):
    src = "import hashlib\nimport pickle\n\n\ndef f(b, want):\n    assert hashlib.sha256(b).hexdigest() == want\n    return pickle.loads(b)\n"
    assert _lines(tmp_path, src) == []


def test_a_hash_of_the_source_the_payload_was_read_from_verifies(tmp_path):
    src = "import hmac\nimport pickle\n\n\ndef f(fh, key, sig):\n    data = fh.read()\n    hmac.compare_digest(sig, hmac.new(key, data).digest())\n    return pickle.loads(data)\n"
    assert _lines(tmp_path, src) == []


def test_a_dump_of_something_else_is_not_a_round_trip(tmp_path):
    src = "import pickle\n\n\ndef f(a, b, fh):\n    pickle.dump(a, fh)\n    return pickle.loads(b)\n"
    assert _lines(tmp_path, src) == [6]


@pytest.mark.parametrize(
    "body",
    [
        "import pandas as pd\n\n\ndef f(p):\n    return pd.read_pickle(p)\n",
        "import pickle\n\n\ndef f(fh):\n    return pickle.Unpickler(fh).load()\n",
        "import marshal\n\n\ndef f(b):\n    return marshal.loads(b)\n",
        "import shelve\n\n\ndef f(p):\n    return shelve.open(p)\n",
        "import jsonpickle\n\n\ndef f(s):\n    return jsonpickle.decode(s)\n",
        "import pickle\n\n\nclass A:\n    d = pickle.loads(b'x' + read())\n",
        "import pickle\n\nf = lambda b: pickle.loads(b)\n",
        "import torch\n\n\ndef f(p, w):\n    return torch.load(p, weights_only=w)\n",
        "from ruamel.yaml import YAML\n\nyaml = YAML(typ='unsafe')\n",
        "from keras.models import load_model\n\nm = load_model('x', safe_mode=False)\n",
    ],
)
def test_each_new_sink_is_reported_exactly_once(tmp_path, body):
    assert len(_lines(tmp_path, body)) == 1


def test_safe_ruamel_and_literal_weights_only_are_clean(tmp_path):
    src = "import torch\nfrom ruamel.yaml import YAML\n\nyaml = YAML(typ='safe')\nm = torch.load('p', weights_only=True)\n"
    assert _lines(tmp_path, src) == []
