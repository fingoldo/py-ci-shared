"""estimator_mixin_order: an sklearn mixin placed after BaseEstimator in a class statement."""

from __future__ import annotations

import pytest

from py_ci_shared.estimator_mixin_order import assert_estimator_mixin_order, find_estimator_mixin_order

BAD = (
    "from sklearn.base import BaseEstimator, ClassifierMixin\n\n"
    "class Bad(BaseEstimator, ClassifierMixin):\n"
    "    def fit(self, X, y):\n        return self\n"
)
GOOD = (
    "from sklearn.base import BaseEstimator, ClassifierMixin\n\n"
    "class Good(ClassifierMixin, BaseEstimator):\n"
    "    def fit(self, X, y):\n        return self\n"
)
WAIVED = (
    "from sklearn.base import BaseEstimator, ClassifierMixin\n\n"
    "class Waived(BaseEstimator, ClassifierMixin):  # mixin-order-ok: tag override is intentional\n"
    "    pass\n"
)
ALIASED = "import sklearn.base as sb\n\nclass Bad(sb.BaseEstimator, sb.RegressorMixin):\n    pass\n"


def _corpus(tmp_path, text: str):
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "m.py").write_text(text, encoding="utf-8")
    return tmp_path


def test_reversed_order_is_reported(tmp_path):
    findings = find_estimator_mixin_order(_corpus(tmp_path, BAD), min_files=1)
    assert [f.rule for f in findings] == ["estimator_mixin_order"]
    assert findings[0].line == 3


def test_aliased_module_is_reported(tmp_path):
    assert len(find_estimator_mixin_order(_corpus(tmp_path, ALIASED), min_files=1)) == 1


def test_correct_order_and_waiver_are_clean(tmp_path):
    assert find_estimator_mixin_order(_corpus(tmp_path, GOOD), min_files=1) == []
    assert find_estimator_mixin_order(_corpus(tmp_path, WAIVED), min_files=1) == []


def test_assert_raises_naming_the_gate(tmp_path):
    with pytest.raises(AssertionError, match="estimator_mixin_order"):
        assert_estimator_mixin_order(_corpus(tmp_path, BAD), min_files=1)


def test_unparsable_file_fails_unless_allowed(tmp_path):
    _corpus(tmp_path, GOOD)
    (tmp_path / "pkg" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="unparsed"):
        assert_estimator_mixin_order(tmp_path, min_files=1)
    assert assert_estimator_mixin_order(tmp_path, min_files=1, allow_unparsed=True) is None


def test_floor_on_empty_corpus(tmp_path):
    from py_ci_shared._core.errors import EmptyScanError

    with pytest.raises(EmptyScanError):
        assert_estimator_mixin_order(tmp_path, min_files=1)
