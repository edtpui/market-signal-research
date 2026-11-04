# Strategy Lab: audit and architecture

_2026-11-03. Dev-only project in `C:\AI\ClaudeWorkspace\projects\strategy_lab` (git, branch `claude-dev`, no remote).
Nothing here is operational. The operational source of truth is `C:\AI\QwenWorkspace`, and it was only read for this
document._

## 1. Audit

### 1.1 Which UI is "the existing UI"
`projects/flag_db_builder` (and the operational `QwenWorkspace/flag_db_builder`) has **no UI**: there are no HTML,
JS, TS or CSS files and no web framework. The UI that presents the flag research is the paper-trading **web
terminal**, `QwenWorkspace/paper_trading/web` (commit `5d55adf`, HEAD `792758b`). It replaced the Dash console.
**Assumption:** that terminal is the UI to reuse. The stack:

| Aspect | Web terminal (`paper_trading/web`) |
|---|---|
| Framework | React 19.3, TypeScript 5.9 (strict), Vite 7.3 |
| Entry | `index.html` → `src/main.tsx` (QueryClient, ThemeProvider, TooltipProvider, Toaster) → `App.tsx` |
| Routing | a 60-line history-API router (`lib/router.tsx`, `match()` → page union), no dependency |
| Layout | `AppShell`: sticky 44 px header with brand, nav tabs, status, theme toggle; `max-w-[1680px]` main |
| Styling | Tailwind v4 CSS-first (`styles/theme.css`), shadcn/ui "new-york" over `radix-ui`, dark default, 13 px base, mono tabular numbers (`.num`), validated 8-slot series palette, pos/neg/warn tokens, period bands |
| Charts | `lightweight-charts` 5.2 (`LineChart`, `CandleChart`), recharts 3.8 (`MonthlyBars`), a CSS grid `MonthlyHeatmap` |
| Tables | `DataTable` on `@tanstack/react-table` 8 (sorting, CSV export) |
| Data | `@tanstack/react-query` 5 hooks over a typed `fetch` client; types generated from `openapi.json` |
| Other | `cmdk` command palette, `sonner` toasts, `StatePanels` (loading/empty/error), `StatusBadge` |

Reused here without flag-specific code: theme tokens, `AppShell` frame, router pattern, the shadcn/ui primitives,
`StatePanels`, `DataTable`, the lightweight-charts line chart, `MonthlyHeatmap` and the number formatters. Replaced:
every page, the API client and hooks, book/series/evidence models, live marks, the market clock.

`web/node_modules` in the operational repo is empty (the build runs in a `node:22` container), and the host npm cache
does not hold the toolchain. Building the UI therefore needs one `npm ci`; see section 6.

### 1.2 Paper-trading system
Canonical: `QwenWorkspace/paper_trading`, git HEAD `792758b` ("One book registry for the engine and the terminal"),
two uncommitted edits (`README.md`, `deploy/compose.yml`). Live in forward paper trading since FT24a with 8 books.
`QwenWorkspace/paper_trading_web` is at the same HEAD; `QwenWorkspace/paper_trading_dev` is older (`571bd97`).
None of the ClaudeWorkspace copies match: `projects/paper_trading` is the FT8 snapshot, `projects/paper_trading_v3*`
are FT11r. Only source files, docs and config were read; `data/`, ledgers and `logs/` were not opened.

| Concern | ptrade at `792758b` |
|---|---|
| Strategy loading | `config/books.toml` is the single book registry (`ptrade/book_registry.py`, pydantic-validated). Two families. **bracket**: a `ptrade.strategy.Strategy` (`name`, `version`, `config_hash()`, `generate_signals(asof, data)` → `Signal`s). **basket**: a `ScoreProducer` (`scores(asof, ctx)`) plus a `PortfolioRule` (`simulate(cands, frames, start, end, params)` → `BookRun`; `pending(today, held, params)`), discovered from `baskets/plugins/*.py` (`RULE`, `PRODUCER`) |
| Lifecycle | registry status active/paused/retired with a reason (`model_events`, append-only); write-once pre-registration hash; `model_id = name:version:config_hash`, so any code/param change is a new model |
| Market data | DuckDB `data/prices.duckdb` (adjusted + raw, yfinance), `DataView(asof)` raises `LookaheadError` past asof; baskets read asof-clipped `Frames` (adjusted closes forward-filled, opens not filled, SPY close, NYSE calendar 60 sessions past asof) |
| Orders and fills | bracket: `Order` → `OrderEvent` (filled/cancelled/rejected) → `Fill`; `Broker` next-open fills, gap-through, stop-first, bps slippage. basket: the rule's own `simulate()` produces trades (buy/trim/exit/hedge at open/close prices) |
| Positions and accounting | bracket: `positions` state table, R and realized PnL; basket: `basket_days` (equity index 1.0 at start), `basket_holdings`, `basket_trades` |
| Persistence | SQLite ledgers `data/ledger.db`, `data/ledger_baskets.db`, append-only triggers, one session = one transaction |
| Scheduling | Windows Task Scheduler → `docker exec open-terminal scripts/daily_job.sh` at 18:45 ET; basket step in its own process (`baskets.job`) with a timeout |
| Re-run model | baskets re-simulate every book **from its start each night** and compare stored days (`basket_restatements`, never overwritten) |
| Monitoring | `run_log`, `data_checks`, `logs/heartbeat.json`, alerts log/webhook, restatement alerts |
| External API | FastAPI `/api/v1` (`gui/v1`, OpenAPI in `web/openapi.json`): books, book detail, equity, holdings, trades, picks, growth, compare, health, ops. Writes: only pause/resume with a reason and UI settings. **No order-creating endpoint, by design** |
| Strategy config | `books.toml` entry (`id, family, unit, ledger, model, scores, rule, params, protocol, ...`); rule params checked against the rule's `ALLOWED` |

### 1.3 Data available in this workspace
`C:\AI\ClaudeWorkspace\data` (research downloads, read-only, yfinance 1.7.0): `yf_2018_2025` (512 large caps + SPY),
`yf_2018_2025_midsmall`, `yf_2018_2025_other`, `yf_etf_2008_2025` (SPY, IJH, IJR), `yf_macro_2018_2025` (sector
ETFs, QQQ, IWM, ^VIX, ^TNX, ...), all ending 2025-12-31. Each ticker has `<T>_1d.csv` (adjusted OHLCV) and
`<T>_1d_raw.csv` (Close, Volume with `auto_adjust=False`, which yfinance **split-adjusts but does not
dividend-adjust**). `yf_2008_2018` is the BACK window and is not configured. Daily bars only: no intraday, no bid/ask,
no explicit split or dividend files. Known data issue A9: the adjusted file's volume can be split-adjusted twice, so
Strategy Lab takes volume from the raw file.

## 2. Architecture decision

**A dedicated, small event-driven engine in Strategy Lab, with ptrade as the forward execution host through its
documented basket plug-in interface. No LEAN.**

- **QuantConnect LEAN** was rejected. It is a .NET engine with its own data format and its own brokerage and
  portfolio model. It cannot run inside ptrade's Python process or plug-in contract, so forward and historical
  results would come from two engines. It needs new installs and containers, and it would duplicate ptrade's ledger.
  It also brings nothing the daily-bar workload needs.
- **Building on ptrade's engine** is not possible for generic strategies. Its bracket pipeline is specific to
  stop/target signals, and its basket "engine" is each research model's own `simulate()`. There is no generic
  order/portfolio engine to extend.
- **Chosen:** Strategy Lab owns one deterministic engine (strategy runner, order book, fill model, cost model,
  portfolio accounting). The same engine code runs in two places:
  1. historical backtests in Strategy Lab;
  2. inside ptrade, as a `PortfolioRule` plug-in whose `simulate()` drives the Strategy Lab runner over ptrade's
     asof-clipped `Frames`. This is exactly how ptrade already runs the six v5-family basket books (their rules call
     pinned research `simulate()` functions).

  ptrade stays the forward authority: it schedules the nightly session, supplies the data, validates the `BookRun`,
  persists it append-only, detects restatements, owns registration and pause/resume, and serves the results.
  Strategy Lab never writes to a ptrade ledger, and it reads results only through API v1.

The earlier platform plan (`QwenWorkspace/plans/2026-10-26_platform_plan.md`) rejected a separate engine because two
engines drift. This design keeps that principle: one engine per strategy family. The plug-in carries Strategy Lab's
engine as a sha256-pinned copy, as `models/fdb_v5fam` + `ptrade/basket_pin.py` do for the research code, and its
config hash includes the pin. A change to the engine therefore becomes a new ptrade `model_id`.

## 3. Components

```
web/ (React terminal design)  Overview · Strategies · Backtesting · Forward · Analytics · Market Data · Experiments · Settings
        │ /api/v1 (FastAPI, 127.0.0.1)
src/strategy_lab/
  core/        types (OrderIntent, TargetWeights, Order, Fill, OrderEvent), Strategy ABC, StrategyContext, MarketView
  data/        DataPolicy (window + holdout ceiling), providers (yfinance CSV dirs, synthetic), DataStore
               (versioned Parquet cache via DuckDB, coverage, missing-data report), corporate actions
  engine/      costs, fill model, broker (order lifecycle), portfolio (cash, positions, corporate actions),
               runner (the session loop shared by every mode), backtest entry point
  analytics/   metrics (one implementation for backtest and forward), round trips, monthly/rolling tables
  experiments/ ExperimentConfig, immutable run store + SQLite catalog, reproduction check, job executor
  strategies/  built-in library + user strategy discovery (strategies_user/)
  paper/       ptrade contract mirror, PortfolioRule/ScoreProducer plug-in, export bundle, API v1 client,
               gateway, deployments + sync, and a mock ptrade host for dev (API v1-shaped)
  api/         FastAPI app
```

### 3.1 Execution timing (all modes)
A session's bar closes, then the strategy sees data up to and including that close. Orders it submits become eligible
on the **next** session: market orders fill at the next open (default) or next close; limit and stop orders fill on
the next bar that reaches them, and fill at the open if the bar gaps through. Day orders expire after one eligible
session; GTC orders persist. Partial fills come from an optional participation cap on bar volume, and the remainder
stays open (GTC) or expires (day). Corporate actions are applied at the open of the ex-date, before fills.

### 3.2 No look-ahead
`MarketView` is built from integer-position slices `[:i+1]` of the session index, and any explicit request past the
current session raises `LookaheadError`. Fills read the next bar only inside the engine. ML helpers build training
rows only from labels whose horizon has ended by the current session. Tests plant poisoned future bars and assert that
decisions do not change.

### 3.3 Data policy (research safety)
- Ceiling **2025-12-31** (the day before `HOLDOUT_START`). It is enforced at ingest and in every query and cannot be
  raised by configuration. Rows past it are counted, never stored or shown.
- History floor 2018-01-01, for indicator warm-up only (ptrade baskets also read from 2018-01-02). Backtest start
  floor 2019-01-01. BACK data (`yf_2008_2018`) is not configured.
- Every result is labelled with the research periods its window overlaps: IS 2019-2022, OOS 2023-2025. Results are
  EXPLORATORY dev artifacts in `var/`, never mixed into `logs/` or `flag_db/`.

### 3.4 Forward paper testing
- `PaperGateway` protocol, two implementations.
  - **`MockPtradeHost`** (dev): mimics `baskets.run.run_book`. It re-runs the plug-in rule from the start each
    session over Frames clipped at asof, compares stored days (restatements), and appends to its own append-only
    SQLite store. It serves the API v1 shapes the real client parses, so the forward workflow runs end to end in this
    workspace on ≤ 2025 data, labelled SIMULATED.
  - **`PtradeGateway`** (operational): builds an export bundle (plug-in module, pinned engine, `books.toml` entry,
    settings key, protocol stub, operator steps) for a human to apply after approval. It reads results through API
    v1. It is disabled in this workspace, because API v1 serves HOLDOUT/FWD evidence.
- Deployments are idempotent: the key is a hash of the strategy, version, params, universe and execution config, and
  the same key returns the same deployment. Sync is idempotent too, by natural keys (date, ticker, kind, shares,
  price), and every row is tagged with the deployment and strategy ids. Reconciliation re-runs the backtest engine
  over the forward window and reports any difference instead of hiding it.
- ptrade-target limits, checked at deploy (fail closed). Frames carry only open and close, adjusted, daily, and the
  trade kinds are buy/trim/exit/hedge. So a deployable strategy uses only open/close fields, market orders, long
  only, adjusted prices, no participation cap, and a universe inside ptrade's `config/universe.txt`.

## 4. Experiment reproducibility
A run directory `var/experiments/<run_id>/` is written once and made read-only. It holds the config, a manifest
(dataset id + content version, strategy source sha256, engine source sha256, package versions, run timestamps), and
the result artifacts with their sha256. `reproduce(run_id)` re-runs from the saved config, refuses if the dataset
version is gone or changed, and compares the artifact hashes.

## 5. Metrics
One module, used for backtests and forward deployments alike. Undefined is never zero: each metric is
`{value, reason}`. Definitions are in `docs/METRICS.md`.

## 6. Status of the open items
- **UI build:** `npm install` approved by the user (2026-11-03) and run in `web/` only. Direct dependencies are
  pinned to the terminal's exact versions; native binaries (esbuild, rollup, lightningcss, oxide) load on this host.
- **Promotion to ptrade:** not done; it needs approval. It also needs three operational edits: a
  `[paths] ledger_strategy_lab` key in `config/settings.toml`, the plug-in file plus its pinned package, and a
  scheduled `python -m baskets.run --ledger data/ledger_strategy_lab.db`. A separate ledger is required, because the
  basket candidate table has no producer column: a second producer in `ledger_baskets.db` would mix rows into the
  six live books' inputs. See `docs/PTRADE_INTEGRATION.md`.
- **Session calendar:** defined by a reference symbol (SPY) and not the union of dates. A union let DX-Y.NYB's bars
  on 2025-01-09 and 2025-05-26 (NYSE closed) give every stock two phantom missing sessions.

## 7. Signal Research Lab (2026-11-04)
A second research mode next to backtesting: event studies of individual signals against matched-random stocks,
random entry dates and benchmarks, with chronological partitions, a locked test period and multiple-testing
control. Signal definitions live in `signals/` (no execution logic); the vectorized engine, statistics and store in
`research/`; routes in `api/signals.py`; pages under `web/src/pages/signals/`. It reuses the DataStore, the data
policy, the catalog database and the run-store conventions, and it never simulates a portfolio: a researched signal
enters the Strategy Library as a generated strategy module (`strategies/signal_rule.py`) that the existing engine
backtests and the existing forward adapter deploys (its export pins the signal package). Methodology, tests and
limitations: `docs/SIGNAL_RESEARCH.md`.
