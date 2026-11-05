# Forward paper trading on ptrade

Market Signal Research runs forward through ptrade's documented basket plug-in interface (`docs/ADDING_A_MODEL.md` in
paper_trading). It adds no new engine, no ledger writes and no order endpoint. Mirror of the contract:
`src/strategy_lab/paper/contract.py` (paper_trading commit `792758b`). Drift check, which reads the operational sources
read-only:

```
bash scripts/sl.sh check-contract
```

## How a deployment runs in ptrade

| ptrade step (`baskets.run.run_session`) | Market Signal Research code |
|---|---|
| loads `baskets/plugins/<module>.py`, reads `RULE` / `PRODUCER`, stamps the file's sha256 | exported plug-in shim (`paper/export.py` `PLUGIN`): standard-library imports only, so it is import-safe |
| `PRODUCER.scores(asof, ctx)` → stored candidates | `UniverseProducer`: one placeholder row per universe symbol with a bar on asof. `direction` is the book id, so books never collide on the candidate key, and ptrade builds Frames over the whole universe |
| `RULE.simulate(cands, frames, start, asof, params)` re-runs the book from its start | `StrategyLabRule`: Frames → `MarketData` (a bar exists only where an open exists) → the same `StrategyRunner` a backtest uses → `BookRun` in index units |
| stored days must re-run identically (`basket_restatements` otherwise) | the engine is deterministic and history-independent once warm-up is declared (tests) |
| `RULE.pending(...)` → `basket_picks` | the orders submitted at asof's close |
| API v1 `/books/{id}`, `/equity`, `/trades`, `/holdings`, `/picks` | `paper/client.py` (GET only) → `ForwardService.sync` → Market Signal Research's append-only forward tables |

The pinned engine (`models/strategy_lab/PIN.json`) covers every module that decides trades. The plug-in verifies
every pinned file's sha256 before it runs. A Market Signal Research code change therefore makes a new pin, a new plug-in
sha256 and a new ptrade `model_id`, which means a new registration and a new clock.

## Strategies from the research labs
An imported entry-exit system (`requires_historical_test`) is refused until a finished backtest of the same strategy
version exists (Backtesting); a backtest with other parameters or universe passes with a warning. The generic
`entry_exit_system` (JSON definition) cannot be deployed. Its export pins `strategies/entry_exit.py`, `entryexit/`
and `signals/`. Only close-based plans (market orders, open/close bars, no benchmark) with open/close entries pass the
checks below; plans with intrabar stops or targets are backtest-only. The pinned engine includes `engine/fills.py`
(2026-11-04): a new export has a new pin and model_id; bundles already written keep their own pinned copies.

## What a ptrade deployment can and cannot do (checked at deploy, fail closed)
- Frames carry **adjusted open and close only**: no high, low or volume. So a deployment allows market orders only,
  no limit/stop orders, no participation cap and no high-low spread model, and `price_mode` must be `adjusted`.
- Long only (trade kinds are buy, trim, exit and hedge).
- SPY cannot be traded: ptrade keeps it out of the basket Frames (`spy_close` only).
- The universe must be inside ptrade's `config/universe.txt`. Set `[ptrade] universe_file` to a copy to check this;
  otherwise the check is reported as not done.
- Daily bars, one decision per session, fills at the next open or close.

## Applying a bundle (operator, after explicit approval)
Each deployment writes `var/forward/exports/<book>/`. Its `OPERATOR.md` has the exact steps:
1. Copy the plug-in, `models/strategy_lab/`, the protocol and `scripts/strategy_lab_job.py`.
2. Add `[paths] ledger_strategy_lab` to settings.toml **before** the books.toml entry. books.toml validation fails
   on an unknown ledger key, and that would stop every book.
3. Append the books entry.
4. Confirm the model id: `python -m ptrade.new_model --model-id <book>`.
5. Pre-register: `python -m baskets.register --ledger data/ledger_strategy_lab.db --books <book>`.
6. Schedule `python scripts/strategy_lab_job.py` as its own Task Scheduler task after the daily job. It applies the
   daily job's basket-step guard (no run until the price store has asof's bar), then `baskets.job.step` (timeout,
   error parsing, ledger backup) on the Market Signal Research ledger. The daily job itself is unchanged
   (`paper/templates/strategy_lab_job.py`; tests run it against stubs of the ptrade modules it imports).

A bundle is deterministic and LF only. The same spec and pinned code always give the same files and the same
model_id: there is no timestamp in the plug-in, and pinned copies are written with LF whatever the checkout's line
endings (`.gitattributes` keeps the repo LF too). Line endings and the export time used to leak into the pin and the
plug-in sha256, so the same commit could give different model_ids (found and fixed 2026-11-04, before any bundle was
applied). `ForwardService.reexport(id)` rebuilds an exported, not yet applied, ptrade deployment with the current
code; after registration a code change is a new book.

A separate ledger is required. The basket candidate table has no producer column, and its rows feed every book of
the ledger, so a Market Signal Research producer must never write into `ledger_baskets.db`.

## Unverified until applied operationally
- The plug-in has only run in the mock host and in a fresh process with no `strategy_lab` on the path (test). It
  has not run inside ptrade's Linux Python 3.12 venv.
- API v1 serving a book whose ledger key is `ledger_strategy_lab` is inferred from `ptrade/bookdata.py`, not
  observed.
- The daily job runs only the default basket ledger; the Market Signal Research ledger needs its own schedule entry. Its
  alerts and heartbeat don't cover that run: its status is one JSON line per run in `logs/strategy_lab_job.out`.
- ptrade's price store and the research downloads can differ (restatements, re-adjustments), so forward results
  versus Market Signal Research backtests can differ for data reasons. `reconcile` reports this; it does not hide it.

## The mock host (`paper/mock_host.py`)
Used in this workspace because the operational API serves HOLDOUT/FWD evidence, which must not enter it. The mock
mirrors plug-in loading, model_id computation, one transaction per session, nightly full re-runs with restatement
rows, per-book savepoints (one failing book never stops the others), the clock that keeps advancing while books
are paused (paused days are appended late on resume), and the API v1 response shapes (tests validate them strictly
against `paper/api_models.py`). Everything it shows is labelled SIMULATED and uses ≤ 2025 data.
