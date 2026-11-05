// One (entry, exit) cell: trade-level statistics per partition (returns, risk, excursions, exit reasons, excess over
// matched random entries and random dates, benchmark), the event-driven portfolio backtest (equity, drawdown, monthly
// returns, against buy-and-hold and random-entry portfolios), the comparison with the baseline exit on identical
// events, distributions (trade returns, holding periods), annual and regime breakdowns, entry/exit timing and the
// trade list. Import sends the pairing to the Strategy Library.
import { Download, Library } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { useCell, useCellEquity, useCellTrades, useEEExperiment, useEEImport, type CellPartition } from "@/api/entryExit";
import { MonthlyHeatmap } from "@/components/Charts";
import { ChoiceSelect, Field } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { LineChart, PALETTE } from "@/components/LineChart";
import { BarChart, Histogram, XYChart } from "@/components/ResearchCharts";
import { Empty, ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge, Stat } from "@/components/common";
import { Button } from "@/components/ui/button";
import type { Row } from "@/api/client";
import * as fmt from "@/lib/format";
import { navigate } from "@/lib/router";
import { EESubNav, pctc, qv } from "@/pages/entryexit/common";
import { VerdictBadge } from "@/pages/signals/common";

const P = (v: number | null | undefined, d = 2) => fmt.pct(v, d);

function TradeStats({ s, timingQ }: { s: CellPartition; timingQ?: number | null }) {
  if (!s.n) return <Empty title="No trades in this partition" />;
  const m = s.matched, rd = s.random_dates, tm = s.timing;
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
      <Stat label="Trades" value={fmt.int(s.n)} sub={`${s.n_dates} signal sessions`} />
      <Stat label="Mean / median return" value={P(s.mean_ret)} sub={`median ${P(s.median_ret)}`} tone={fmt.tone(s.mean_ret)} hint="net of slippage, spread and commissions" />
      <Stat label="Win rate" value={fmt.pct(s.win_rate, 1, false)} sub={`PF ${fmt.num(s.profit_factor, 2)}`} />
      <Stat label="Expectancy" value={`${fmt.num(s.expectancy_r, 2)} R`} sub={`${fmt.num(s.expectancy_ref_r, 2)} R (ATR ref)`} tone={fmt.tone(s.expectancy_r)} hint="mean net P&L per trade in units of the initial risk (own stop; the ATR reference R for comparisons across exits)" />
      <Stat label="Avg win / loss" value={`${P(s.avg_win)} / ${P(s.avg_loss)}`} sub={`${fmt.num(s.avg_win_bars, 1)} / ${fmt.num(s.avg_loss_bars, 1)} sessions`} />
      <Stat label="Holding" value={`${fmt.num(s.avg_bars, 1)} sessions`} sub={`median ${fmt.num(s.median_bars, 0)}`} />
      <Stat label="MAE / MFE" value={`${P(s.mae)} / ${P(s.mfe)}`} sub={`${fmt.num(s.mae_r, 2)} / ${fmt.num(s.mfe_r, 2)} R`} hint="mean adverse / favourable excursion while held" />
      <Stat label="Tail" value={P(s.q05)} sub={`CVaR5 ${P(s.cvar05)} · worst ${P(s.worst)}`} tone="text-neg" />
      <Stat label="Excess vs matched random" value={P(m?.excess)} tone={fmt.tone(m?.excess)} sub={m?.ci ? `[${P(m.ci[0])}, ${P(m.ci[1])}] p ${m.p?.toFixed(3) ?? "—"}` : ""} hint="the same exit replayed on random stocks of the same session and matching group" />
      <Stat label="Excess vs random dates" value={P(rd?.excess)} tone={fmt.tone(rd?.excess)} sub={rd?.p !== undefined && rd?.p !== null ? `p ${rd.p.toFixed(3)}` : ""} />
      {tm && <Stat label="Excess vs random timing" value={P(tm.excess)} tone={fmt.tone(tm.excess)}
        sub={`${tm.ci ? `[${P(tm.ci[0])}, ${P(tm.ci[1])}] ` : ""}p ${tm.p?.toFixed(3) ?? "—"}${timingQ !== undefined && timingQ !== null ? ` q ${timingQ.toFixed(3)}` : ""}`}
        hint={`this exit's sell signals moved to random dates on the same stock (same count and spacing), replayed on the same entries; real ${P(tm.real_mean)} vs shifted ${P(tm.shifted_mean)}; ${fmt.pct(tm.beat_share, 0, false)} of trades beat their random timing`} />}
      <Stat label="Excess vs benchmark" value={P(s.excess_bench)} tone={fmt.tone(s.excess_bench)} sub={`benchmark ${P(s.bench_ret)}`} hint="the benchmark over the same holding windows (buy-and-hold over equivalent intervals)" />
      <Stat label="Exit mix" value={`${fmt.pct(s.stop_rate, 0, false)} stop`} sub={`${fmt.pct(s.target_rate, 0, false)} target · ${fmt.pct(s.condition_rate, 0, false)} cond · ${fmt.pct(s.max_hold_rate, 0, false)} cap`} />
      <Stat label="Partial exits" value={fmt.pct(s.partial_rate, 0, false)} sub={`period-end ${fmt.pct(s.period_end_rate, 1, false)}`} />
      <Stat label="Return per session held (x252)" value={P(s.ret_per_session)} sub={`trade Sharpe ${fmt.num(s.trade_sharpe, 2)}`} hint="mean return / mean sessions held x 252" />
      <Stat label="Consistency" value={fmt.pct(s.consistency_years, 0, false)} sub={`years with positive excess; ${fmt.pct(s.positive_years, 0, false)} positive mean`} />
      <Stat label="Own stop defined" value={fmt.pct(s.own_risk_rate, 0, false)} sub={`stop breached at entry ${fmt.pct(s.stop_breach_rate, 1, false)}`} />
    </div>
  );
}

export function CellView({ id, cell }: { id: string; cell: string }) {
  const exp = useEEExperiment(id);
  const c = useCell(id, cell);
  const parts = Object.keys(c.data?.partitions ?? {});
  const [partition, setPartition] = useState("");
  const part = partition || parts[0] || "";
  const eq = useCellEquity(id, cell, part, !!c.data && !!c.data.portfolio[part]);
  const trades = useCellTrades(id, cell, part);
  const imp = useEEImport();
  const s = c.data?.partitions[part];
  const pr = c.data?.portfolio[part];
  const test = c.data?.tests.find((t) => t.partition === part);
  const vsBase = c.data?.vs_baseline.find((r) => r.partition === part);

  const equityLines = useMemo(() => {
    const e = eq.data?.equity ?? [];
    const bh = eq.data?.buy_and_hold ?? [];
    const lines = [{ key: "eq", name: "entry-exit system", color: PALETTE[0], points: e.map((r) => ({ time: String(r.date).slice(0, 10), value: Number(r.equity) })) }];
    if (e.length && e[0].benchmark !== null) lines.push({ key: "bm", name: "benchmark", color: PALETTE[2], points: e.filter((r) => r.benchmark !== null).map((r) => ({ time: String(r.date).slice(0, 10), value: Number(r.benchmark) })) });
    if (bh.length) lines.push({ key: "bh", name: "buy & hold universe", color: PALETTE[3], points: bh.map((r) => ({ time: String(r.date).slice(0, 10), value: Number(r.equity) })) });
    return lines;
  }, [eq.data]);
  const dd = useMemo(() => [{ key: "dd", name: "drawdown", color: PALETTE[7], area: true, invert: true, points: (eq.data?.equity ?? []).map((r) => ({ time: String(r.date).slice(0, 10), value: Number(r.drawdown) })) }], [eq.data]);
  const timing = useMemo(() => {
    const t = (trades.data ?? []).filter((r) => r.status === "ok");
    const byMonth = new Map<string, { n: number; ret: number }>();
    for (const r of t) {
      const k = String(r.entry_date).slice(0, 7);
      const g = byMonth.get(k) ?? { n: 0, ret: 0 };
      g.n += 1; g.ret += Number(r.ret);
      byMonth.set(k, g);
    }
    return [...byMonth.entries()].sort().map(([k, v], i) => [i, v.ret / v.n, k, v.n] as const);
  }, [trades.data]);
  const byHolding = useMemo(() => {
    const g = new Map<number, { n: number; sum: number }>();
    for (const r of trades.data ?? []) {
      if (r.status !== "ok") continue;
      const k = Number(r.bars) + 1;
      const x = g.get(k) ?? { n: 0, sum: 0 };
      x.n += 1; x.sum += Number(r.ret);
      g.set(k, x);
    }
    return [...g.entries()].filter(([, v]) => v.n >= 5).sort((a, b) => a[0] - b[0]).map(([k, v]) => [k, v.sum / v.n] as [number, number]);
  }, [trades.data]);

  if (c.isLoading || exp.isLoading) return <div className="space-y-3"><PageHeader title={cell} /><EESubNav /><Loading rows={10} /></div>;
  if (c.error || !c.data) return <div className="space-y-3"><PageHeader title={cell} /><EESubNav /><ErrorState error={c.error ?? "not found"} /></div>;
  const d = c.data;
  const cols: Col<Row>[] = [
    { id: "signal_date", header: "Signal", value: (r) => r.signal_date }, { id: "symbol", header: "Symbol", value: (r) => r.symbol },
    { id: "entry_date", header: "Entry", value: (r) => r.entry_date }, { id: "exit_date", header: "Exit", value: (r) => r.exit_date },
    { id: "entry_price", header: "Entry px", value: (r) => r.entry_price, cell: (r) => <span className="num">{fmt.price(r.entry_price as number)}</span>, align: "right" },
    { id: "exit_price", header: "Exit px (avg)", value: (r) => r.exit_price, cell: (r) => <span className="num">{fmt.price(r.exit_price as number)}</span>, align: "right" },
    { id: "ret", header: "Return", value: (r) => r.ret, cell: (r) => pctc(r.ret as number), align: "right" },
    { id: "r_mult", header: "R", value: (r) => r.r_mult, cell: (r) => <span className="num">{fmt.num(r.r_mult as number, 2)}</span>, align: "right" },
    { id: "bars", header: "Sessions", value: (r) => (typeof r.bars === "number" ? r.bars + 1 : null), align: "right" },
    { id: "reason", header: "Exit reason", value: (r) => r.reason }, { id: "tranches", header: "Tranches", value: (r) => r.tranches, align: "right" },
    { id: "mae", header: "MAE", value: (r) => r.mae, cell: (r) => pctc(r.mae as number), align: "right" },
    { id: "mfe", header: "MFE", value: (r) => r.mfe, cell: (r) => pctc(r.mfe as number), align: "right" },
    { id: "matched_mean", header: "Random mean", value: (r) => r.matched_mean, cell: (r) => pctc(r.matched_mean as number), align: "right" },
    { id: "bench_ret", header: "Benchmark", value: (r) => r.bench_ret, cell: (r) => pctc(r.bench_ret as number), align: "right" },
    { id: "status", header: "Status", value: (r) => r.status, hidden: true },
  ];
  const pm = pr?.metrics as Record<string, number | null> | undefined;
  return (
    <div className="space-y-3">
      <PageHeader title={<span>{d.entry.name} <span className="text-muted-foreground">→</span> {d.exit.name}</span>}
        sub={<span className="flex flex-wrap items-center gap-2">{d.exit.trigger} · <a className="text-primary underline" href={`/entry-exit/experiments/${id}`}>{exp.data?.meta.name || id}</a> · {cell} <PeriodBadge period="EXPLORATORY" />{!d.exit.deployable && <span className="text-warn">intrabar orders: backtest only (not deployable on ptrade)</span>}</span>}
        actions={<>
          <Field label="Partition"><ChoiceSelect value={part} onChange={setPartition} className="h-8 w-36 text-xs" options={parts.map((x) => ({ value: x, label: `${x} (${d.partitions[x].role})` }))} aria-label="partition" /></Field>
          <Button size="sm" disabled={imp.isPending} onClick={() => imp.mutate({ experiment_id: id, cell }, {
            onSuccess: (r) => { toast.success(`${r.created ? "imported" : "already imported"}: ${r.strategy_id}`); navigate(`/strategies/${r.strategy_id}`); },
            onError: (e) => toast.error(String(e)) })} data-action="import"><Library className="size-3.5" />Import to Strategy Library</Button>
        </>} />
      <EESubNav />
      {s && <TradeStats s={s} timingQ={c.data?.timing_tests?.find((t) => t.partition === part)?.q} />}
      <div className="grid gap-3 xl:grid-cols-[1fr_380px]">
        <div className="space-y-3">
          <Panel title="Portfolio backtest (event-driven engine)" info="the cell's events of this partition run through the existing engine with capital, sizing, the position cap and costs; positions open at the end are marked to market">
            {!pr ? <Empty title="No portfolio backtest for this cell">The experiment backtested only the top cells (portfolio.run = top) or none.</Empty> : eq.isLoading ? <Loading rows={5} /> : (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
                  <Stat label="Total return" value={P(pm?.total_return)} tone={fmt.tone(pm?.total_return)} />
                  <Stat label="CAGR" value={P(pm?.cagr)} tone={fmt.tone(pm?.cagr)} />
                  <Stat label="Sharpe" value={fmt.num(pm?.sharpe, 2)} sub={`Sortino ${fmt.num(pm?.sortino, 2)}`} />
                  <Stat label="Max drawdown" value={P(pm?.max_drawdown)} tone="text-neg" sub={`Calmar ${fmt.num(pm?.calmar, 2)}`} />
                  <Stat label="Volatility" value={P(pm?.volatility)} />
                  <Stat label="Exposure / turnover" value={fmt.pct(pm?.exposure, 0, false)} sub={`turnover ${fmt.num(pm?.turnover, 1)}x/yr`} />
                  <Stat label="Trades" value={fmt.int(pm?.trade_count)} sub={`win ${fmt.pct(pm?.win_rate, 0, false)} · PF ${fmt.num(pm?.profit_factor, 2)}`} />
                  <Stat label="vs random-entry portfolios" value={fmt.num(pr.random_entry?.sharpe?.excess, 2)} sub={`Sharpe above ${fmt.pct(pr.random_entry?.sharpe?.share_below, 0, false)} of ${pr.n_random}`} hint="Sharpe minus the mean Sharpe of portfolios that take matched random entries with the same exit, sizing and costs" />
                </div>
                <LineChart name={`equity-${cell}-${part}`} lines={equityLines} unit="usd" height={260} />
                <LineChart name={`drawdown-${cell}-${part}`} lines={dd} unit="pct" height={120} legend={false} />
                {(eq.data?.monthly ?? []).length > 0 && <MonthlyHeatmap rows={(eq.data?.monthly ?? []).map((r) => ({ year: Number(r.year), month: Number(r.month), ret: r.ret as number | null }))} />}
              </div>
            )}
          </Panel>
          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="Trade returns">{s?.hist_ret ? <Histogram name={`ret-${cell}`} edges={s.hist_ret.edges} counts={s.hist_ret.counts} markers={[{ value: s.mean_ret, label: "mean", color: "var(--primary)" }, { value: s.matched?.control_mean, label: "random entries", color: "var(--warn)", dashed: true }]} /> : <Empty title="—" />}</Panel>
            <Panel title="Holding periods (sessions)">{s?.hist_bars ? <Histogram name={`bars-${cell}`} edges={s.hist_bars.edges} counts={s.hist_bars.counts} unit="num" markers={[{ value: s.avg_bars, label: "mean", color: "var(--primary)" }]} /> : <Empty title="—" />}</Panel>
            <Panel title="By year (mean return, excess vs random)">
              <BarChart name={`year-${cell}`} rows={(s?.by_year ?? []).map((g) => ({ label: String(g.group), value: g.excess ?? null, n: g.n, note: `mean ${P(g.mean_ret)}` }))} />
            </Panel>
            <Panel title="By market regime">
              <BarChart name={`regime-${cell}`} rows={[...(s?.by_market_trend ?? []).map((g) => ({ label: `trend ${g.group}`, value: g.excess ?? null, n: g.n, note: `mean ${P(g.mean_ret)}` })),
                ...(s?.by_market_vol ?? []).map((g) => ({ label: `vol ${g.group}`, value: g.excess ?? null, n: g.n, note: `mean ${P(g.mean_ret)}` })),
                ...(s?.by_stock_vol ?? []).map((g) => ({ label: `stock vol ${g.group}`, value: g.excess ?? null, n: g.n, note: `mean ${P(g.mean_ret)}` }))]} />
            </Panel>
            <Panel title="Entry timing (mean return by entry month)">
              <XYChart name={`timing-${cell}`} unit="pct" height={180} xLabel="month index" series={[{ key: "m", name: "mean return", color: PALETTE[0], points: timing.map(([i, v]) => [i, v]) }]} />
            </Panel>
            <Panel title="Mean return by holding period" info="trades grouped by sessions held (groups of at least 5 trades): which exits cut losers early and let winners run">
              <XYChart name={`holding-${cell}`} unit="pct" height={180} xLabel="sessions held" series={[{ key: "t", name: "mean return", color: PALETTE[1], points: byHolding }]} />
            </Panel>
          </div>
          <Panel title="Trades" bodyClass="p-0" actions={<span className="text-2xs text-muted-foreground"><Download className="mr-1 inline size-3" />CSV from the table toolbar</span>}>
            {trades.isLoading ? <Loading rows={6} /> : <DataTable rows={trades.data ?? []} cols={cols} name={`trades-${cell}-${part}`} maxHeight={420} />}
          </Panel>
        </div>
        <div className="space-y-3">
          <Panel title="Significance">
            <KV rows={[["Matched-random test", test ? <span>p {qv(test.p)} · q {qv(test.q)} of {test.m_tests} tests</span> : "—"],
              ["Verdict", <VerdictBadge v={test?.verdict.category} />], ["Reason", <span className="text-2xs text-muted-foreground">{test?.verdict.reason}</span>],
              ["Annualized excess", P(test?.verdict.annualized_net_excess)]]} />
          </Panel>
          <Panel title="Against the baseline exit (identical events)">
            {vsBase ? <KV rows={[["Mean difference", pctc(vsBase.diff)], ["95% CI", vsBase.ci ? `[${P(vsBase.ci[0])}, ${P(vsBase.ci[1])}]` : "—"], ["Better on", fmt.pct(vsBase.win_share, 0, false)], ["p / q", <span>{qv(vsBase.p)} / {qv(vsBase.q)}</span>], ["Trades", fmt.int(vsBase.n)]]} />
              : <div className="text-xs text-muted-foreground">This is the baseline exit, or the experiment has none.</div>}
          </Panel>
          <Panel title="Exit reasons">
            <BarChart name={`reasons-${cell}`} unit="int" rows={Object.entries(s?.reasons ?? {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, value: v }))} />
          </Panel>
          <Panel title="Partitions">
            <table className="w-full text-2xs">
              <thead><tr className="text-left text-muted-foreground"><th>partition</th><th className="text-right">trades</th><th className="text-right">mean</th><th className="text-right">excess</th><th className="text-right">Sharpe</th><th className="text-right">CAGR</th></tr></thead>
              <tbody>{parts.map((x) => { const y = d.partitions[x]; const pp = d.portfolio[x]?.metrics as Record<string, number | null> | undefined; return (
                <tr key={x} className="border-t"><td>{x} ({y.role})</td><td className="num text-right">{y.n}</td><td className="text-right">{pctc(y.mean_ret)}</td><td className="text-right">{pctc(y.matched?.excess)}</td><td className="num text-right">{fmt.num(pp?.sharpe, 2)}</td><td className="text-right">{pctc(pp?.cagr)}</td></tr>); })}</tbody>
            </table>
            <div className="mt-1 text-2xs text-muted-foreground">Train is in-sample; validation is the chronological out-of-sample check; the test partition stays locked unless the experiment spent it.</div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
