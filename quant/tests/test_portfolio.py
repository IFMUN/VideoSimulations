import numpy as np
import pandas as pd
import pytest

from equity_lab.config import (
    CrashOverlayConfig, DrawdownThrottleConfig, RiskConfig, StopConfig,
)
from equity_lab.portfolio import asymmetry, risk
from equity_lab.portfolio.risk import SectorGrouping


def _scores(n=40):
    return pd.Series(np.linspace(-2.5, 2.5, n), index=[f"A{i:03d}" for i in range(n)])


def test_convex_response_is_asymmetric_in_the_intended_direction():
    """Convex longs accelerate away from the threshold; concave shorts flatten.

    That shape is the whole point: crash risk is concentrated in the most
    distressed names, so the short leg deliberately owns less of its extreme.
    """
    scores = _scores()
    raw = asymmetry.convex_response(scores, 1.5, 0.7, 0.8, -0.8)
    longs = raw[raw > 0].sort_values()
    shorts = (-raw[raw < 0]).sort_values()
    long_curve = longs / longs.max()
    short_curve = shorts / shorts.max()
    # measured at the same rank depth, the long leg is more concentrated
    assert long_curve.iloc[len(long_curve) // 2] < short_curve.iloc[len(short_curve) // 2]


def test_equal_convexity_is_symmetric():
    scores = _scores()
    raw = asymmetry.convex_response(scores, 1.0, 1.0, 1.0, -1.0)
    assert raw[raw > 0].sum() == pytest.approx(-raw[raw < 0].sum(), rel=1e-6)


def test_position_cap_redistributes_instead_of_shrinking_the_book():
    weights = np.array([0.50, 0.30, 0.10, 0.10])
    capped = risk.cap_and_redistribute(weights, 0.30)
    assert capped.max() <= 0.30 + 1e-12
    assert capped.sum() == pytest.approx(weights.sum(), rel=1e-9)


def test_position_cap_degrades_gracefully_when_infeasible():
    weights = np.array([0.6, 0.6])
    capped = risk.cap_and_redistribute(weights, 0.2)
    assert capped.max() <= 0.2 + 1e-12       # the cap binds absolutely
    assert capped.sum() < weights.sum()      # the book is allowed to be smaller


def test_sector_cap_never_flips_a_position_through_zero():
    """Subtracting a flat amount would turn small longs into shorts — silently
    inverting the view the signal expressed."""
    index = [f"A{i}" for i in range(6)]
    weights = pd.Series([0.30, 0.20, 0.02, -0.05, 0.10, -0.02], index=index)
    sectors = pd.Series(["Tech"] * 4 + ["Energy"] * 2, index=index)
    cfg = RiskConfig(max_weight=1.0, max_sector_net=0.10, max_gross=10.0)
    out = risk.apply_constraints(weights, cfg, SectorGrouping(sectors, pd.Index(index)))
    assert (np.sign(out[out != 0]) == np.sign(weights[out != 0])).all()
    assert abs(out.groupby(sectors).sum()).max() <= 0.10 + 1e-9


def test_beta_hedge_zeroes_net_beta_without_reversing_positions():
    index = [f"A{i}" for i in range(8)]
    weights = pd.Series([0.2, 0.15, 0.1, 0.05, -0.1, -0.08, -0.05, -0.02], index=index)
    betas = pd.Series(np.linspace(0.6, 1.6, 8), index=index)
    hedged = risk.beta_neutralise(weights, betas)
    assert abs((hedged * betas).sum()) < 1e-9
    moved = hedged[(hedged != 0) & (weights != 0)]
    assert (np.sign(moved) == np.sign(weights[moved.index])).all()


def test_turnover_cap_is_relative_to_gross_not_absolute():
    """An absolute cap silently becomes a different constraint at a different
    leverage, so a config stops following its own signal when gross changes."""
    index = [f"A{i}" for i in range(10)]
    previous = pd.Series(0.0, index=index)
    for scale in (1.0, 4.0):
        target = pd.Series(np.linspace(-0.1, 0.1, 10) * scale, index=index)
        out = risk.apply_turnover_controls(target, previous, 0.0, 0.5)
        assert out.abs().sum() / target.abs().sum() == pytest.approx(0.5, rel=1e-6)


def test_no_trade_band_holds_small_moves():
    index = [f"A{i}" for i in range(4)]
    previous = pd.Series([0.10, 0.10, 0.10, 0.10], index=index)
    target = pd.Series([0.101, 0.10, 0.30, 0.10], index=index)
    out = risk.apply_turnover_controls(target, previous, 0.5, 10.0)
    assert out.iloc[0] == pytest.approx(0.10)     # held: the move is inside the band
    assert out.iloc[2] == pytest.approx(0.30)     # traded: the move is large


def test_drawdown_throttle_cuts_faster_than_it_recovers():
    cfg = DrawdownThrottleConfig(start_dd=0.05, full_dd=0.20, floor=0.4,
                                 cut_halflife=1.0, recover_halflife=20.0,
                                 peak_window_days=0)
    throttle = asymmetry.DrawdownThrottle(cfg)
    for equity in (1.0, 1.0, 1.0):
        throttle.update(equity)
    down = [throttle.update(e) for e in (0.90, 0.85, 0.80)]
    assert down[-1] < 0.8
    up = [throttle.update(e) for e in (0.85, 0.90, 0.95)]
    assert up[-1] < down[-1] + (1.0 - down[-1]) * 0.5     # recovery is slow
    assert all(0.4 - 1e-9 <= level <= 1.0 for level in down + up)


def test_trailing_stop_spares_winners_and_cuts_losers():
    tickers = pd.Index(["WIN", "LOSE"])
    stops = asymmetry.StopTracker(StopConfig(enabled=True, trailing=0.10), tickers)
    stops.sync(np.array([100.0, 100.0]), np.array([10.0, 10.0]))
    stops.mark(np.array([20.0, 10.0]))                 # WIN doubles
    fired = stops.mark(np.array([17.0, 8.5]))          # both fall 15% from best
    assert not fired[0], "a position still in profit must not be stopped"
    assert fired[1], "a losing position past its trailing limit must be stopped"


def test_market_state_cuts_the_short_leg_only_when_both_conditions_hold(panel):
    """A drawdown alone, or a volatility spike alone, must not cut the short leg;
    momentum crashes need both."""
    cfg = CrashOverlayConfig(bear_short_scale=0.3, bear_rule="drawdown")
    state = asymmetry.market_state(panel.market_return(), cfg)
    quiet = state[~state["bear"] & ~state["vol_stressed"]]
    assert quiet["short_scale"].min() > 0.99
    assert state["short_scale"].min() >= cfg.bear_short_scale - 1e-9
    assert state["short_scale"].max() <= 1.0 + 1e-9


def test_conditional_betas_recover_a_known_asymmetry():
    """Construct a name with beta 2 down and beta 0.5 up and check both are found."""
    index = pd.date_range("2015-01-01", periods=900, freq="B")
    rng = np.random.default_rng(4)
    market = pd.Series(rng.normal(0, 0.01, len(index)), index=index)
    asset = np.where(market < 0, 2.0 * market, 0.5 * market)
    frame = pd.DataFrame({"A": asset}, index=index)
    up, down = asymmetry.conditional_betas(frame, market, window=500)
    assert up["A"].iloc[-1] == pytest.approx(0.5, abs=0.05)
    assert down["A"].iloc[-1] == pytest.approx(2.0, abs=0.05)


def test_downside_deviation_ignores_upside():
    index = pd.date_range("2015-01-01", periods=400, freq="B")
    calm_up = pd.DataFrame({"A": [0.01] * 400}, index=index)
    assert asymmetry.downside_deviation(calm_up, 252).iloc[-1]["A"] == pytest.approx(0.0)


def test_volatility_scaler_targets_the_configured_volatility():
    cfg = CrashOverlayConfig(enabled=True, vol_window=126, target_strategy_vol=0.10,
                             min_scale=0.1, max_scale=5.0)
    scaler = asymmetry.VolatilityScaler(cfg)
    rng = np.random.default_rng(1)
    for value in rng.normal(0, 0.20 / np.sqrt(252), 300):   # realised vol ~20%
        scaler.update(float(value))
    assert scaler.scale() == pytest.approx(0.5, rel=0.25)
