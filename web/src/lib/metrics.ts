// Metric labels, formats and definitions. The definitions are the backend's (src/strategy_lab/analytics/metrics.py,
// docs/METRICS.md); a metric the backend could not compute arrives as {value: null, reason} and is shown as a dash
// with the reason, never as 0.
import * as fmt from "@/lib/format";

export interface Metric {
  value: number | null;
  reason: string | null;
}
export type Metrics = Record<string, Metric>;

type Kind = "pct" | "ratio" | "int" | "usd" | "num";

export const METRICS: Record<string, { label: string; kind: Kind; def: string; better?: "up" | "down" }> = {
  total_return: { label: "Total return", kind: "pct", def: "Final equity / initial capital − 1.", better: "up" },
  cagr: { label: "CAGR", kind: "pct", def: "(Final / initial)^(1 / years) − 1, years = periods / periods per year. Undefined below one year.", better: "up" },
  sharpe: { label: "Sharpe", kind: "ratio", def: "Mean period return over its standard deviation (rf = 0), × √periods per year. Needs ≥ 20 returns.", better: "up" },
  sortino: { label: "Sortino", kind: "ratio", def: "Mean period return over the downside deviation √mean(min(r, 0)²), × √periods per year.", better: "up" },
  max_drawdown: { label: "Max drawdown", kind: "pct", def: "Largest fall of equity from its running peak.", better: "up" },
  calmar: { label: "Calmar", kind: "ratio", def: "CAGR / |max drawdown|.", better: "up" },
  volatility: { label: "Volatility", kind: "pct", def: "Standard deviation of period returns × √periods per year." },
  win_rate: { label: "Win rate", kind: "pct", def: "Share of closed round trips (FIFO-matched fills) with positive net P&L.", better: "up" },
  profit_factor: { label: "Profit factor", kind: "ratio", def: "Gross profit / gross loss of closed round trips. Undefined with no losing trade.", better: "up" },
  exposure: { label: "Exposure", kind: "pct", def: "Mean gross exposure (|long| + |short|) / equity over the sessions." },
  time_in_market: { label: "Time in market", kind: "pct", def: "Share of sessions with any position." },
  turnover: { label: "Turnover", kind: "ratio", def: "Annualised one-way turnover: traded notional / 2 / mean equity / years." },
  trade_count: { label: "Trades", kind: "int", def: "Closed round trips (FIFO-matched)." },
  fill_count: { label: "Fills", kind: "int", def: "Executed fills, partial fills included." },
  avg_trade_return: { label: "Avg trade", kind: "pct", def: "Mean net return of closed round trips." },
  avg_holding_periods: { label: "Avg hold", kind: "num", def: "Mean holding period of closed round trips, in sessions." },
  total_costs: { label: "Costs", kind: "usd", def: "Commissions + slippage and spread paid versus the reference prices." },
  cost_drag: { label: "Cost drag", kind: "pct", def: "Total costs / mean equity / years." },
  dividends: { label: "Dividends", kind: "usd", def: "Cash dividends credited (raw price mode only; adjusted prices embed them)." },
  benchmark_total_return: { label: "Benchmark", kind: "pct", def: "The benchmark's total return over the same sessions (adjusted close)." },
  excess_return: { label: "Excess", kind: "pct", def: "Total return − benchmark total return." },
  beta: { label: "Beta", kind: "ratio", def: "cov(r, r_b) / var(r_b) on period returns." },
  alpha: { label: "Alpha", kind: "pct", def: "(mean r − β · mean r_b) × periods per year." },
  correlation: { label: "Correlation", kind: "ratio", def: "Correlation of period returns with the benchmark." },
  tracking_error: { label: "Tracking error", kind: "pct", def: "Std of (r − r_b) × √periods per year." },
  information_ratio: { label: "Info ratio", kind: "ratio", def: "Annualised mean (r − r_b) / tracking error." },
  max_drawdown_duration: { label: "DD duration", kind: "int", def: "Periods from the peak before the max drawdown to its recovery (or the end)." },
};

// Percentages with a direction get a sign; levels (volatility, exposure, win rate) do not.
const SIGNED = new Set(["total_return", "cagr", "max_drawdown", "excess_return", "alpha", "avg_trade_return",
  "benchmark_total_return", "cost_drag"]);

export const HEADLINE = ["total_return", "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "volatility", "win_rate",
  "profit_factor", "exposure", "turnover", "trade_count"];

export function formatMetric(key: string, m: Metric | undefined): string {
  if (!m || m.value === null || m.value === undefined) return fmt.DASH;
  const k = METRICS[key]?.kind ?? "num";
  if (k === "pct") return fmt.pct(m.value, 2, SIGNED.has(key));
  if (k === "int") return fmt.int(m.value);
  if (k === "usd") return fmt.usd(m.value);
  return fmt.num(m.value, 2);
}

export function metricTone(key: string, m: Metric | undefined): string {
  if (!m || m.value === null) return "text-muted-foreground";
  if (["total_return", "cagr", "sharpe", "sortino", "calmar", "excess_return", "alpha", "avg_trade_return",
    "information_ratio"].includes(key)) return fmt.tone(m.value);
  if (key === "max_drawdown") return m.value < 0 ? "text-neg" : "text-muted-foreground";
  return "";
}
