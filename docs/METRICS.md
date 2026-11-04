# Metric definitions

One implementation (`src/strategy_lab/analytics/metrics.py`) serves backtests, forward deployments and
comparisons. The UI tooltips show the same text (`web/src/lib/metrics.ts`).

**Conventions**
- The equity curve starts at the initial capital on the session before the first, so every session has one
  return.
- Returns are simple period returns. `ppy` is periods per year: 252 daily, 52 weekly, 12 monthly. The risk-free
  rate is 0 (the ptrade/v7.1 convention).
- Every value is `{value, reason}`. When a metric cannot be computed, value is null and the reason says why. It is
  never 0.

| Metric | Definition | Undefined when |
|---|---|---|
| Total return | E_end / E_0 − 1 | fewer than 2 points |
| CAGR | (E_end / E_0)^(1/years) − 1, years = n / ppy | under 1 year |
| Volatility | std(r, ddof=1) · √ppy | fewer than 20 returns |
| Sharpe | mean(r) / std(r) · √ppy | fewer than 20 returns; zero variance |
| Sortino | mean(r) / √mean(min(r,0)²) · √ppy | fewer than 20 returns; no negative returns |
| Max drawdown | min(E / cummax(E) − 1); duration = periods from the peak to recovery (or the end) | — |
| Calmar | CAGR / \|max drawdown\| | CAGR undefined; no drawdown |
| Exposure | mean((\|long\| + \|short\|) / equity) | — |
| Time in market | share of sessions with any position | — |
| Turnover | Σ traded notional / 2 / mean equity / years (annual, one-way) | — |
| Costs, cost drag | Σ commission + slippage/spread paid; ÷ mean equity ÷ years | — |
| Trades | closed round trips, FIFO-matched from fills; commissions split per share | — |
| Win rate | share of round trips with net P&L > 0 | no closed trades |
| Profit factor | Σ winning P&L / Σ losing \|P&L\| | no losing trades |
| Benchmark return, excess | the benchmark's total return on the same sessions (adjusted close); difference | no benchmark |
| Beta, alpha | cov(r, r_b) / var(r_b); (mean r − β mean r_b) · ppy | < 20 overlapping returns; zero variance |
| Correlation, tracking error, information ratio | corr(r, r_b); std(r − r_b) · √ppy; mean(r − r_b) · ppy / TE | as above |
| Monthly / annual returns | compounded within the calendar period, from the previous period's last value | — |
| Rolling return / vol / Sharpe | over a trailing window of N periods | until the window is full |

Forward books use exactly these functions. The test `test_forward_metrics_use_the_backtest_definitions` checks
that a forward book's metrics equal a backtest's over the same window.
