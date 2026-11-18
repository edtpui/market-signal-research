# Signal Research Lab: methodology, architecture and limitations

_2026-11-04. Dev-only, EXPLORATORY: data ≤ 2025-12-31 (HOLDOUT starts 2026-01-01 and cannot be loaded)._

## 1. What it answers, and what it does not

The Signal Research Lab tests whether an individual market signal carries information about the *subsequent*
returns of the stocks it selects, separately from any trading strategy. For every signal instance it answers:

| Question | How |
|---|---|
| Does the signal predict higher (bullish) or lower (bearish) returns? | mean forward return of its events per horizon, in the signal's own direction |
| Better than comparable random stocks? | excess over **matched random stocks** (same session, same trailing-volatility group, optional sector / liquidity / price / market cap), randomization test |
| Better than random timing in the same stock? | excess over **random entry dates** of the same stock (same partition, optionally same volatility group) |
| Better than the market? | excess over SPY (or any configured benchmark) and the stock's sector ETF over the *same* entry and exit sessions |
| Is it statistically meaningful? | empirical randomization p-values, clustered bootstrap CIs, Benjamini-Hochberg FDR across every test |
| Is it economically meaningful? | annualized net excess after round-trip costs, against a threshold, reported apart from significance |
| Is it consistent? | by year, sector, stock-volatility regime and market regime (trend, volatility), with consistency shares |
| Does it persist? | chronological train / validation / (locked) test partitions |

It does **not** turn a promising signal into a profitable strategy. A supported signal is imported into the Strategy
Library as a plain rule (§9) and must then be backtested with costs on data it was not selected on.

### The reference study
The design follows the approach described for the video "I Tested All of Technical Analysis on 17,768 Stocks"
(https://www.youtube.com/watch?v=KsymcEPU7hQ): signal-selected stocks compared against randomly selected stocks in the
same volatility group. Its figures (17,768 US stocks, 362 signals, 32 indicator families, daily data) come from the
summary in the request and were **not verified**: the video page exposes no description or transcript to this
environment (fetched 2026-11-04), and a web search found no written methodology. Nothing below claims to reproduce
details of that study beyond "same-date, same-volatility-group random comparison", which is the engine's default.

## 2. Architecture

```
src/strategy_lab/signals/            signal definitions (no portfolio or execution logic)
  indicators.py   Panel (aligned wide frames + benchmark) and Indicators: memoized, vectorized, causal indicators
  base.py         SignalDef, SignalRegistry (id, version), SignalSpec / CombinationSpec / RegimeFilter, evaluate()
  library/        the catalog: trend, momentum, volatility, volume, price, market (33 families, 217 signals)
src/strategy_lab/research/           the event-study engine
  config.py       SignalExperimentConfig (pydantic): everything an experiment depends on
  engine.py       resolve_config, prepare (shared per experiment), run_signal (per signal), finalize_tests (BH),
                  inspect_signal (one symbol's calculation)
  stats.py        cluster bootstrap, randomization p-values, Benjamini-Hochberg, verdicts
  reference.py    SIC -> sector mapping, sector ETFs, optional point-in-time market caps
  store.py        write-once experiment directories, catalog tables, ledgers, jobs, cancellation, reproduction
  imports.py      researched signal -> generated strategy module (Experiments > Strategies)
src/strategy_lab/strategies/signal_rule.py   the Strategy built from an imported signal (backtest + forward)
src/strategy_lab/api/signals.py              /api/v1/signals/* routes
web/src/pages/signals/*                       Experiments > Signals (library, signal details), New signal
                                              study, random benchmarking (study results), signal results,
                                              Results > Signal rankings. Studies are listed in Experiments and
                                              the usage and test-period ledgers in Results > Research ledgers
web/src/components/ResearchCharts.tsx         histogram, signed bars with CIs, forest plot, numeric-x lines (SVG)
web/src/components/SignalChart.tsx            candles + overlays + trigger markers (lightweight-charts)
```

Reused, not duplicated: the `DataStore` (versioned Parquet snapshots, data policy, coverage reports, corporate
actions), `Universes`, the `Param` type, the catalog database, the run-store conventions (write-once, read-only,
manifest with code and data provenance), the backtest engine and the forward paper adapter. The event-driven engine
remains the only portfolio simulator; the research engine is a vectorized event study and never simulates a
portfolio.

## 3. Signals

A `SignalDef` has: unique `id`, `family`, `name`, `direction` (bullish / bearish: the hypothesis being tested),
`trigger` definition, documentation, typed parameters with defaults and ranges, the bar `fields` it reads, a
`warmup` (history required before the first possible trigger, from its parameters), a `version`, a `scope` and an
optional `show` (the indicator series behind it, for the inspection chart). A definition is a pure function
`fn(indicators, params) -> boolean frame (sessions × symbols)`; a market-level Series is broadcast to every symbol.

`evaluate()` is the only path from a spec to triggers, for the engine and the imported strategy alike:
definition (or combination) AND regime filter AND every field the signal reads is valid on t.

- **No look-ahead.** Rolling windows end at t, exponential averages are recursive, nothing is shifted backwards.
  `tests/test_signals.py::test_no_signal_looks_ahead` poisons every bar after a cut-off and checks that no trigger on
  or before it changes, for all 217 signals plus a regime-filtered signal and a combination.
- **Missing bars never trigger** and are never filled.
- **Scope.** `stock` signals use the symbol's own bars. `cross_section` signals rank the symbol against the
  experiment's universe on t (so they depend on that universe). `market` signals (breadth) fire for every symbol on
  the same session: a matched random stock drawn that session is a draw from the same population, so the matched
  comparison has no power by construction; their evidence is the random-date and benchmark comparisons. The builder
  says so when one is selected.
- **Parameter variants** are instances, not new signals: a spec's key is `id@version:hash(params, regime)`, and a
  parameter comparison (grid) expands into instances that are all counted in the multiple-testing correction. The
  catalog's distinct ids (e.g. RSI below 20 vs below 30) are the commonly cited variants, not an exhaustive grid.
- **Combinations**: AND, OR, k-of-n, weighted score with a threshold, negated components (confirmation that an
  opposite signal is absent), a confirmation window (component active within the last w sessions), optional
  first-session-only, and regime filters (market trend: benchmark vs its SMA200; stock volatility tercile).

### Catalog (v1.0, 217 signals in 33 families)
moving_average 16 · momentum 11 · rsi 11 · macd 7 · stochastic 4 · williams_r 4 · cci 4 · trix 4 ·
ultimate_oscillator 4 · mean_reversion 9 · bollinger 6 · volatility 7 · atr 6 · volatility_compression 6 ·
channel 6 · volume 8 · money_flow 5 · gap 10 · breakout 9 · price_action 10 · candlestick 12 ·
support_resistance 9 · trend_reversal 8 · drawdown 3 · trend_strength 5 · aroon 4 · parabolic_sar 3 · ichimoku 4 ·
linear_regression 4 · heikin_ashi 3 · vortex 2 · relative_strength 7 · market_breadth 6.
(`GET /api/v1/signals/catalog` is authoritative.)

## 4. The event study

**Timing.** A signal is evaluated with bars up to and including the close of session t. Entry is at the next
executable price: the open of t+1 (default) or its close. The exit is the same price field of session t+1+h, so every
observation holds exactly h sessions. Horizons default to 1, 5, 10, 20, 60 sessions (configurable, ≤ 252).

**Prices.** Returns use adjusted prices (splits and dividends embedded: total return). Price levels for the
`min_price` filter, price groups and dollar volume use the raw close (split-adjusted, not dividend-adjusted) on daily
data; weekly and monthly bars have no raw price, so the adjusted close is used and the report says so.

**Window and partitions.** The experiment window bounds everything: no bar after its end is loaded, only warm-up
bars before its start. Partitions are chronological and disjoint (default: train 2019–2022, validation 2023–2024,
test 2025), never shuffled. An event belongs to the partition of its signal session and is kept only if its exit is
inside that partition (**purging**: no holding period crosses a boundary; such events are counted as censored).

**Missing data.** An event without an entry or exit price is excluded and counted (`missing_forward`); nothing is
filled or substituted. The data report lists late-starting and early-ending symbols, never-eligible symbols and the
warm-up actually available.

**Point-in-time eligibility on t** (applied identically to events and to every control): a valid bar on t, at least
`min_history` valid bars, a trailing volatility, raw close ≥ `min_price` (default $5) and median raw dollar volume
over 21 sessions ≥ `min_dollar_volume` (default $1M). All of it is known at t.

**Event sampling.** `first` (default: the first session of each run of consecutive triggers; a run whose first
session is ineligible yields no event, it is never shifted later), `all`, `cooldown` (skip a symbol's triggers for N
sessions after an event) and `non_overlapping` (per horizon h, a symbol's events are ≥ h sessions apart). The method
is shown with every result.

**Per-event measures.** Return per horizon, net return (minus round-trip cost in the signal's direction), win rate
in the signal's direction, maximum favorable / adverse excursion over the holding window (high/low; close when the
dataset has no high/low), realized forward volatility, mean cumulative path by holding day (one cohort: the events
whose longest horizon completes).

## 5. Random controls

### Matched random stocks (primary)
Volatility groups are cross-sectional quantiles (default 10) of trailing realized volatility (default 63 daily log
returns ending at t), among the stocks eligible on t. Optional extra dimensions: sector (SIC-derived), liquidity
terciles (dollar volume), price terciles (raw close), market-cap terciles (only with a point-in-time file). The pool
of an event (t, s) is every other eligible stock on t with the same key. If it has fewer than `min_pool` members
(default 10), or the stock has no sector when sector matching is on, the event is **unmatched**: it is excluded from
the matched comparison and counted by reason; the matching rule is never relaxed.

- The per-event control is the **exact leave-one-out pool mean** of the forward return (no sampling noise), so the
  per-event excess is `r_event − mean(pool)`.
- The **randomization test** draws, for each of B permutations (default 1,000), one control stock uniformly from each
  event's pool, and records the mean return of those B random portfolios. The actual signal mean is compared with
  that null: empirical one-sided p = (1 + #null at least as extreme in the signal's direction) / (B + 1); a two-sided
  p and a normal approximation from the null's mean and sd (no 1/(B+1) floor) are reported too. Because controls
  come from the same sessions, common market moves and overlapping holding periods affect signal and null alike.
- A draw whose control has no forward price is excluded from that permutation's mean (the same rule as for events).

### Random entry dates
For each event, random sessions of the same stock, in the same partition, whose holding period completes inside it,
optionally restricted to sessions on which the stock was in the same volatility group (default on). Exact pool mean
per event and a randomization null as above. A pool smaller than `min_pool` (default 20 sessions) is unmatched.

### Benchmarks
Each configured benchmark (default SPY) and, when sector data exists, the stock's sector SPDR ETF, measured over
exactly the event's entry and exit sessions with the same price field. Absolute and market-relative performance are
reported separately.

### Reproducibility
Each signal draws from its own PCG64 stream seeded by `SeedSequence(experiment seed, sha256(signal key))`, separate
streams for stocks, dates and bootstraps, consumed in event order. Draws therefore do not depend on which other signals
are in the batch, on thread scheduling, or on chunking (the null sums differ only in floating-point summation order
across chunk sizes; for a given configuration they are bit-identical). Saved per experiment: seed, sampling method,
matching criteria, the first `save_controls` control stocks and random dates of every event, every permutation's
null mean, and the resolved config. Opening an experiment reads these files; nothing is re-sampled.
`tests/test_signal_research.py` checks seed reproducibility, chunk independence, batch independence and a full
store-level reproduction by content hash.

## 6. Statistics

- **Confidence intervals.** Cluster bootstrap of the mean (default 1,000 resamples): (a) date blocks of h
  consecutive sessions (keeps same-session cross-sectional correlation and overlapping holding periods inside one
  cluster) and (b) whole tickers (serial dependence within a stock). The reported, conservative interval uses the
  larger of the two standard errors.
- **Multiple testing.** Benjamini-Hochberg over every (signal instance × horizon) test of an experiment, per
  partition. The rankings page re-applies BH across every row it shows (`q_across_rows`), the stricter view when
  several experiments looked at the same data. With B permutations the smallest empirical p is 1/(B+1); the builder
  warns when that floor cannot pass BH for the number of tests and suggests B ≥ m/α or the normal approximation.
- **Verdict per test** (direction-aware: a bearish signal is supported by a negative excess):
  `supported` = significant (BH q < α AND the conservative CI excludes zero, by default) AND economically meaningful
  (annualized net excess = net per-event excess × 252/h ≥ threshold, default 2%/yr); `significant_negligible` =
  significant but below the economic threshold (never ranked as attractive); `reversed` = the CI excludes zero on the
  opposite side; `not_significant`; `insufficient_sample` (< 30 events or < 10 sessions).
- **Consistency.** Share of years (and sectors) with ≥ 10 matched events whose excess has the signal's sign;
  performance by stock-volatility regime (volatility-group terciles), market trend (benchmark vs SMA200 at t) and
  market volatility (benchmark 21-session realized vol: < 15%, 15–25%, > 25%, fixed thresholds, no look-ahead).

## 7. Chronological validation and research integrity

- The **test partition is locked**: unless `evaluate_test` is on, the analysis ends the day before it and its bars
  are never loaded (a reproduction keeps it locked: the saved config carries the partition).
- Every evaluation of a test partition is appended to `signal_test_ledger` (SQLite triggers forbid UPDATE and
  DELETE). The first evaluation of a (signal key, methodology hash, dataset version, universe, test window) is stored
  in `signal_locked_results` and stays the authoritative one; later evaluations are flagged as re-evaluations, in the
  experiment, the signal page and the imported strategy's provenance.
- `signal_data_usage` records which dataset partitions every experiment used and how many tests it ran there; each
  experiment shows how many experiments and tests had used the same partitions before it.
- Experiment directories are write-once and read-only; reproduction runs a new experiment and compares content
  hashes (`code_changed` is reported when the signal/research/data code differs).

## 8. Performance and operations

Signal-independent work (data load, indicators, eligibility, groups, forward returns, pool indices, path sums) is
done once per experiment; indicators are memoized across signals; signals run on `workers` threads; results are
written per signal as it finishes; failures are isolated per signal (`error.txt`, status failed) and the others
continue; experiments can be cancelled (between chunks; partial results keep their BH over completed signals and are
flagged partial); at the next server start, an experiment whose owner process (`owner.json`) is gone is
marked failed; one that another live process is executing is left alone.

Measured on this host (2026-11-04), `us_core` ∩ S&P 1500 list (1,493 symbols, 2019-01-02…2024-12-31), volatility +
sector matching, 1,000 permutations, 5 horizons, 2 workers: preparation 18 s; per signal 4 s (6.8k events) to 31 s
(78k events); 6 signals in 77 s. A full 217-signal run is therefore roughly 1–1.5 hours.

## 9. From signal to strategy

`POST /api/v1/signals/import` (or "Import as a strategy" on a signal's results page) writes a generated,
write-once module to `var/signal_imports/sig_<id>_<hash>.py`. It embeds the record: signal id, version, parameters,
spec and source sha256; the source experiment (dataset version, universe, window, methodology hash, seed); the
results per partition and horizon with q-values and verdicts; the validation partitions and any test-period
evaluations; and an explicit warning that research significance is not trading profitability. The Lab loads that
directory like `strategies_user/`, so the strategy appears under Experiments > Strategies (category `signal`, with a
provenance panel), runs in the existing backtest engine, and deploys through the existing forward adapter. The rule:
enter at the next open in the signal's direction (bearish signals short and need `allow_short`), equal
`position_size`, at most `max_positions`, exit after `hold_bars` (default: the researched primary horizon) or at a
close-based stop / target. The export bundle pins the signal package with the engine. ptrade's constraints still
apply: only signals that read open/close bars, bullish, can deploy there (`tests/test_signal_research.py` deploys
one to the mock host and checks the pin).

`test_imported_signal_strategy_trades_the_research_events` checks that a backtest of an imported signal fires on
exactly the sessions and symbols the event study found.

## 10. Known limitations

- **Survivorship bias.** The configured universes are today's lists and the yfinance downloads hold no delisted
  stocks. Results describe survivors; every experiment states it. Delisted securities would be used if a data source
  provided them (the store reports early-ending symbols).
- **Sectors** are SEC SIC codes mapped to approximate GICS-like sectors, **current, not point-in-time**; one ticker
  (OZK) has no SIC entry. **Market-cap matching is refused**: there is no point-in-time market-cap or
  shares-outstanding data in the workspace (the code path exists for a `market_cap_file`).
- The universe of ~1,500 stocks is far smaller than the ~17,768 of the reference study; volatility groups are
  therefore coarser (median pool ≈ 11 with 10 groups × 11 sectors).
- Within one permutation, two events of the same session and group may draw the same control (sampling with
  replacement across events). The effect on the null is small for pools ≥ 10 but not zero.
- Controls whose forward price is missing are dropped from that permutation (the same rule as for events); the
  dropped share is not yet reported separately.
- Indicators with infinite memory (EMA, Wilder RSI/ATR) depend slightly on the first loaded bar; warm-ups are set
  to several spans so the difference is negligible after warm-up, but an imported strategy started at a different
  date can differ from the research triggers in its first weeks (simple-average signals match exactly; tested).
- Cross-sectional and breadth signals depend on the universe they are computed over; an imported strategy computes
  them over its own universe.
- Intraday questions (gap fills during the day, stops inside the bar) are evaluated at bar resolution only.
- The research store has no deletion: experiments are permanent records by design.

## 11. Next milestone (recommended)

1. Expand the catalog toward 300+ distinct definitions where they are genuinely different (fundamental-free:
   pattern recognition such as cup-and-handle / flags, multi-timeframe confirmations, seasonality, volume profile),
   each with crafted-bar tests.
2. Performance for full-catalog runs: float32 forward-return matrices, a numba-free gather in blocks per session,
   and an experiment-level cache of prepared studies keyed by (dataset version, universe, eligibility, matching).
3. Point-in-time reference data (historical index membership, delisted securities, shares outstanding) to remove
   the survivorship and current-sector limitations.
4. Report the share of missing control draws, and optionally sample controls without replacement within a session.
