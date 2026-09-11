"""Where the return actually came from.

A headline Sharpe says nothing about whether a strategy is doing what it claims.
These decompositions are the check: if a "market-neutral momentum" book earns
everything in one sector, in one regime, or entirely from its long leg during a
bull market, that is worth knowing before committing capital to it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..utils import TRADING_DAYS


def leg_attribution(weights: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    """Split daily P&L into long-leg and short-leg contributions.

    Weights are lagged one day: the book you held into ``t`` earns ``t``'s return.
    """
    lagged = weights.shift(1)
    aligned = returns.reindex_like(lagged)
    contrib = (lagged * aligned).fillna(0.0)
    long_contrib = contrib.where(lagged > 0, 0.0).sum(axis=1)
    short_contrib = contrib.where(lagged < 0, 0.0).sum(axis=1)
    return pd.DataFrame({
        "long_leg": long_contrib,
        "short_leg": short_contrib,
        "total": long_contrib + short_contrib,
    })


def sector_attribution(
    weights: pd.DataFrame, returns: pd.DataFrame, sectors: pd.Series
) -> pd.DataFrame:
    """Annualised contribution and average net exposure per sector."""
    lagged = weights.shift(1)
    contrib = (lagged * returns.reindex_like(lagged)).fillna(0.0)
    groups = sectors.reindex(contrib.columns)
    known = groups.dropna()
    if known.empty:
        return pd.DataFrame()
    by_sector = contrib[known.index].T.groupby(known).sum().T
    exposure = lagged[known.index].T.groupby(known).sum().T
    return pd.DataFrame({
        "ann_contribution": by_sector.mean() * TRADING_DAYS,
        "contribution_vol": by_sector.std() * np.sqrt(TRADING_DAYS),
        "avg_net_exposure": exposure.mean(),
        "share_of_total": by_sector.sum() / by_sector.sum().sum()
        if by_sector.sum().sum() != 0 else np.nan,
    }).sort_values("ann_contribution", ascending=False)


def regime_attribution(returns: pd.Series, state: pd.DataFrame) -> pd.DataFrame:
    """Performance conditioned on the market state the overlay reacts to.

    This is the direct test of the crash machinery: the interesting cell is
    "bear and volatility-stressed", where an unprotected momentum book bleeds.
    """
    aligned = state.reindex(returns.index)
    label = pd.Series("calm", index=returns.index, dtype=object)
    bear = aligned["bear"].fillna(False).astype(bool)
    stressed = aligned["vol_stressed"].fillna(False).astype(bool)
    label[bear & ~stressed] = "bear"
    label[~bear & stressed] = "volatile"
    label[bear & stressed] = "bear+volatile"

    rows = []
    for name, block in returns.groupby(label):
        sd = block.std(ddof=1)
        rows.append({
            "regime": name,
            "n_days": len(block),
            "share_of_days": len(block) / len(returns),
            "ann_return": block.mean() * TRADING_DAYS,
            "ann_vol": sd * np.sqrt(TRADING_DAYS),
            "sharpe": block.mean() / sd * np.sqrt(TRADING_DAYS) if sd > 0 else np.nan,
            "worst_day": block.min(),
            "cumulative": (1 + block).prod() - 1,
        })
    return pd.DataFrame(rows).set_index("regime")


def decile_spread(
    score: pd.DataFrame, close: pd.DataFrame, horizon: int = 21,
    step: int = 21, n_buckets: int = 10,
) -> pd.DataFrame:
    """Average forward return by score decile.

    The single most informative signal diagnostic there is. A monotone staircase
    means the score orders the cross-section; a flat middle with two spiky ends
    means the result rests on a handful of extreme names and will not survive
    position limits or transaction costs.
    """
    forward = close.shift(-horizon) / close - 1.0
    sample = score.index[::step]
    buckets: dict[int, list[float]] = {i: [] for i in range(n_buckets)}
    for date in sample:
        s, f = score.loc[date], forward.loc[date]
        both = s.notna() & f.notna()
        if both.sum() < n_buckets * 3:
            continue
        ranks = s[both].rank(pct=True)
        labels = np.minimum((ranks * n_buckets).astype(int), n_buckets - 1)
        for bucket, value in f[both].groupby(labels).mean().items():
            buckets[int(bucket)].append(float(value))

    rows = []
    for bucket, values in buckets.items():
        if not values:
            continue
        series = pd.Series(values)
        rows.append({
            "decile": bucket + 1,
            "mean_forward_return": series.mean(),
            "annualised": series.mean() * (TRADING_DAYS / horizon),
            "hit_rate": float((series > 0).mean()),
            "n_periods": len(series),
        })
    frame = pd.DataFrame(rows).set_index("decile")
    if len(frame) >= 2:
        frame.attrs["top_minus_bottom"] = float(
            frame["annualised"].iloc[-1] - frame["annualised"].iloc[0]
        )
        # Rank correlation via explicit ranks rather than method="spearman": the
        # latter pulls in scipy, and the statistical core here deliberately runs
        # on numpy and pandas alone.
        values = frame["annualised"].reset_index(drop=True)
        frame.attrs["monotonicity"] = (
            float(values.rank().corr(pd.Series(range(len(frame)), dtype=float).rank()))
            if len(frame) > 2 else np.nan
        )
    return frame


def signal_attribution(
    components: dict[str, pd.DataFrame], close: pd.DataFrame,
    horizon: int = 21, step: int = 21,
) -> pd.DataFrame:
    """Per-component information coefficients and their correlations.

    Two components with an IC of 0.04 each are worth far more when their ICs are
    uncorrelated than when they are the same bet under two names.
    """
    from ..signals import signal_diagnostics

    rows, series = [], {}
    forward = close.shift(-horizon) / close - 1.0
    for name, frame in components.items():
        diag = signal_diagnostics(frame, _PanelShim(close), (horizon,), step)
        if len(diag):
            row = diag.iloc[0].to_dict()
            row["signal"] = name
            rows.append(row)
        ics = []
        for date in frame.index[::step]:
            a, b = frame.loc[date], forward.loc[date]
            both = a.notna() & b.notna()
            ics.append(a[both].rank().corr(b[both].rank()) if both.sum() >= 20 else np.nan)
        series[name] = pd.Series(ics, index=frame.index[::step])

    table = pd.DataFrame(rows).set_index("signal") if rows else pd.DataFrame()
    if len(series) > 1:
        table.attrs["ic_correlation"] = pd.DataFrame(series).corr()
    return table


def marginal_ic(
    components: dict[str, pd.DataFrame], close: pd.DataFrame,
    horizon: int = 21, step: int = 21, min_names: int = 30,
) -> pd.DataFrame:
    """Information each component adds *beyond the others*.

    A raw IC answers "does this signal predict?". The question that actually
    decides a blend is "does this signal predict anything the rest of the blend
    does not?" — so each component is regressed cross-sectionally on all the
    others and the IC of its **residual** is measured.

    The gap between ``mean_ic`` and ``marginal_ic`` is the redundancy. A
    component whose marginal IC collapses to zero is being paid a weight for
    information already present; one that holds its IC after orthogonalisation is
    carrying its own bet, however correlated it looks.
    """
    names = list(components)
    if len(names) < 2:
        return pd.DataFrame()

    forward = close.shift(-horizon) / close - 1.0
    index = components[names[0]].index
    raw_ic: dict[str, list[float]] = {n: [] for n in names}
    residual_ic: dict[str, list[float]] = {n: [] for n in names}

    for date in index[::step]:
        fwd = forward.loc[date]
        frame = pd.DataFrame({n: components[n].loc[date] for n in names})
        usable = frame.notna().all(axis=1) & fwd.notna()
        if usable.sum() < min_names:
            continue
        block = frame[usable]
        target = fwd[usable].rank()

        for name in names:
            raw_ic[name].append(block[name].rank().corr(target))

            others = block.drop(columns=name).to_numpy(dtype=float)
            design = np.column_stack([np.ones(len(others)), others])
            y = block[name].to_numpy(dtype=float)
            try:
                coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
            except np.linalg.LinAlgError:  # pragma: no cover - degenerate cross-section
                continue
            residual = pd.Series(y - design @ coefficients, index=block.index)
            if residual.std() < 1e-12:
                residual_ic[name].append(0.0)
            else:
                residual_ic[name].append(residual.rank().corr(target))

    rows = []
    for name in names:
        raw = pd.Series(raw_ic[name], dtype=float).dropna()
        marginal = pd.Series(residual_ic[name], dtype=float).dropna()
        if raw.empty or marginal.empty:
            continue
        rows.append({
            "signal": name,
            "mean_ic": raw.mean(),
            "marginal_ic": marginal.mean(),
            "retained": marginal.mean() / raw.mean() if abs(raw.mean()) > 1e-9 else np.nan,
            "marginal_t": (marginal.mean() / marginal.std() * np.sqrt(len(marginal))
                           if marginal.std() > 0 else np.nan),
            "n_periods": len(marginal),
        })
    return pd.DataFrame(rows).set_index("signal").sort_values("marginal_ic", ascending=False)


class _PanelShim:
    """Minimal duck-type so signal diagnostics can run on a bare price frame."""

    def __init__(self, close: pd.DataFrame):
        self.close = close
