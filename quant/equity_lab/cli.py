"""Command line entry point: ``python -m equity_lab.cli <command>``.

The commands mirror the research loop itself — inspect the data, inspect the
signal, run one backtest, ablate it, sweep it, validate it out of sample,
compare everything — so that the workflow is the interface rather than a
convention someone has to remember.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

from .config import Config
from .data import build_universe, load_market_data, universe_summary
from .evaluate import report as report_mod
from .research import (
    expand_grid, leaderboard, run_ablation, run_experiment, run_sweep,
    run_walkforward, sensitivity,
)
from .research.ablation import run_multiseed_ablation
from .research import summaries
from .signals import build_score, signal_diagnostics
from .signals.momentum import REGISTRY, rolling_beta
from .signals.transforms import apply_transforms
from .utils import ensure_dir, get_logger

LOG = get_logger("equity_lab.cli")
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)


def _load(args) -> Config:
    cfg = Config.load(args.config)
    for override in args.set or []:
        key, _, raw = override.partition("=")
        if not _:
            raise SystemExit(f"--set expects key=value, got '{override}'")
        cfg = cfg.with_overrides({key: yaml.safe_load(raw)})
    if getattr(args, "name", None):
        cfg.name = args.name
    return cfg


def _grid(path: str | None) -> list[dict]:
    if not path:
        return [{}]
    payload = yaml.safe_load(Path(path).read_text()) or {}
    return expand_grid(payload.get("grid", payload))


def cmd_data(args) -> None:
    cfg = _load(args)
    data = load_market_data(cfg.data, seed=cfg.seed)
    mask = build_universe(data, cfg.data.universe)
    print("\nPanel:", json.dumps(data.describe(), indent=2))
    print("\nInvestable universe by year:")
    print(universe_summary(mask).to_string())


def cmd_signals(args) -> None:
    """Information coefficients before any portfolio construction touches them."""
    cfg = _load(args)
    data = load_market_data(cfg.data, seed=cfg.seed)
    mask = build_universe(data, cfg.data.universe)
    betas = rolling_beta(data.returns(), data.market_return(), cfg.portfolio.risk.beta_window)

    names = [s.name for s in cfg.signals] if not args.all else sorted(REGISTRY)
    rows = []
    for name in names:
        params = next((s.params for s in cfg.signals if s.name == name), {})
        raw = REGISTRY[name](data, **params)
        scores = apply_transforms(raw, cfg.transforms, data.sectors, betas, mask)
        diag = signal_diagnostics(scores, data, (21, 63))
        for _, row in diag.iterrows():
            rows.append({"signal": name, **row.to_dict()})

    blended, _ = build_score(data, cfg.signals, cfg.transforms, mask, betas)
    for _, row in signal_diagnostics(blended, data, (21, 63)).iterrows():
        rows.append({"signal": "BLEND", **row.to_dict()})
    print("\nInformation coefficients (rank correlation with forward returns):")
    print(pd.DataFrame(rows).set_index(["signal", "horizon_days"]).round(4).to_string())


def cmd_backtest(args) -> None:
    cfg = _load(args)
    experiment = run_experiment(cfg, write=not args.no_write, make_report=not args.no_report)
    print(f"\n=== {cfg.name} ({experiment.run_id}) ===")
    print(pd.Series(experiment.metrics).round(4).to_string())
    if experiment.statistics:
        print("\nSignificance:")
        print(pd.Series(experiment.statistics).round(4).to_string())
    for title, frame in experiment.tables.items():
        if frame is not None and len(frame):
            print(f"\n{title}:")
            print(frame.round(4).to_string())
    if experiment.path:
        print(f"\nartifacts: {experiment.path}")


def cmd_ablation(args) -> None:
    cfg = _load(args)
    if args.seeds:
        return _multiseed_ablation(cfg, [int(s) for s in args.seeds.split(",")])
    data = load_market_data(cfg.data, seed=cfg.seed)
    outcome = run_ablation(cfg, data)
    print("\n=== Asymmetry ablation ===")
    print(outcome["table"].round(4).to_string())
    print("\nDifference vs. the full construction:")
    print(outcome["delta"].round(4).to_string())
    path = ensure_dir(Path(cfg.output_dir) / f"{cfg.name}-ablation")
    outcome["table"].to_csv(path / "ablation.csv")
    report_mod.write_report(path / "report.html", summaries.ablation_report(cfg, outcome))
    print(f"\nartifacts: {path}")


def _multiseed_ablation(cfg, seeds: list[int]) -> None:
    """Repeat the ablation on independent panels; report sign consistency."""
    outcome = run_multiseed_ablation(cfg, seeds)
    print(f"\n=== Ablation across {len(seeds)} independent panels (seeds {seeds}) ===")
    print("\nMean across panels:")
    print(outcome["mean"].round(4).to_string())
    print("\nStandard deviation across panels (the scale that differences must beat):")
    print(outcome["std"].round(4).to_string())
    print("\nSign consistency vs. the full construction "
          "(1.0 = same direction on every panel, ~0.5 = noise):")
    print(outcome["sign_consistency"].round(3).to_string())
    path = ensure_dir(Path(cfg.output_dir) / f"{cfg.name}-ablation-multiseed")
    outcome["per_seed"].to_csv(path / "per_seed.csv")
    outcome["sign_consistency"].to_csv(path / "sign_consistency.csv")
    print(f"\nartifacts: {path}")


def cmd_sweep(args) -> None:
    cfg = _load(args)
    data = load_market_data(cfg.data, seed=cfg.seed)
    grid = _grid(args.grid)
    outcome = run_sweep(cfg, data, grid)
    print("\n=== Sweep leaderboard ===")
    print(outcome["table"].round(4).to_string())
    print("\nSelection statistics:")
    print(pd.Series(outcome["statistics"]).round(4).to_string())
    for parameter in (args.sensitivity or []):
        print(f"\nSensitivity to {parameter}:")
        print(sensitivity(outcome["table"], parameter).round(4).to_string())
    path = ensure_dir(Path(cfg.output_dir) / f"{cfg.name}-sweep")
    outcome["table"].to_csv(path / "sweep.csv")
    outcome["trial_returns"].to_csv(path / "trial_returns.csv")
    report_mod.write_report(path / "report.html", summaries.sweep_report(cfg, outcome))
    print(f"\nartifacts: {path}")


def cmd_walkforward(args) -> None:
    cfg = _load(args)
    data = load_market_data(cfg.data, seed=cfg.seed)
    outcome = run_walkforward(cfg, data, _grid(args.grid))
    print("\n=== Walk-forward folds ===")
    print(outcome["folds"].round(4).to_string(index=False))
    print("\nStitched out-of-sample:")
    print(pd.Series(outcome["oos_metrics"]).round(4).to_string())
    print(f"\nIn-sample minus out-of-sample Sharpe: {outcome['selection_degradation']:.3f}")
    path = ensure_dir(Path(cfg.output_dir) / f"{cfg.name}-walkforward")
    outcome["folds"].to_csv(path / "folds.csv", index=False)
    outcome["oos_returns"].to_csv(path / "oos_returns.csv")
    report_mod.write_report(path / "report.html", summaries.walkforward_report(cfg, outcome))
    print(f"\nartifacts: {path}")


def cmd_leaderboard(args) -> None:
    table = leaderboard(args.dir, args.sort_by)
    if table.empty:
        print(f"no runs found in {args.dir}")
        return
    print(table.round(4).to_string())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="equity_lab",
        description="Research stack for systematic, momentum-driven equity strategies.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def with_config(p):
        p.add_argument("--config", "-c", required=True, help="path to a YAML config")
        p.add_argument("--set", "-s", action="append", metavar="KEY=VALUE",
                       help="override any config field, e.g. -s portfolio.risk.target_vol=0.08")
        p.add_argument("--name", help="override the run name")
        return p

    with_config(sub.add_parser("data", help="load the panel and describe the universe")).set_defaults(func=cmd_data)

    p = with_config(sub.add_parser("signals", help="information coefficients per signal"))
    p.add_argument("--all", action="store_true", help="score every registered signal")
    p.set_defaults(func=cmd_signals)

    p = with_config(sub.add_parser("backtest", help="run one backtest and write a report"))
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--no-write", action="store_true")
    p.set_defaults(func=cmd_backtest)

    p = with_config(sub.add_parser("ablation", help="turn each asymmetry off in turn"))
    p.add_argument("--seeds", help="comma-separated seeds; repeats the ablation on "
                                   "independent panels and reports sign consistency")
    p.set_defaults(func=cmd_ablation)

    p = with_config(sub.add_parser("sweep", help="grid search with overfitting statistics"))
    p.add_argument("--grid", "-g", help="YAML file with a 'grid' mapping")
    p.add_argument("--sensitivity", action="append", help="dotted path to summarise marginally")
    p.set_defaults(func=cmd_sweep)

    p = with_config(sub.add_parser("walkforward", help="purged, embargoed out-of-sample test"))
    p.add_argument("--grid", "-g", help="YAML file with a 'grid' mapping to select from")
    p.set_defaults(func=cmd_walkforward)

    p = sub.add_parser("leaderboard", help="compare every run written to disk")
    p.add_argument("--dir", default="runs")
    p.add_argument("--sort-by", default="sharpe")
    p.set_defaults(func=cmd_leaderboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        LOG.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
