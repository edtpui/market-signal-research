# Entry-Exit Research Lab: methodology, architecture and limitations

_2026-11-04. Dev-only, EXPLORATORY: data ≤ 2025-12-31 (HOLDOUT starts 2026-01-01 and cannot be loaded)._

The Entry-Exit Research Lab measures which combinations of entry signals, exit signals, protective stops, trailing
exits and profit targets work, and separates the contribution of the entry from that of the exit. It extends the
Signal Research Lab (same data, eligibility, matching pools, partitions, statistics, ledgers) and the Strategy Lab
engine (one execution model, one event-driven portfolio simulator, one forward adapter).

## 1. The supplied video

`https://www.youtube.com/watch?v=0RRXFUA-72M` is "I Tested 152 Exit Strategies on 17,768 Stocks" by the channel
Algovibes (title and channel from YouTube's oEmbed endpoint, fetched 2026-11-04). **Its content could not be examined:**

| Source tried (2026-11-04) | Result |
|---|---|
| the watch page | 923 characters of page text: title only, no description, chapters or captions |
| YouTube caption endpoint (`api/timedtext`, auto captions) | empty response |
| youtubetotranscript.com, youtube-transcript.io, notegpt.io | 403 / landing page, no transcript |
| web search (exact title; channel + "152 exit strategies") | no write-up, transcript or summary of this video |
| candleforrest.com (lists Algovibes strategies) | page renders no entries |

So no technique, rule or parameter of the video is known here, and **none is implemented or claimed**. The exit
library below is the generalized library of the request. Integrating the video's techniques is an open task: given a
transcript or the author's list, each technique becomes an `ExitDef` (section 3) with a crafted-bar test and a
look-ahead test, and its original description is recorded next to its implementation in this document.

## 2. Architecture

```
src/strategy_lab/engine/fills.py          fill prices, slippage, spread, commissions: the broker and the replay
core/types.py  AttachedExit               stop/limit legs attached to an entry, active after the entry fill
core/strategy.py  Strategy.precompute     causal series computed once, visible only up to the current session
engine/runner.py  verify_precompute       recomputes on truncated data; a difference is a LookaheadError
src/strategy_lab/entryexit/
  components.py   ExitDef, ExitRegistry, the exit library (57 components, 6 families), Ctx, causal series
  plan.py         ExitPlanSpec (components + condition logic), resolve_plan (key, deployability), PlanRuntime
  replay.py       the trade replay: every event an independent position, vectorized across trades
  config.py       EntryExitExperimentConfig (entries, exits, grids, controls, portfolio, validation)
  engine.py       resolution, preparation (the Signal Research `prepare`), events, controls, cell statistics,
                  paired comparisons, portfolio runs, BH, walk-forward, parameter sensitivity
  store.py        write-once experiment directories, catalog tables, ledgers, matrix and leaderboards
  imports.py      a cell -> a generated Strategy Library module (record with results and provenance)
src/strategy_lab/strategies/entry_exit.py the Strategy that runs a pairing in the event-driven engine
src/strategy_lab/api/entry_exit.py        /api/v1/entry-exit/*
web/src/pages/entryexit/*                 Exit Library, Experiment Builder, matrix, cell detail, leaderboards
scripts/ee_reference_experiment.py        the reference experiments of this report
```

**Two measurement layers, one execution model.**

1. *Trade replay* (`replay.py`): each entry event is an independent unit position managed by the exit plan, so every
   exit of an entry sees exactly the same events, and random-entry controls can be replayed with the same exit. It
   is a measurement of trades, not a portfolio: positions never compete for cash or slots. It is vectorized across
   trades and stepped through holding sessions (a 150k-event cell with 7 controls per event replays in ~15-60 s).
2. *Portfolio backtest*: the existing event-driven engine (`StrategyRunner`, broker, portfolio accounting) runs the
   cell's events through `strategies/entry_exit.py` with capital, sizing, a position cap and costs, per partition.

Both layers call the same plan code (`PlanRuntime`: `at_signal`, `at_fill`, `observe`, `decide`, `tranche_filled`)
and the same fill functions (`engine/fills.py`, which the broker now uses). `test_replay_matches_the_event_driven_engine`
checks, for ten plans (stops, targets, trailing, partials with breakeven, conditions, close-only, price action,
profit locks, missing bars, slippage, a high-low spread model and commissions), that the two layers agree on every
trade's entry and exit session, entry price and average exit price.

### Engine extensions (pinned for forward deployment)
- `engine/fills.py`: the broker's reference-price, execution-price, spread and commission rules moved into vectorized
  functions; the broker calls them (behaviour unchanged: the 140 earlier tests pass). Added to the export pin.
- `AttachedExit`: an entry order may carry stop/limit legs priced `mult x fill price + add` (so a stop 2 ATR below an
  entry price unknown at the decision close is exact). With next-open fills they are evaluated on the rest of the entry
  session after the fill, in the given order (stop first), then expire; the strategy places resting orders from the
  close on. Without this the entry session would be unprotected.
- `Strategy.precompute` / `MarketView.derived`: series computed once over the loaded window and shown only up to the
  current session. `run_backtest` verifies causality on the run's own data (`verify_precompute`, two cuts); a forward
  host passes data ending at its asof anyway. Without it a signal strategy recomputes indicators every session (O(T²)).
- `ExecutionInfo.fractional_shares` so a strategy can size partial exits consistently with the broker.

A change to pinned engine files gives new exports a new pin and a new ptrade `model_id`. The prepared bundle
`var/forward/exports/sl-768f08fd0f/` is unchanged on disk (it carries its own pinned copy); re-exporting it now would
create a different model_id (CLAUDE.local.md: do not re-export it after registration).

## 3. Components and roles

The component registry (`GET /api/v1/entry-exit/catalog`) distinguishes:

| Role | Components |
|---|---|
| entry signals | the bullish catalog signals (124 of 217) and combinations of any catalog signals: AND, OR, k-of-n consensus, weighted score with a threshold, negated components, confirmation windows, regime filters (Signal Research `CombinationSpec`) |
| exit signals | close-based conditions: the indicator, price-action and time exits below, and every bearish catalog signal (93) through `signal_exit` |
| protective stops | fixed levels set at entry |
| trailing exits | levels updated at each close, never lowered |
| profit taking | limit levels fixed at entry, each a tranche (`fraction` of the original position) |
| position management | partial tranches, `then` = breakeven / lock after a tranche, `min_bars`, the experiment's `max_hold`, sizing and the position cap |

Every component: an id, family, kind, exact rule, typed parameters with ranges, the bar fields it reads, a warm-up,
and causal series (`tests/test_entry_exit.py::test_no_exit_component_looks_ahead` poisons every bar after a cut for
all 56 parameterless-default components and checks that their series and every trade that closed before the cut are
unchanged).

**Implemented exits (57).**
- A. Indicator: `rsi_overbought`, `rsi_cross_below`, `macd_bearish_cross`, `ma_bearish_cross` (SMA/EMA),
  `close_below_sma`, `close_below_ema` (cross or below), `bb_upper_rejection`, `momentum_deterioration` (ROC crosses
  below 0), `rs_deterioration` (RS line below its SMA; reads the benchmark), `volatility_shock` (return ≤ −k·sd),
  `volatility_expansion` (RV ratio), `signal_exit` (any catalog signal or combination).
- B. Targets: `target_1r`, `target_1_5r`, `target_2r`, `target_3r`, `target_4r`, `target_5r`, `target_r` (any k),
  `target_pct`, `target_atr`, `target_level` (n-session high, upper Bollinger band, pivot R1, fixed price).
- C. Stops: `stop_pct`, `stop_dollar`, `stop_atr`, `stop_swing_low` (lowest low of n), `stop_candle_low` (signal-bar
  low), `stop_ma`, `stop_volatility` (k · daily sd), `stop_structure` (last confirmed pivot low).
- D. Trailing: `trail_pct` (high or close basis), `trail_atr`, `chandelier` (HH(n) − k·ATR(n)), `trail_sma`,
  `trail_ema` (moving-average trailing exits, close-triggered by default), `trail_prior_low`, `trail_swing_low` (last
  confirmed pivot low), `donchian_exit` (lowest low of n), `profit_lock` (lock + optional trail in R),
  `breakeven_stop`. Trailing stops accept `activation` = always / after_r / after_partial.
- E. Momentum and price action: `first_bearish_after_momentum`, `consecutive_bearish`, `loss_of_momentum` (ROC below
  its maximum since entry), `no_new_high`, `lower_high` (confirmed pivot high below the previous one),
  `structure_break` (close below the last confirmed swing low), `resistance_reversal`, `failed_breakout` (close below
  the pre-signal breakout level within k sessions), `failed_continuation` (MFE ≥ min_r·R then a close below entry),
  `bearish_reversal_pattern` (one or any of 8 bearish catalog patterns).
- F. Time: `time_exit` (n), `time_1d`, `time_5d`, `time_10d`, `time_20d`, `time_60d`, `thesis_expiry` (after n
  sessions, exit unless the close is ≥ min_r·R above entry).
- G. Partial profit-taking: any target with `fraction` < 1 (25/50/75 % or several tranches at separate targets) and
  `then` = breakeven / lock (`lock_r`), which raises the remaining position's stop floor after the tranche.

## 4. Trigger semantics (deterministic, identical in both layers)

- **Timing.** A signal is evaluated with bars up to the close of session t; the entry is a market order at the open of
  t+1 (entry session, holding session 0). Exit decisions at a close execute in the next session.
- **Kinds.** Conditions sell the remaining position at the next open. Stops are price levels; `trigger=intrabar` rests
  a stop order (fills at the level, at the open on a gap through it), `trigger=close` sells at the next open after a
  close at or below the level (market orders only: deployable on ptrade). Targets rest limit orders (at the level, at
  the open on a gap above it).
- **Combination.** Conditions combine by `condition_mode` (or / and / k_of_n / weighted ≥ threshold), suppressed until
  `min_bars`. Stops are always protective and combine as the tightest level (maximum); close-triggered stops act on
  their own. Targets fill independently. `max_hold` (experiment) applies to every plan.
- **Ratchet.** A stop level is `max(previous level, new candidate)`: it never moves down. A trailing level is *armed*
  only once a close is above it (at the signal close or later); fixed levels are literal (a fixed stop at or above
  the entry price stops the trade out at the open, counted as `stop_breach`).
- **Same-session priority** (intrabar order is unknowable on daily bars, so the conservative order is used):
  1. a market exit decided at the previous close fills at the open; 2. the protective stop; 3. targets in plan order
  for what the stop left. When a stop and a target are both reached in one bar, the stop wins (worst case). On the
  entry session the initial stop and targets are attached to the entry and evaluated after the fill in the same order.
- **R.** Entry price minus the plan's initial protective stop (the tightest stop at the signal close). A plan without
  a stop, or whose stop is at/above the entry, uses the common reference R = `ref_atr_mult` × ATR(`ref_atr_n`) at the
  signal close (close-to-close Wilder average on open/close-only data). Every trade reports R-multiples against its own
  R and against the common reference, so exits are comparable.
- **Missing bars** are never filled: no fill can happen on a missing bar, decisions are not updated, and the previous
  orders are placed again; a trade still open at its partition's last session is closed at that close (`period_end`,
  at the last valid close when the bar is missing).
- **Costs.** Slippage (bps), the spread model (fixed or a share of the previous bar's range) and commissions (bps,
  per share, per order, minimum; per-share terms use `trade_notional` in the replay) on every fill, both layers.

## 5. Experiment design

`EntryExitExperimentConfig` (saved resolved: dataset version, universe, every entry and plan with its key, explicit
partitions, grids, seed) selects entries (signals, families' bullish members, combinations, parameter grids), exits
(catalog ids, families, composed plans, parameter sweeps of one component), `protective` components added to every
plan, the `baseline_exit`, the universe and window (≥ 2019-01-02, ≤ the data ceiling), sampling and eligibility (the
Signal Research rules), `max_hold`, the common R reference, costs, portfolio settings (capital, sizing mode risk/weight,
risk per position, position cap, benchmark), random controls and the validation design.

**Fair comparisons.**
- *Hold the entry constant, change the exit:* every cell of an entry replays the identical events
  (`test_identical_events_across_exits_and_the_matrix`). Each exit is compared with the baseline exit on the same
  events: paired mean difference, date-block bootstrap CI, a two-sided sign-flip randomization p by date block, BH
  across all pairs of a partition.
- *Hold the exit constant, change the entry:* each entry's events are compared with matched random entries managed
  by the identical exit plan; the excess isolates the entry from the exit.
- *Randomized entry controls:* per event, `draws` random stocks of the same session and matching group (volatility
  decile; optional sector, liquidity, price) and `date_draws` random sessions of the same stock (same partition,
  optionally same volatility group), drawn once per entry (the same controls for every exit) and replayed with the
  cell's exit plan and costs. Randomization test: the event mean against B means of one control per event;
  calibration and power are tested (`test_random_entries_are_calibrated_and_an_oracle_is_detected`).
- *Baselines:* fixed holding periods (time exits; the baseline exit), the benchmark over each trade's holding window
  (buy-and-hold over equivalent intervals), buy-and-hold of the universe in the event-driven engine per partition,
  and random-entry *portfolios* (matched draws replacing the events, same exit, sizing and costs).

## 6. Statistics and validation

- Per cell and partition: trade-level return, risk and trade metrics (section 7), excess over matched random entries
  with the wider of a date-block and a ticker cluster bootstrap, the randomization p, excess over random dates and the
  benchmark, consistency by year, market trend, market volatility and stock-volatility tercile.
- Multiple testing: Benjamini-Hochberg over every cell of a partition (the matched-random test); the number of cells
  tested is stored in `tests.json` and the append-only `ee_data_usage` ledger, and every experiment shows how many
  earlier experiments used the same partitions. Verdicts as in Signal Research (supported needs BH significance, a CI
  excluding zero and an annualized excess ≥ the economic threshold, annualized by the mean holding period).
- Chronological partitions (default train 2019–2022, validation 2023–2024, test 2025 **locked**). Trades are closed
  at their partition's end, so no holding period crosses a boundary. Evaluating the test partition is a deliberate
  setting recorded in `ee_test_ledger`; the first evaluation of a (cell, methodology, data) key stays authoritative
  in `ee_locked_results`.
- Walk-forward: expanding folds inside the unlocked window (train 2019–2020 → test 2021, …, → test 2024). For each
  group (all cells; the exits of each entry; the entries of each exit) the best cell on a fold's training window is
  chosen and its result on the next window recorded: OOS of the selected cell, OOS mean of all, selection skill (the
  difference) and the IS/OOS rank correlation. Trades crossing a fold boundary are purged from both sides.
- Parameter sweeps: every grid point is a cell (counted in BH). Sensitivity reports train and validation values, a
  neighbourhood-smoothed train score (the point and its grid neighbours) and the best-on-train and plateau choices,
  so a stable region is preferred over an isolated best point.

## 7. Metrics

Trade level (per partition): trades, mean / median net return, sd, win rate, profit factor (Σ gains / Σ |losses| of
trade returns), expectancy in R (own and reference), average win / loss and their holding sessions, average and median
holding, return per session held (mean return / mean sessions × 252), a per-trade Sharpe annualized by holding, MAE and
MFE (% and R), worst trade, 5 % quantile and CVaR, exit-reason shares (stop, target, condition, max_hold, period_end),
partial-exit share, own-stop share, stop-breach share, excess over matched random entries / random dates / benchmark,
consistency across years and regimes. Portfolio level (the existing `analytics/metrics.py`, docs/METRICS.md): total
return, CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, exposure, turnover, costs, win rate, profit factor,
trades, benchmark-relative metrics, monthly and annual returns, and the share of random-entry portfolios below it.
Leaderboards rank by risk-adjusted return, drawdown (with a cap), expectancy, consistency, excess over random entries,
statistical confidence, CAGR or mean trade, separately for train and validation rows.

## 8. Strategy Library and forward paper testing

`POST /api/v1/entry-exit/import` (Import on a cell page) writes a write-once module to `var/entry_exit_imports/`
with the record: entry and exit definitions, parameters, sizing, costs, the cell's trade-level and portfolio results
per partition, random-entry and baseline comparisons, walk-forward rows, the source experiment (dataset version,
universe, window, config and methodology hashes, seed), the validation partitions and a warning. The strategy loads
like `strategies_user/` (category `entry_exit`), backtests in the existing engine (Backtesting) and deploys through
the existing forward adapter; its export pins `strategies/entry_exit.py`, `entryexit/` and `signals/`.

`ForwardService.validate` refuses any `requires_historical_test` strategy without a finished backtest of the same
strategy version (and warns when the backtests used other parameters or universe); the generic
`entry_exit_system` (JSON definition) cannot be deployed. ptrade's limits still apply: only close-based plans
(market orders, open/close bars, no benchmark) with open/close entries are deployable there; others are backtest-only
(the matrix marks them `·ib`). No live execution exists.

## 9. Known limitations

- **The video's techniques are not implemented** (section 1).
- **Survivorship bias**: today's universe lists; no delisted stocks in the downloads.
- **Daily bars only**: intrabar order is unknown; stop-first is conservative for stop/target conflicts but a stop and
  a later recovery within one bar are not distinguishable.
- **Overlapping trades**: the replay treats every event independently (a stock can have overlapping trades); the
  portfolio layer cannot (it skips entries in held names), so the portfolio trades a subset of the cell's events.
- **Positions open at a partition end** are closed at that close in the replay but kept (marked) in the portfolio.
- **Eligibility differences**: research uses raw prices for the price and dollar-volume floors; a standalone backtest
  of an imported strategy uses its own (adjusted) bars, so its entries can differ slightly from the researched ones.
- **Controls** are drawn with replacement across events of one session; per-share commissions in the replay use a
  reference notional; sector data are current SIC codes, not point-in-time.
- **Warm-up**: indicators with infinite memory start at the first loaded bar (2018 for a 2019 start); exits whose
  declared warm-up exceeds the loaded history are noted in the study report.
- **Daily data only** (`frequency = 1d`): attached exits, holding sessions and the same-session priority assume
  daily sessions; weekly or monthly bars are not offered for entry-exit experiments.
- **Participation caps** (partial fills on bar volume) act in the portfolio layer only; the replay's unit positions
  always fill.
- **Multi-process**: an entry-exit experiment records its owner process (`owner.json`); the API server's startup
  recovery marks it interrupted only when that process is gone (fixed 2026-11-04 after a server start mislabelled a
  running script's experiment). The backtest and Signal Research stores still mark every queued/running row
  interrupted at startup: do not start the server while one of their scripts runs.
- **R-multiples against the plan's own stop** become very large when the initial stop sits just below the entry (e.g.
  a 20-EMA trail on a breakout); compare exits with the reference-R expectancy or with returns.
- **Cost**: replay time grows with events × controls × holding length; portfolio runs (one per cell, partition and
  random control) dominate big matrices: use `portfolio.run = top`.
