# Writing a strategy

```python
from strategy_lab.core.strategy import Param, Strategy
from strategy_lab.core.types import OrderIntent, TargetWeights, CancelIntent

class MyStrategy(Strategy):
    id = "my_strategy"; version = "1.0"; category = "rule"   # rule | indicator | allocation | ml | ensemble | custom
    params_schema = {"lookback": Param(20, "int", 2, 250, description="...")}
    required_fields = frozenset({"close"})     # bars you read; a host without them refuses the strategy
    order_types = frozenset({"market"})        # order types you emit (limit/stop need OHLC bars)
    long_only = True

    def warmup(self) -> int:                   # history you need before deciding: declare all of it
        return self.params["lookback"]

    def precompute(self, data): ...            # optional, once: causal series over the loaded window (see below)
    def initialize(self, ctx): ...             # once: ctx.universe, params, start, end, initial_capital, fields, state, rng
    def on_order_event(self, ctx, event): ...  # each order event (submitted, partial/filled, expired, rejected, cancelled)
    def on_bar(self, ctx):                     # after each close: return intents for the next session (or None)
        closes = ctx.market.history("close", bars=self.params["lookback"])
        ...
        return TargetWeights({"AAPL": 0.5, "MSFT": 0.5})
    def shutdown(self, ctx): ...
```

**Context** (`StrategyContext`)
- `ctx.market` is a `MarketView` clipped at the current close:
  - `history(field, symbols, bars)`, `latest(field)`, `price(sym)`, `has_bar(sym)`, `bars_available(sym)`;
  - `at(field, sym, date)` and `window(start, end)` raise `LookaheadError` past now;
  - `past(k)` gives the view k sessions earlier.
  - `derived(name, bars)` and `derived_now(name)`: the strategy's precomputed series, up to now only.
- `ctx.portfolio`: a snapshot with `cash`, `equity`, `positions`, `weights()`.
- `ctx.execution`: open orders, this session's events, the fill timing and the session index.
- `ctx.state`: a JSON-serialisable dict, saved in the run as `state.json`. `ctx.rng` is seeded from the config.
  `ctx.log(msg)` writes to the run log.

**Intents**
- `OrderIntent.market(sym, qty)` is DAY by default: no bar next session means it expires with a reason.
- `OrderIntent.limit(sym, qty, price)` (DAY) and `OrderIntent.stop(sym, qty, price)` (GTC).
- `TargetWeights({sym: w})` rebalances to weights of equity at this close. Weights for symbols held but not listed
  are closed. The gross weight is capped by `max_gross_leverage`.
- `CancelIntent(order_id)`.
- `OrderIntent.market(sym, qty, attached=(AttachedExit(OrderType.STOP, 1.0, mult, add, tag), ...))`: exit legs created
  when the entry fills, priced `mult x fill price + add`; with next-open fills they are evaluated on the rest of the
  entry session after the fill, in the given order (stop first), then expire. Declare `stop`/`limit` in order_types.
- Quantities are signed share counts, whole numbers unless `fractional_shares`.

**Rules the engine enforces**
- No data after the decision close is visible.
- Fills happen at the next session (open or close), or when a limit or stop is reached.
- Undeclared fields, order types, symbols outside the universe, and short weights from a long-only strategy are
  `StrategyError`s.

**precompute(data)** returns frames indexed by the data's sessions, computed once; every row t must depend only on
rows <= t. Backtests verify it (`engine.runner.verify_precompute` recomputes on truncated data and raises
`LookaheadError` on a difference). A forward host passes data that ends at its asof.

**Rules you must follow**
- Everything that changes behaviour is a parameter.
- Bump `version` on any change; old versions stay registered for reproduction.
- Declare your full warm-up. Results must not depend on how much extra history a host loads: forward hosts load
  everything since 2018 (`test_results_do_not_depend_on_extra_history`).
- ML models train only on labels that are already known. `strategies/ml.py`'s `supervised_rows` enforces this.
  Never fit on data past the current session, and never precompute features over the whole window.

**Where to put it:** `strategies_user/*.py` (see `inverse_vol.py`). It is discovered at startup; an import error is
shown under Experiments > Strategies and on the Overview page.
