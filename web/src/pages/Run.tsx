// One experiment: headline metrics, equity vs benchmark, drawdown, rolling performance, monthly heatmap, exposure,
// trades / orders / fills / positions / logs, and provenance (config, data version, code hashes). From here a run
// can be reproduced, cloned, compared or deployed to forward paper testing.
import { ProgressBar } from "@/components/Progress";
import { Copy, GitCompare, Repeat, Rocket } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import type { EquityRow, Row } from "@/api/client";
import { useDeploy, useReproduce, useRolling, useRun, useRunEquity, useRunLogs, useRunTable } from "@/api/hooks";
import { MetricGrid, MonthlyHeatmap } from "@/components/Charts";
import { ChoiceSelect, Field } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { LineChart, PALETTE, type Line } from "@/components/LineChart";
import { ChartSkeleton, Empty, ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, Input, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { HEADLINE } from "@/lib/metrics";
import { Link, navigate } from "@/lib/router";

const RELATIVE = ["benchmark_total_return", "excess_return", "beta", "alpha", "correlation", "information_ratio",
  "tracking_error", "fill_count", "avg_trade_return", "avg_holding_periods", "total_costs", "cost_drag"];

const n = (r: Row, k: string) => (typeof r[k] === "number" ? (r[k] as number) : null);
const s = (r: Row, k: string) => (r[k] === null || r[k] === undefined ? "" : String(r[k]));

const tradeCols: Col<Row>[] = [
  { id: "symbol", header: "Symbol", value: (r) => s(r, "symbol"), cell: (r) => <span className="num font-medium">{s(r, "symbol")}</span> },
  { id: "side", header: "Side", value: (r) => s(r, "side") },
  { id: "entry", header: "Entry", value: (r) => s(r, "entry_date"), cell: (r) => <span className="num">{s(r, "entry_date")}</span> },
  { id: "exit", header: "Exit", value: (r) => s(r, "exit_date"), cell: (r) => <span className="num">{s(r, "exit_date")}</span> },
  { id: "qty", header: "Qty", align: "right", value: (r) => n(r, "quantity"), cell: (r) => fmt.shares(n(r, "quantity")) },
  { id: "ep", header: "Entry px", align: "right", value: (r) => n(r, "entry_price"), cell: (r) => fmt.price(n(r, "entry_price")) },
  { id: "xp", header: "Exit px", align: "right", value: (r) => n(r, "exit_price"), cell: (r) => fmt.price(n(r, "exit_price")) },
  { id: "pnl", header: "P&L", align: "right", value: (r) => n(r, "pnl"), cell: (r) => <span className={fmt.tone(n(r, "pnl"))}>{fmt.usd(n(r, "pnl"), { cents: true })}</span> },
  { id: "ret", header: "Return", align: "right", value: (r) => n(r, "return"), cell: (r) => <span className={fmt.tone(n(r, "return"))}>{fmt.pct(n(r, "return"))}</span> },
  { id: "hold", header: "Held", align: "right", value: (r) => n(r, "holding_periods"), cell: (r) => fmt.int(n(r, "holding_periods")) },
];
const orderCols: Col<Row>[] = [
  { id: "id", header: "Order", value: (r) => s(r, "order_id"), cell: (r) => <span className="num text-muted-foreground">{s(r, "order_id")}</span> },
  { id: "created", header: "Decided", value: (r) => s(r, "created_at"), cell: (r) => <span className="num">{s(r, "created_at")}</span> },
  { id: "symbol", header: "Symbol", value: (r) => s(r, "symbol"), cell: (r) => <span className="num font-medium">{s(r, "symbol")}</span> },
  { id: "type", header: "Type", value: (r) => s(r, "order_type") },
  { id: "tif", header: "TIF", value: (r) => s(r, "tif") },
  { id: "qty", header: "Qty", align: "right", value: (r) => n(r, "quantity"), cell: (r) => fmt.shares(n(r, "quantity")) },
  { id: "filled", header: "Filled", align: "right", value: (r) => n(r, "filled_quantity"), cell: (r) => fmt.shares(n(r, "filled_quantity")) },
  { id: "avg", header: "Avg px", align: "right", value: (r) => n(r, "avg_fill_price"), cell: (r) => fmt.price(n(r, "avg_fill_price")) },
  { id: "limit", header: "Limit", align: "right", value: (r) => n(r, "limit_price"), cell: (r) => fmt.price(n(r, "limit_price")), hidden: true },
  { id: "stop", header: "Stop", align: "right", value: (r) => n(r, "stop_price"), cell: (r) => fmt.price(n(r, "stop_price")), hidden: true },
  { id: "status", header: "Status", value: (r) => s(r, "status"), cell: (r) => <span className={s(r, "status") === "filled" ? "text-pos" : ["rejected", "expired", "cancelled"].includes(s(r, "status")) ? "text-warn" : ""}>{s(r, "status")}</span> },
  { id: "tag", header: "Tag", value: (r) => s(r, "tag") },
  { id: "reason", header: "Reason", value: (r) => s(r, "reason"), cell: (r) => <span className="text-muted-foreground">{s(r, "reason")}</span> },
];
const fillCols: Col<Row>[] = [
  { id: "id", header: "Fill", value: (r) => s(r, "fill_id"), cell: (r) => <span className="num text-muted-foreground">{s(r, "fill_id")}</span> },
  { id: "date", header: "Date", value: (r) => s(r, "timestamp"), cell: (r) => <span className="num">{s(r, "timestamp")}</span> },
  { id: "order", header: "Order", value: (r) => s(r, "order_id"), cell: (r) => <span className="num text-muted-foreground">{s(r, "order_id")}</span>, hidden: true },
  { id: "symbol", header: "Symbol", value: (r) => s(r, "symbol"), cell: (r) => <span className="num font-medium">{s(r, "symbol")}</span> },
  { id: "qty", header: "Qty", align: "right", value: (r) => n(r, "quantity"), cell: (r) => <span className={fmt.tone(n(r, "quantity"))}>{fmt.shares(n(r, "quantity"))}</span> },
  { id: "price", header: "Price", align: "right", value: (r) => n(r, "price"), cell: (r) => fmt.price(n(r, "price")) },
  { id: "ref", header: "Reference", align: "right", value: (r) => n(r, "reference_price"), cell: (r) => fmt.price(n(r, "reference_price")) },
  { id: "com", header: "Commission", align: "right", value: (r) => n(r, "commission"), cell: (r) => fmt.usd(n(r, "commission"), { cents: true }) },
  { id: "slip", header: "Slippage $", align: "right", value: (r) => n(r, "slippage_cost"), cell: (r) => fmt.usd(n(r, "slippage_cost"), { cents: true }) },
  { id: "reason", header: "Kind", value: (r) => s(r, "reason") },
];
const holdCols: Col<Row>[] = [
  { id: "symbol", header: "Symbol", value: (r) => s(r, "symbol"), cell: (r) => <span className="num font-medium">{s(r, "symbol")}</span> },
  { id: "qty", header: "Qty", align: "right", value: (r) => n(r, "quantity"), cell: (r) => fmt.shares(n(r, "quantity")) },
  { id: "cost", header: "Avg cost", align: "right", value: (r) => n(r, "avg_cost"), cell: (r) => fmt.price(n(r, "avg_cost")) },
  { id: "close", header: "Mark", align: "right", value: (r) => n(r, "close"), cell: (r) => fmt.price(n(r, "close")) },
  { id: "mv", header: "Value", align: "right", value: (r) => n(r, "market_value"), cell: (r) => fmt.usd(n(r, "market_value")) },
  { id: "w", header: "Weight", align: "right", value: (r) => n(r, "weight"), cell: (r) => fmt.pct(n(r, "weight"), 1, false) },
  { id: "md", header: "Mark date", value: (r) => s(r, "mark_date"), cell: (r) => <span className="num text-muted-foreground">{s(r, "mark_date")}</span> },
];

function TablePanel({ id, name, cols, enabled }: { id: string; name: string; cols: Col<Row>[]; enabled: boolean }) {
  const t = useRunTable(id, name, enabled);
  if (t.isLoading) return <Loading rows={6} />;
  if (t.error) return <ErrorState error={t.error} />;
  return <DataTable name={`${id}-${name}`} rows={t.data?.rows ?? []} cols={cols} maxHeight={560} />;
}

function Holdings({ id, dates, enabled }: { id: string; dates: string[]; enabled: boolean }) {
  const [date, setDate] = useState<string>("");
  const t = useRunTable(id, "holdings", enabled);
  const d = date || dates.at(-1) || "";
  const rows = useMemo(() => (t.data?.rows ?? []).filter((r) => r.timestamp === d), [t.data, d]);
  if (t.isLoading) return <Loading rows={6} />;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-muted-foreground">Positions after the close of</span>
        <Input type="date" className="num h-7 w-40 text-xs" value={d} min={dates[0]} max={dates.at(-1)} onChange={(e) => setDate(e.target.value)} aria-label="holdings date" />
      </div>
      <DataTable name={`${id}-holdings-${d}`} rows={rows} cols={holdCols} empty="No positions on this date" />
    </div>
  );
}

function DeployDialog({ runId, defaultStart, open, onOpenChange }: { runId: string; defaultStart: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [start, setStart] = useState(defaultStart);
  const [target, setTarget] = useState<"mock" | "ptrade">("mock");
  const [name, setName] = useState("");
  const deploy = useDeploy();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Deploy to forward paper testing</DialogTitle>
        <DialogDescription>
          Freezes this run's strategy, version, parameters, universe and execution assumptions into a deployment. The paper
          host executes it; Market Signal Research only reads the results back. The mock host is SIMULATED on ≤ 2025 data; the ptrade
          target only writes an export bundle for an operator to review and apply.
        </DialogDescription>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Start date"><Input type="date" className="num h-8 text-xs" value={start} onChange={(e) => setStart(e.target.value)} aria-label="deploy start" /></Field>
          <Field label="Target">
            <ChoiceSelect value={target} onChange={(v) => setTarget(v as "mock" | "ptrade")} aria-label="target"
              options={[{ value: "mock", label: "Mock host (SIMULATED)" }, { value: "ptrade", label: "ptrade (export bundle)" }]} />
          </Field>
          <Field label="Name" className="col-span-2"><Input className="h-8 text-xs" value={name} onChange={(e) => setName(e.target.value)} placeholder="optional" aria-label="deploy name" /></Field>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button size="sm" disabled={deploy.isPending} data-action="deploy"
            onClick={() => deploy.mutate({ from_run_id: runId, start_date: start, name, target }, {
              onSuccess: (d) => { toast.success(d.created ? "Deployed" : "Already deployed: the same configuration"); navigate(`/forward/${d.deployment_id}`); },
              onError: (e) => toast.error(`${e.message}`),
            })}>
            <Rocket className="size-3.5" />Deploy
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function RunPage({ id }: { id: string }) {
  const run = useRun(id);
  const done = run.data?.run.status === "done";
  const eq = useRunEquity(id, done);
  const [tab, setTab] = useState("performance");
  const [window, setWindow] = useState(63);
  const rolling = useRolling(id, window, done && tab === "performance");
  const monthly = useRunTable(id, "monthly", done);
  const logs = useRunLogs(id, done && tab === "logs");
  const reproduce = useReproduce();
  const [deployOpen, setDeployOpen] = useState(false);

  const lines = useMemo(() => {
    const rows = eq.data ?? [];
    const eqLines: Line[] = [{ key: "equity", name: "Strategy", color: PALETTE[0], points: rows.map((r) => ({ time: r.date, value: r.equity })) }];
    if (rows.some((r) => r.benchmark !== null)) {
      eqLines.push({ key: "bench", name: `Benchmark (${run.data?.config.benchmark})`, color: "#9b9a94", dashed: true,
        points: rows.filter((r) => r.benchmark !== null).map((r) => ({ time: r.date, value: r.benchmark as number })) });
    }
    const dd: Line[] = [{ key: "dd", name: "Drawdown", color: "#e66767", area: true, invert: true, points: rows.map((r) => ({ time: r.date, value: r.drawdown })) }];
    const bdd = benchmarkDrawdown(rows);
    if (bdd.length) dd.push({ key: "bdd", name: "Benchmark drawdown", color: "#9b9a94", dashed: true, points: bdd });
    const expo: Line[] = [
      { key: "gross", name: "Gross exposure", color: PALETTE[2], points: rows.map((r) => ({ time: r.date, value: r.gross_exposure })) },
      { key: "net", name: "Net exposure", color: PALETTE[3], dashed: true, points: rows.map((r) => ({ time: r.date, value: r.net_exposure })) },
    ];
    return { eqLines, dd, expo };
  }, [eq.data, run.data]);

  const rollLines = useMemo(() => {
    const rows = rolling.data?.rows ?? [];
    const mk = (k: string, name: string, color: string, src = rows, dashed = false): Line => ({
      key: `${k}-${name}`, name, color, dashed, points: src.filter((r) => typeof r[k] === "number").map((r) => ({ time: String(r.date), value: r[k] as number })),
    });
    const b = rolling.data?.benchmark ?? [];
    return {
      sharpe: [mk("sharpe", "Strategy", PALETTE[0]), ...(b.length ? [mk("sharpe", "Benchmark", "#9b9a94", b, true)] : [])],
      ret: [mk("return", "Strategy", PALETTE[0]), ...(b.length ? [mk("return", "Benchmark", "#9b9a94", b, true)] : [])],
      vol: [mk("volatility", "Strategy", PALETTE[1]), ...(b.length ? [mk("volatility", "Benchmark", "#9b9a94", b, true)] : [])],
    };
  }, [rolling.data]);

  if (run.isLoading) return <Loading rows={10} />;
  if (run.error) return <ErrorState error={run.error} retry={run.refetch} />;
  const d = run.data!;
  const r = d.run;
  const cfg = d.config;
  const dates = (eq.data ?? []).map((x) => x.date);
  return (
    <div className="space-y-3" data-run={id}>
      <PageHeader
        title={<span className="flex flex-wrap items-center gap-2">{r.name || r.strategy}<StatusBadge status={r.status} />{r.periods.map((p) => <PeriodBadge key={p} period={p} />)}<PeriodBadge period="EXPLORATORY" /></span>}
        sub={<span className="num">{r.strategy}@{r.strategy_version} · {r.dataset}@{fmt.bytesHash(r.dataset_version, 8)} · {r.start} → {r.end} · {r.frequency} · {fmt.usd(cfg.initial_capital)} · run {r.run_id}</span>}
        actions={<>
          <Button size="sm" variant="outline" onClick={() => navigate(`/backtest?from=${r.run_id}`)}><Copy className="size-3.5" />Clone config</Button>
          <Button size="sm" variant="outline" disabled={!done} onClick={() => navigate(`/results/compare?ids=${r.run_id}`)}><GitCompare className="size-3.5" />Compare</Button>
          <Button size="sm" variant="outline" disabled={!done || reproduce.isPending} data-action="reproduce"
            onClick={() => reproduce.mutate(r.run_id, { onSuccess: () => toast.success("Reproduction queued: it appears in Experiments"), onError: (e) => toast.error(e.message) })}>
            <Repeat className="size-3.5" />Reproduce
          </Button>
          <Button size="sm" disabled={!done} onClick={() => setDeployOpen(true)} data-action="open-deploy"><Rocket className="size-3.5" />Deploy forward</Button>
        </>} />
      {r.kind === "reproduction" && r.reproduction && (
        <div className={`rounded-md border px-3 py-2 text-xs ${r.reproduction.identical ? "border-pos/40 bg-pos/5" : "border-neg/40 bg-neg/5"}`}>
          Reproduction of <Link to={`/runs/${r.parent_run_id}`} className="num text-primary underline">{r.parent_run_id}</Link>:{" "}
          {r.reproduction.identical ? "every compared artifact is byte-identical." : `differs in ${r.reproduction.differing_artifacts.join(", ")}.`}
          {r.reproduction.code_changed.length > 0 && ` Code changed since the original: ${r.reproduction.code_changed.join(", ")}.`}
        </div>
      )}
      {(r.status === "queued" || r.status === "running") && <ProgressBar value={null} label={r.status === "queued" ? "Queued" : "Backtest running"} detail="the engine reports no session count; this page refreshes when it finishes" startedAt={r.started_at_utc} />}
      {r.status === "failed" && <ErrorState title="This run failed" error={`${r.error}${d.error ? `\n\n${d.error.slice(-4000)}` : ""}`} />}
      {done && (
        <>
          <MetricGrid metrics={d.metrics} keys={HEADLINE} />
          <MetricGrid metrics={d.metrics} keys={RELATIVE} />
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              {[["performance", "Performance"], ["trades", "Trades"], ["orders", "Orders"], ["fills", "Fills"], ["positions", "Positions"], ["logs", "Logs"], ["provenance", "Provenance"]].map(([k, l]) => (
                <TabsTrigger key={k} value={k}>{l}</TabsTrigger>
              ))}
            </TabsList>
            <TabsContent value="performance" className="space-y-3">
              <Panel title="Equity vs benchmark">{eq.isLoading ? <ChartSkeleton /> : <LineChart name="equity" lines={lines.eqLines} unit="usd" height={320} />}</Panel>
              <div className="grid gap-3 xl:grid-cols-2">
                <Panel title="Drawdown">{eq.isLoading ? <ChartSkeleton height={220} /> : <LineChart name="drawdown" lines={lines.dd} unit="pct" height={220} />}</Panel>
                <Panel title="Exposure">{eq.isLoading ? <ChartSkeleton height={220} /> : <LineChart name="exposure" lines={lines.expo} unit="pct" height={220} />}</Panel>
              </div>
              <Panel title="Rolling performance" actions={
                <ChoiceSelect className="h-7 w-32" value={String(window)} onChange={(v) => setWindow(Number(v))} aria-label="rolling window"
                  options={[{ value: "21", label: "21 sessions" }, { value: "63", label: "63 sessions" }, { value: "126", label: "126 sessions" }, { value: "252", label: "252 sessions" }]} />}>
                {rolling.isLoading ? <ChartSkeleton height={200} /> : (
                  <div className="grid gap-3 xl:grid-cols-3">
                    <div><div className="mb-1 text-2xs text-muted-foreground">Return</div><LineChart name="roll-ret" lines={rollLines.ret} unit="pct" height={200} /></div>
                    <div><div className="mb-1 text-2xs text-muted-foreground">Sharpe</div><LineChart name="roll-sharpe" lines={rollLines.sharpe} unit="ratio" height={200} /></div>
                    <div><div className="mb-1 text-2xs text-muted-foreground">Volatility</div><LineChart name="roll-vol" lines={rollLines.vol} unit="pct" height={200} /></div>
                  </div>
                )}
              </Panel>
              <Panel title="Monthly returns">
                {monthly.isLoading ? <Loading /> : <MonthlyHeatmap rows={(monthly.data?.rows ?? []).map((m) => ({ year: Number(m.year), month: Number(m.month), ret: m.ret as number }))} />}
              </Panel>
            </TabsContent>
            <TabsContent value="trades"><Panel title="Closed round trips (FIFO)"><TablePanel id={id} name="trades" cols={tradeCols} enabled={tab === "trades"} /></Panel></TabsContent>
            <TabsContent value="orders"><Panel title="Orders and their outcome"><TablePanel id={id} name="orders" cols={orderCols} enabled={tab === "orders"} /></Panel></TabsContent>
            <TabsContent value="fills"><Panel title="Fills"><TablePanel id={id} name="fills" cols={fillCols} enabled={tab === "fills"} /></Panel></TabsContent>
            <TabsContent value="positions"><Panel title="Positions"><Holdings id={id} dates={dates} enabled={tab === "positions"} /></Panel></TabsContent>
            <TabsContent value="logs">
              <Panel title="Strategy log">
                {logs.isLoading ? <Loading /> : (logs.data ?? []).length === 0 ? <Empty title="The strategy logged nothing" /> : (
                  <pre className="num max-h-[560px] overflow-auto rounded-md border bg-surface-2 p-2 text-[11px] leading-snug">
                    {logs.data!.map((l) => `${l.timestamp}  ${l.message}`).join("\n")}
                  </pre>
                )}
              </Panel>
            </TabsContent>
            <TabsContent value="provenance" className="space-y-3">
              <div className="grid gap-3 xl:grid-cols-2">
                <Panel title="Reproducibility">
                  {d.manifest && (
                    <KV rows={[
                      ["Label", d.manifest.label], ["Config hash", <span className="num text-2xs">{d.manifest.config_hash}</span>],
                      ["Strategy", <span className="num text-2xs">{d.manifest.strategy.id} v{d.manifest.strategy.version} · {d.manifest.strategy.source_sha256}</span>],
                      ["Engine", <span className="num text-2xs">{d.manifest.engine.source_sha256}{d.manifest.engine.commit ? ` · git ${d.manifest.engine.commit.slice(0, 10)}${d.manifest.engine.src_dirty ? " (dirty)" : ""}` : " · no git commit"}</span>],
                      ["Dataset", <span className="num text-2xs">{d.manifest.dataset.id}@{d.manifest.dataset.version} ({d.manifest.dataset.provider}, ingested {fmt.utc(d.manifest.dataset.ingested_at_utc)})</span>],
                      ["Sources", <span className="num text-2xs">{d.manifest.dataset.sources.join("; ")}</span>],
                      ["Versions", <span className="num text-2xs">{Object.entries(d.manifest.versions).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>],
                      ...Object.entries(d.manifest.artifacts).map(([k, v]) => [k, <span className="num text-2xs">{v.slice(0, 16)}…</span>] as [string, React.ReactNode]),
                    ]} />
                  )}
                </Panel>
                <Panel title="Data report">
                  <pre className="num max-h-[420px] overflow-auto text-[11px] leading-snug">{JSON.stringify(d.data_report, null, 1)}</pre>
                </Panel>
              </div>
              <Panel title="Resolved configuration (config.json)">
                <pre className="num max-h-[420px] overflow-auto text-[11px] leading-snug">{JSON.stringify(cfg, null, 1)}</pre>
              </Panel>
              {d.state && Object.keys(d.state).length > 0 && <Panel title="Final strategy state"><pre className="num text-[11px]">{JSON.stringify(d.state, null, 1)}</pre></Panel>}
            </TabsContent>
          </Tabs>
          <DeployDialog runId={r.run_id} defaultStart={r.end} open={deployOpen} onOpenChange={setDeployOpen} />
        </>
      )}
    </div>
  );
}

function benchmarkDrawdown(rows: EquityRow[]): { time: string; value: number }[] {
  let peak = -Infinity;
  const out: { time: string; value: number }[] = [];
  for (const r of rows) {
    if (r.benchmark === null) continue;
    peak = Math.max(peak, r.benchmark);
    out.push({ time: r.date, value: r.benchmark / peak - 1 });
  }
  return out;
}
