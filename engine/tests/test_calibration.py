"""Rolling-calibration corrections: interval-coverage widening + bias adjustment."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))

from calibration import bias_adjustment, uncertainty_scale  # noqa: E402


def test_uncertainty_widens_when_interval_coverage_is_low():
    low = {"n": 1000, "interval_n": 575, "interval_coverage": 0.565, "rmse": 2.4}
    good = {"n": 1000, "interval_n": 575, "interval_coverage": 0.90, "rmse": 2.4}
    tight = {"n": 1000, "interval_n": 575, "interval_coverage": 0.99, "rmse": 2.4}
    assert uncertainty_scale(low) > 1.0
    assert abs(uncertainty_scale(good) - 1.0) < 0.05
    assert uncertainty_scale(tight) < 1.0


def test_uncertainty_falls_back_to_rmse_without_coverage_evidence():
    assert uncertainty_scale({"n": 500, "rmse": 3.0}) == 1.0
    assert uncertainty_scale({"n": 500, "rmse": 6.0}) == 1.75          # clamped
    assert uncertainty_scale({"n": 10, "rmse": 6.0}) == 1.0           # below min_rows


def test_bias_adjustment_is_zero_without_enough_data():
    assert bias_adjustment({"n": 5, "bias": -3.0}, "MID") == 0.0
    assert bias_adjustment(None, "MID") == 0.0
    assert bias_adjustment({}, "MID") == 0.0


def test_bias_adjustment_subtracts_position_bias_and_is_capped():
    summary = {
        "n": 1000, "bias": 0.3,
        "by_position": {
            "GKP": {"n": 250, "bias": 1.0},     # over-predicted -> negative adj
            "DEF": {"n": 250, "bias": 0.3},
            "MID": {"n": 250, "bias": -8.0},    # noisy huge -> hard-capped
            "FWD": {"n": 20, "bias": 5.0},      # thin -> falls back to pooled
        },
    }
    assert bias_adjustment(summary, "GKP") == -1.0                     # over-pred -> subtract
    assert bias_adjustment(summary, "DEF") == -0.3
    assert bias_adjustment(summary, "MID") == 1.5                      # under-pred by 8 -> +cap
    assert bias_adjustment(summary, "FWD") == -0.3                     # thin -> pooled bias 0.3


def test_bias_adjustment_shrinks_toward_zero_with_small_sample():
    thin = {"n": 0, "by_position": {"DEF": {"n": 75, "bias": 2.0}}}
    # 75 / full_trust_n(250) = 0.3 shrink -> -2.0 * 0.3 = -0.6
    assert bias_adjustment(thin, "DEF") == -0.6
