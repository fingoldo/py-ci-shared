"""SUB-14: the mutation runner keeps a bounded tail of a child's output, and its classifiers must still see the whole stream.

The reference functions below are the unbounded logic as it was before the change. A marker that sits EARLY in a
child's output and is followed by more than the tail cap must classify exactly as it did when the full text was kept.
"""

from __future__ import annotations

import queue
import random
import sys
from pathlib import Path

from py_ci_shared import _mutation_runner as runner
from py_ci_shared._mutation_runner import (
    _TAIL_CHARS,
    OutputScan,
    _first_failing_file,
    _killed_by_crash,
    _put_latest,
    _scan_of,
    run_with_deadline,
)
from py_ci_shared._mutation_worker import is_crash_message


def reference_killed_by_crash(output: str) -> bool:
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("E "):
            continue
        verdict = is_crash_message(stripped[2:])
        if verdict is not None:
            return verdict
    return False


def reference_first_failing_file(output: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        for prefix in ("FAILED ", "ERROR "):
            if stripped.startswith(prefix):
                target = stripped[len(prefix) :].split(" ", 1)[0]
                return target.split("::", 1)[0] or None
    return None


def reference_read_pytest_outcome(stdout: str) -> tuple[str, list[str]]:
    lines = stdout.splitlines()
    summary = next(
        (ln.strip() for ln in reversed(lines) if " passed" in ln or " failed" in ln or " error" in ln),
        "NO RESULT LINE",
    )
    return summary, [ln.strip() for ln in lines if ln.startswith(("FAILED", "ERROR "))]


def _noisy_output(head: str, noise_chars: int) -> str:
    """*head*, then enough noise lines to be well past the tail cap, then a pytest-style summary."""
    unit = "noise line that the child prints in a loop " * 2 + "\n"
    noise = unit * (noise_chars // len(unit) + 1)
    assert len(noise) >= noise_chars
    return head + noise + "=========== 1 failed, 4 passed in 0.10s ===========\n"


EARLY_HEAD = (
    "collected 5 items\n"
    "FAILED tests/test_early.py::test_boom - ZeroDivisionError: division by zero\n"
    "E   ZeroDivisionError: division by zero\n"
    "E   assert 1 == 2\n"
)


class TestClassifiersOnAStreamLongerThanTheCap:
    def test_early_crash_marker_survives_the_cap(self):
        output = _noisy_output(EARLY_HEAD, _TAIL_CHARS * 3)
        assert len(output) > _TAIL_CHARS * 3
        assert reference_killed_by_crash(output) is True
        assert _killed_by_crash(output) is True
        scan = OutputScan()
        scan.feed(output)
        scan.finish()
        assert scan.killed_by_crash is True

    def test_early_failing_file_survives_the_cap(self):
        output = _noisy_output(EARLY_HEAD, _TAIL_CHARS * 3)
        assert reference_first_failing_file(output) == "tests/test_early.py"
        assert _first_failing_file(output) == "tests/test_early.py"

    def test_early_assertion_marker_still_reads_as_assertion(self):
        head = "E   assert 1 == 2\nE   ZeroDivisionError: late\n"
        output = _noisy_output(head, _TAIL_CHARS * 2)
        assert reference_killed_by_crash(output) is False
        assert _killed_by_crash(output) is False

    def test_the_summary_and_names_match_the_reference_on_a_long_stream(self):
        head = "FAILED tests/a.py::t1 - x\nERROR tests/b.py::t2\n"
        output = _noisy_output(head, _TAIL_CHARS * 2)
        summary, names = reference_read_pytest_outcome(output)
        scan = OutputScan()
        scan.feed(output)
        scan.finish()
        assert (scan.summary or "NO RESULT LINE", scan.failed) == (summary, names)

    def test_no_summary_gives_the_placeholder(self):
        scan = OutputScan()
        scan.feed("the process died before pytest printed anything\n")
        scan.finish()
        assert scan.summary is None
        assert reference_read_pytest_outcome("the process died before pytest printed anything\n")[0] == "NO RESULT LINE"


class TestStreamingEqualsWholeText:
    """Feeding the stream in pieces, cut anywhere, must give what feeding the whole text gives."""

    TRICKY = (
        "  FAILED tests/x.py::a - boom\r\n"
        "E   ValueError: nope\x0bFAILED tests/y.py::b\n"
        "E   assert 2 == 3 passed\x0c\n"
        "ERROR tests/z.py E   KeyError: k\n"
        "1 failed, 2 passed in 0.2s"
    )

    def test_every_two_way_split_matches_the_whole(self):
        whole = OutputScan()
        whole.feed(self.TRICKY)
        whole.finish()
        for cut in range(len(self.TRICKY) + 1):
            pieces = OutputScan()
            pieces.feed(self.TRICKY[:cut])
            pieces.feed(self.TRICKY[cut:])
            pieces.finish()
            assert (pieces.summary, pieces.failed, pieces.killed_by_crash, pieces.first_failing_file) == (
                whole.summary,
                whole.failed,
                whole.killed_by_crash,
                whole.first_failing_file,
            ), cut

    def test_random_chunking_matches_the_reference_on_the_whole_text(self):
        rng = random.Random(20261021)
        alphabet = "E FAILED ERROR passed::x\n\r\x0b\x0c  ab"
        for _ in range(300):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))
            scan = OutputScan()
            i = 0
            while i < len(text):
                step = rng.randint(1, 17)
                scan.feed(text[i : i + step])
                i += step
            scan.finish()
            assert scan.killed_by_crash == reference_killed_by_crash(text), repr(text)
            assert scan.first_failing_file == reference_first_failing_file(text), repr(text)
            assert (scan.summary or "NO RESULT LINE", scan.failed) == reference_read_pytest_outcome(text), repr(text)


class TestRunWithDeadlineKeepsABoundedTail:
    def _child(self, tmp_path: Path, head: str, noise_lines: int) -> list[str]:
        script = tmp_path / "chatty.py"
        script.write_text(
            "import sys\n"
            f"sys.stdout.write({head!r})\n"
            "line = 'noise line that the child prints in a loop ' * 2 + chr(10)\n"
            f"for _ in range({noise_lines}):\n"
            "    sys.stdout.write(line)\n"
            "sys.stdout.write('=========== 1 failed, 4 passed in 0.10s ===========' + chr(10))\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        return [sys.executable, str(script)]

    def test_a_chatty_child_is_truncated_but_classified_as_the_whole_stream(self, tmp_path):
        noise_lines = (_TAIL_CHARS * 4) // 90
        head = EARLY_HEAD
        result = run_with_deadline(self._child(tmp_path, head, noise_lines), cwd=tmp_path, env=None, timeout=120)
        assert result is not None and result.returncode == 1
        assert len(result.stdout) <= _TAIL_CHARS + runner._PIECE_CHARS
        assert len(result.stdout) < _TAIL_CHARS * 2
        assert result.stdout.endswith("1 failed, 4 passed in 0.10s ===========\n")
        scan = _scan_of(result)
        assert scan.killed_by_crash is True
        assert scan.first_failing_file == "tests/test_early.py"
        full = head + ("noise line that the child prints in a loop " * 2 + "\n") * noise_lines + "=========== 1 failed, 4 passed in 0.10s ===========\n"
        assert scan.killed_by_crash == reference_killed_by_crash(full)
        assert scan.first_failing_file == reference_first_failing_file(full)

    def test_a_long_line_with_an_early_marker_is_read_in_pieces(self, tmp_path):
        head = "E   ZeroDivisionError: " + "x" * (runner._PIECE_CHARS * 2) + "\n"
        result = run_with_deadline(self._child(tmp_path, head, 10), cwd=tmp_path, env=None, timeout=120)
        assert result is not None
        assert _scan_of(result).killed_by_crash is True

    def test_a_timeout_still_returns_none(self, tmp_path):
        script = tmp_path / "hang.py"
        script.write_text("import sys, time\nsys.stdout.write('x' * 100000)\nsys.stdout.flush()\ntime.sleep(60)\n", encoding="utf-8")
        assert run_with_deadline([sys.executable, str(script)], cwd=tmp_path, env=None, timeout=2) is None


class TestWarmQueueKeepsTheNewestLines:
    def test_the_oldest_lines_are_dropped_and_the_reply_survives(self):
        lines: queue.Queue = queue.Queue(maxsize=3)
        for i in range(10):
            _put_latest(lines, f"stray {i}\n")
        _put_latest(lines, '{"id": 7, "rc": 0}\n')
        got = [lines.get_nowait() for _ in range(lines.qsize())]
        assert got[-1] == '{"id": 7, "rc": 0}\n'
        assert len(got) == 3
