"""HTML summaries for the multi-run workflows (ablation, sweep, walk-forward)."""
from __future__ import annotations

from typing import Any

from ..config import Config
from ..evaluate import plots, report


def ablation_report(cfg: Config, outcome: dict[str, Any]) -> str:
    table = outcome["table"]
    figures = {
        "equity": plots.equity_overlay(
            {k: outcome["curves"][k] for k in list(outcome["curves"])[:3]},
            "Full construction vs. ablated variants",
        ),
        "drawdown": plots.comparison_bars(
            table["max_drawdown"], "Maximum drawdown by variant", "{:.1%}"
        ),
        "rolling_sharpe": plots.comparison_bars(
            table["skew"], "Return skew by variant (less negative is better)", "{:.2f}"
        ),
        "exposures": plots.comparison_bars(
            table["sharpe"], "Sharpe by variant", "{:.2f}"
        ),
    }
    best = table["sharpe"].idxmax()
    metrics = {
        "variants_tested": float(len(table)),
        "best_sharpe_variant_is_full": float(best == "full"),
        "full_sharpe": float(table.loc["full", "sharpe"]) if "full" in table.index else float("nan"),
        "full_skew": float(table.loc["full", "skew"]) if "full" in table.index else float("nan"),
        "full_max_drawdown": float(table.loc["full", "max_drawdown"]) if "full" in table.index else float("nan"),
        "worst_variant_drawdown": float(table["max_drawdown"].min()),
    }
    payload = cfg.to_dict()
    payload["_period"] = "ablation of the asymmetric risk construction"
    payload["_universe"] = "identical data and signal across every variant"
    return report.build_report(
        title=f"{cfg.name} — asymmetry ablation",
        metrics=metrics, figures=figures,
        tables={"Variant comparison": table.round(4),
                "Difference vs. full model": outcome["delta"].round(4)},
        statistics={}, config=payload,
        notes=["one mechanism disabled per row", "read skew and drawdown before Sharpe"],
    )


def sweep_report(cfg: Config, outcome: dict[str, Any]) -> str:
    table = outcome["table"]
    figures = {
        "exposures": plots.comparison_bars(
            table["sharpe"].head(18), "Sharpe by candidate (top 18)", "{:.2f}"
        ),
        "drawdown": plots.comparison_bars(
            table["max_drawdown"].head(18), "Maximum drawdown by candidate", "{:.1%}"
        ),
    }
    payload = cfg.to_dict()
    payload["_period"] = f"{len(table)} candidates"
    payload["_universe"] = f"best: {outcome['best']}"
    return report.build_report(
        title=f"{cfg.name} — parameter sweep",
        metrics={"candidates": float(len(table)),
                 "best_sharpe": float(table['sharpe'].iloc[0]),
                 "median_sharpe": float(table['sharpe'].median())},
        figures=figures,
        tables={"Candidates": table.round(4)},
        statistics=outcome["statistics"], config=payload,
        notes=["PBO near or above 0.5 means the selection, not the strategy, produced the result"],
    )


def walkforward_report(cfg: Config, outcome: dict[str, Any]) -> str:
    equity = outcome["oos_equity"]
    figures = {
        "equity": plots.equity_overlay({"Out-of-sample": equity}, "Stitched out-of-sample equity"),
        "drawdown": plots.drawdown(equity),
        "rolling_sharpe": plots.rolling_sharpe(outcome["oos_returns"]),
    }
    payload = cfg.to_dict()
    payload["_period"] = f"{len(outcome['folds'])} walk-forward folds"
    payload["_universe"] = f"{outcome['n_candidates']} candidate(s) selected per fold"
    return report.build_report(
        title=f"{cfg.name} — walk-forward validation",
        metrics={**outcome["oos_metrics"],
                 "selection_degradation": outcome["selection_degradation"]},
        figures=figures,
        tables={"Folds": outcome["folds"].round(4)},
        statistics={}, config=payload,
        notes=["only the out-of-sample curve is quotable",
               "training windows are purged and embargoed"],
    )
