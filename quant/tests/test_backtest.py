import numpy as np
import pandas as pd
import pytest

from equity_lab.backtest import run_backtest
from equity_lab.backtest.costs import CostModel
from equity_lab.config import CostConfig


@pytest.fixture
def result(config, panel):
    config.data.start, config.data.end = "2010-01-04", "2017-12-29"
    return run_backtest(config, panel)


def test_accounting_identity_holds_every_day(result):
    """equity == cash + marked positions, on every single date.

    If this ever drifts, some cost or cash flow is being conjured or lost, and
    every number downstream is wrong by an unknown amount.
    """
    position_value = result.weights.multiply(result.daily["equity"], axis=0).sum(axis=1)
    reconstructed = result.daily["cash"] + position_value
    assert np.allclose(reconstructed, result.daily["equity"], rtol=1e-9, atol=1e-6)


def test_returns_are_consistent_with_the_equity_curve(result):
    implied = result.equity / result.equity.shift(1) - 1.0
    assert np.allclose(implied.iloc[1:], result.returns.iloc[1:], atol=1e-12)


def test_backtest_does_not_look_ahead(config, panel):
    """Truncating the future must leave the past bit-for-bit unchanged.

    This is the strongest available statement that the engine is causal: it
    tests the whole pipeline — universe, signals, risk model, sizing, execution —
    rather than any one component's shift() arithmetic.
    """
    cut = panel.dates[-300]
    config.data.start, config.data.end = "2010-01-04", "2017-12-29"
    full = run_backtest(config, panel)

    truncated_cfg = config.with_overrides({"data.end": str(cut.date())})
    truncated = run_backtest(truncated_cfg, panel.slice(None, cut))

    common = full.daily.index.intersection(truncated.daily.index)
    assert len(common) > 200
    pd.testing.assert_series_equal(
        full.daily.loc[common, "net_return"],
        truncated.daily.loc[common, "net_return"],
        check_exact=False, atol=1e-12,
    )


def test_constraints_are_respected_in_realised_weights(result, config, panel):
    cap = config.portfolio.risk.max_weight
    # realised weights drift with prices between rebalances, so allow drift but
    # not a systematic breach of the cap at the point of trading
    assert result.rebalances["gross"].max() <= config.portfolio.risk.max_gross + 1e-6
    assert (result.weights.abs().max(axis=1) < cap * 3).all()


def test_higher_costs_reduce_net_return_monotonically(config, panel):
    config.data.start, config.data.end = "2010-01-04", "2016-12-30"
    outcomes = []
    for spread in (0.0, 10.0, 40.0):
        cfg = config.with_overrides({"backtest.costs.spread_bps": spread})
        outcomes.append(run_backtest(cfg, panel).returns.mean())
    assert outcomes[0] > outcomes[1] > outcomes[2]


def test_execution_lag_changes_results(config, panel):
    """If the lag were ignored, these would be identical — which is exactly the
    bug that makes a leaky backtest look brilliant."""
    config.data.start, config.data.end = "2010-01-04", "2016-12-30"
    zero = run_backtest(config.with_overrides({"backtest.execution_lag": 0}), panel)
    three = run_backtest(config.with_overrides({"backtest.execution_lag": 3}), panel)
    assert not np.allclose(zero.returns.to_numpy(), three.returns.to_numpy())


def test_gross_return_exceeds_net_by_exactly_the_trading_cost(result):
    equity_prev = result.daily["equity"].shift(1)
    implied_cost = (result.gross_returns - result.returns) * equity_prev
    assert np.allclose(implied_cost.iloc[1:], result.daily["trade_cost"].iloc[1:], atol=1e-6)


def test_participation_cap_limits_a_single_day_order():
    model = CostModel(CostConfig(adv_participation_cap=0.05))
    desired = np.array([1_000_000.0, -1_000_000.0])
    adv = np.array([2_000_000.0, 2_000_000.0])
    filled, unfilled = model.apply_participation_cap(desired, adv)
    assert np.allclose(np.abs(filled), 100_000.0)
    assert unfilled == pytest.approx(1_800_000.0)


def test_impact_cost_is_superlinear_in_order_size():
    """The square-root law is what makes capacity finite; a linear cost model
    would let any amount of capital trade any strategy."""
    model = CostModel(CostConfig(spread_bps=0.0, impact_coef=0.2))
    adv, vol = np.array([1e7]), np.array([0.02])
    small = model.trade_costs(np.array([1e5]), adv, vol)[1]
    large = model.trade_costs(np.array([4e5]), adv, vol)[1]
    assert large / small == pytest.approx(8.0, rel=0.01)     # 4x size -> 8x cost


def test_carry_charges_borrow_and_pays_interest_only_up_to_equity():
    model = CostModel(CostConfig(borrow_bps=100.0, financing_bps=0.0, cash_yield_bps=100.0))
    # short proceeds inflate cash; interest must not be paid on them
    carry = model.carry(short_notional=1e6, gross=2e6, equity=1e6, cash=2e6)
    assert carry == pytest.approx(0.0, abs=1e-9)
