"""equity_lab — a research stack for systematic equity strategies.

Layers (each usable on its own):

    data/       point-in-time price panels from pluggable sources
    signals/    momentum family + transforms, all lag-safe
    portfolio/  score -> weights, with asymmetric risk construction
    backtest/   sequential daily engine with explicit cost accounting
    evaluate/   performance metrics and multiple-testing-aware statistics
    research/   walk-forward CV, parameter sweeps, experiment tracking
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
