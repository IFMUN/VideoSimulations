import pytest

from equity_lab.config import Config


def test_nested_dataclasses_are_built_from_plain_dicts():
    cfg = Config.from_dict({"name": "x", "portfolio": {"risk": {"target_vol": 0.07}}})
    assert cfg.portfolio.risk.target_vol == 0.07
    assert cfg.portfolio.asymmetry.stops.trailing > 0       # defaults survive


def test_unknown_keys_are_rejected_rather_than_silently_ignored():
    """A typo in a config is otherwise a silent no-op that wastes a research cycle."""
    with pytest.raises(ValueError, match="unknown keys"):
        Config.from_dict({"portfoilo": {}})
    with pytest.raises(ValueError, match="unknown keys"):
        Config.from_dict({"portfolio": {"risk": {"targetvol": 0.1}}})


def test_dotted_overrides_reach_nested_fields_and_list_members(config):
    updated = config.with_overrides({
        "portfolio.asymmetry.crash_overlay.bear_short_scale": 0.1,
        "signals.0.params.lookback": 63,
    })
    assert updated.portfolio.asymmetry.crash_overlay.bear_short_scale == 0.1
    assert updated.signals[0].params["lookback"] == 63
    assert config.signals[0].params["lookback"] != 63    # original is untouched


def test_run_id_is_stable_and_sensitive(config):
    assert config.run_id() == Config.from_dict(config.to_dict()).run_id()
    assert config.run_id() != config.with_overrides({"seed": 99}).run_id()
