"""The reference Entry-Exit experiments on the workspace research data (EXPLORATORY; read-only data <= 2025-12-31;
the 2025 test partition stays locked). Usage: <python> scripts/ee_reference_experiment.py [main|rsi_grid|all]

main      the requested entries (and four combinations) against the requested exits, combinations of exits and a
          1R-5R target sweep, with a common catastrophic 3 x ATR stop on every plan
rsi_grid  RSI(14) < level for level 15..40 against three exits (entry parameter sensitivity)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from strategy_lab.entryexit.config import EntryExitExperimentConfig  # noqa: E402
from strategy_lab.lab import default_lab  # noqa: E402

S = lambda i, **p: {"kind": "signal", "id": i, "params": p}  # noqa: E731
C = lambda i, **p: {"id": i, "params": p}  # noqa: E731

COMMON = dict(
    dataset="us_core", universe_name="sp1500", start="2019-01-02", end="2025-12-31", max_hold=120,
    protective=[C("stop_atr", k=3.0)], baseline_exit="time_20d",
    controls={"draws": 5, "date_draws": 2, "permutations": 1000},
    portfolio={"run": "top", "top": 30, "random_controls": 2, "max_positions": 20, "initial_capital": 1_000_000,
               "sizing": {"mode": "risk", "risk_per_position": 0.01, "max_position_weight": 0.15}},
    execution={"slippage_bps": 5.0, "commission": {"bps": 1.0}},
    walk_forward={"enabled": True, "first_train_years": 2, "step_years": 1, "objective": "excess_matched"},
)

MAIN = EntryExitExperimentConfig(
    name="Reference: requested entries x exits (S&P 1500 list, 2019-2024, test locked)",
    notes="Entries and exits of the Strategy Lab request; common 3 ATR catastrophic stop; baseline exit 20 sessions.",
    entries=[S("rsi14_below_30"), S("macd_signal_cross_up"), S("breakout_20d"), S("rvol_up_day"), S("bb_lower_reentry"),
             S("ema20_reclaim"), S("mom_acceleration"),
             {"kind": "combination", "name": "RSI oversold AND MACD bullish", "direction": "bullish", "mode": "and",
              "window": 3, "components": [{"id": "rsi14_below_30"}, {"id": "macd_signal_cross_up"}]},
             {"kind": "combination", "name": "Breakout AND relative volume", "direction": "bullish", "mode": "and",
              "window": 1, "components": [{"id": "breakout_20d"}, {"id": "rvol_up_day"}]},
             {"kind": "combination", "name": "EMA reclaim AND momentum acceleration", "direction": "bullish",
              "mode": "and", "window": 3, "components": [{"id": "ema20_reclaim"}, {"id": "mom_acceleration"}]},
             {"kind": "combination", "name": "2 of 4 bullish reversal signals", "direction": "bullish",
              "mode": "k_of_n", "k": 2, "window": 3,
              "components": [{"id": "rsi14_cross_above_30"}, {"id": "macd_signal_cross_up"},
                             {"id": "bb_lower_reentry"}, {"id": "stoch_bull_cross_oversold"}]}],
    exit_ids=["rsi_overbought", "macd_bearish_cross", "target_3r", "trail_atr", "chandelier", "time_5d", "time_60d",
              "trail_swing_low", "structure_break"],
    exits=[{"name": "20 EMA exit", "components": [C("trail_ema", n=20)]},
           {"name": "Half at 2R, breakeven, ATR trail", "components": [C("target_2r", fraction=0.5, then="breakeven"),
                                                                      C("trail_atr", k=3.0)]},
           {"name": "MACD bearish OR ATR trail", "components": [C("macd_bearish_cross"), C("trail_atr", k=3.0)]},
           {"name": "RSI overbought OR 3R target", "components": [C("rsi_overbought"), C("target_3r")]},
           {"name": "EMA breakdown AND momentum deterioration", "condition_mode": "and",
            "components": [C("close_below_ema", n=20, mode="below"), C("momentum_deterioration", n=10)]},
           {"name": "2 ATR stop OR 20 sessions", "components": [C("stop_atr", k=2.0), C("time_20d")]}],
    exit_grids=[{"plan": {"name": "", "components": [C("stop_atr", k=2.0), C("target_r", k=2.0)]}, "component": 1,
                 "grid": {"k": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0]}}],
    **COMMON)

RSI_GRID = EntryExitExperimentConfig(
    name="Reference: RSI(14) < level, level 15..40, three exits (S&P 1500 list, 2019-2024, test locked)",
    entry_grids=[{"id": "rsi14_below_30", "params": {}, "grid": {"level": [15, 20, 25, 30, 35, 40]}}],
    exit_ids=["target_2r", "trail_atr"], **{**COMMON, "portfolio": {**COMMON["portfolio"], "top": 6}})


def run(cfg: EntryExitExperimentConfig) -> str:
    lab = default_lab()
    try:
        t0 = time.time()
        eid = lab.entry_exit.create(cfg)
        print(f"created {eid}", flush=True)
        out = lab.entry_exit.execute(eid)
        print(json.dumps(dict(experiment_id=eid, status=out["status"], seconds=round(time.time() - t0, 1),
                              summary=out.get("summary")), default=str), flush=True)
        return eid
    finally:
        lab.close()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("main", "all"):
        run(MAIN)
    if which in ("rsi_grid", "all"):
        run(RSI_GRID)
