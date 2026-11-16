"""Canary of estimator_mixin_order (fragment; the loader in _gate_teeth_canaries_more globs only ``cuda_*`` fragments today)."""

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "estimator_mixin_order",
    lambda d: _gate("estimator_mixin_order").assert_estimator_mixin_order(d),
    tokens=("estimator_mixin_order", "after BaseEstimator"),
)
