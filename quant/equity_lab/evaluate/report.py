"""Self-contained HTML research report.

One file, no external assets, safe to archive next to the run that produced it.
The layout is deliberately ordered the way a sceptic reads a backtest: headline
numbers first, then the equity curve, then everything that could invalidate it —
costs, capacity, regime dependence, and the statistics that account for how many
configurations were tried.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..utils import ensure_dir

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; background:#f4f3f0; color:#0b0b0b;
  font:14px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
.wrap { max-width:1040px; margin:0 auto; padding:32px 20px 72px; }
h1 { font-size:22px; margin:0 0 4px; letter-spacing:-0.01em; }
h2 { font-size:15px; margin:34px 0 12px; letter-spacing:.04em; text-transform:uppercase;
  color:#52514e; font-weight:600; }
.sub { color:#52514e; margin:0 0 24px; font-size:13px; }
.card { background:#fcfcfb; border:1px solid #e4e3df; border-radius:10px;
  padding:18px 20px; margin-bottom:18px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
.tile { background:#fcfcfb; border:1px solid #e4e3df; border-radius:10px; padding:14px 16px; }
.tile .k { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:#52514e; }
.tile .v { font-size:22px; font-weight:600; margin-top:4px; letter-spacing:-0.01em;
  font-variant-numeric:tabular-nums; }
.tile .n { font-size:11px; color:#52514e; margin-top:2px; }
img { width:100%; display:block; border-radius:8px; }
table { border-collapse:collapse; width:100%; font-size:12.5px;
  font-variant-numeric:tabular-nums; }
th,td { text-align:right; padding:6px 9px; border-bottom:1px solid #e4e3df; }
th:first-child,td:first-child { text-align:left; }
th { color:#52514e; font-weight:600; font-size:11px; text-transform:uppercase;
  letter-spacing:.04em; }
tbody tr:last-child td { border-bottom:none; }
.note { font-size:12.5px; color:#52514e; margin:8px 0 0; }
.flag { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
  border:1px solid #e4e3df; background:#fcfcfb; margin-right:6px; }
details { margin-top:10px; }
summary { cursor:pointer; color:#52514e; font-size:12.5px; }
pre { overflow-x:auto; font-size:11.5px; background:#f4f3f0; padding:12px;
  border-radius:8px; border:1px solid #e4e3df; }
@media (max-width:560px){ .wrap{padding:20px 14px 48px;} h1{font-size:19px;} }
"""

_PERCENT = {
    "total_return", "cagr", "ann_return", "ann_vol", "max_drawdown", "hit_rate",
    "var_95", "cvar_95", "best_day", "worst_day", "alpha", "cost_drag",
    "carry_contribution", "pct_days_throttled", "return", "vol", "max_dd",
    "ann_contribution", "contribution_vol", "share_of_total", "mean_forward_return",
    "annualised", "share_of_days", "cumulative",
}


def _fmt(key: str, value: Any) -> str:
    if isinstance(value, str):
        return html.escape(value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, (int,)) or (isinstance(value, float) and float(value).is_integer()
                                     and abs(value) > 1000):
        return f"{value:,.0f}"
    if key in _PERCENT:
        return f"{value:.2%}"
    return f"{value:.3f}"


def _table(frame: pd.DataFrame, index_name: str = "") -> str:
    if frame is None or not len(frame):
        return "<p class='note'>No data.</p>"
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in frame.columns)
    rows = []
    for idx, row in frame.iterrows():
        cells = "".join(f"<td>{_fmt(col, row[col])}</td>" for col in frame.columns)
        rows.append(f"<tr><td>{html.escape(str(idx))}</td>{cells}</tr>")
    return (
        f"<table><thead><tr><th>{html.escape(index_name)}</th>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _tiles(items: list[tuple[str, str, str]]) -> str:
    cards = "".join(
        f"<div class='tile'><div class='k'>{html.escape(k)}</div>"
        f"<div class='v'>{html.escape(v)}</div>"
        f"<div class='n'>{html.escape(n)}</div></div>"
        for k, v, n in items
    )
    return f"<div class='tiles'>{cards}</div>"


def _image(b64: str, caption: str = "") -> str:
    cap = f"<p class='note'>{html.escape(caption)}</p>" if caption else ""
    return f"<div class='card'><img alt='{html.escape(caption)}' src='data:image/png;base64,{b64}'/>{cap}</div>"


def build_report(
    title: str,
    metrics: dict[str, float],
    figures: dict[str, str],
    tables: dict[str, pd.DataFrame],
    statistics: dict[str, float],
    config: dict[str, Any],
    notes: list[str] | None = None,
) -> str:
    """Assemble the report. Returns the complete HTML document as a string."""
    m = metrics
    tiles = _tiles([
        ("Sharpe", _fmt("sharpe", m.get("sharpe")), f"Sortino {_fmt('x', m.get('sortino'))}"),
        ("Ann. return", _fmt("ann_return", m.get("ann_return")),
         f"vol {_fmt('ann_vol', m.get('ann_vol'))}"),
        ("Max drawdown", _fmt("max_drawdown", m.get("max_drawdown")),
         f"{_fmt('x', m.get('max_dd_days'))} days underwater"),
        ("Skew", _fmt("x", m.get("skew")), f"tail ratio {_fmt('x', m.get('tail_ratio'))}"),
        ("Deflated Sharpe", _fmt("x", statistics.get("deflated_sharpe")),
         f"{int(statistics.get('n_trials', 1))} trial(s)"),
        ("Ann. turnover", _fmt("x", m.get("ann_turnover")),
         f"cost {_fmt('cost_drag', m.get('cost_drag'))}"),
    ])

    body = [f"<h1>{html.escape(title)}</h1>"]
    body.append(
        f"<p class='sub'>{html.escape(str(config.get('_period', '')))} &middot; "
        f"{html.escape(str(config.get('_universe', '')))}</p>"
    )
    if notes:
        body.append("<div class='card'>" + "".join(
            f"<span class='flag'>{html.escape(n)}</span>" for n in notes
        ) + "</div>")
    body.append(tiles)

    order = [
        ("equity", "Cumulative performance", "Log scale: equal vertical distance is equal return."),
        ("drawdown", "", ""),
        ("rolling_sharpe", "", ""),
        ("exposures", "", "Gross, net and the drawdown throttle, all as multiples of equity."),
        ("distribution", "", ""),
        ("deciles", "Signal quality",
         "Computed before portfolio construction, so leverage and sizing cannot flatter it."),
        ("yearly", "", ""),
    ]
    for key, heading, caption in order:
        if key not in figures:
            continue
        if heading:
            body.append(f"<h2>{html.escape(heading)}</h2>")
        body.append(_image(figures[key], caption))

    if tables:
        body.append("<h2>Tables</h2>")
    for name, frame in tables.items():
        body.append(f"<div class='card'><h2 style='margin-top:0'>{html.escape(name)}</h2>"
                    f"{_table(frame, frame.index.name or '')}</div>")

    if statistics:
        body.append("<h2>Statistical significance</h2>")
        stats_frame = pd.DataFrame(
            {"value": pd.Series(statistics)}
        )
        body.append(
            "<div class='card'>" + _table(stats_frame, "statistic") +
            "<p class='note'>The deflated Sharpe asks whether this result beats what "
            "selecting the best of N tried configurations would produce by luck alone. "
            "The bootstrap interval keeps the return series' own volatility clustering "
            "rather than assuming independence.</p></div>"
        )

    body.append("<h2>Full metric table</h2>")
    body.append("<div class='card'>" + _table(
        pd.DataFrame({"value": pd.Series(metrics)}), "metric"
    ) + "</div>")

    body.append(
        "<details><summary>Configuration that produced this run</summary>"
        f"<pre>{html.escape(json.dumps({k: v for k, v in config.items() if not k.startswith('_')}, indent=2, default=str))}</pre></details>"
    )

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><div class='wrap'>{''.join(body)}</div></body></html>"
    )


def write_report(path: Path, content: str) -> Path:
    ensure_dir(Path(path).parent)
    Path(path).write_text(content, encoding="utf-8")
    return Path(path)
