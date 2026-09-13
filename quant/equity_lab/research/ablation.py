"""Ablation of the asymmetric risk construction.

The claim "asymmetric construction improves momentum" is only worth anything if
it can be falsified. This module turns each component off in turn, on identical
data with an identical signal, and reports what changed. If a component's row is
indistinguishable from the full model, that component is decoration and should
be deleted rather than defended.

Read the table by *skew* and *max drawdown* first, not Sharpe. Every piece of
this construction is paid for in expected return; the question is whether the
tail it buys back is worth the premium.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ..backtest import run_backtest
from ..config import Config
from ..data import MarketData
from ..evaluate import metrics as metric_mod
from ..utils import get_logger

LOG = get_logger(__name__)

A = "portfolio.asymmetry"

#: Each variant disables exactly one mechanism, so differences are attributable.
VARIANTS: dict[str, dict[str, Any]] = {
    "full": {},
    "symmetric response": {
        f"{A}.long_convexity": 1.0,
        f"{A}.short_convexity": 1.0,
        f"{A}.base_short_ratio": 1.0,
    },
    "no downside risk sizing": {
        f"{A}.downside_risk_weight": 0.0,
        f"{A}.downside_beta_penalty": 0.0,
    },
    "no crash-state short cut": {f"{A}.crash_overlay.bear_short_scale": 1.0},
    "no constant-vol scaling": {f"{A}.crash_overlay.enabled": False},
    "no drawdown throttle": {f"{A}.drawdown_throttle.enabled": False},
    # The base configuration has stops off; this row measures what adding them costs.
    "with trailing stops": {f"{A}.stops.enabled": True},
    "symmetric baseline": {
        f"{A}.long_convexity": 1.0,
        f"{A}.short_convexity": 1.0,
        f"{A}.base_short_ratio": 1.0,
        f"{A}.downside_risk_weight": 0.0,
        f"{A}.downside_beta_penalty": 0.0,
        f"{A}.crash_overlay.enabled": False,
        f"{A}.crash_overlay.bear_short_scale": 1.0,
        f"{A}.drawdown_throttle.enabled": False,
        f"{A}.stops.enabled": False,
    },
}

_COLUMNS = [
    "sharpe", "sortino", "calmar", "ann_return", "ann_vol", "max_drawdown",
    "skew", "tail_ratio", "cvar_95", "worst_day", "ann_turnover", "cost_drag",
]


def run_ablation(
    base: Config, data: MarketData, variants: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run each variant and return a comparison table plus the equity curves."""
    variants = variants if variants is not None else VARIANTS
    rows, curves, returns = [], {}, {}
    for name, overrides in variants.items():
        # Always copy, even for the empty override set: `with_overrides({})`
        # returns a new Config, whereas reusing `base` and renaming it would
        # mutate the caller's object and silently rename their run directory.
        cfg = base.with_overrides(overrides or {})
        cfg.name = f"{base.name}::{name.replace(' ', '_')}"
        LOG.info("ablation: %s", name)
        result = run_backtest(cfg, data)
        summary = metric_mod.summarise(result.returns, result.equity, daily=result.daily)
        rows.append({"variant": name, **{c: summary.get(c) for c in _COLUMNS}})
        curves[name] = result.equity
        returns[name] = result.returns

    table = pd.DataFrame(rows).set_index("variant")
    if "full" in table.index:
        delta = table.subtract(table.loc["full"], axis=1).drop(index="full")
        delta.index = [f"{i} (vs full)" for i in delta.index]
    else:  # pragma: no cover
        delta = pd.DataFrame()
    return {"table": table, "delta": delta, "curves": curves, "returns": pd.DataFrame(returns)}


def run_multiseed_ablation(
    base: Config, seeds: list[int], variants: dict[str, dict[str, Any]] | None = None,
    reference: str = "full",
) -> dict[str, Any]:
    """Repeat the ablation on independent panels and test the *sign* of each effect.

    A single-panel ablation is a single draw. Differences of 0.03-0.2 in Sharpe
    sit comfortably inside the sampling error of a ten-year backtest, so reading
    one column of one table and declaring a mechanism dead is precisely the
    selection error the rest of this package exists to prevent — and it is easy
    to walk into, because the table looks so definitive.

    The robust question is not "how big was the difference" but "did it point the
    same way every time". ``sign_consistency`` reports the share of panels on
    which a variant moved a metric in the same direction, so a result that flips
    across panels is visible as inconclusive rather than quoted as a finding.
    """
    from ..data import load_market_data

    variants = variants if variants is not None else VARIANTS
    frames = []
    for seed in seeds:
        cfg = base.with_overrides({"seed": seed})
        data = load_market_data(cfg.data, seed=seed, use_cache=False)
        outcome = run_ablation(cfg, data, variants)
        frames.append(outcome["table"].assign(seed=seed))

    stacked = pd.concat(frames)
    mean = stacked.drop(columns="seed").groupby(level=0).mean()
    dispersion = stacked.drop(columns="seed").groupby(level=0).std()

    consistency = {}
    for metric in ("sharpe", "max_drawdown", "skew", "ann_turnover"):
        wide = stacked.reset_index().pivot(index="seed", columns="variant", values=metric)
        if reference not in wide.columns:
            continue
        delta = wide.sub(wide[reference], axis=0).drop(columns=reference)
        # 1.0 means the variant moved the metric the same way on every panel;
        # values near 0.5 mean the effect is indistinguishable from noise here.
        consistency[metric] = pd.Series({
            column: float(max((delta[column] > 0).mean(), (delta[column] < 0).mean()))
            for column in delta.columns
        })
        consistency[metric].name = metric

    return {
        "per_seed": stacked,
        "mean": mean,
        "std": dispersion,
        "sign_consistency": pd.DataFrame(consistency),
        "seeds": seeds,
    }
