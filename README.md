# Strategy Lab

Historical backtesting and forward paper testing on one engine. Built in the ptrade web terminal's design (React,
Tailwind, shadcn/ui, lightweight-charts) over a FastAPI backend. Dev-only project in ClaudeWorkspace: there is no
broker, no live order and no access to HOLDOUT data. The paper-trading system in `C:\AI\QwenWorkspace\paper_trading`
remains the forward execution authority; Strategy Lab exports to it and reads from it.

- Architecture and the audit behind it: `docs/ARCHITECTURE.md`
- Forward integration with ptrade: `docs/PTRADE_INTEGRATION.md`
- Writing strategies: `docs/STRATEGY_API.md`
- Metric definitions: `docs/METRICS.md`

## Run
Interpreter: `../paper_trading/.venv` (Python 3.14.7, numpy/pandas/duckdb/fastapi/pydantic/uvicorn already
installed). Nothing is installed globally.

```
bash scripts/run_tests.sh                       # 94 Python tests (synthetic fixtures; 3 read the workspace data read-only)
cd web && npx vitest run && npx tsc -b && npx vite build   # UI tests, typecheck, build into web/dist
bash scripts/sl.sh serve --port 8070            # API + UI at http://127.0.0.1:8070
bash scripts/sl.sh ingest us_core               # cache a dataset (versioned Parquet snapshot)
bash scripts/sl.sh backtest config.json         # an ExperimentConfig file -> a run
bash scripts/sl.sh reproduce RUN_ID             # re-run and compare artifact hashes
bash scripts/sl.sh forward-advance --to 2025-06-30   # advance the SIMULATED paper host
bash scripts/sl.sh check-contract               # ptrade contract drift (read-only)
```
Without bash: `<python> scripts/serve.py serve --port 8070`.

## Layout
```
config/settings.toml      data policy, datasets, universes, storage, forward host
src/strategy_lab/         core (types, strategy API, market view) · engine (broker, portfolio, runner)
                          data (policy, providers, store) · analytics · experiments · strategies · paper · api
strategies_user/          custom strategies, discovered at startup
web/                      the UI (8 pages: Overview, Strategy Library, Backtesting, Forward Testing,
                          Performance Analytics, Market Data, Experiment History, Settings)
var/                      generated, gitignored: cache/ (datasets), experiments/ (read-only run dirs), catalog.db,
                          forward/ (deployments, mock host, export bundles)
```

## Guarantees and their tests
| Guarantee | Test |
|---|---|
| no look-ahead (poisoned future bars never change past decisions, for every built-in strategy) | `tests/test_lookahead.py` |
| orders fill only at the next session; full order lifecycle, partial fills, cash/short rules | `tests/test_engine.py` |
| equity = cash + Σ qty·mark every day; cash and positions reconcile to fills | `test_accounting_invariants_hold_every_day` |
| commissions, slippage, spread exact | `test_costs_*`, `test_hl_range_spread_uses_the_previous_bar` |
| splits and dividends (raw mode) | `test_split_*`, `test_dividend_*`, `test_derive_dividends_*` |
| HOLDOUT ceiling, missing data never filled, exchange-holiday calendar | `tests/test_data.py` |
| reproducible runs, immutable artifacts, legacy snapshots | `tests/test_experiments.py` |
| forward == backtest on deterministic data; failure isolation; restatements; idempotent deploy/sync | `tests/test_paper.py` |
| API contract and the end-to-end workflows | `tests/test_api.py` |

## Data policy
- Bars are loaded from 2018-01-01 (warm-up only), backtests start from 2019-01-01, and the hard ceiling is
  2025-12-31 (HOLDOUT starts 2026-01-01). Configuration can lower the ceiling but never raise it.
- Results are labelled with the research periods they overlap (IS 2019-22, OOS 2023-25) and with EXPLORATORY.
- Universes are today's lists, so results carry survivorship bias.
