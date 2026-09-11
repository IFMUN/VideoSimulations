"""Shared fixtures. The panel is generated once per session — it is the slowest
thing in the suite and every test wants the same one."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from equity_lab.config import Config, SyntheticConfig  # noqa: E402
from equity_lab.data.synthetic import generate  # noqa: E402


@pytest.fixture(scope="session")
def panel():
    return generate(SyntheticConfig(n_assets=90, n_sectors=5), "2010-01-01", "2017-12-31", seed=11)


@pytest.fixture(scope="session")
def small_panel():
    return generate(SyntheticConfig(n_assets=60, n_sectors=4), "2012-01-01", "2016-12-31", seed=3)


@pytest.fixture
def config() -> Config:
    cfg = Config.load(ROOT / "configs" / "quick.yaml")
    cfg.data.universe.top_n_by_adv = 70
    cfg.data.universe.min_adv_usd = 0.0
    cfg.portfolio.risk.max_weight = 0.08
    cfg.evaluation.bootstrap_samples = 40
    return cfg
