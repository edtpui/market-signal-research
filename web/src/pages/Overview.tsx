// Overview: what exists and its state: experiments, forward deployments, data coverage, strategy library health.
import { AlertTriangle } from "lucide-react";

import type { RunRow } from "@/api/client";
import { useDatasets, useDeployments, useExperiments, useHealth, useHost, useStrategies } from "@/api/hooks";
import { DataTable, type Col } from "@/components/DataTable";
import { Empty, ErrorState, Loading, PageHeader, Panel, PeriodBadge, Stat, StatusBadge } from "@/components/common";
import { Sparkline } from "@/components/Sparkline";
import { Button } from "@/components/ui/button";
import * as fmt from "@/lib/format";
import { Link, navigate } from "@/lib/router";

export const runCols: Col<RunRow>[] = [
  { id: "created", header: "Created", value: (r) => r.created_at_utc, cell: (r) => <span className="num text-muted-foreground">{fmt.utc(r.created_at_utc)}</span> },
  { id: "name", header: "Name", value: (r) => r.name || r.strategy, cell: (r) => <span className="font-medium">{r.name || r.strategy}</span> },
  { id: "strategy", header: "Strategy", value: (r) => `${r.strategy}@${r.strategy_version}`, cell: (r) => <span className="num">{r.strategy}<span className="text-muted-foreground">@{r.strategy_version}</span></span> },
  { id: "dataset", header: "Dataset", value: (r) => `${r.dataset}@${r.dataset_version}`, cell: (r) => <span className="num text-muted-foreground">{r.dataset}@{fmt.bytesHash(r.dataset_version, 6)}</span>, hidden: true },
  { id: "window", header: "Window", value: (r) => `${r.start}..${r.end}`, cell: (r) => <span className="num">{r.start} → {r.end}</span> },
  { id: "freq", header: "Freq", value: (r) => r.frequency, hidden: true },
  { id: "periods", header: "Periods", value: (r) => r.periods.join(","), cell: (r) => <span className="flex gap-1">{r.periods.map((p) => <PeriodBadge key={p} period={p} />)}</span> },
  { id: "status", header: "Status", value: (r) => r.status, cell: (r) => <StatusBadge status={r.status} /> },
  { id: "ret", header: "Total return", align: "right", value: (r) => r.summary?.total_return ?? null, cell: (r) => <span className={fmt.tone(r.summary?.total_return)}>{fmt.pct(r.summary?.total_return)}</span> },
  { id: "cagr", header: "CAGR", align: "right", value: (r) => r.summary?.cagr ?? null, cell: (r) => fmt.pct(r.summary?.cagr) },
  { id: "sharpe", header: "Sharpe", align: "right", value: (r) => r.summary?.sharpe ?? null, cell: (r) => fmt.num(r.summary?.sharpe) },
  { id: "mdd", header: "Max DD", align: "right", value: (r) => r.summary?.max_drawdown ?? null, cell: (r) => <span className="text-neg">{fmt.pct(r.summary?.max_drawdown)}</span> },
  { id: "trades", header: "Trades", align: "right", value: (r) => r.summary?.trade_count ?? null, cell: (r) => fmt.int(r.summary?.trade_count) },
  { id: "kind", header: "Kind", value: (r) => r.kind, cell: (r) => r.kind === "reproduction"
    ? <span className="text-2xs">reproduction of <span className="num">{r.parent_run_id?.slice(0, 15)}</span>{r.reproduction && <span className={r.reproduction.identical ? "ml-1 text-pos" : "ml-1 text-neg"}>{r.reproduction.identical ? "identical" : "DIFFERS"}</span>}</span>
    : <span className="text-2xs text-muted-foreground">backtest</span>, hidden: true },
  { id: "id", header: "Run id", value: (r) => r.run_id, cell: (r) => <span className="num text-2xs text-muted-foreground">{r.run_id}</span>, hidden: true },
];

export function OverviewPage() {
  const exps = useExperiments();
  const deps = useDeployments();
  const ds = useDatasets();
  const st = useStrategies();
  const health = useHealth();
  const host = useHost();
  const runs = exps.data ?? [];
  const counts = (s: string) => runs.filter((r) => r.status === s).length;
  return (
    <div className="space-y-3">
      <PageHeader title="Overview" sub={health.data ? `${health.data.workspace} · bars ${health.data.policy.history_floor} → ${health.data.policy.ceiling}, backtests from ${health.data.policy.backtest_floor}` : undefined}
        actions={<><Button size="sm" onClick={() => navigate("/backtest")}>New backtest</Button><Button size="sm" variant="outline" onClick={() => navigate("/forward")}>Forward testing</Button></>} />
      {(st.data?.errors.length ?? 0) > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-xs" role="alert">
          <AlertTriangle className="mt-0.5 size-4 text-warn" />
          <div>{st.data!.errors.map((e) => <div key={e.file}><span className="num">{e.file}</span>: {e.error}</div>)}</div>
        </div>
      )}
      <div className="grid grid-cols-2 gap-1.5 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Experiments" value={fmt.int(runs.length)} sub={`${counts("done")} done · ${counts("failed")} failed`} />
        <Stat label="Running" value={fmt.int(counts("running") + counts("queued"))} tone={counts("running") ? "text-primary" : ""} />
        <Stat label="Strategies" value={fmt.int(st.data?.strategies.length)} sub={`${new Set(st.data?.strategies.map((s) => s.category)).size} categories`} />
        <Stat label="Datasets cached" value={`${(ds.data ?? []).filter((d) => d.cached).length} / ${ds.data?.length ?? 0}`} />
        <Stat label="Deployments" value={fmt.int(deps.data?.length)} sub={`${(deps.data ?? []).filter((d) => d.deployment.status === "running").length} running`} />
        <Stat label="Paper host" value={host.data?.label ?? "—"} sub={host.data?.clock ? `clock ${host.data.clock}` : "not started"} />
        <Stat label="Data ceiling" value={health.data?.policy.ceiling ?? "—"} hint="Hard maximum: HOLDOUT data (2026-01-01 on) never enters this workspace." />
        <Stat label="ptrade API" value={health.data?.forward.ptrade_api_enabled ? "enabled" : "disabled"} hint="The operational API serves HOLDOUT/FWD evidence; it stays disabled in the dev workspace." />
      </div>
      <div className="grid gap-3 xl:grid-cols-[2fr_1fr]">
        <Panel title="Recent experiments" actions={<Link to="/experiments" className="text-xs text-primary hover:underline">All</Link>}>
          {exps.isLoading ? <Loading /> : exps.error ? <ErrorState error={exps.error} retry={exps.refetch} /> : runs.length === 0
            ? <Empty title="No experiments yet"><Link to="/backtest" className="text-primary underline">Configure a backtest</Link></Empty>
            : <DataTable name="recent-runs" rows={runs.slice(0, 12)} cols={runCols} filter={false} maxHeight={420} onRowClick={(r) => navigate(`/runs/${r.run_id}`)} />}
        </Panel>
        <div className="space-y-3">
          <Panel title="Forward deployments" actions={<Link to="/forward" className="text-xs text-primary hover:underline">Open</Link>}>
            {deps.isLoading ? <Loading /> : (deps.data ?? []).length === 0 ? <Empty title="Nothing deployed">Deploy a finished backtest from its run page.</Empty> : (
              <div className="space-y-1.5">
                {deps.data!.slice(0, 6).map((d) => (
                  <Link key={d.deployment.deployment_id} to={`/forward/${d.deployment.deployment_id}`} className="flex items-center gap-3 rounded-md border px-2.5 py-1.5 hover:bg-accent/50">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium">{d.deployment.name || d.deployment.book_id}</div>
                      <div className="num truncate text-2xs text-muted-foreground">{d.deployment.book_id} · {d.label}</div>
                    </div>
                    <Sparkline points={d.sparkline} />
                    <div className="w-20 text-right">
                      <div className="num text-xs">{fmt.usd(d.equity_value, { compact: true })}</div>
                      <div className={`num text-2xs ${fmt.tone(d.since_start)}`}>{fmt.pct(d.since_start)}</div>
                    </div>
                    <StatusBadge status={d.deployment.host_status ?? d.deployment.status} />
                  </Link>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Market data" actions={<Link to="/data" className="text-xs text-primary hover:underline">Coverage</Link>}>
            {ds.isLoading ? <Loading /> : (
              <div className="space-y-1">
                {(ds.data ?? []).map((d) => (
                  <Link key={d.id} to={`/data/${d.id}`} className="flex items-center justify-between gap-2 rounded px-1 py-0.5 text-xs hover:bg-accent/50">
                    <span className="truncate">{d.id}</span>
                    <span className="num text-2xs text-muted-foreground">
                      {d.current ? `${d.current.first_date} → ${d.current.last_date} · ${fmt.int(d.current.n_symbols)} sym` : "not cached"}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
