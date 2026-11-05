// Forward Testing: the paper host (execution authority) and every deployment: status, start, equity, cash,
// positions, pending orders, fills, performance with the backtest's metric definitions, drawdown, execution logs,
// reconciliation against the backtest engine, pause/resume (mock host) and the ptrade export bundle.
import { FastForward, Pause, Play, RefreshCw, Scale } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import type { Reconciliation, Snapshot } from "@/api/client";
import { useAdvance, useBundle, useDeployments, useHost, useReconcile, useSetStatus, useSnapshot, useSync } from "@/api/hooks";
import { MetricGrid, MonthlyHeatmap } from "@/components/Charts";
import { DataTable, type Col } from "@/components/DataTable";
import { LineChart, PALETTE } from "@/components/LineChart";
import { Sparkline } from "@/components/Sparkline";
import { Empty, ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge, Stat, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, Input, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { HEADLINE } from "@/lib/metrics";
import { Link, navigate } from "@/lib/router";

type Pos = Snapshot["positions"][number];
type Fill = Snapshot["fills"][number];
type Order = Snapshot["orders"][number];

const posCols: Col<Pos>[] = [
  { id: "symbol", header: "Symbol", value: (r) => r.symbol, cell: (r) => <span className="num font-medium">{r.symbol}</span> },
  { id: "qty", header: "Qty", align: "right", value: (r) => r.quantity, cell: (r) => fmt.shares(r.quantity) },
  { id: "close", header: "Mark", align: "right", value: (r) => r.close, cell: (r) => fmt.price(r.close) },
  { id: "value", header: "Value", align: "right", value: (r) => r.value, cell: (r) => fmt.usd(r.value) },
  { id: "weight", header: "Weight", align: "right", value: (r) => r.weight, cell: (r) => fmt.pct(r.weight, 1, false) },
];
const fillCols: Col<Fill>[] = [
  { id: "date", header: "Date", value: (r) => r.timestamp, cell: (r) => <span className="num">{r.timestamp}</span> },
  { id: "symbol", header: "Symbol", value: (r) => r.symbol, cell: (r) => <span className="num font-medium">{r.symbol}</span> },
  { id: "kind", header: "Kind", value: (r) => r.kind },
  { id: "qty", header: "Qty", align: "right", value: (r) => r.quantity, cell: (r) => <span className={fmt.tone(r.quantity)}>{fmt.shares(r.quantity)}</span> },
  { id: "price", header: "Price", align: "right", value: (r) => r.price, cell: (r) => fmt.price(r.price) },
  { id: "com", header: "Commission", align: "right", value: (r) => r.commission, cell: (r) => fmt.usd(r.commission, { cents: true }) },
];
const orderCols: Col<Order>[] = [
  { id: "rank", header: "#", align: "right", value: (r) => r.rank },
  { id: "symbol", header: "Symbol", value: (r) => r.symbol, cell: (r) => <span className="num font-medium">{r.symbol}</span> },
  { id: "size", header: "Size (of equity)", align: "right", value: (r) => r.size, cell: (r) => <span className={fmt.tone(r.size)}>{fmt.pct(r.size)}</span> },
  { id: "eligible", header: "Eligible", value: (r) => r.eligible, cell: (r) => <span className="num">{r.eligible}</span> },
  { id: "renew", header: "Held", value: (r) => (r.renew ? "yes" : "no") },
  { id: "status", header: "Status", value: (r) => r.status },
];

function HostPanel() {
  const host = useHost();
  const advance = useAdvance();
  const [to, setTo] = useState("");
  const go = (b: { to?: string; sessions?: number }) => advance.mutate(b, {
    onSuccess: (r) => toast.success(`Host ran ${r.sessions.length} session(s); clock ${r.clock}`), onError: (e) => toast.error(e.message),
  });
  const h = host.data;
  return (
    <Panel title="Paper host" actions={h && <PeriodBadge period={h.label} />} info="The execution authority: it runs the deployed plug-ins each session and keeps the append-only record.">
      {host.isLoading ? <Loading /> : host.error ? <ErrorState error={host.error} /> : h && (
        <div className="space-y-3">
          <KV rows={[
            ["Host", h.target === "mock" ? "Mock of ptrade's basket host (in-workspace)" : h.target],
            ["Clock", <span className="num">{h.clock ?? "not started (starts with the first deployment)"}</span>],
            ["Data", <span className="num">{h.dataset} · {h.first_session} → {h.last_session}</span>],
            ["Books", <span className="num">{h.books.length} registered</span>],
            ["ptrade API", h.ptrade_api ? "configured" : "disabled in this workspace (it serves HOLDOUT/FWD evidence)"],
          ]} />
          <div className="flex flex-wrap items-center gap-1.5">
            {[1, 5, 21, 63].map((n) => (
              <Button key={n} size="sm" variant="outline" className="h-7" disabled={advance.isPending || !h.books.length} onClick={() => go({ sessions: n })} data-action={`advance-${n}`}>
                <FastForward className="size-3.5" />+{n}
              </Button>
            ))}
            <Input type="date" className="num h-7 w-36 text-xs" value={to} min={h.clock ?? h.first_session} max={h.last_session} onChange={(e) => setTo(e.target.value)} aria-label="advance to" />
            <Button size="sm" className="h-7" disabled={advance.isPending || !to || !h.books.length} onClick={() => go({ to })}>Run to date</Button>
          </div>
          {advance.isPending && <div className="text-2xs text-muted-foreground">Running sessions: each re-runs every active book from its start, as ptrade does.</div>}
          <div>
            <div className="mb-1 text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Run log</div>
            <div className="max-h-40 overflow-auto rounded-md border">
              <table className="w-full text-2xs">
                <tbody>
                  {h.run_log.map((l) => (
                    <tr key={l.id} className="border-b last:border-0">
                      <td className="num px-2 py-0.5">{l.asof}</td>
                      <td className={l.status === "ok" ? "text-pos" : "text-warn"}>{l.status}</td>
                      <td className="num truncate px-2 text-muted-foreground" title={l.details}>{summarize(l.details)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!h.run_log.length && <div className="p-2 text-2xs text-muted-foreground">No sessions run yet.</div>}
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function summarize(details: string): string {
  try {
    const d = JSON.parse(details) as { books: Record<string, { equity?: number; error?: string; trades?: number }> };
    return Object.entries(d.books).map(([k, v]) => (v.error ? `${k}: ERROR ${v.error}` : `${k}: ${fmt.num(v.equity, 4)} (${v.trades} trades)`)).join(" · ");
  } catch {
    return details;
  }
}

function StatusDialog({ id, current, open, onOpenChange }: { id: string; current: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [reason, setReason] = useState("");
  const set = useSetStatus();
  const next = current === "paused" ? "active" : "paused";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>{next === "paused" ? "Pause" : "Resume"} this book</DialogTitle>
        <DialogDescription>A reason is required and is recorded in the host's model events. A paused book is skipped; on resume, the host re-runs it from its start and appends the missed days as late days.</DialogDescription>
        <Input placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="reason" />
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button size="sm" disabled={!reason.trim() || set.isPending}
            onClick={() => set.mutate({ id, status: next, reason }, { onSuccess: () => { toast.success(`Book ${next}`); onOpenChange(false); setReason(""); }, onError: (e) => toast.error(e.message) })}>
            Confirm
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Detail({ id }: { id: string }) {
  const snap = useSnapshot(id);
  const sync = useSync();
  const reconcile = useReconcile();
  const [rec, setRec] = useState<Reconciliation | null>(null);
  const [statusOpen, setStatusOpen] = useState(false);
  const [tab, setTab] = useState("performance");
  const bundle = useBundle(id, tab === "bundle");
  const lines = useMemo(() => {
    const e = snap.data?.equity ?? [];
    return {
      eq: [{ key: "eq", name: "Equity", color: PALETTE[0], points: e.map((x) => ({ time: x.date, value: x.equity })) }],
      dd: [{ key: "dd", name: "Drawdown", color: "#e66767", area: true, invert: true, points: e.map((x) => ({ time: x.date, value: x.drawdown })) }],
    };
  }, [snap.data]);
  if (snap.isLoading) return <Loading rows={8} />;
  if (snap.error) return <ErrorState error={snap.error} retry={snap.refetch} />;
  const s = snap.data!;
  const d = s.deployment;
  const hostStatus = d.host_status ?? d.status;
  return (
    <div className="space-y-3" data-deployment={id}>
      <PageHeader
        title={<span className="flex flex-wrap items-center gap-2">{d.name || d.book_id}<StatusBadge status={hostStatus} /><PeriodBadge period={d.target === "mock" ? "SIMULATED" : "FORWARD"} /></span>}
        sub={<span className="num">{d.book_id} · {d.strategy}@{d.strategy_version} · start {d.start_date ?? d.requested_start} · last session {d.last_asof ?? "—"} · synced {fmt.utc(d.last_sync_utc)}</span>}
        actions={<>
          <Button size="sm" variant="outline" disabled={d.target !== "mock" || sync.isPending} onClick={() => sync.mutate(id, { onSuccess: (r) => toast.success(`Synced: ${JSON.stringify(r)}`), onError: (e) => toast.error(e.message) })}><RefreshCw className="size-3.5" />Sync</Button>
          <Button size="sm" variant="outline" disabled={!d.last_asof || reconcile.isPending} data-action="reconcile"
            onClick={() => reconcile.mutate(id, { onSuccess: (r) => { setRec(r); toast[r.identical ? "success" : "warning"](r.identical ? "Forward matches the backtest engine exactly" : "Forward and backtest differ"); }, onError: (e) => toast.error(e.message) })}>
            <Scale className="size-3.5" />Reconcile
          </Button>
          <Button size="sm" variant="outline" disabled={d.target !== "mock"} onClick={() => setStatusOpen(true)}>
            {hostStatus === "paused" ? <><Play className="size-3.5" />Resume</> : <><Pause className="size-3.5" />Pause</>}
          </Button>
          {d.source_run_id && <Button size="sm" variant="ghost" onClick={() => navigate(`/runs/${d.source_run_id}`)}>Source run</Button>}
        </>} />
      {d.error && <ErrorState error={d.error} />}
      {rec && (
        <div className={`rounded-md border px-3 py-2 text-xs ${rec.identical ? "border-pos/40 bg-pos/5" : "border-warn/40 bg-warn/5"}`} data-reconciliation={rec.identical ? "identical" : "differs"}>
          Reconciliation {rec.window[0]} → {rec.window[1]} on <span className="num">{rec.dataset}</span>: {rec.compared_days} days compared, max |Δ equity index| <span className="num">{fmt.num(rec.max_abs_equity_diff, 12)}</span>,
          {" "}{rec.mismatched_days} mismatched days; trades {rec.forward_trades} forward vs {rec.backtest_trades} backtest ({rec.trades_identical ? "identical" : "different"}).
        </div>
      )}
      <div className="grid grid-cols-2 gap-1.5 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Portfolio value" value={fmt.usd(s.equity_value)} />
        <Stat label="Since start" value={fmt.pct(s.equity_value !== null ? s.equity_value / s.capital - 1 : null)} tone={fmt.tone(s.equity_value !== null ? s.equity_value / s.capital - 1 : null)} />
        <Stat label="Cash" value={fmt.usd(s.cash)} />
        <Stat label="Positions" value={fmt.int(s.positions.length)} />
        <Stat label="Current drawdown" value={fmt.pct(s.current_drawdown)} tone={s.current_drawdown ? "text-neg" : ""} />
        <Stat label="Pending orders" value={fmt.int(s.orders.length)} hint="Submitted at the last close; eligible at the next session." />
        <Stat label="Sessions" value={fmt.int(s.equity.length)} sub={d.start_date ? `from ${d.start_date}` : undefined} />
      </div>
      {s.equity.length === 0 ? (
        <Empty title={d.target === "mock" ? "No sessions yet" : "Exported: awaiting an operator"}>
          {d.target === "mock" ? "Advance the paper host to run this book." : "The ptrade bundle is under the Bundle tab; nothing is read until it is applied and the API is configured."}
        </Empty>
      ) : (
        <MetricGrid metrics={s.metrics} keys={HEADLINE} />
      )}
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          {[["performance", "Performance"], ["positions", "Positions"], ["orders", "Orders"], ["fills", "Fills"], ["logs", "Execution log"], ["bundle", "Deployment"]].map(([k, l]) => <TabsTrigger key={k} value={k}>{l}</TabsTrigger>)}
        </TabsList>
        <TabsContent value="performance" className="space-y-3">
          <div className="grid gap-3 xl:grid-cols-[2fr_1fr]">
            <Panel title="Equity"><LineChart name="fwd-equity" lines={lines.eq} unit="usd" height={280} /></Panel>
            <Panel title="Drawdown"><LineChart name="fwd-dd" lines={lines.dd} unit="pct" height={280} /></Panel>
          </div>
          <Panel title="Monthly returns"><MonthlyHeatmap rows={s.monthly ?? []} /></Panel>
        </TabsContent>
        <TabsContent value="positions"><Panel title={`Positions after ${d.last_asof ?? "—"}`}><DataTable name={`${id}-positions`} rows={s.positions} cols={posCols} empty="Flat" /></Panel></TabsContent>
        <TabsContent value="orders"><Panel title="Pending orders (next session)"><DataTable name={`${id}-orders`} rows={s.orders} cols={orderCols} empty="No pending orders" /></Panel></TabsContent>
        <TabsContent value="fills"><Panel title="Fills reported by the host"><DataTable name={`${id}-fills`} rows={s.fills} cols={fillCols} /></Panel></TabsContent>
        <TabsContent value="logs">
          <Panel title="Sync and execution log">
            <div className="max-h-[480px] overflow-auto rounded-md border">
              <table className="w-full text-xs">
                <tbody>
                  {s.logs.map((l, i) => (
                    <tr key={i} className="border-b align-top last:border-0">
                      <td className="num px-2 py-1 whitespace-nowrap text-muted-foreground">{fmt.utc(l.at_utc)}</td>
                      <td className={l.level === "info" ? "text-muted-foreground" : l.level === "warn" ? "text-warn" : "text-neg"}>{l.level}</td>
                      <td className="px-2 py-1">{l.message}{l.details && <div className="num text-2xs break-all text-muted-foreground">{l.details}</div>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </TabsContent>
        <TabsContent value="bundle" className="space-y-3">
          <div className="grid gap-3 xl:grid-cols-2">
            <Panel title="Deployment (frozen)">
              <KV rows={[
                ["Deployment key", <span className="num text-2xs">{d.deployment_key}</span>],
                ["Book / model id", <span className="num text-2xs">{d.book_id} · {d.model_id}</span>],
                ["Target", d.target === "mock" ? "mock host (SIMULATED)" : "ptrade (export bundle; human applies it)"],
                ["Universe", <span className="num text-2xs">{d.spec.universe.join(", ")}</span>],
                ["Params", <span className="num text-2xs">{JSON.stringify(d.spec.params)}</span>],
                ["Execution", <span className="num text-2xs">{JSON.stringify(d.spec.execution)}</span>],
                ["Capital", fmt.usd(d.spec.initial_capital)],
                ["Bundle", <span className="num text-2xs break-all">{d.bundle_dir}</span>],
              ]} />
            </Panel>
            <Panel title="Operator steps (ptrade)">
              {bundle.isLoading ? <Loading /> : bundle.data && <pre className="max-h-[360px] overflow-auto text-[11px] leading-snug whitespace-pre-wrap">{bundle.data.operator}</pre>}
            </Panel>
          </div>
          {bundle.data && (
            <div className="grid gap-3 xl:grid-cols-2">
              <Panel title="books.toml entry"><pre className="num text-[11px]">{bundle.data.books_entry}</pre></Panel>
              <Panel title="Pre-registration stub"><pre className="max-h-[360px] overflow-auto text-[11px] whitespace-pre-wrap">{bundle.data.protocol}</pre></Panel>
            </div>
          )}
        </TabsContent>
      </Tabs>
      <StatusDialog id={id} current={hostStatus} open={statusOpen} onOpenChange={setStatusOpen} />
    </div>
  );
}

export function ForwardPage({ id }: { id?: string }) {
  const deps = useDeployments();
  if (id) return <Detail id={id} />;
  return (
    <div className="space-y-3">
      <PageHeader title="Forward Testing" sub="Deploy a finished backtest from its run page. The paper host executes every book; Market Signal Research reads the results back and reconciles them with the backtest engine." />
      <div className="grid gap-3 xl:grid-cols-[1fr_420px]">
        <Panel title="Deployments">
          {deps.isLoading ? <Loading rows={5} /> : deps.error ? <ErrorState error={deps.error} /> : (deps.data ?? []).length === 0 ? (
            <Empty title="No deployments">Open a finished run in <Link to="/experiments" className="text-primary underline">Experiment History</Link> and use “Deploy forward”.</Empty>
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-xs" data-deployments>
                <thead><tr className="border-b text-2xs text-muted-foreground">
                  {["Deployment", "Strategy", "Target", "Status", "Start", "Last", "Equity", "Since start", "DD", "Pos", ""].map((h) => <th key={h} className="h-7 px-2 text-left font-medium">{h}</th>)}
                </tr></thead>
                <tbody>
                  {deps.data!.map((x) => {
                    const d = x.deployment;
                    return (
                      <tr key={d.deployment_id} className="cursor-pointer border-b last:border-0 hover:bg-muted/50" onClick={() => navigate(`/forward/${d.deployment_id}`)}>
                        <td className="px-2 py-1"><div className="font-medium">{d.name || d.book_id}</div><div className="num text-2xs text-muted-foreground">{d.book_id}</div></td>
                        <td className="num px-2">{d.strategy}@{d.strategy_version}</td>
                        <td className="px-2"><PeriodBadge period={d.target === "mock" ? "SIMULATED" : "FORWARD"} /></td>
                        <td className="px-2"><StatusBadge status={d.host_status ?? d.status} /></td>
                        <td className="num px-2">{d.start_date ?? d.requested_start}</td>
                        <td className="num px-2">{d.last_asof ?? "—"}</td>
                        <td className="num px-2 text-right">{fmt.usd(x.equity_value, { compact: true })}</td>
                        <td className={`num px-2 text-right ${fmt.tone(x.since_start)}`}>{fmt.pct(x.since_start)}</td>
                        <td className="num px-2 text-right text-neg">{fmt.pct(x.current_drawdown)}</td>
                        <td className="num px-2 text-right">{x.n_positions}</td>
                        <td className="px-2"><Sparkline points={x.sparkline} width={80} height={22} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <HostPanel />
      </div>
    </div>
  );
}
