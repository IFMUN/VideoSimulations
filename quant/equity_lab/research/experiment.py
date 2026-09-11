"""Running, recording and comparing experiments.

An experiment is reproducible only if three things travel together: the config
that produced it, the code state it ran against, and the data it saw. Every run
writes all three into its own directory, keyed by a hash of the config, so
re-running the same configuration lands in the same place and a leaderboard can
be rebuilt from disk at any time.
"""
from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ..backtest import BacktestResult, run_backtest
from ..config import Config
from ..data import MarketData, load_market_data
from ..evaluate import attribution, metrics, plots, report, stats
from ..portfolio.asymmetry import market_state
from ..utils import ensure_dir, get_logger, git_sha, to_jsonable

LOG = get_logger(__name__)


@dataclass
class Experiment:
    config: Config
    result: BacktestResult
    metrics: dict[str, float]
    statistics: dict[str, float]
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    path: Path | None = None

    @property
    def run_id(self) -> str:
        return self.config.run_id()


def _benchmark_returns(cfg: Config, data: MarketData, index: pd.Index) -> pd.Series:
    """Equal-weighted universe by default; any ticker in the panel by name."""
    name = cfg.data.benchmark
    if name and name != "EQW" and name in data.close.columns:
        series = data.close[name].pct_change()
    else:
        series = data.market_return()
    return series.reindex(index)


def evaluate(
    cfg: Config, data: MarketData, result: BacktestResult,
    n_trials: int | None = None, trial_sharpes: pd.Series | None = None,
) -> tuple[dict[str, float], dict[str, float], dict[str, pd.DataFrame]]:
    """Metrics, significance statistics and diagnostic tables for one run."""
    bench = _benchmark_returns(cfg, data, result.returns.index)
    summary = metrics.summarise(
        result.returns, result.equity, bench, cfg.evaluation.risk_free, result.daily
    )

    significance = stats.haircut_summary(
        result.returns,
        n_trials=n_trials if n_trials is not None else cfg.evaluation.n_trials,
        trial_sharpes=trial_sharpes,
        bootstrap_samples=cfg.evaluation.bootstrap_samples,
        block_size=cfg.evaluation.block_size,
    )

    state = market_state(data.market_return(), cfg.portfolio.asymmetry.crash_overlay)
    tables = {
        "By calendar year": metrics.by_year(result.returns, result.equity),
        "By market regime": attribution.regime_attribution(result.returns, state),
        "Forward return by score decile": attribution.decile_spread(
            result.score, data.close
        ),
        "Contribution by sector": attribution.sector_attribution(
            result.weights, data.returns(), data.sectors
        ).head(12),
    }
    components = attribution.signal_attribution(result.components, data.close)
    if len(components):
        tables["Signal component quality"] = components
        correlation = components.attrs.get("ic_correlation")
        if correlation is not None and len(correlation) > 1:
            # Two components with the same IC are worth very different amounts
            # depending on whether their ICs are correlated — i.e. whether they
            # are two bets or the same bet under two names.
            tables["IC correlation between components"] = correlation.round(3)

    legs = attribution.leg_attribution(result.weights, data.returns())
    tables["Long leg vs short leg"] = pd.DataFrame({
        "ann_return": legs.mean() * 252,
        "ann_vol": legs.std() * (252 ** 0.5),
        "worst_day": legs.min(),
        "best_day": legs.max(),
    })
    summary["long_leg_ann"] = float(legs["long_leg"].mean() * 252)
    summary["short_leg_ann"] = float(legs["short_leg"].mean() * 252)
    return summary, significance, tables


def build_html(
    cfg: Config, data: MarketData, result: BacktestResult,
    summary: dict[str, float], significance: dict[str, float],
    tables: dict[str, pd.DataFrame],
) -> str:
    bench = _benchmark_returns(cfg, data, result.returns.index)
    deciles = tables.get("Forward return by score decile")
    figures = {
        "equity": plots.equity_curve(result.equity, bench),
        "drawdown": plots.drawdown(result.equity),
        "rolling_sharpe": plots.rolling_sharpe(result.returns),
        "exposures": plots.exposures(result.daily),
        "distribution": plots.return_distribution(result.returns),
        "yearly": plots.yearly_returns(tables["By calendar year"]),
    }
    if deciles is not None and len(deciles):
        figures["deciles"] = plots.decile_chart(deciles)

    notes = [
        f"{result.meta['n_rebalances']} rebalances",
        f"execution lag {cfg.backtest.execution_lag}d",
        f"costs {cfg.backtest.costs.spread_bps:.0f}bp spread + sqrt impact",
    ]
    if data.meta.get("synthetic") or cfg.data.source == "synthetic":
        notes.insert(0, "SYNTHETIC DATA — not a live-market result")

    payload = cfg.to_dict()
    payload["_period"] = f"{result.meta['start']} to {result.meta['end']}"
    payload["_universe"] = (
        f"{result.meta['data']['n_tickers']} names, "
        f"{result.meta['data']['avg_live_names']:.0f} live on average"
    )
    return report.build_report(
        title=f"{cfg.name} — systematic equity research report",
        metrics=summary, figures=figures, tables=tables,
        statistics=significance, config=payload, notes=notes,
    )


def _manifest(cfg: Config, data: MarketData, result: BacktestResult) -> dict[str, Any]:
    return {
        "run_id": cfg.run_id(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": {name: _version(name) for name in ("numpy", "pandas", "matplotlib")},
        "data": result.meta["data"],
        "data_source": cfg.data.source,
        "config_hash": cfg.run_id().rsplit("-", 1)[-1],
    }


def _version(package: str) -> str | None:
    try:
        module = __import__(package)
        return getattr(module, "__version__", None)
    except Exception:  # pragma: no cover
        return None


def run_experiment(
    cfg: Config, data: MarketData | None = None, write: bool = True,
    make_report: bool = True, n_trials: int | None = None,
) -> Experiment:
    """Load data if needed, run the backtest, evaluate it, and persist everything."""
    if data is None:
        data = load_market_data(cfg.data, seed=cfg.seed)
    result = run_backtest(cfg, data)
    summary, significance, tables = evaluate(cfg, data, result, n_trials=n_trials)

    path = None
    if write:
        path = ensure_dir(Path(cfg.output_dir) / cfg.run_id())
        (path / "config.yaml").write_text(yaml.safe_dump(cfg.to_dict(), sort_keys=False))
        (path / "manifest.json").write_text(
            json.dumps(to_jsonable(_manifest(cfg, data, result)), indent=2)
        )
        (path / "metrics.json").write_text(
            json.dumps(to_jsonable({**summary, **significance, "name": cfg.name}), indent=2)
        )
        result.daily.to_csv(path / "daily.csv")
        result.equity.to_csv(path / "equity.csv")
        if len(result.rebalances):
            result.rebalances.to_csv(path / "rebalances.csv")
        try:
            result.weights.astype("float32").to_parquet(path / "weights.parquet")
        except Exception as exc:  # pragma: no cover
            LOG.warning("could not write weights parquet (%s)", exc)
        table_dir = ensure_dir(path / "tables")
        for name, frame in tables.items():
            if frame is not None and len(frame):
                frame.to_csv(table_dir / f"{name.lower().replace(' ', '_')}.csv")
        if make_report:
            report.write_report(path / "report.html",
                                build_html(cfg, data, result, summary, significance, tables))
        LOG.info("run %s written to %s", cfg.run_id(), path)

    return Experiment(cfg, result, summary, significance, tables, path)


def leaderboard(output_dir: str | Path = "runs", sort_by: str = "sharpe") -> pd.DataFrame:
    """Rebuild a comparison table from every run on disk.

    Deliberately reconstructed from artefacts rather than held in memory: the
    point of recording runs is that yesterday's experiments are still comparable
    today, from a different process.
    """
    root = Path(output_dir)
    if not root.exists():
        return pd.DataFrame()
    rows = []
    for metrics_file in sorted(root.glob("*/metrics.json")):
        try:
            payload = json.loads(metrics_file.read_text())
        except Exception:  # pragma: no cover
            continue
        payload["run_id"] = metrics_file.parent.name
        manifest = metrics_file.parent / "manifest.json"
        if manifest.exists():
            try:
                payload["created_utc"] = json.loads(manifest.read_text()).get("created_utc")
            except Exception:  # pragma: no cover
                pass
        rows.append(payload)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).set_index("run_id")
    columns = [
        c for c in (
            "name", "sharpe", "sortino", "ann_return", "ann_vol", "max_drawdown",
            "skew", "calmar", "ann_turnover", "cost_drag", "deflated_sharpe", "created_utc",
        ) if c in frame.columns
    ]
    frame = frame[columns]
    return frame.sort_values(sort_by, ascending=False) if sort_by in frame else frame
