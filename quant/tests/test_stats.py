import numpy as np
import pandas as pd
import pytest

from equity_lab.evaluate import metrics, stats


def test_inverse_normal_matches_known_quantiles():
    assert stats.norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert stats.norm_ppf(0.5) == pytest.approx(0.0, abs=1e-12)
    assert stats.norm_cdf(stats.norm_ppf(0.3)) == pytest.approx(0.3, abs=1e-9)


def test_psr_falls_with_negative_skew_and_fat_tails():
    """Momentum's signature is exactly the shape that makes a raw Sharpe
    overstate its own significance."""
    clean = stats.probabilistic_sharpe_ratio(1.0, 2000, skew=0.0, excess_kurtosis=0.0)
    ugly = stats.probabilistic_sharpe_ratio(1.0, 2000, skew=-2.0, excess_kurtosis=10.0)
    assert clean > ugly


def test_deflation_threshold_rises_with_the_number_of_trials():
    low = stats.expected_max_sharpe(5, 0.01)
    high = stats.expected_max_sharpe(500, 0.01)
    assert high > low > 0


def test_deflated_sharpe_penalises_a_wide_search():
    kwargs = dict(observed_sr=1.2, n_obs=2500, skew=-0.5, excess_kurtosis=4.0,
                  sr_variance=0.004)
    few = stats.deflated_sharpe_ratio(n_trials=2, **kwargs)["deflated_sharpe"]
    many = stats.deflated_sharpe_ratio(n_trials=2000, **kwargs)["deflated_sharpe"]
    assert few > many


def test_pbo_is_near_one_half_for_pure_noise():
    """Selecting among worthless candidates should rank at chance out of sample."""
    rng = np.random.default_rng(0)
    scores = [
        stats.probability_of_backtest_overfitting(
            pd.DataFrame(rng.normal(0, 0.01, (1500, 16)))
        )["pbo"]
        for _ in range(6)
    ]
    assert 0.25 < float(np.mean(scores)) < 0.75


def test_pbo_collapses_when_one_candidate_has_a_real_edge():
    rng = np.random.default_rng(1)
    frame = pd.DataFrame(rng.normal(0, 0.01, (2500, 12)))
    frame[0] += 0.0012
    assert stats.probability_of_backtest_overfitting(frame)["pbo"] < 0.15


def test_duplicate_candidates_are_counted_once():
    """A grid toggling a parameter that is inert under another setting produces
    identical series; counting them as separate trials corrupts PBO."""
    rng = np.random.default_rng(2)
    base = pd.DataFrame(rng.normal(0, 0.01, (900, 3)))
    duplicated = pd.concat([base, base, base], axis=1)
    duplicated.columns = range(duplicated.shape[1])
    assert stats.probability_of_backtest_overfitting(duplicated)["n_trials"] == 3


def test_bootstrap_interval_brackets_the_point_estimate():
    rng = np.random.default_rng(3)
    returns = pd.Series(rng.normal(0.0005, 0.01, 1500))
    out = stats.stationary_bootstrap(returns, n_samples=200, block_size=21)
    assert out["ci_low_5"] < out["point_estimate"] < out["ci_high_95"]


def test_sortino_exceeds_sharpe_for_positively_skewed_returns():
    rng = np.random.default_rng(5)
    returns = pd.Series(np.abs(rng.normal(0, 0.01, 2000)) - 0.006)
    assert metrics.sortino(returns) > metrics.sharpe(returns)


def test_capture_ratios_detect_a_deliberately_asymmetric_book():
    index = pd.date_range("2015-01-01", periods=600, freq="B")
    rng = np.random.default_rng(6)
    bench = pd.Series(rng.normal(0, 0.01, 600), index=index)
    strategy = pd.Series(np.where(bench > 0, bench * 1.2, bench * 0.4), index=index)
    up, down = metrics.capture_ratios(strategy, bench)
    assert up == pytest.approx(1.2, abs=0.02)
    assert down == pytest.approx(0.4, abs=0.02)
