import pandas as pd
import pytest

from equity_lab.research import expand_grid, run_experiment, run_sweep, leaderboard
from equity_lab.research.walkforward import make_splits, purge_mask, run_walkforward


def test_grid_expansion_counts_trials_before_anything_runs():
    grid = expand_grid({"a": [1, 2, 3], "b": [10, 20]})
    assert len(grid) == 6
    assert {"a": 2, "b": 20} in grid
    assert expand_grid({}) == [{}]


def test_walkforward_splits_never_train_on_the_test_window():
    dates = pd.bdate_range("2005-01-01", "2024-12-31")
    splits = make_splits(dates, 4, 5.0, 2.0, embargo_days=21, anchored=True)
    assert len(splits) >= 3
    for split in splits:
        assert split.train_end < split.test_start
        gap = len(dates[(dates > split.train_end) & (dates < split.test_start)])
        assert gap >= 20                       # the embargo is real


def test_purge_mask_excludes_the_test_block_and_its_horizon():
    dates = pd.bdate_range("2010-01-01", "2020-12-31")
    test_start, test_end = pd.Timestamp("2016-01-04"), pd.Timestamp("2017-12-29")
    mask = purge_mask(dates, test_start, test_end, horizon_days=21, embargo_days=21)
    assert not mask.loc[test_start:test_end].any()
    # a training date whose 21-day evaluation window reaches into the test block
    # must also be dropped
    boundary = dates[dates < test_start][-10]
    assert not bool(mask.loc[boundary])


def test_splits_reject_a_sample_that_is_too_short():
    with pytest.raises(ValueError, match="too short"):
        make_splits(pd.bdate_range("2020-01-01", "2021-01-01"), 3, 5.0, 2.0, 21)


def test_sweep_reports_dispersion_not_just_the_winner(config, panel, tmp_path):
    config.data.start, config.data.end = "2010-01-04", "2015-12-31"
    config.output_dir = str(tmp_path)
    grid = expand_grid({"portfolio.risk.target_vol": [0.08, 0.12]})
    outcome = run_sweep(config, panel, grid, compute_pbo=False)
    assert len(outcome["table"]) == 2
    assert "sharpe_median" in outcome["statistics"]
    assert outcome["table"]["sharpe"].is_monotonic_decreasing


def test_walkforward_returns_only_out_of_sample_returns(config, panel, tmp_path):
    config.data.start, config.data.end = "2010-01-04", "2017-12-29"
    config.walkforward.n_splits = 2
    config.walkforward.train_years = 3.0
    config.walkforward.test_years = 1.5
    outcome = run_walkforward(config, panel, [{}])
    assert len(outcome["oos_returns"]) > 100
    assert outcome["oos_returns"].index.is_monotonic_increasing
    assert not outcome["oos_returns"].index.duplicated().any()
    for _, fold in outcome["folds"].iterrows():
        assert fold["oos_days"] > 0


def test_experiment_writes_a_reproducible_artifact_set(config, panel, tmp_path):
    config.data.start, config.data.end = "2010-01-04", "2015-12-31"
    config.output_dir = str(tmp_path)
    experiment = run_experiment(config, panel, write=True, make_report=True)
    path = experiment.path
    for name in ("config.yaml", "manifest.json", "metrics.json", "daily.csv", "report.html"):
        assert (path / name).exists(), f"missing artifact {name}"
    assert (path / "report.html").read_text().startswith("<!doctype html>")

    board = leaderboard(tmp_path)
    assert experiment.run_id in board.index
    assert "sharpe" in board.columns


def test_marginal_ic_separates_redundant_from_independent_signals():
    """A duplicate of an existing component must lose nearly all of its IC once
    orthogonalised, while an independent one keeps most of its own.

    This is the diagnostic that decides a blend, so the claim under test is the
    *relative* one: redundancy is a ratio, not a level. Thresholds are set well
    outside the estimator's own standard error at this sample size.
    """
    import numpy as np
    from equity_lab.evaluate.attribution import marginal_ic

    rng = np.random.default_rng(0)
    dates = pd.date_range("2012-01-01", periods=1600, freq="B")
    names = [f"A{i:03d}" for i in range(150)]

    def frame():
        return pd.DataFrame(rng.normal(size=(len(dates), len(names))),
                            index=dates, columns=names)

    truth, other, noise = frame(), frame(), frame()
    signal = truth + other + 2.5 * noise          # what the next 21 days will pay

    # Build prices so that close[t+21]/close[t] - 1 really is driven by signal[t].
    daily = 0.01 * signal.shift(21) / 21.0
    close = 100.0 * (1.0 + daily.fillna(0.0)).cumprod()

    components = {"truth": truth, "clone": truth + 0.05 * frame(), "other": other}
    table = marginal_ic(components, close, horizon=21, step=10)

    clone, independent = table.loc["clone"], table.loc["other"]
    assert clone["retained"] < 0.25, "a duplicate should keep almost none of its IC"
    assert independent["retained"] > 0.60, "an independent signal should keep most of its IC"
    assert clone["marginal_ic"] < independent["marginal_ic"] / 3.0


def test_stress_test_reports_sign_consistency_per_hazard_level(config, panel, tmp_path):
    """The stress grid must separate 'this mechanism tracks the hazard' from
    'this mechanism does something unrelated'."""
    from equity_lab.research.stress import gradient, run_stress_test

    config.data.start, config.data.end = "2010-01-04", "2014-12-31"
    config.data.synthetic.n_assets = 60
    config.output_dir = str(tmp_path)
    variants = {"full": {}, "no drawdown throttle":
                {"portfolio.asymmetry.drawdown_throttle.enabled": False}}
    outcome = run_stress_test(config, variants, seeds=[5], levels=(0.0, 2.2))

    assert set(outcome["grid"]["level"]) == {0.0, 2.2}
    assert len(outcome["surface"]) == 4
    table = gradient(outcome, "max_drawdown")
    assert "no drawdown throttle" in table.columns
    assert list(table.index) == [0.0, 2.2]
