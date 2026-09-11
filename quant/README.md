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
python -m equity_lab.cli stress      -c configs/quick.yaml   # vary the hazard; see which mechanisms track it
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
| Sharpe / Sortino | 2.01 / 2.55 |
| Annual return / vol | 17.2% / 8.6% |
| Max drawdown | -27.7% (953 days underwater) |
| Skew / excess kurtosis | -5.74 / 95.8 |
| Beta / annual alpha | -0.16 / 18.7% |
| Annual turnover | 4.94x equity |
| Cost drag / carry | -0.73% / +0.82% |

**Do not read that Sharpe as a claim about real markets.** The synthetic panel's
12-1 momentum IC is about 0.06; the real-world figure is closer to 0.02-0.04, so
the generator is roughly twice as generous as reality and the Sharpe scales with
it. What *is* worth reading is the shape. The score-decile table runs
monotonically from -7.1% to +31.0% annualised with hit rates rising from 41% to
71%, and the regime table puts +29.5% annualised in calm markets against -31.9%
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

**Blend weights are a priori, not fitted — with one documented exception.**

Every report carries two diagnostics that decide a blend. The first is the IC
correlation matrix between components; the second, and the one that actually
settles the question, is the **marginal IC**: each component is regressed
cross-sectionally on all the others and the IC of its *residual* is measured.
A raw IC answers "does this predict?". Marginal IC answers "does this predict
anything the rest of the blend does not?"

On the current blend:

| Component | Mean IC | Marginal IC | Retained |
|---|---|---|---|
| `short_term_reversal` | 0.053 | **0.060** | 114% |
| `momentum_consistency` | 0.058 | 0.015 | 26% |
| `risk_adjusted_momentum` | 0.055 | −0.002 | −4% |
| `pct_52w_high` | 0.001 | −0.024 | — |

The momentum family is **one bet**. `risk_adjusted_momentum` and
`momentum_consistency` have an IC correlation of 0.94 and retain 26% and −4% of
their IC once orthogonalised. `short_term_reversal` is the only component that
survives orthogonalisation — it *gains*, because stripping the momentum
component sharpens it.

That does **not** mean deleting the redundant ones. Averaging several noisy
estimates of the same bet reduces noise; picking the one with the best in-sample
IC is just selection under another name. So the momentum variants are kept and
averaged, and the weights stay round.

The exception: **`residual_momentum` was removed.** It was the highest-weighted
signal in the original config, chosen on theory (stripping the market component
should remove the dynamic beta that makes momentum crash). Its marginal IC came
out negative on all four test panels — including it with a positive weight was
actively subtracting information. Re-running the backtest with it dropped beat
the original blend on **4 of 4 panels**, by +0.36, +0.37, +0.15 and +0.14 of
Sharpe, while simultaneously improving skew (−1.03 vs −1.34), drawdown (−23.4%
vs −24.3%), turnover (4.87 vs 5.34) and cost.

That is a stronger result than a grid-search winner for a specific reason: the
hypothesis came from an independent diagnostic *before* any blend was
backtested, and it won on every panel across five metrics at once. The blend's
own IC rose from 0.045 to 0.058, so it now matches its best single component
instead of trailing it. The signal stays in the registry — the theoretical case
is sound and the removal is an empirical call on this generator.

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

Disabling one mechanism at a time on the `quick` config, against an identical
signal and identical data:

| Variant | Sharpe | Max DD | Skew | Turnover |
|---|---|---|---|---|
| **full construction** | 1.50 | **-18.6%** | **-1.32** | **5.18** |
| symmetric response | **1.65** | -20.0% | -1.59 | 5.07 |
| no constant-vol scaling | 1.53 | -17.2% | -1.97 | 5.42 |
| no downside risk sizing | 1.52 | -18.8% | -1.19 | 5.16 |
| fully symmetric baseline | 1.52 | -20.5% | -1.71 | 6.05 |
| no crash-state short cut | 1.51 | -19.0% | -1.30 | 5.18 |
| no drawdown throttle | 1.41 | -23.0% | -1.12 | 5.59 |
| with trailing stops | 1.18 | -29.9% | -2.31 | 6.12 |

Read on its own, that table invites at least two wrong conclusions. **A single
ablation on a single panel is one draw**, and Sharpe differences of 0.03-0.2 sit
comfortably inside the sampling error of a ten-year backtest. `ablation --seeds
11,23,41` repeats it on independent panels and asks the only question that
survives: *did the effect point the same way every time?*

| Effect of removing it | Mean Sharpe | Mean max DD | Mean skew | Turnover | Sign-consistent on |
|---|---|---|---|---|---|
| **full construction** (reference) | 1.93 | -25.0% | -0.93 | 4.76 | — |
| fully symmetric baseline | 1.86 | -27.4% | **-1.39** | **5.40** | **skew, turnover** |
| symmetric response | 1.93 | **-26.7%** | -1.03 | 4.72 | **max DD** |
| no drawdown throttle | 1.91 | -25.2% | **-1.07** | 4.84 | **skew** |
| no constant-vol scaling | 1.91 | -22.8% | **-1.19** | 5.02 | **max DD, skew** |
| no downside risk sizing | 1.90 | -25.9% | -0.94 | 4.78 | **Sharpe, max DD** |
| no crash-state short cut | 1.93 | -25.3% | -0.92 | 4.76 | *nothing* |
| with trailing stops | **2.13** | -28.7% | -1.11 | 5.35 | turnover |

**The honest summary is narrower than the single-panel table suggests.**
Removing the entire asymmetric construction makes skew and turnover worse on
*every* panel — those two effects are sign-consistent. Its Sharpe and drawdown
effects are positive on average (+0.07 and +2.4 points) but flip sign across
panels, so they are not claims I would defend. That is the expected profile for
a construction that explicitly trades expected return for tail shape: it is
doing what it says, and what it says is not "higher Sharpe".

Two individual findings hold up. The **drawdown throttle** and
**constant-volatility scaling** each buy skew on every panel. The **convex/concave
response** buys drawdown on every panel while costing Sharpe.

Two do not. The **crash-state short cut** is sign-consistent on *nothing* — see
the stress test below, which reaches the same verdict from a different angle. And
the **trailing stops** story is the cautionary tale: on the default panel they
look catastrophic (-0.32 Sharpe, -11 points of drawdown), and a dedicated
12-candidate sweep appeared to confirm it with a monotone gradient. Across
panels they *raise* mean Sharpe to 2.13, the highest of any variant. That sweep
ran entirely on one panel, so it measured one draw very precisely — the exact
overfitting failure the `sweep` command's own PBO statistic exists to flag, and
I walked into it while building the tool designed to catch it.

Stops stay off by default on the two effects that *are* consistent: they cost
turnover, and they leave drawdown and skew worse. That is a defensible default,
not a demonstrated truth, and the docstring says so.

**Read this table by skew and drawdown before Sharpe.** Every mechanism here is
paid for in expected return; the question is whether the tail it buys back is
worth the premium.

---

## Is the drawdown throttle fitting the synthetic crash?

The throttle is the single largest contributor in the ablation, which makes it
the mechanism most likely to be fitting this generator's particular crash regime.
There is no real-market data here to settle that, but there is a second question
the generator *can* answer, and it is the more useful one for a risk control:
**how does its value change as the hazard it targets gets stronger or weaker?**

`stress -c configs/quick.yaml` varies `crash_intensity` — the strength of the
rebound regime that pays prior losers — and re-runs the ablation at each level
across three seeds:

| Crash intensity | Δ max drawdown from removing it | Δ skew | Sign consistency (DD) |
|---|---|---|---|
| 0.0 (no crash mechanism) | −0.001 | 0.000 | 0.33 (noise) |
| 1.0 | −0.007 | −0.026 | 0.67 |
| 2.2 (default) | **−0.021** | **−0.095** | **1.00** |

At zero crash intensity the throttle is **inert**: Sharpe 3.939 with it and
3.939 without, identical skew, identical turnover. It never fires, because the
strategy never gets deep enough into drawdown. As the hazard intensifies its
drawdown and skew benefits grow monotonically and become sign-consistent across
every seed.

So my prior was wrong. I expected the throttle to generalise because it keys on
the strategy's own equity curve rather than on a market regime — but its
measured benefit is entirely contingent on deep drawdowns existing. It is not a
general-purpose risk control; it is crash insurance.

**Decision: keep it on by default.** Not because it raises Sharpe — it does not
reliably do that at any hazard level — but because it has the profile you want
from insurance: *no measurable premium when the hazard is absent*, and a
sign-consistent payoff in drawdown and skew when it is present. The mechanism
should transfer to real data, since real momentum crashes are at least as severe
as this generator's; the *magnitude* shown here should not be quoted, because it
is a function of a dial I set.

The same test has a negative result worth stating: the **crash-state short cut**
shows no reliable effect at *any* hazard level (sign consistency 0.33–0.67
throughout), including the regime it was designed for. The most likely
explanation is that the throttle and the constant-volatility scaler already
de-risk in those states, leaving it nothing to do. It is kept because it costs
nothing measurable, but it is not demonstrable, and the obvious next experiment
is to re-run the stress test with the throttle disabled to separate "redundant"
from "useless".

---

## Walk-forward, and what selection costs

`walkforward -c configs/quick.yaml -g configs/grid_asymmetry.yaml` selects among
18 candidates on each purged, embargoed training window and applies the winner
out of sample:

| Fold | Train | Test | In-sample Sharpe | Out-of-sample Sharpe |
|---|---|---|---|---|
| 0 | 2008-01 → 2011-11 | 2011-12 → 2013-11 | 2.74 | **0.50** |
| 1 | 2008-01 → 2014-06 | 2014-07 → 2016-06 | 1.67 | 2.24 |
| 2 | 2008-01 → 2016-12 | 2017-01 → 2018-12 | 1.48 | 2.52 |

Stitched out-of-sample: annual return 11.8%, vol 7.4%, over 6 years.
**In-sample minus out-of-sample Sharpe: 0.21.**

That degradation figure is the headline, not the Sharpe. Selecting the best of
18 candidates on a training window cost 0.21 of Sharpe on average, and on fold 0
it cost 2.24 — the in-sample winner delivered almost nothing out of sample. Any
process that reports only the selected configuration's full-sample Sharpe is
reporting that gap as if it were skill.

One cross-fold signal emerged, and it is not the one I expected:
**`long_convexity: 1.0` — the symmetric response — was selected in all three
folds.** An earlier version of this repository, with a weaker signal blend,
selected `1.8` in all three, and the README said so. Improving the signal
reversed it. A parameter chosen independently on three training windows is worth
taking seriously either way, and here it agrees with the multi-seed ablation:
convexity in the long leg buys drawdown and costs Sharpe, so a selector
optimising Sharpe will keep turning it off.

The shipped default keeps `long_convexity: 1.35`. That is a deliberate choice to
pay Sharpe for tail shape, not an oversight — and it is exactly the sort of
choice that should be made explicitly, with the evidence against it written down
next to it, rather than buried in a default nobody re-examines.

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
- **Two worked failures are documented, not hidden.** A single-panel sweep
  "rejected" trailing stops decisively; across panels the effect reverses. A
  theory-led weighting put the highest weight on `residual_momentum`; its
  marginal IC is negative on every panel. Both are written up in full, because a
  repository that only records its successes is not evidence of a process.
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
  quoting any individual row; this repository's own development contains two
  worked examples of getting that wrong.
- `stress` varies a *simulated* hazard. It establishes how a mechanism's value
  responds to the thing it targets, which is a real and useful property — but it
  is not evidence about real markets, and the magnitudes it reports are a
  function of a dial that was set by hand.
- Marginal IC is a linear, contemporaneous orthogonalisation. A component that
  adds nothing linearly may still add something conditionally (in a particular
  regime, or interacted with another signal); the diagnostic will not see it.
- PBO is itself a noisy statistic (its ~70 CSCV combinations are highly
  dependent). Read it as "clearly below 0.5" versus "around or above 0.5", not
  as a precise number.

## Requirements

Python 3.11+, `numpy`, `pandas`, `pyyaml`, `matplotlib`, `pyarrow`; `pytest` for
the suite. Deliberately no scipy — the statistical core (inverse normal CDF,
PSR/DSR, stationary bootstrap, PBO) is implemented on numpy alone.
