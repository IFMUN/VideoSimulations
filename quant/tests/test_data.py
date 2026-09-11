import pandas as pd

from equity_lab.config import SyntheticConfig, UniverseConfig
from equity_lab.data.synthetic import generate
from equity_lab.data.universe import build_universe, universe_summary


def test_generation_is_deterministic():
    a = generate(SyntheticConfig(n_assets=40), "2012-01-01", "2014-12-31", seed=5)
    b = generate(SyntheticConfig(n_assets=40), "2012-01-01", "2014-12-31", seed=5)
    pd.testing.assert_frame_equal(a.close, b.close)


def test_panel_has_the_stylised_facts_the_strategy_relies_on(panel):
    market = panel.market_return()
    assert market.abs().autocorr(1) > 0.05, "no volatility clustering"
    assert market.kurt() > 1.0, "tails are too thin to stress the risk model"
    assert panel.close.isna().any().any(), "no listing/delisting means no survivorship test"


def test_first_return_of_each_name_is_dropped(panel):
    """A name's first print is a level, not a return; treating it as one would
    manufacture a huge fake move on every listing."""
    returns = panel.returns()
    first_print = panel.close.notna() & ~panel.close.notna().shift(1, fill_value=False)
    assert not returns.where(first_print).notna().any().any()


def test_universe_screens_use_only_past_information(panel):
    cfg = UniverseConfig(top_n_by_adv=50, min_adv_usd=0.0, min_history_days=252)
    full = build_universe(panel, cfg)
    cut = panel.dates[-200]
    truncated = build_universe(panel.slice(None, cut), cfg)
    pd.testing.assert_frame_equal(
        full.loc[:cut][truncated.columns], truncated, check_dtype=False
    )


def test_minimum_history_screen_is_respected(panel):
    cfg = UniverseConfig(top_n_by_adv=0, min_adv_usd=0.0, min_history_days=252, min_price=0.0)
    mask = build_universe(panel, cfg)
    history = panel.close.notna().rolling(252, min_periods=1).sum().shift(1)
    assert not (mask & (history < 252)).any().any()


def test_entry_exit_counts_are_plausible(panel):
    """Guards the boolean-shift trap: ``~`` on an object frame is a bitwise not,
    so ``~True == -2`` and every name looks like it enters the universe daily."""
    cfg = UniverseConfig(top_n_by_adv=50, min_adv_usd=0.0)
    summary = universe_summary(build_universe(panel, cfg))
    steady = summary.iloc[1:-1]
    assert (steady["entries"] < steady["n_names"]).all()


def test_slice_preserves_column_alignment(panel):
    sliced = panel.slice("2012-01-01", "2013-12-31")
    assert list(sliced.tickers) == list(panel.tickers)
    assert sliced.dates[0] >= pd.Timestamp("2012-01-01")
    assert sliced.dates[-1] <= pd.Timestamp("2013-12-31")
