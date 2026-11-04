"""Example user strategy: inverse-volatility weights, rebalanced on a fixed schedule.

Drop any module defining Strategy subclasses into strategies_user/ and it appears in the Strategy Library after a
restart. Rules: decide only from ctx (it is clipped at the session's close), put every setting in params_schema,
declare the bars you read (required_fields) and the history you need (warmup), and bump `version` on any change:
old versions stay runnable, so their experiments still reproduce.
"""
from __future__ import annotations

import numpy as np

from strategy_lab.core.strategy import Param, Strategy, StrategyContext
from strategy_lab.core.types import TargetWeights


class InverseVolatility(Strategy):
    id = "inverse_vol"
    version = "1.0"
    name = "Inverse volatility (example user strategy)"
    category = "allocation"
    description = ("Every rebalance_every sessions, weights each symbol by 1 / its annualised volatility over the last "
                   "`window` sessions (symbols without a full window are skipped), scaled to `gross`.")
    params_schema = {
        "window": Param(63, "int", 10, 252, description="volatility window (sessions)"),
        "rebalance_every": Param(21, "int", 1, 252),
        "gross": Param(1.0, "float", 0.05, 1.0),
    }

    def warmup(self) -> int:
        return self.params["window"] + 1

    def on_bar(self, ctx: StrategyContext):
        p = self.params
        if ctx.execution.session_index % p["rebalance_every"]:
            return None
        closes = ctx.market.history("close", bars=p["window"] + 1)
        if len(closes) <= p["window"]:
            return None
        vol = np.log(closes).diff().iloc[1:].std(ddof=1)
        vol = vol[(vol > 0) & vol.notna()]
        vol = vol[[ctx.market.has_bar(s) for s in vol.index]]
        if vol.empty:
            return None
        inv = 1.0 / vol
        w = inv / inv.sum() * p["gross"]
        ctx.log(f"inverse-vol weights: {', '.join(f'{s} {x:.1%}' for s, x in w.sort_values(ascending=False).items())}")
        return TargetWeights({s: float(x) for s, x in w.items()}, tag="inverse_vol")
