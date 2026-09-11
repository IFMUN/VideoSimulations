# equity_lab — systematic momentum strategies with asymmetric risk construction

A research stack for building, backtesting and — more importantly — *disbelieving*
momentum-driven equity strategies. It contains a momentum signal library, a
sequential backtest engine with real cost and capacity modelling, an explicitly
asymmetric portfolio construction layer, and the evaluation machinery needed to
tell a real effect from a well-fitted one.

The design principle throughout: **anything the strategy claims should be
falsifiable by something in this repository.** Every mechanism can be switched
off and measured (`ablation`), every parameter can be swept with the cost of
searching accounted for (`sweep`), and every result can be re-derived out of
sample on purged, embargoed windows (`walkforward`).

---

## Quick start

```bash
cd quant
pip install -r requirements.txt

python -m equity_lab.cli data        -c configs/quick.yaml   # inspect the panel
python -m equity_lab.cli signals     -c configs/quick.yaml   # ICs, before any sizing
python -m equity_lab.cli backtest    -c configs/quick.yaml   # one run + HTML report
python -m equity_lab.cli ablation    -c configs/quick.yaml   # is the asymmetry earning its keep?
python -m equity_lab.cli ablation    -c configs/quick.yaml --seeds 11,23,41   # ...on independent panels
python -m equity_lab.cli sweep       -c configs/quick.yaml -g configs/grid_asymmetry.yaml
python -m equity_lab.cli walkforward -c configs/momentum_asymmetric.yaml
python -m equity_lab.cli leaderboard
```

`make research` runs the whole loop; `make test` runs the suite.

Any field can be overridden from the command line without editing a file:

```bash
python -m equity_lab.cli backtest -c configs/quick.yaml \
  -s portfolio.risk.target_vol=0.08 \
  -s portfolio.asymmetry.long_convexity=1.8
```

Every run writes `runs/<name>-<config-hash>/` containing the resolved config, a
provenance manifest (git SHA, package versions, data description), metrics,
daily records, weights, per-table CSVs, and a self-contained `report.html`.
The hash is of the config, so re-running an identical configuration lands in the
same directory and `leaderboard` can rebuild the comparison from disk at any
time, from a different process, weeks later.

---

## A note on the data

**The default data source is synthetic, and every number below is therefore a
statement about a simulated market, not a live-market result.** The environment
this was built in blocks outbound access to market-data hosts at the network
policy layer, so no vendor prices could be fetched.

That constraint shaped the design rather than compromising it. The data layer is
an interface (`equity_lab/data/loaders.py`); the engine never learns where prices
came from. Point it at real data by changing two lines:

```yaml
data:
  source: csv            # a directory of <TICKER>.csv files you already have
  csv_dir: data/prices   # optional sectors.csv maps ticker -> sector
```

`stooq` (free, no key) and `yfinance` loaders ship as well. Loaded panels cache
to parquet keyed by a hash of the request, so a sweep over 200 configurations
hits the network once.

The synthetic generator is not noise dressed as prices. It is built so that the
phenomena the strategy claims to exploit — and the pathologies it claims to
defend against — are present *for stated reasons*:

| Property | Mechanism |
|---|---|
| cross-sectional momentum | persistent latent drift, AR(1) with a multi-month half-life |
| short-horizon reversal | additive microstructure noise in the observed log price |
| volatility clustering, fat tails | GARCH(1,1) market variance with Student-t shocks |
| **momentum crashes** | a rebound regime that pays prior losers after deep drawdowns |
| downside beta asymmetry | per-name betas that differ in up and down markets |
| survivorship | names list and delist through the sample |

Measured on the default panel: 12-1 momentum IC ≈ 0.06 (t ≈ 5.7); 5-day reversal
IC ≈ 0.02 at a 5-day horizon, decaying to ~0 by 21 days; market excess kurtosis
≈ 13; and a naive momentum long/short with skew ≈ −5 and a ~40% drawdown
concentrated *entirely* in rebound days. Those are the right shapes and roughly
the right magnitudes. The crash regime matters most: without it the asymmetric
machinery would have nothing to defend against and the ablation would be theatre.

---

## What one run looks like

`backtest -c configs/momentum_asymmetric.yaml` on the default 400-name synthetic
panel (2005-2025, 235 monthly rebalances, 20.2 years):

| | |
|---|---|
| Sharpe / Sortino | 1.99 / 2.49 |
| Annual return / vol | 16.9% / 8.5% |
| Max drawdown | -28.1% (985 days underwater) |
| Skew / excess kurtosis | -6.25 / 105 |
| Beta / annual alpha | -0.14 / 18.2% |
| Annual turnover | 5.41x equity |
| Cost drag / carry | -0.79% / +0.81% |

**Do not read that Sharpe as a claim about real markets.** The synthetic panel's
12-1 momentum IC is about 0.06; the real-world figure is closer to 0.02-0.04, so
the generator is roughly twice as generous as reality and the Sharpe scales with
it. What *is* worth reading is the shape. The score-decile table runs
monotonically from -6.2% to +24.5% annualised with hit rates rising from 40% to
65%, and the regime table puts +29.6% annualised in calm markets against -33.7%
on the 7.6% of days that are both bearish and volatile. That concentration of
pain into a small number of days is the thing the asymmetric construction exists
to address, and it is visible in every report the stack produces.

---

## Architecture

```
equity_lab/
  config.py         every knob, typed; a run is fully described by one YAML file
  data/
    synthetic.py    generative market (momentum, reversal, crashes, delistings)
    loaders.py      csv / stooq / yfinance / synthetic, with a parquet cache
    universe.py     point-in-time investability, with buffered liquidity ranks
    panel.py        the one container every other layer consumes
  signals/
    momentum.py     the momentum family + the short-horizon effects it interacts with
    transforms.py   winsorise -> standardise -> neutralise -> smooth
  portfolio/
    asymmetry.py    the asymmetric risk construction (see below)
    risk.py         covariance, vol targeting, neutralisation, constraints
    construction.py score -> weights, in one readable sequence
  backtest/
    engine.py       sequential daily loop; positions in shares, orders worked over days
    costs.py        spread + square-root impact + borrow / financing / interest
  evaluate/
    metrics.py      return, risk and implementation metrics
    stats.py        PSR, deflated Sharpe, block bootstrap, PBO via CSCV
    attribution.py  by leg, sector, regime, and score decile
    plots.py        figures        report.py  self-contained HTML
  research/
    experiment.py   run + record + leaderboard
    walkforward.py  purged, embargoed out-of-sample validation
    sweep.py        grid search that measures its own overfitting
    ablation.py     turn each mechanism off and see what breaks
```

---

## The signals

| Signal | Idea |
|---|---|
| `momentum` | 12-1 cumulative return; the gap skips the reversal month |
| `risk_adjusted_momentum` | momentum / trailing vol — a cross-sectional Sharpe sort, not a volatility bet in disguise |
| `residual_momentum` | momentum of market-residual returns, scaled by residual vol |
| `pct_52w_high` | distance to the trailing high — anchor-based, fires on names that *held* their gains |
| `information_discreteness` | was the move continuous or jumpy? continuous information is absorbed slowly and drifts further |
| `frog_in_pan_momentum` | momentum modulated by that continuity, with a `tilt` knob that degenerates to plain momentum at 0 |
| `momentum_consistency` | share of trailing sub-periods that were positive |
| `short_term_reversal` | the one effect that reliably works *against* the rest of the blend |
| `time_series_trend` | fast/slow MA spread; has a meaningful per-name sign, unlike the cross-sectional signals |
| `low_volatility` | the defensive leg of a blend |

Every signal is verified lag-safe by a parameterised test that recomputes it on
truncated data and asserts the past is unchanged.

**The shipped blend weights are a priori, not fitted — and it shows.** Running
`signals` on the default panel gives the blend an IC of 0.045 at a 21-day
horizon, *below* `momentum_consistency` (0.058) and `risk_adjusted_momentum`
(0.055) on their own, because `pct_52w_high` (0.001) and `residual_momentum`
(0.024) drag it down.

Every report includes an **IC correlation matrix** between components, and on
this panel it explains the shortfall precisely:

|  | resid_mom | risk_adj_mom | mom_consist | 52w_high | st_reversal |
|---|---|---|---|---|---|
| **risk_adj_mom** | 0.72 | 1.00 | **0.94** | 0.85 | −0.43 |
| **mom_consist** | 0.69 | **0.94** | 1.00 | 0.79 | −0.40 |
| **st_reversal** | −0.29 | −0.43 | −0.40 | −0.67 | 1.00 |

`risk_adjusted_momentum` and `momentum_consistency` have an IC correlation of
0.94 — they are the same bet under two names, so the blend is paying two weights
for one signal. `pct_52w_high` contributes almost no IC and is 0.85 correlated
with what does. `short_term_reversal` is the only genuine diversifier in the set.
Two components with identical ICs are worth very different amounts depending on
whether their ICs are correlated, which is why the matrix is in every report
rather than buried in a notebook.

The weights are nonetheless left round on purpose. Fitting them on this panel
would raise the headline and mean nothing; tune them with `sweep` and read the
PBO, or with `walkforward` and read only the out-of-sample curve.

---

## Asymmetric risk construction

Momentum's problem is not its average volatility, it is the *shape* of its left
tail: sharp, negatively skewed crashes concentrated in the short leg when a bear
market turns. Sizing it symmetrically is a modelling error, not a style choice.
Five mechanisms attack different parts of that tail:

1. **Asymmetric signal response.** The long leg is convex in the score
   (`long_convexity > 1` — concentrate in the strongest winners); the short leg
   is concave (`short_convexity < 1` — spread out, own less of the extreme losers
   that rebound hardest). Costs nothing in turnover; changes the tail you hold.
2. **Downside risk in the sizing denominator.** Positions are scaled by a blend
   of volatility and *downside deviation*, then penalised for conditional beta
   asymmetry — one-sided per leg: a long is shrunk when `beta_down > beta_up`, a
   short when `beta_up > beta_down` (the profile that destroys a short book in a
   rebound).
3. **Crash-state short-leg scaling.** After a sustained market decline *and* a
   volatility spike — both, not either — the short leg is cut to a fraction of
   its normal size. Two definitions of "weak market" are offered, because they
   disagree exactly when it matters: negative two-year return (the classic) misses
   a violent drawdown inside a strong bull run, which is precisely the setup for
   a sharp reversal, so drawdown-from-trailing-peak is the default.
4. **Hysteretic drawdown throttle.** Exposure is cut fast into the strategy's own
   drawdown (`cut_halflife` = 1 day) and restored slowly out of it
   (`recover_halflife` = 20 days). Symmetric de-risking sells the bottom and buys
   back into the next leg down. The high-water mark is trailing rather than
   all-time, so one crash does not pin the book at its floor for years.
5. **Constant-volatility scaling** of the whole book — the single most effective
   known fix for momentum's negative skew.

A sixth — **one-sided trailing stops**, cutting losers while leaving winners
alone — is implemented but **disabled by default**. See the next section for why.

---

## What the ablation actually found

Run on the `quick` config (synthetic panel, 2008–2018), disabling one mechanism
at a time against an identical signal and identical data:

| Variant | Sharpe | Max DD | Skew | Turnover |
|---|---|---|---|---|
| **full construction** | **1.14** | **−21.2%** | **−2.13** | **5.74** |
| no downside risk sizing | 1.18 | −21.2% | −2.15 | 5.71 |
| no constant-vol scaling | 1.16 | −18.7% | −2.51 | 5.64 |
| symmetric response | 1.14 | −22.3% | −2.55 | 5.66 |
| no crash-state short cut | 1.14 | −21.8% | −2.12 | 5.71 |
| no drawdown throttle | 1.11 | −25.4% | −1.75 | 6.12 |
| fully symmetric baseline | 1.09 | −25.1% | −2.30 | 6.58 |
| with trailing stops | 0.91 | −30.6% | −3.22 | 6.57 |

Read on its own, that table says trailing stops are catastrophic and everything
else is noise. **That reading would be wrong, and the error is instructive.**

A Sharpe difference of 0.03–0.2 sits comfortably inside the sampling error of a
ten-year backtest. So the ablation was repeated on **three independent panels**
(`ablation --seeds 11,23,41`), asking not "how large was the difference" but
"did it point the same way every time":

| Variant vs. full | Sharpe | Max DD | Skew | Verdict |
|---|---|---|---|---|
| fully symmetric baseline | −0.11, −0.27, −0.13 | worse ×3 | worse ×3 | **consistent** |
| symmetric response | +0.09, −0.08, −0.07 | worse ×3 | mixed | drawdown consistent |
| with trailing stops | +0.28, −0.20, +0.17 | mixed | mixed | **inconclusive** |

The headline finding survives: **removing the asymmetric construction entirely
makes Sharpe, drawdown, skew and turnover worse on every panel tested** — mean
Sharpe 1.54 vs 1.71, mean max drawdown −30.4% vs −25.3%, mean skew −1.50 vs
−1.08, and higher turnover. Modest on Sharpe, meaningful on exactly the two
statistics the construction exists to improve, and sign-consistent, which is the
part that matters.

The stops finding did **not** survive. On the default panel stops looked
decisively harmful, and a dedicated 12-candidate sweep appeared to confirm it
(Sharpe rising monotonically as the stop loosened: 0.12 → 1.01, 0.20 → 1.05,
0.35 → 1.11, none → 1.14). That sweep was run entirely on one panel, so it
measured one draw very precisely — which is the exact overfitting failure the
`sweep` command's own PBO statistic is built to flag. Across panels the Sharpe
effect changes sign. What does hold is that stops cost ~0.8× of annual turnover
and leave drawdown and skew slightly worse on average, so the default is
`stops.enabled: false` as the cheaper and simpler choice, not as a demonstrated
truth.

Individual mechanisms below the whole — the downside-risk sizing and the
crash-state short cut in particular — are **not** separately demonstrable on this
data. Honest answer: they are within noise, and the aggregate effect is carried
mostly by the drawdown throttle and the convex/concave response. `--seeds`
exists because of this finding; use it before quoting any single row.


**Read that table by skew and drawdown before Sharpe.** Every mechanism here is
paid for in expected return; the question is whether the tail it buys back is
worth the premium.

---

## Walk-forward, and what selection costs

`walkforward -c configs/quick.yaml -g configs/grid_asymmetry.yaml` selects among
18 candidates on each purged, embargoed training window and applies the winner
out of sample:

| Fold | Train | Test | In-sample Sharpe | Out-of-sample Sharpe |
|---|---|---|---|---|
| 0 | 2008-01 → 2011-11 | 2011-12 → 2013-11 | 2.57 | **0.29** |
| 1 | 2008-01 → 2014-06 | 2014-07 → 2016-06 | 1.42 | 2.20 |
| 2 | 2008-01 → 2016-12 | 2017-01 → 2018-12 | 1.23 | 1.50 |

Stitched out-of-sample: Sharpe 1.21, annual return 9.2%, max drawdown −17.3%,
skew −0.69. **In-sample minus out-of-sample Sharpe: 0.41.**

That degradation figure is the headline, not the Sharpe. Selecting the best of
18 candidates on a training window cost 0.41 of Sharpe on average, and on fold 0
it cost 2.3 — the in-sample winner delivered almost nothing out of sample. Any
research process that reports only the selected configuration's full-sample
Sharpe is reporting that 0.41 as if it were skill.

One genuine cross-fold signal did emerge: `long_convexity: 1.8` was selected in
all three folds. A parameter chosen independently on three different training
windows is the kind of consistency worth taking seriously — considerably more so
than a single large number from a single fit.

---

## Guarding against fooling yourself

- **No-lookahead, structurally.** The engine is a sequential daily loop with a
  single lag policy: a decision on day `d` is filled on `d + execution_lag` at
  that day's price. A test truncates the panel and asserts the past is
  bit-for-bit unchanged — covering the universe screen, signals, risk model,
  sizing and execution at once, not just one `shift()`. That test caught a real
  end-of-sample artefact: `resample("ME").last()` invents a month-end rebalance
  on the cut date when a sample ends mid-month.
- **Costs are not optional.** Spread, square-root market impact, stock borrow,
  financing on gross above equity, and interest on cash — paid only up to equity,
  since short proceeds inflate the cash balance and paying full rate on them
  while separately charging borrow double-counts the same financing leg.
- **Orders are worked, not assumed filled.** Anything an ADV participation cap
  blocks today stays outstanding and is worked again tomorrow, expiring after
  `order_horizon_days`. Dropping the unfilled remainder — the usual shortcut —
  leaves the book permanently and invisibly under-invested.
- **Capacity is real.** Positions are capped at a multiple of ADV, so the same
  weights become unreachable as capital grows and the backtest disagrees with
  itself at different AUM. That disagreement is the honest answer.
- **Selection is priced.** Sweeps report the deflated Sharpe (measured against
  what the best of N worthless candidates would produce) and PBO via
  combinatorially symmetric cross-validation. Identical candidates are
  deduplicated first: a grid that toggles a parameter which is inert under some
  other setting otherwise inflates PBO for reasons unrelated to overfitting.
- **Out-of-sample means out-of-sample.** Walk-forward windows are purged (drop
  training observations whose evaluation horizon reaches into the test block) and
  embargoed (drop a buffer after it). Only the stitched out-of-sample curve is
  quotable; the in-sample selection Sharpe is a maximum over candidates and is
  biased upward by construction, so it is reported *next to* the OOS number as an
  explicit degradation figure.
- **Configs reject typos.** An unknown key raises rather than being silently
  ignored — a mis-spelled parameter is otherwise a no-op that wastes a research
  cycle and produces a confidently wrong conclusion. (This caught a stray
  placeholder during development.)

---

## Known limitations

- Results shown are on synthetic data. The pipeline is real; the prices are not.
- A single equal-weighted market proxy stands in for a full risk model. Beta
  neutralisation is a one-factor hedge, not a Barra-style decomposition.
- Costs are modelled, not calibrated. `impact_coef` in particular should be
  fitted to your own execution data before any capacity claim is made.
- The covariance is a shrunk sample matrix. Adequate for volatility targeting;
  not adequate for mean-variance optimisation.
- Rebalancing is calendar-based. An event- or signal-decay-driven schedule would
  likely trade less for the same exposure.
- The synthetic market has no earnings dates, no index events, no borrow
  availability constraints, and no intraday structure.
- A single ablation or sweep on one panel is one draw. Use `--seeds` before
  quoting any individual row; this repository's own development contains a
  worked example of getting that wrong.
- PBO is itself a noisy statistic (its ~70 CSCV combinations are highly
  dependent). Read it as "clearly below 0.5" versus "around or above 0.5", not
  as a precise number.

## Requirements

Python 3.11+, `numpy`, `pandas`, `pyyaml`, `matplotlib`, `pyarrow`; `pytest` for
the suite. Deliberately no scipy — the statistical core (inverse normal CDF,
PSR/DSR, stationary bootstrap, PBO) is implemented on numpy alone.
