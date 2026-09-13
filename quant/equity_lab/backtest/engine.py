"""The backtest engine.

Deliberately a *sequential* daily loop rather than a vectorised
``(weights.shift(1) * returns).sum()`` one-liner, for two reasons:

1. Parts of the strategy feed back on realised results — the drawdown throttle
   reads the equity curve, the volatility scaler reads the strategy's own
   realised volatility, and trailing stops read marks since entry. None of that
   can be expressed as a shift.
2. The lag policy lives in exactly one place. A decision taken on date ``d`` is
   filled on ``d + execution_lag`` at that day's price, so no information from
   the fill date can reach the decision.

Positions are carried in **shares**, not weights, so drift between rebalances,
cash balances, financing and delisting are real rather than assumed away. The
day is ordered: mark -> accrue carry -> feedback controls -> stops -> rebalance
decision -> execute -> record. Costs incurred on day ``t`` are therefore in day
``t``'s return, and the gross/net decomposition is exact by construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import Config
from ..data.panel import MarketData
from ..data.universe import build_universe
from ..portfolio.asymmetry import DrawdownThrottle, StopTracker, VolatilityScaler
from ..portfolio.construction import ConstructionInputs, PortfolioConstructor
from ..signals import build_score
from ..signals.momentum import rolling_beta
from ..utils import TRADING_DAYS, get_logger, rebalance_dates
from .costs import CostModel

LOG = get_logger(__name__)


@dataclass
class BacktestResult:
    """Everything a downstream evaluator or report needs, and nothing implicit."""

    equity: pd.Series
    returns: pd.Series
    gross_returns: pd.Series
    weights: pd.DataFrame
    daily: pd.DataFrame
    rebalances: pd.DataFrame
    score: pd.DataFrame
    components: dict[str, pd.DataFrame] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def cost_drag(self) -> float:
        """Annualised return given up to spread and market impact.

        Financing and borrow are reported separately as ``carry_contribution``:
        netting them here would let cash interest flatter a strategy's apparent
        trading efficiency.
        """
        return float((self.gross_returns.mean() - self.returns.mean()) * TRADING_DAYS)

    @property
    def carry_contribution(self) -> float:
        """Annualised contribution of cash interest net of borrow and financing."""
        return float((self.daily["carry"] / self.equity.shift(1).bfill()).mean() * TRADING_DAYS)

    @property
    def annual_turnover(self) -> float:
        """One-way traded notional per unit of average equity, per year."""
        years = max(len(self.daily) / TRADING_DAYS, 1e-9)
        return float(self.daily["traded_notional"].sum() / self.equity.mean() / years)


def run_backtest(cfg: Config, data: MarketData) -> BacktestResult:
    returns = data.returns()
    market = data.market_return()
    mask = build_universe(data, cfg.data.universe)
    betas = rolling_beta(returns, market, cfg.portfolio.risk.beta_window)

    score, components = build_score(data, cfg.signals, cfg.transforms, mask, betas)
    inputs = ConstructionInputs.build(data, score, mask, cfg.portfolio)
    constructor = PortfolioConstructor(cfg.portfolio, inputs)

    cost_model = CostModel(cfg.backtest.costs)
    throttle = DrawdownThrottle(cfg.portfolio.asymmetry.drawdown_throttle)
    scaler = VolatilityScaler(cfg.portfolio.asymmetry.crash_overlay)

    dates = data.dates
    tickers = data.tickers
    stops = StopTracker(cfg.portfolio.asymmetry.stops, tickers)

    close_arr = data.close.to_numpy(dtype=float)
    adv_arr = data.adv(21).shift(1).to_numpy(dtype=float)
    vol_arr = returns.rolling(63, min_periods=20).std().shift(1).to_numpy(dtype=float)
    short_scale_series = inputs.state["short_scale"].reindex(dates).fillna(1.0)

    warmup = max(
        cfg.data.universe.min_history_days,
        cfg.portfolio.risk.beta_window,
        cfg.portfolio.asymmetry.downside_window,
    )
    if warmup >= len(dates) - 60:
        raise ValueError(
            f"not enough history: {len(dates)} days for a {warmup}-day warm-up. "
            "Lengthen the sample or shorten the lookbacks."
        )
    rebal = set(rebalance_dates(dates[warmup:], cfg.backtest.rebalance))
    lag = max(int(cfg.backtest.execution_lag), 0)

    n = len(tickers)
    shares = np.zeros(n)
    cash = float(cfg.backtest.capital)
    equity_prev = cash
    standing_target = pd.Series(0.0, index=tickers, dtype=float)

    pending_target: dict[pd.Timestamp, np.ndarray] = {}
    pending_exit: dict[pd.Timestamp, np.ndarray] = {}
    open_orders = np.zeros(n)      # signed notional still to be worked
    order_age = np.zeros(n, dtype=np.int32)
    order_horizon = max(int(cfg.backtest.order_horizon_days), 1)
    weight_rows, daily_rows, rebalance_rows = {}, [], []

    for i in range(warmup, len(dates)):
        date = dates[i]
        prices = close_arr[i]
        tradeable = np.isfinite(prices) & (prices > 0)

        # --- 1. mark to market; liquidate anything that stopped printing ---
        held = np.abs(shares) > 1e-12
        gone = held & ~tradeable
        if gone.any():
            last_good = close_arr[i - 1]
            proceeds = np.where(np.isfinite(last_good[gone]), last_good[gone], 0.0)
            cash += float((shares[gone] * proceeds).sum())
            shares[gone] = 0.0
            stops.sync(shares * np.nan_to_num(prices), prices)

        position_value_vec = shares * np.where(tradeable, prices, 0.0)
        position_value = float(position_value_vec.sum())
        short_value = float(-position_value_vec.clip(max=0.0).sum())
        gross_value = float(np.abs(position_value_vec).sum())

        # --- 2. carry on the book held into today ---------------------------
        carry = cost_model.carry(short_value, gross_value, cash + position_value, cash)
        cash += carry
        equity_mark = cash + position_value

        # --- 3. feedback controls, on realised history only -----------------
        mark_return = equity_mark / equity_prev - 1.0 if equity_prev else 0.0
        scaler.update(mark_return)
        throttle_level = throttle.update(equity_mark)
        exposure_scale = throttle_level * scaler.scale()

        # --- 4. asymmetric stops --------------------------------------------
        triggered = stops.mark(prices)
        if triggered.any():
            exec_date = dates[min(i + lag, len(dates) - 1)]
            standing_target.iloc[triggered] = 0.0
            prior = pending_exit.get(exec_date)
            pending_exit[exec_date] = triggered if prior is None else (prior | triggered)

        # --- 5. rebalance decision -------------------------------------------
        if date in rebal:
            current = pd.Series(
                position_value_vec / equity_mark if equity_mark > 0 else position_value_vec * 0.0,
                index=tickers,
            )
            standing_target, diagnostics = constructor.target_weights(
                date, current, blocked=stops.blocked(), exposure_scale=exposure_scale,
                equity=equity_mark,
            )
            diagnostics.update({
                "throttle": throttle_level,
                "vol_scale_strategy": scaler.scale(),
                "equity": equity_mark,
            })
            rebalance_rows.append(diagnostics)
            exec_date = dates[min(i + lag, len(dates) - 1)]
            pending_target[exec_date] = standing_target.to_numpy(dtype=float).copy()

        # --- 6. execution ------------------------------------------------------
        # Orders are *worked*, not assumed filled. Anything the participation cap
        # blocks today stays outstanding and is worked again tomorrow, until a new
        # target replaces it. Simply dropping the unfilled remainder — the usual
        # shortcut — leaves the book permanently and invisibly under-invested.
        trade_cost = traded_notional = 0.0
        current_notional = np.where(tradeable, shares * prices, 0.0)

        target_now = pending_target.pop(date, None)
        exits_now = pending_exit.pop(date, None)
        if target_now is not None:
            desired = np.where(tradeable, target_now * equity_mark, current_notional)
            if exits_now is not None:
                desired[exits_now] = 0.0
            open_orders = desired - current_notional          # supersedes stale orders
            order_age = np.zeros(n, dtype=np.int32)
        elif exits_now is not None:
            open_orders[exits_now] = -current_notional[exits_now]
            order_age[exits_now] = 0

        # Orders expire. A desk does not still be working a month-old order against
        # a signal that has since changed its mind, and letting them live forever
        # turns an illiquid name into a permanent churn machine.
        order_age = np.where(np.abs(open_orders) > 1e-8, order_age + 1, 0)
        expired = order_age > order_horizon
        open_orders[expired] = 0.0
        order_age[expired] = 0

        open_orders[~tradeable] = 0.0
        open_orders[np.abs(open_orders) < 1e-8] = 0.0

        if np.abs(open_orders).sum() > 0:
            filled, _ = cost_model.apply_participation_cap(open_orders, adv_arr[i])
            spread, impact = cost_model.trade_costs(filled, adv_arr[i], vol_arr[i])
            trade_cost = spread + impact
            traded_notional = float(np.abs(filled).sum())

            with np.errstate(invalid="ignore", divide="ignore"):
                share_delta = np.where(tradeable, filled / np.where(prices > 0, prices, 1.0), 0.0)
            shares = shares + share_delta
            cash -= float(filled.sum()) + trade_cost
            open_orders = open_orders - filled

            position_value_vec = shares * np.where(tradeable, prices, 0.0)
            position_value = float(position_value_vec.sum())
            stops.sync(position_value_vec, prices)

        unfilled = float(np.abs(open_orders).sum())
        equity = cash + position_value

        # --- 7. record ---------------------------------------------------------
        weights_now = position_value_vec / equity if equity > 0 else position_value_vec * 0.0
        weight_rows[date] = weights_now
        net_return = equity / equity_prev - 1.0 if equity_prev else 0.0
        gross_return = (equity + trade_cost) / equity_prev - 1.0 if equity_prev else 0.0
        daily_rows.append({
            "date": date,
            "equity": equity,
            "net_return": net_return,
            "gross_return": gross_return,
            "cash": cash,
            "gross_exposure": float(np.abs(weights_now).sum()),
            "net_exposure": float(weights_now.sum()),
            "long_exposure": float(weights_now.clip(min=0.0).sum()),
            "short_exposure": float(-weights_now.clip(max=0.0).sum()),
            "n_positions": int((np.abs(weights_now) > 1e-9).sum()),
            "trade_cost": trade_cost,
            "carry": carry,
            "traded_notional": traded_notional,
            "unfilled_notional": unfilled,
            "throttle": throttle_level,
            "strategy_vol_scale": scaler.scale(),
            "market_short_scale": float(short_scale_series.iloc[i]),
            "stops_fired": stops.stops_fired,
        })
        equity_prev = equity
        if equity <= 0:
            LOG.error("equity went non-positive on %s — stopping", date.date())
            break

    daily = pd.DataFrame(daily_rows).set_index("date")
    daily["stops_fired"] = daily["stops_fired"].diff().fillna(daily["stops_fired"]).astype(int)
    weights = pd.DataFrame(weight_rows, index=tickers).T

    meta = {
        "config_name": cfg.name,
        "start": str(daily.index[0].date()),
        "end": str(daily.index[-1].date()),
        "capital": cfg.backtest.capital,
        "warmup_days": int(warmup),
        "n_rebalances": len(rebalance_rows),
        "total_stops": int(stops.stops_fired),
        "data": data.describe(),
    }
    return BacktestResult(
        equity=daily["equity"],
        returns=daily["net_return"],
        gross_returns=daily["gross_return"],
        weights=weights,
        daily=daily,
        rebalances=pd.DataFrame(rebalance_rows).set_index("date") if rebalance_rows else pd.DataFrame(),
        score=score,
        components=components,
        meta=meta,
    )
