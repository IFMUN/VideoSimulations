"""Vary the data-generating process and see which mechanisms survive.

A backtest answers "did this work on that history". It cannot answer "will this
work when the world is different", because there is only one history. With a
generative panel there is a second question available, and it is the more useful
one for a *risk control*: **how does this mechanism's value change as the hazard
it targets gets stronger or weaker?**

A control whose benefit is flat in the hazard is not doing what its docstring
claims. A control that is inert when the hazard is absent and pays increasingly
as it intensifies is behaving like insurance — which is the profile you want,
because it means the mechanism costs nothing in the states where it is not
needed. Reading that gradient is a far stronger test than any single Sharpe
comparison, and it is the closest available substitute for out-of-sample data
from a market that has not happened yet.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..backtest import run_backtest
from ..config import Config
from ..data import load_market_data
from ..evaluate import metrics as metric_mod
from ..utils import get_logger

LOG = get_logger(__name__)

#: The generator knob that controls how hard prior losers rebound after a
#: drawdown — i.e. how crash-prone momentum is in this world.
DEFAULT_AXIS = "data.synthetic.crash_intensity"
DEFAULT_LEVELS = (0.0, 1.0, 2.2)

_METRICS = ("sharpe", "max_drawdown", "skew", "ann_turnover")


def run_stress_test(
    base: Config,
    variants: dict[str, dict[str, Any]],
    seeds: list[int],
    axis: str = DEFAULT_AXIS,
    levels: tuple[float, ...] = DEFAULT_LEVELS,
    reference: str = "full",
) -> dict[str, Any]:
    """Run every variant at every hazard level on every seed.

    Returns the raw grid, the mean surface, and — the part that matters — the
    per-level sign consistency of each variant's effect relative to ``reference``.
    """
    rows = []
    total = len(levels) * len(seeds) * len(variants)
    done = 0
    for level in levels:
        for seed in seeds:
            data = load_market_data(
                base.with_overrides({axis: level}).data, seed=seed, use_cache=False
            )
            for name, overrides in variants.items():
                cfg = base.with_overrides({**overrides, "seed": seed, axis: level})
                cfg.name = f"{base.name}::{name.replace(' ', '_')}"
                result = run_backtest(cfg, data)
                summary = metric_mod.summarise(
                    result.returns, result.equity, daily=result.daily
                )
                rows.append({
                    "level": level, "seed": seed, "variant": name,
                    **{m: summary.get(m) for m in _METRICS},
                })
                done += 1
                LOG.info("stress %d/%d  %s=%s seed=%s %s -> sharpe %.3f",
                         done, total, axis.split(".")[-1], level, seed, name,
                         summary["sharpe"])

    grid = pd.DataFrame(rows)
    surface = grid.groupby(["level", "variant"])[list(_METRICS)].mean()

    consistency: dict[str, pd.DataFrame] = {}
    for metric in _METRICS:
        wide = grid.pivot_table(index=["level", "seed"], columns="variant", values=metric)
        if reference not in wide.columns:
            continue
        delta = wide.sub(wide[reference], axis=0).drop(columns=reference)
        consistency[metric] = pd.DataFrame({
            column: delta[column].groupby(level=0).apply(
                lambda s: float(max((s > 0).mean(), (s < 0).mean()))
            )
            for column in delta.columns
        })

    return {
        "grid": grid,
        "surface": surface,
        "deltas": {
            metric: (
                lambda wide: wide.sub(wide[reference], axis=0).drop(columns=reference)
            )(grid.pivot_table(index=["level", "seed"], columns="variant", values=metric))
            for metric in _METRICS
            if reference in grid["variant"].unique()
        },
        "sign_consistency": consistency,
        "axis": axis,
        "levels": list(levels),
        "seeds": list(seeds),
    }


def gradient(outcome: dict[str, Any], metric: str = "max_drawdown") -> pd.DataFrame:
    """Mean effect of removing each mechanism, as the hazard intensifies.

    A column that trends away from zero as the level rises is a mechanism whose
    value is genuinely contingent on the hazard. A flat column is a mechanism
    doing something other than what it claims.
    """
    delta = outcome["deltas"].get(metric)
    if delta is None or delta.empty:
        return pd.DataFrame()
    mean = delta.groupby(level=0).mean()
    consistent = outcome["sign_consistency"].get(metric)
    if consistent is not None:
        mean = mean.join(consistent, rsuffix=" (sign consistency)")
    return mean.replace([np.inf, -np.inf], np.nan)
