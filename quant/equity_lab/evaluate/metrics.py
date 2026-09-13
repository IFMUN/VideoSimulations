"""Performance metrics.

Split deliberately into *return* metrics, *risk* metrics and *implementation*
metrics. A momentum strategy can look excellent on the first group while being
untradeable on the third, so the report always shows all three together.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..utils import TRADING_DAYS


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def max_drawdown_duration(equity: pd.Series) -> int:
    """Longest run of days spent below a previous high-water mark."""
    under = equity < equity.cummax()
    if not under.any():
        return 0
    groups = (~under).cumsum()
    return int(under.groupby(groups).sum().max())


def annualised_return(returns: pd.Series) -> float:
    return float(returns.mean() * TRADING_DAYS)


def cagr(equity: pd.Series) -> float:
    years = len(equity) / TRADING_DAYS
    if years <= 0 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return float("nan")
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0)


def annualised_vol(returns: pd.Series) -> float:
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))


def sharpe(returns: pd.Series, risk_free: float = 0.0) -> float:
    excess = returns - risk_free / TRADING_DAYS
    sd = excess.std(ddof=1)
    if sd <= 0:
        return float("nan")
    return float(excess.mean() / sd * np.sqrt(TRADING_DAYS))


def sortino(returns: pd.Series, risk_free: float = 0.0) -> float:
    """Like Sharpe but penalising only downside deviation.

    For an explicitly asymmetric strategy this is the more honest ratio: the
    construction is *trying* to buy upside volatility.
    """
    excess = returns - risk_free / TRADING_DAYS
    downside = excess.clip(upper=0.0)
    dd = np.sqrt((downside ** 2).mean())
    if dd <= 0:
        return float("nan")
    return float(excess.mean() / dd * np.sqrt(TRADING_DAYS))


def calmar(equity: pd.Series, returns: pd.Series) -> float:
    mdd = abs(drawdown_series(equity).min())
    if mdd <= 0:
        return float("nan")
    return annualised_return(returns) / mdd


def tail_ratio(returns: pd.Series, quantile: float = 0.05) -> float:
    """Right tail over left tail. Below 1.0 means the bad days are bigger."""
    right = returns.quantile(1 - quantile)
    left = abs(returns.quantile(quantile))
    return float(right / left) if left > 0 else float("nan")


def value_at_risk(returns: pd.Series, level: float = 0.95) -> float:
    return float(returns.quantile(1 - level))


def conditional_var(returns: pd.Series, level: float = 0.95) -> float:
    cutoff = value_at_risk(returns, level)
    tail = returns[returns <= cutoff]
    return float(tail.mean()) if len(tail) else float("nan")


def omega(returns: pd.Series, threshold: float = 0.0) -> float:
    gains = (returns - threshold).clip(lower=0.0).sum()
    losses = -(returns - threshold).clip(upper=0.0).sum()
    return float(gains / losses) if losses > 0 else float("nan")


def capture_ratios(returns: pd.Series, benchmark: pd.Series) -> tuple[float, float]:
    """Share of benchmark up-moves and down-moves captured.

    The pair matters more than either number: a good asymmetric book has up
    capture well above down capture, whatever their absolute levels.
    """
    bench = benchmark.reindex(returns.index).dropna()
    aligned = returns.reindex(bench.index)
    up, down = bench > 0, bench < 0
    up_capture = float(aligned[up].mean() / bench[up].mean()) if up.any() and bench[up].mean() else float("nan")
    down_capture = float(aligned[down].mean() / bench[down].mean()) if down.any() and bench[down].mean() else float("nan")
    return up_capture, down_capture


def market_exposure(returns: pd.Series, benchmark: pd.Series) -> tuple[float, float]:
    """OLS alpha (annualised) and beta against the benchmark."""
    bench = benchmark.reindex(returns.index)
    both = returns.notna() & bench.notna()
    if both.sum() < 30:
        return float("nan"), float("nan")
    y, x = returns[both], bench[both]
    var = x.var(ddof=1)
    if var <= 0:
        return float("nan"), float("nan")
    beta = float(x.cov(y) / var)
    alpha = float((y.mean() - beta * x.mean()) * TRADING_DAYS)
    return alpha, beta


def summarise(
    returns: pd.Series, equity: pd.Series, benchmark: pd.Series | None = None,
    risk_free: float = 0.0, daily: pd.DataFrame | None = None,
) -> dict[str, float]:
    """The full metric block used by the report and the leaderboard."""
    returns = returns.dropna()
    out: dict[str, float] = {
        "n_days": float(len(returns)),
        "years": float(len(returns) / TRADING_DAYS),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0),
        "cagr": cagr(equity),
        "ann_return": annualised_return(returns),
        "ann_vol": annualised_vol(returns),
        "sharpe": sharpe(returns, risk_free),
        "sortino": sortino(returns, risk_free),
        "calmar": calmar(equity, returns),
        "max_drawdown": float(drawdown_series(equity).min()),
        "max_dd_days": float(max_drawdown_duration(equity)),
        "skew": float(returns.skew()),
        "excess_kurtosis": float(returns.kurt()),
        "hit_rate": float((returns > 0).mean()),
        "tail_ratio": tail_ratio(returns),
        "var_95": value_at_risk(returns),
        "cvar_95": conditional_var(returns),
        "omega": omega(returns),
        "best_day": float(returns.max()),
        "worst_day": float(returns.min()),
    }
    if benchmark is not None:
        alpha, beta = market_exposure(returns, benchmark)
        up, down = capture_ratios(returns, benchmark)
        out.update({
            "alpha": alpha, "beta": beta,
            "up_capture": up, "down_capture": down,
            "capture_spread": up - down if np.isfinite(up) and np.isfinite(down) else float("nan"),
        })
    if daily is not None and len(daily):
        equity_prev = daily["equity"].shift(1).bfill()
        out.update({
            "avg_gross": float(daily["gross_exposure"].mean()),
            "avg_net": float(daily["net_exposure"].mean()),
            "avg_positions": float(daily["n_positions"].mean()),
            "ann_turnover": float(
                daily["traded_notional"].sum() / daily["equity"].mean()
                / max(len(daily) / TRADING_DAYS, 1e-9)
            ),
            "cost_drag": float((daily["trade_cost"] / equity_prev).mean() * TRADING_DAYS),
            "carry_contribution": float((daily["carry"] / equity_prev).mean() * TRADING_DAYS),
            "stops_per_year": float(daily["stops_fired"].sum() / max(len(daily) / TRADING_DAYS, 1e-9)),
            "pct_days_throttled": float((daily["throttle"] < 0.999).mean()),
            "avg_throttle": float(daily["throttle"].mean()),
        })
    return out


def by_year(returns: pd.Series, equity: pd.Series) -> pd.DataFrame:
    """Calendar-year table — the first place a regime-dependent strategy shows itself."""
    frame = pd.DataFrame({"ret": returns, "eq": equity})
    rows = []
    for year, block in frame.groupby(frame.index.year):
        # A stub year (a warm-up tail, a partial final year) has no meaningful
        # ratio; reporting one produces absurd numbers that undermine the table.
        usable = len(block) >= 20
        rows.append({
            "year": year,
            "return": float((1 + block["ret"]).prod() - 1),
            "vol": annualised_vol(block["ret"]) if usable else float("nan"),
            "sharpe": sharpe(block["ret"]) if usable else float("nan"),
            "max_dd": float(drawdown_series(block["eq"]).min()),
            "hit_rate": float((block["ret"] > 0).mean()),
        })
    return pd.DataFrame(rows).set_index("year")


def rolling_sharpe(returns: pd.Series, window: int = TRADING_DAYS) -> pd.Series:
    mean = returns.rolling(window).mean()
    sd = returns.rolling(window).std(ddof=1)
    return (mean / sd.replace(0.0, np.nan)) * np.sqrt(TRADING_DAYS)
