"""``printed_advice`` finds messages that tell the reader to act, keys them stably, and ignores plain statements."""

from __future__ import annotations

from py_ci_shared.printed_advice import find_printed_advice


def _write(tmp_path, source, name="m.py"):
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def test_advice_in_logs_errors_and_warnings_is_found(tmp_path):
    """A logger call, an exception and warnings.warn each carry advice; the key names the file and the enclosing function."""
    p = _write(tmp_path,
               "def fit(self):\n"
               "    logger.warning('too few rows: raise mi_sample_n or lower mi_nbins.')\n"
               "    raise ValueError('Set drop_invalid_rows=True to drop them.')\n"
               "class A:\n"
               "    def run(self):\n"
               "        warnings.warn(f'budget {x} exceeded; set knn_mi_auto_downgrade=False to keep knn')\n")
    found = find_printed_advice([p], tmp_path)
    assert [a.key for a in found] == ["m.py::fit#1", "m.py::fit#2", "m.py::A.run#1"]


def test_concatenated_and_formatted_literals_are_read_whole(tmp_path):
    """Advice split across implicit concatenation or an f-string still matches."""
    p = _write(tmp_path, "def f():\n    logger.info('dropped. If wrong, pass it '\n                'via base_candidates=[...].')\n")
    assert len(find_printed_advice([p], tmp_path)) == 1


def test_statements_without_advice_are_ignored(tmp_path):
    """Describing what happened is not advice, and strings outside message calls are not scanned."""
    p = _write(tmp_path,
               "def f():\n"
               "    logger.info('reduced the sample to %d rows', n)\n"
               "    doc = 'set x=1 to enable'\n"
               "    raise ValueError('input has no rows')\n")
    assert find_printed_advice([p], tmp_path) == []


def test_keys_survive_moving_the_message(tmp_path):
    """Inserting lines above a message changes its line number but not its key."""
    a = find_printed_advice([_write(tmp_path, "def f():\n    raise ValueError('Pass kfold=1 to use it')\n")], tmp_path)
    b = find_printed_advice([_write(tmp_path, "import os\n\n\ndef f():\n    x = 1\n    raise ValueError('Pass kfold=1 to use it')\n")], tmp_path)
    assert [x.key for x in a] == [x.key for x in b] and a[0].lineno != b[0].lineno
