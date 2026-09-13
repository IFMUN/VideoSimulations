"""Typed configuration objects.

Every knob in the stack lives here, so an experiment is fully described by one
YAML file. That is what makes runs reproducible and sweepable: the config is
hashed to form the run id, and the resolved config is written next to results.
"""
from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field, fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, get_type_hints

import yaml


# --------------------------------------------------------------------------- data


@dataclass
class SyntheticConfig:
    """Parameters of the generative market used when no vendor data is reachable."""

    n_assets: int = 220
    n_sectors: int = 8
    market_vol: float = 0.16
    market_drift: float = 0.06
    garch_alpha: float = 0.09          # ARCH term  -> volatility clustering
    garch_beta: float = 0.88           # GARCH term -> volatility persistence
    sector_vol: float = 0.10
    idio_vol_mean: float = 0.32
    idio_vol_disp: float = 0.12
    idio_tail_df: float = 4.0          # Student-t d.o.f. -> fat tails
    alpha_persistence: float = 0.995   # AR(1) on latent drift -> real momentum
    alpha_vol: float = 0.35            # annualised dispersion of latent drift
    reversal_noise_bps: float = 45.0   # bid-ask-bounce style microstructure noise
    crash_intensity: float = 2.2       # loser rebound strength after bear markets
    bear_threshold: float = -0.15      # drawdown that arms the rebound regime
    listing_turnover: float = 0.05     # fraction of names that enter/exit per year
    adv_log_mean: float = 15.5         # log dollar ADV
    adv_log_disp: float = 1.2


@dataclass
class UniverseConfig:
    """Point-in-time screens applied before any signal is computed."""

    top_n_by_adv: int = 500
    rank_buffer: float = 0.25          # hysteresis band on the liquidity rank
    min_price: float = 5.0
    min_history_days: int = 252
    min_adv_usd: float = 1_000_000.0


@dataclass
class DataConfig:
    source: str = "synthetic"          # synthetic | csv | stooq | yfinance
    start: str = "2005-01-02"
    end: str = "2025-12-31"
    tickers: list[str] = field(default_factory=list)
    csv_dir: str = "data/prices"
    cache_dir: str = ".cache"
    benchmark: str = "EQW"             # EQW | a ticker present in the panel
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    synthetic: SyntheticConfig = field(default_factory=SyntheticConfig)


# ------------------------------------------------------------------------ signals


@dataclass
class SignalSpec:
    name: str
    weight: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransformConfig:
    winsorize: float = 0.02
    standardize: str = "z"             # z | rank | none
    sector_neutral: bool = True
    beta_neutral_signal: bool = False
    smooth_halflife: float = 0.0       # >0 smooths the score to damp turnover


# ---------------------------------------------------------------------- portfolio


@dataclass
class CrashOverlayConfig:
    """Barroso/Santa-Clara constant-volatility scaling + Daniel/Moskowitz state.

    Momentum's pathology is not average volatility, it is the crash: a violently
    negative-skew event concentrated in the *short* leg when a bear market turns.
    """

    enabled: bool = True
    vol_window: int = 126              # realised strategy vol used for scaling
    target_strategy_vol: float = 0.12
    max_scale: float = 1.5
    min_scale: float = 0.25
    bear_rule: str = "drawdown"        # drawdown | cumulative
    bear_lookback: int = 504           # window for the cumulative return / trailing peak
    bear_drawdown: float = 0.10        # drawdown from the trailing peak that counts as bear
    market_vol_window: int = 126
    market_vol_threshold: float = 1.5  # multiple of long-run vol that counts as stressed
    bear_short_scale: float = 0.35     # how much of the short leg survives a stressed bear


@dataclass
class DrawdownThrottleConfig:
    """Asymmetric response to the strategy's *own* equity curve.

    De-risk quickly into a drawdown, re-risk slowly out of it. The asymmetry is
    the point: it is cheap insurance against the second half of a fat left tail.
    """

    enabled: bool = True
    start_dd: float = 0.05             # drawdown at which throttling begins
    full_dd: float = 0.20              # drawdown at which exposure hits floor
    floor: float = 0.40
    cut_halflife: float = 1.0          # days to move down  (fast)
    recover_halflife: float = 20.0     # days to move up    (slow)
    peak_window_days: int = 504        # trailing high-water mark; 0 = all-time


@dataclass
class StopConfig:
    """Per-name trailing stop applied only to positions that are losing.

    Winners are left alone — cutting them would truncate exactly the right tail
    momentum exists to harvest.

    **Off by default**, for a weaker reason than it first appeared. On the
    default panel the ablation and a 12-candidate sweep both rejected stops
    decisively — Sharpe rose monotonically as the stop was loosened. Repeating
    the ablation on three *independent* panels did not confirm it: the Sharpe
    effect changes sign across panels, so that first result was a property of
    one draw, not of the mechanism. (Reading a single ablation column as a
    finding is exactly the selection error this package exists to catch; run
    ``ablation --seeds`` before trusting any row of it, including this one.)

    What does hold across panels is that stops cost roughly 0.8x of annual
    turnover and leave drawdown and skew slightly worse on average. Off is
    therefore the cheaper, simpler default rather than the demonstrably correct
    one. The mechanism is retained because the sign plausibly flips on a return
    process with short-horizon *continuation*; at daily frequency prices
    mean-revert, so a trailing stop keeps selling into reversals.
    """

    enabled: bool = False
    trailing: float = 0.18             # adverse move from best mark since entry
    cooldown_days: int = 21            # name is un-investable for this long after a stop


@dataclass
class AsymmetryConfig:
    long_convexity: float = 1.35       # >1 concentrates the long leg in top scores
    short_convexity: float = 0.85      # <1 flattens the short leg (crash defence)
    base_short_ratio: float = 0.75     # structural short/long notional ratio
    downside_risk_weight: float = 0.5  # blend of vol vs downside deviation in sizing
    downside_beta_penalty: float = 0.5 # shrink names that are beta-asymmetric
    downside_window: int = 252
    crash_overlay: CrashOverlayConfig = field(default_factory=CrashOverlayConfig)
    drawdown_throttle: DrawdownThrottleConfig = field(default_factory=DrawdownThrottleConfig)
    stops: StopConfig = field(default_factory=StopConfig)


@dataclass
class RiskConfig:
    target_vol: float = 0.10
    vol_window: int = 63
    vol_halflife: float = 42.0
    cov_shrinkage: float = 0.30        # Ledoit-Wolf style shrink to diagonal
    beta_neutral: bool = True
    beta_window: int = 252
    max_weight: float = 0.03
    max_sector_net: float = 0.15
    max_gross: float = 2.0
    max_leverage_change: float = 0.5
    max_adv_multiple: float = 0.5      # position notional cap, as a multiple of ADV


@dataclass
class TurnoverConfig:
    no_trade_band: float = 0.20        # fraction of target weight that must move to trade
    max_turnover: float = 0.40         # one-way turnover cap per rebalance


@dataclass
class PortfolioConfig:
    style: str = "long_short"          # long_short | long_only
    long_quantile: float = 0.20
    short_quantile: float = 0.20
    weighting: str = "convex_z"        # convex_z | rank | equal
    asymmetry: AsymmetryConfig = field(default_factory=AsymmetryConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    turnover: TurnoverConfig = field(default_factory=TurnoverConfig)


# ----------------------------------------------------------------------- backtest


@dataclass
class CostConfig:
    spread_bps: float = 5.0            # half-spread paid on every trade
    impact_coef: float = 0.15          # k in  k * sigma * sqrt(participation)
    borrow_bps: float = 50.0           # annualised fee on short notional
    financing_bps: float = 120.0       # annualised cost of gross above equity
    cash_yield_bps: float = 200.0      # earned on unencumbered cash
    adv_participation_cap: float = 0.05


@dataclass
class BacktestConfig:
    rebalance: str = "M"
    execution_lag: int = 1             # days between decision and fill
    order_horizon_days: int = 5        # unfilled orders are cancelled after this
    capital: float = 25_000_000.0
    costs: CostConfig = field(default_factory=CostConfig)


# --------------------------------------------------------------------- evaluation


@dataclass
class EvaluationConfig:
    risk_free: float = 0.0
    n_trials: int = 1                  # configurations tried, for the deflated Sharpe
    bootstrap_samples: int = 500
    block_size: int = 21


@dataclass
class WalkForwardConfig:
    enabled: bool = False
    n_splits: int = 5
    embargo_days: int = 21
    train_years: float = 5.0
    test_years: float = 2.0
    anchored: bool = True


@dataclass
class Config:
    name: str = "unnamed"
    seed: int = 7
    output_dir: str = "runs"
    data: DataConfig = field(default_factory=DataConfig)
    signals: list[SignalSpec] = field(default_factory=list)
    transforms: TransformConfig = field(default_factory=TransformConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    walkforward: WalkForwardConfig = field(default_factory=WalkForwardConfig)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Config":
        return _build(cls, payload)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        payload = yaml.safe_load(Path(path).read_text()) or {}
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        from .utils import to_jsonable

        return to_jsonable(self)

    def with_overrides(self, overrides: dict[str, Any]) -> "Config":
        """Return a copy with dotted-path overrides applied.

        ``{"portfolio.risk.target_vol": 0.08}`` is the sweep interface — it keeps
        parameter grids declarative instead of requiring bespoke code per knob.
        """
        payload = copy.deepcopy(self.to_dict())
        for dotted, value in overrides.items():
            node = payload
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = _descend(node, part)
            _assign(node, parts[-1], value)
        return Config.from_dict(payload)

    def run_id(self) -> str:
        from .utils import stable_hash

        return f"{self.name}-{stable_hash(self.to_dict())}"


def _descend(node: Any, key: str) -> Any:
    """Step into a mapping or, when ``key`` is numeric, into a list element.

    Lets a sweep grid address list members directly — ``signals.0.params.lookback``
    — instead of forcing every per-signal experiment to be written out by hand.
    """
    if isinstance(node, list):
        return node[int(key)]
    return node.setdefault(key, {})


def _assign(node: Any, key: str, value: Any) -> None:
    if isinstance(node, list):
        node[int(key)] = value
    else:
        node[key] = value


def _build(cls, payload: Any):
    """Recursively instantiate nested dataclasses from plain dicts.

    ``from __future__ import annotations`` turns field types into strings, so the
    annotations are resolved once per class and memoised.
    """
    if not is_dataclass(cls):
        return payload
    if payload is None:
        return cls()
    if not isinstance(payload, dict):
        raise TypeError(f"expected mapping for {cls.__name__}, got {type(payload).__name__}")

    hints = _hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = set(payload) - known
    if unknown:
        raise ValueError(f"unknown keys for {cls.__name__}: {sorted(unknown)}")

    kwargs: dict[str, Any] = {}
    for name in known:
        if name not in payload:
            continue
        value = payload[name]
        target = hints.get(name)
        if name == "signals":
            kwargs[name] = [_build(SignalSpec, item) for item in (value or [])]
        elif isinstance(target, type) and is_dataclass(target):
            kwargs[name] = _build(target, value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


@lru_cache(maxsize=None)
def _hints(cls) -> dict[str, Any]:
    return get_type_hints(cls, globalns=vars(sys.modules[cls.__module__]))
