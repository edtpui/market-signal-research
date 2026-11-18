// Overview: current research activity across backtests, signal studies and entry-exit studies, the forward
// deployments, the state of the market data and the strategy library's health.
import { AlertTriangle } from "lucide-react";
import { useMemo } from "react";

import { useEEExperiments } from "@/api/entryExit";
import { useDatasets, useDeployments, useExperiments, useHealth, useHost, useStrategies } from "@/api/hooks";
import { useSignalExperiments } from "@/api/signals";
import { DataTable } from "@/components/DataTable";
import { experimentCols } from "@/components/experimentColumns";
import { Empty, ErrorState, Loading, PageHeader, Panel, Stat, StatusBadge } from "@/components/common";
import { Sparkline } from "@/components/Sparkline";
import { Button } from "@/components/ui/button";
import { activity, unify } from "@/lib/experiments";
import * as fmt from "@/lib/format";
import { Link, navigate } from "@/lib/router";

export function OverviewPage() {
  const runs = useExperiments();
  const sig = useSignalExperiments();
  const ee = useEEExperiments();
  const deps = useDeployments();
  const ds = useDatasets();
  const st = useStrategies();
  const health = useHealth();
  const host = useHost();
  const all = useMemo(() => unify(runs.data, sig.data, ee.data), [runs.data, sig.data, ee.data]);
  const a = activity(all);
  const loading = runs.isLoading || sig.isLoading || ee.isLoading;
  const errors = [runs, sig, ee].filter((x) => x.error);
  const cached = (ds.data ?? []).filter((d) => d.current);
  const invalid = cached.reduce((n, d) => n + (d.current?.invalid_bars ?? 0), 0);
  const running = (deps.data ?? []).filter((d) => d.deployment.status === "running").length;
  return (
    <div className="space-y-3">
      <PageHeader title="Overview" sub={health.data ? `${health.data.workspace} · bars ${health.data.policy.history_floor} → ${health.data.policy.ceiling}, backtests from ${health.data.policy.backtest_floor}` : undefined}
        actions={<>
          <Button size="sm" onClick={() => navigate("/backtest")}>New backtest</Button>
          <Button size="sm" variant="outline" onClick={() => navigate("/signals/new")}>New signal study</Button>
          <Button size="sm" variant="outline" onClick={() => navigate("/entry-exit/new")}>New entry-exit study</Button>
        </>} />
      {(st.data?.errors.length ?? 0) > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-xs" role="alert">
          <AlertTriangle className="mt-0.5 size-4 text-warn" />
          <div>{st.data!.errors.map((e) => <div key={e.file}><span className="num">{e.file}</span>: {e.error}</div>)}</div>
        </div>
      )}
      <div className="grid grid-cols-2 gap-1.5 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Experiments" value={fmt.int(a.total)} sub={`${a.byKind.backtest} backtests · ${a.byKind.signal} signal · ${a.byKind["entry-exit"]} entry-exit`} />
        <Stat label="Running" value={fmt.int(a.running + a.queued)} tone={a.running ? "text-primary" : ""} sub={a.queued ? `${a.queued} queued` : "nothing queued"} />
        <Stat label="Failed" value={fmt.int(a.failed)} tone={a.failed ? "text-warn" : ""} hint="Failed and interrupted experiments are kept as permanent records." sub="kept as records" />
        <Stat label="Strategies" value={fmt.int(st.data?.strategies.length)} tone={st.data?.errors.length ? "text-warn" : ""}
          sub={`${new Set(st.data?.strategies.map((s) => s.category)).size} categories${st.data?.errors.length ? ` · ${st.data.errors.length} failed to load` : ""}`} />
        <Stat label="Datasets cached" value={`${cached.length} / ${ds.data?.length ?? 0}`} tone={invalid ? "text-warn" : ""}
          sub={invalid ? `${fmt.int(invalid)} invalid bars (treated as missing)` : "no invalid bars"} />
        <Stat label="Forward deployments" value={fmt.int(deps.data?.length)} sub={`${running} running · host ${host.data ? host.data.clock ?? "not started" : "…"}`} />
      </div>
      <div className="grid gap-3 xl:grid-cols-[2fr_1fr]">
        <Panel title="Recent experiments" actions={<Link to="/experiments" className="text-xs text-primary hover:underline">All {fmt.int(a.total)}</Link>}>
          {errors.map((x, i) => <ErrorState key={i} error={x.error} retry={x.refetch} />)}
          {loading ? <Loading /> : all.length === 0
            ? <Empty title="No experiments yet"><Link to="/backtest" className="text-primary underline">Configure a backtest</Link></Empty>
            : <DataTable name="recent-experiments" rows={all.slice(0, 12)} cols={experimentCols} rowKey={(x) => `${x.kind}:${x.id}`} filter={false} maxHeight={460}
                onRowClick={(x) => navigate(x.href)} />}
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
                      {d.current ? <>{d.current.first_date} → {d.current.last_date} · {fmt.int(d.current.n_symbols)} sym{d.current.invalid_bars ? <span className="text-warn"> · {fmt.int(d.current.invalid_bars)} invalid</span> : null}</> : "not cached"}
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
