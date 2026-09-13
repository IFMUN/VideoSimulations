import pandas as pd

from equity_lab.evaluate.report import _fmt, _table, build_report


def test_counts_render_as_counts_not_ratios():
    """'985.000 days underwater' is not a number anyone wants to read."""
    assert _fmt("max_dd_days", 985.0) == "985"
    assert _fmt("n_days", 5094.0) == "5,094"
    assert _fmt("avg_positions", 236.53) == "237"


def test_percent_metrics_render_as_percentages():
    assert _fmt("ann_return", 0.1687) == "16.87%"
    assert _fmt("max_drawdown", -0.2813) == "-28.13%"


def test_ratios_keep_decimals_and_missing_values_are_visible():
    assert _fmt("sharpe", 1.9905) == "1.990"
    assert _fmt("sharpe", float("nan")) == "—"
    assert _fmt("sharpe", None) == "—"


def test_tables_are_wrapped_in_their_own_scroll_container():
    """Tables are the one element allowed to exceed the page width, and only
    inside a container — otherwise the whole page scrolls sideways on a phone."""
    frame = pd.DataFrame({"a": [1.0], "b": [2.0]}, index=["row"])
    assert "table-wrap" in _table(frame, "idx")


def test_report_is_a_complete_self_contained_document():
    frame = pd.DataFrame({"return": [0.1]}, index=[2020])
    frame.index.name = "year"
    html = build_report(
        title="t", metrics={"sharpe": 1.0}, figures={}, tables={"By calendar year": frame},
        statistics={"deflated_sharpe": 0.9, "n_trials": 3}, config={"a": 1},
        notes=["SYNTHETIC DATA"],
    )
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    assert "SYNTHETIC DATA" in html
    assert "http://" not in html and "https://" not in html, "no external assets"


def test_report_escapes_untrusted_text():
    html = build_report(
        title="<script>alert(1)</script>", metrics={}, figures={}, tables={},
        statistics={}, config={},
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
