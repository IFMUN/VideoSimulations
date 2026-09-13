"""Parameter sweeps, with the overfitting cost measured rather than ignored.

A sweep is a search, and every search inflates the best result it finds. This
module therefore always returns two things together: the leaderboard, and the
statistics that say how much of the leader's advantage is attributable to having
looked at N candidates — the deflated Sharpe and the probability of backtest
overfitting.
"""
from __future__ import annotations

from itertools import product
from typing import Any, Iterable

import pandas as pd

from ..backtest import run_backtest
from ..config import Config
from ..data import MarketData
from ..evaluate import metrics as metric_mod
from ..evaluate import stats
from ..utils import get_logger

LOG = get_logger(__name__)


def expand_grid(spec: dict[str, Iterable[Any]]) -> list[dict[str, Any]]:
    """Cartesian product of dotted-path overrides.

    ``{"portfolio.risk.target_vol": [0.08, 0.12]}`` becomes two configs. Keeping
    the grid declarative means the trial count is known before anything runs,
    which is exactly the number the deflation statistics need.
    """
    if not spec:
        return [{}]
    keys = list(spec)
    return [dict(zip(keys, values)) for values in product(*(list(spec[k]) for k in keys))]


def run_sweep(
    base: Config, data: MarketData, grid: list[dict[str, Any]],
    label: str = "sweep", compute_pbo: bool = True,
) -> dict[str, Any]:
    """Run every candidate on the same data and rank them honestly."""
    rows, series = [], {}
    for i, overrides in enumerate(grid):
        cfg = base.with_overrides(overrides or {})   # never mutate the caller's config
        cfg.name = f"{base.name}-{i:03d}"
        try:
            result = run_backtest(cfg, data)
        except Exception as exc:  # a bad corner of the grid must not kill the sweep
            LOG.warning("candidate %d failed (%s): %s", i, overrides, exc)
            continue
        summary = metric_mod.summarise(
            result.returns, result.equity, daily=result.daily
        )
        key = _label(overrides, i)
        series[key] = result.returns
        rows.append({
            "candidate": key,
            **{k: v for k, v in overrides.items()},
            "sharpe": summary["sharpe"],
            "sortino": summary["sortino"],
            "ann_return": summary["ann_return"],
            "ann_vol": summary["ann_vol"],
            "max_drawdown": summary["max_drawdown"],
            "skew": summary["skew"],
            "calmar": summary["calmar"],
            "ann_turnover": summary.get("ann_turnover"),
            "cost_drag": summary.get("cost_drag"),
        })
        LOG.info("[%s] %d/%d %s -> sharpe %.2f", label, i + 1, len(grid), key, summary["sharpe"])

    if not rows:
        raise RuntimeError("every sweep candidate failed")

    table = pd.DataFrame(rows).set_index("candidate").sort_values("sharpe", ascending=False)
    trial_returns = pd.DataFrame(series)

    best_name = table.index[0]
    significance = stats.haircut_summary(
        trial_returns[best_name], n_trials=len(table),
        trial_sharpes=table["sharpe"],
        bootstrap_samples=base.evaluation.bootstrap_samples,
        block_size=base.evaluation.block_size,
    )
    if compute_pbo and trial_returns.shape[1] >= 2:
        significance.update(stats.probability_of_backtest_overfitting(trial_returns))

    # Dispersion across the grid is itself a robustness signal: a strategy whose
    # Sharpe collapses off the optimum is a fitted point, not an effect.
    significance["sharpe_median"] = float(table["sharpe"].median())
    significance["sharpe_iqr"] = float(
        table["sharpe"].quantile(0.75) - table["sharpe"].quantile(0.25)
    )
    significance["fraction_positive"] = float((table["sharpe"] > 0).mean())

    return {
        "table": table,
        "trial_returns": trial_returns,
        "best": best_name,
        "best_overrides": grid[list(series).index(best_name)] if best_name in series else {},
        "statistics": significance,
    }


def _label(overrides: dict[str, Any], index: int) -> str:
    if not overrides:
        return "base"
    parts = []
    for key, value in overrides.items():
        short = key.split(".")[-1]
        parts.append(f"{short}={value}")
    return f"{index:03d}|" + ",".join(parts)


def sensitivity(table: pd.DataFrame, parameter: str, metric: str = "sharpe") -> pd.DataFrame:
    """Marginal effect of one swept parameter, averaging over the others.

    A parameter whose effect survives averaging over everything else is a real
    lever; one that only matters at a single combination of the others is a
    fitted artefact.
    """
    if parameter not in table.columns:
        raise KeyError(f"{parameter} was not swept; available: "
                       f"{[c for c in table.columns if c.count('.') > 0]}")
    grouped = table.groupby(parameter)[metric]
    return pd.DataFrame({
        "mean": grouped.mean(),
        "median": grouped.median(),
        "std": grouped.std(),
        "min": grouped.min(),
        "max": grouped.max(),
        "n": grouped.count(),
    })
