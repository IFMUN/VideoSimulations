"""Figures for the research report.

Charts are rendered to base64 PNGs and embedded, so a report is a single file
that can be emailed or archived with no asset directory to lose.

Colour follows one rule: hues carry *identity* (which series), never rank or
magnitude. The three categorical slots below are a validated colourblind-safe
set; charts are capped at three series so no chart ever needs a fourth. Every
chart that uses colour also direct-labels or legends its series, so identity is
never carried by colour alone.
"""
from __future__ import annotations

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from ..utils import TRADING_DAYS  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]      # blue, orange, aqua
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e4e3df"


def _style(ax, title: str, ylabel: str = "") -> None:
    """Recessive axes and grid: the data should be the only assertive thing."""
    ax.set_title(title, fontsize=11, color=INK, loc="left", pad=10)
    ax.set_ylabel(ylabel, fontsize=9, color=INK_MUTED)
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=8.5)


def _encode(fig) -> str:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=140, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _figure(height: float = 3.2):
    fig, ax = plt.subplots(figsize=(9.5, height), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    return fig, ax


def equity_curve(equity: pd.Series, benchmark: pd.Series | None = None) -> str:
    """Growth of one unit, log scale — equal vertical distance is equal *return*."""
    fig, ax = _figure(3.6)
    normalised = equity / equity.iloc[0]
    ax.plot(normalised.index, normalised.values, color=SERIES[0], linewidth=2.0, label="Strategy")
    ax.annotate("Strategy", (normalised.index[-1], normalised.iloc[-1]),
                textcoords="offset points", xytext=(6, 0), color=SERIES[0], fontsize=9)
    if benchmark is not None and len(benchmark.dropna()) > 1:
        bench = (1 + benchmark.reindex(equity.index).fillna(0.0)).cumprod()
        ax.plot(bench.index, bench.values, color=SERIES[1], linewidth=1.6,
                linestyle="--", label="Benchmark")
        ax.annotate("Benchmark", (bench.index[-1], bench.iloc[-1]),
                    textcoords="offset points", xytext=(6, 0), color=SERIES[1], fontsize=9)
    ax.set_yscale("log")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    _style(ax, "Growth of 1.00 (log scale)", "multiple")
    return _encode(fig)


def drawdown(equity: pd.Series) -> str:
    fig, ax = _figure(2.4)
    dd = equity / equity.cummax() - 1.0
    ax.fill_between(dd.index, dd.values, 0.0, color=SERIES[0], alpha=0.22, linewidth=0)
    ax.plot(dd.index, dd.values, color=SERIES[0], linewidth=1.4)
    trough = dd.idxmin()
    ax.annotate(f"{dd.min():.1%}", (trough, dd.min()), textcoords="offset points",
                xytext=(6, -2), color=INK, fontsize=9)
    _style(ax, "Drawdown from high-water mark", "")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    return _encode(fig)


def rolling_sharpe(returns: pd.Series, window: int = TRADING_DAYS) -> str:
    fig, ax = _figure(2.4)
    mean = returns.rolling(window).mean()
    sd = returns.rolling(window).std(ddof=1).replace(0.0, np.nan)
    series = (mean / sd) * np.sqrt(TRADING_DAYS)
    ax.axhline(0.0, color=INK_MUTED, linewidth=1.0)
    ax.plot(series.index, series.values, color=SERIES[0], linewidth=1.8)
    _style(ax, f"Rolling {window}-day Sharpe", "Sharpe")
    return _encode(fig)


def exposures(daily: pd.DataFrame) -> str:
    """Gross, net and the throttle level — all dimensionless, so one axis is honest."""
    fig, ax = _figure(2.6)
    ax.plot(daily.index, daily["gross_exposure"], color=SERIES[0], linewidth=1.5, label="Gross")
    ax.plot(daily.index, daily["net_exposure"], color=SERIES[1], linewidth=1.5, label="Net")
    ax.plot(daily.index, daily["throttle"], color=SERIES[2], linewidth=1.5,
            linestyle="--", label="Risk throttle")
    ax.axhline(0.0, color=INK_MUTED, linewidth=0.8)
    ax.legend(frameon=False, fontsize=9, ncol=3, loc="upper left")
    _style(ax, "Exposure and the asymmetric risk throttle", "x equity")
    return _encode(fig)


def return_distribution(returns: pd.Series) -> str:
    """Histogram against a fitted normal — the gap *is* the tail risk."""
    fig, ax = _figure(2.6)
    values = returns.dropna()
    ax.hist(values, bins=90, color=SERIES[0], alpha=0.75, linewidth=0)
    grid = np.linspace(values.min(), values.max(), 400)
    normal = (
        np.exp(-0.5 * ((grid - values.mean()) / values.std()) ** 2)
        / (values.std() * np.sqrt(2 * np.pi))
    )
    ax.plot(grid, normal * len(values) * (values.max() - values.min()) / 90,
            color=SERIES[1], linewidth=1.8, label="Normal, same mean/vol")
    ax.legend(frameon=False, fontsize=9)
    _style(ax, f"Daily returns (skew {values.skew():.2f}, excess kurtosis {values.kurt():.1f})",
           "days")
    return _encode(fig)


def decile_chart(deciles: pd.DataFrame) -> str:
    """One hue: the bars encode magnitude, and the baseline already shows sign."""
    fig, ax = _figure(2.6)
    values = deciles["annualised"]
    ax.bar(values.index, values.values, color=SERIES[0], width=0.68)
    ax.axhline(0.0, color=INK_MUTED, linewidth=1.0)
    for x, y in values.items():
        ax.annotate(f"{y:.1%}", (x, y), ha="center", fontsize=7.5, color=INK_MUTED,
                    textcoords="offset points", xytext=(0, 3 if y >= 0 else -10))
    ax.set_xticks(list(values.index))
    _style(ax, "Forward return by score decile (annualised)", "")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    return _encode(fig)


def yearly_returns(table: pd.DataFrame) -> str:
    fig, ax = _figure(2.4)
    ax.bar(table.index, table["return"], color=SERIES[0], width=0.68)
    ax.axhline(0.0, color=INK_MUTED, linewidth=1.0)
    for x, y in table["return"].items():
        ax.annotate(f"{y:.0%}", (x, y), ha="center", fontsize=7.5, color=INK_MUTED,
                    textcoords="offset points", xytext=(0, 3 if y >= 0 else -10))
    _style(ax, "Calendar-year return", "")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    return _encode(fig)


def comparison_bars(values: pd.Series, title: str, fmt: str = "{:.2f}") -> str:
    """Horizontal bars for comparing variants — labels read left to right."""
    fig, ax = plt.subplots(figsize=(9.5, max(2.0, 0.42 * len(values) + 1.0)), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    order = values.sort_values()
    ax.barh(range(len(order)), order.values, color=SERIES[0], height=0.62)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(order.index, fontsize=9)
    ax.axvline(0.0, color=INK_MUTED, linewidth=1.0)
    span = max(abs(order.max()), abs(order.min()), 1e-9)
    for i, v in enumerate(order.values):
        ax.annotate(fmt.format(v), (v, i), va="center", fontsize=8, color=INK_MUTED,
                    textcoords="offset points",
                    xytext=(5 if v >= 0 else -5, 0), ha="left" if v >= 0 else "right")
    ax.set_xlim(min(0, order.min()) - 0.18 * span, max(0, order.max()) + 0.18 * span)
    _style(ax, title, "")
    return _encode(fig)


def equity_overlay(curves: dict[str, pd.Series], title: str) -> str:
    """At most three curves — beyond that, identity by colour stops being readable."""
    fig, ax = _figure(3.4)
    for (name, series), colour in zip(list(curves.items())[:3], SERIES):
        normalised = series / series.iloc[0]
        ax.plot(normalised.index, normalised.values, color=colour, linewidth=1.8, label=name)
        ax.annotate(name, (normalised.index[-1], normalised.iloc[-1]),
                    textcoords="offset points", xytext=(6, 0), color=colour, fontsize=9)
    ax.set_yscale("log")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    _style(ax, title, "multiple")
    return _encode(fig)
