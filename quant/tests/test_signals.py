import numpy as np
import pandas as pd
import pytest

from equity_lab.config import SignalSpec, TransformConfig, UniverseConfig
from equity_lab.data.universe import build_universe
from equity_lab.signals import build_score, compute_signal, signal_diagnostics
from equity_lab.signals.momentum import REGISTRY
from equity_lab.signals.transforms import apply_transforms, blend, neutralise_beta


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_signal_is_lag_safe(panel, name):
    """The value on date t must not change when data after t is removed.

    This is the property the whole backtest rests on. It is cheaper to assert
    here, per signal, than to hunt a leak through the engine later.
    """
    cut = panel.dates[-260]
    full = compute_signal(name, panel).loc[:cut]
    truncated = compute_signal(name, panel.slice(None, cut))
    common = full.index.intersection(truncated.index)
    pd.testing.assert_frame_equal(
        full.loc[common], truncated.loc[common], check_exact=False, atol=1e-10
    )


def test_momentum_skips_the_reversal_window(panel):
    """The gap must genuinely exclude the most recent month."""
    momentum = compute_signal("momentum", panel, {"lookback": 252, "gap": 21})
    manual = panel.close.shift(21) / panel.close.shift(252) - 1.0
    pd.testing.assert_frame_equal(momentum, manual)


def test_standardisation_produces_unit_cross_sections(panel):
    cfg = TransformConfig(winsorize=0.0, standardize="z", sector_neutral=False,
                          smooth_halflife=0.0)
    scores = apply_transforms(compute_signal("momentum", panel), cfg, panel.sectors)
    row = scores.dropna(how="all").iloc[-1].dropna()
    assert abs(row.mean()) < 1e-9
    assert abs(row.std(ddof=0) - 1.0) < 1e-9


def test_sector_neutralisation_removes_sector_means(panel):
    cfg = TransformConfig(winsorize=0.0, standardize="z", sector_neutral=True,
                          smooth_halflife=0.0)
    scores = apply_transforms(compute_signal("momentum", panel), cfg, panel.sectors)
    row = scores.dropna(how="all").iloc[-1].dropna()
    sector_means = row.groupby(panel.sectors.reindex(row.index)).mean()
    assert sector_means.abs().max() < 1e-8


def test_beta_neutralisation_leaves_no_beta_slope(panel):
    from equity_lab.signals.momentum import rolling_beta

    betas = rolling_beta(panel.returns(), panel.market_return(), 252)
    scores = neutralise_beta(compute_signal("momentum", panel), betas)
    row = scores.dropna(how="all").iloc[-1]
    beta_row = betas.loc[row.name]
    both = row.notna() & beta_row.notna()
    assert abs(np.corrcoef(row[both], beta_row[both])[0, 1]) < 1e-8


def test_blend_renormalises_when_a_component_is_missing():
    """A name with one missing component should get the average of what is known,
    not be shrunk toward zero by the missing piece."""
    index = pd.date_range("2020-01-01", periods=3)
    a = pd.DataFrame({"X": [1.0, 1.0, 1.0], "Y": [2.0, 2.0, 2.0]}, index=index)
    b = pd.DataFrame({"X": [3.0, 3.0, 3.0], "Y": [np.nan] * 3}, index=index)
    out = blend({"a": a, "b": b}, {"a": 0.5, "b": 0.5})
    assert out["X"].iloc[0] == pytest.approx(2.0)
    assert out["Y"].iloc[0] == pytest.approx(2.0)


def test_momentum_has_predictive_power_in_this_panel(panel):
    """A guard on the *fixture*, not the strategy: if the generated market loses
    its momentum structure, every downstream test becomes meaningless."""
    diag = signal_diagnostics(compute_signal("momentum", panel), panel, (21,))
    assert diag["mean_ic"].iloc[0] > 0.01


def test_build_score_returns_components_for_attribution(panel):
    mask = build_universe(panel, UniverseConfig(top_n_by_adv=60, min_adv_usd=0.0))
    specs = [SignalSpec("momentum", 0.6), SignalSpec("pct_52w_high", 0.4)]
    score, components = build_score(panel, specs, TransformConfig(), mask)
    assert set(components) == {"momentum", "pct_52w_high"}
    assert score.shape == panel.close.shape
    assert not score.where(~mask.astype(bool)).notna().any().any()
