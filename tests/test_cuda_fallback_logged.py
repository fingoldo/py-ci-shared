"""Tests for py_ci_shared.cuda_fallback_logged."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.cuda_fallback_logged import RULE, assert_cuda_fallback_logged, find_cuda_fallback_logged

HEAD = "import logging\nimport cupy as cp\nlogger = logging.getLogger(__name__)\n\n\n"


def _find(tmp_path: Path, body: str, **kw):
    (tmp_path / "m.py").write_text(HEAD + body, encoding="utf-8")
    return find_cuda_fallback_logged(tmp_path, use_git=False, **kw)


def test_device_error_handler_that_falls_back_silently_is_reported(tmp_path):
    found = _find(tmp_path, "def f(x):\n    try:\n        return _s_gpu(cp.asarray(x))\n    except cp.cuda.memory.OutOfMemoryError:\n        return _s_cpu(x)\n")
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 9, RULE)]
    assert "OutOfMemoryError" in found[0].message


def test_broad_and_bare_handlers_are_reported(tmp_path):
    body = "def f(x):\n    try:\n        cp.asarray(x)\n    except Exception:\n        pass\n    try:\n        cp.asarray(x)\n    except:\n        x = 1\n"
    assert [f.line for f in _find(tmp_path, body)] == [9, 13]


def test_a_computed_exception_type_is_reported(tmp_path):
    body = "def f(x):\n    try:\n        cp.asarray(x)\n    except tuple(ERRS):\n        pass\n"
    assert len(_find(tmp_path, body)) == 1


@pytest.mark.parametrize(
    "handler",
    [
        "logger.warning('oom %s', exc)",
        "log_throttle(logger, 'k', 30, 'oom')",
        "warnings.warn('oom')",
        "_log_fallback(exc)",
        "raise",
    ],
)
def test_logging_or_reraising_is_clean(tmp_path, handler):
    body = f"def f(x):\n    try:\n        cp.asarray(x)\n    except Exception as exc:\n        {handler}\n"
    assert _find(tmp_path, body) == []


def test_excluded_error_types_are_clean(tmp_path):
    body = "def f(x):\n    try:\n        cp.asarray(x)\n    except (ImportError, KeyError):\n        pass\n    except np.linalg.LinAlgError:\n        pass\n"
    assert _find(tmp_path, body) == []


def test_try_without_gpu_work_is_clean(tmp_path):
    assert _find(tmp_path, "def f(x):\n    try:\n        return int(x)\n    except Exception:\n        return 0\n") == []


def test_a_callee_named_gpu_makes_the_body_gpu_work(tmp_path):
    body = "def f(x):\n    try:\n        return score_gpu(x)\n    except Exception:\n        return 0\n"
    assert len(_find(tmp_path, body)) == 1


def test_marker_needs_a_reason(tmp_path):
    ok = "def f(x):\n    try:\n        cp.asarray(x)\n    except Exception:  # fallback-logged-ok: logged by the caller\n        pass\n"
    empty = ok.replace("logged by the caller", "")
    assert _find(tmp_path, ok) == []
    assert len(_find(tmp_path, empty)) == 1


def test_marker_inside_the_handler_body_suppresses(tmp_path):
    body = "def f(x):\n    try:\n        cp.asarray(x)\n    except Exception:\n        pass  # fallback-logged-ok: logged above\n"
    assert _find(tmp_path, body) == []


def test_narrowing_sibling_outer_and_earlier_log(tmp_path):
    sibling = "def f(x):\n    try:\n        cp.asarray(x)\n    except MemoryError as e:\n        logger.warning('%s', e)\n    except Exception:\n        pass\n"
    outer = "def f(x):\n    try:\n        try:\n            cp.asarray(x)\n        except Exception:\n            pass\n    except MemoryError as e:\n        logger.warning('%s', e)\n"
    earlier = "def f(x):\n    logger.warning('over budget')\n    try:\n        cp.asarray(x)\n    except MemoryError:\n        pass\n"
    for body in (sibling, outer, earlier):
        assert _find(tmp_path, body) == [], body


def test_narrowing_does_not_cover_a_handler_that_does_work(tmp_path):
    body = "def f(x):\n    logger.warning('start')\n    try:\n        cp.asarray(x)\n    except MemoryError:\n        return _cpu(x)\n"
    assert len(_find(tmp_path, body)) == 1


def test_custom_parameters(tmp_path):
    body = "import torch\n\n\ndef f(x):\n    try:\n        torch.zeros(1)\n    except Exception:\n        pass\n"
    assert _find(tmp_path, body) == []
    assert len(_find(tmp_path, body, gpu_modules=("torch",))) == 1


def test_assert_message_names_file_line_rule_and_fix(tmp_path):
    (tmp_path / "m.py").write_text(HEAD + "def f(x):\n    try:\n        cp.asarray(x)\n    except Exception:\n        pass\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"m\.py:9: \[cuda-fallback-not-logged\].*fallback-logged-ok"):
        assert_cuda_fallback_logged(tmp_path, use_git=False)


def test_baseline_accepts_known_and_rejects_new(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    corpus = tmp_path / "src"
    corpus.mkdir()
    (corpus / "m.py").write_text(HEAD + "def f(x):\n    try:\n        cp.asarray(x)\n    except Exception:\n        pass\n", encoding="utf-8")
    base = tmp_path / "base.json"
    with pytest.raises(pytest.skip.Exception):
        assert_cuda_fallback_logged(corpus, baseline_path=base, refresh=True, use_git=False)
    assert_cuda_fallback_logged(corpus, baseline_path=base, use_git=False)
    (corpus / "n.py").write_text(HEAD + "def g(x):\n    try:\n        cp.asarray(x)\n    except Exception:\n        x = 2\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        assert_cuda_fallback_logged(corpus, baseline_path=base, use_git=False)


def test_unparsable_file_is_a_failure_and_floor_is_enforced(tmp_path):
    (tmp_path / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(UnparsedFilesError):
        find_cuda_fallback_logged(tmp_path, use_git=False)
    with pytest.raises(EmptyScanError):
        find_cuda_fallback_logged(tmp_path, use_git=False, min_files=5, allow_unparsed=True)
