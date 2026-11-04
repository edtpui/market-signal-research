// Performance Analytics: compare finished runs on their common window: rebased equity, drawdown, the metric table
// (full window or overlap only) and the return correlation matrix. The overlap and its research periods are always
// shown, because comparing different windows is not comparing strategies.
import { useMemo, useState } from "react";

import { useCompare, useExperiments } from "@/api/hooks";
import { MetricTable } from "@/components/Charts";
import { ChoiceSelect } from "@/components/ConfigForm";
import { LineChart, PALETTE } from "@/components/LineChart";
import { Empty, ErrorState, Loading, PageHeader, Panel, PeriodBadge } from "@/components/common";
import { Checkbox } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { HEADLINE } from "@/lib/metrics";
import { setSearch, useLocation } from "@/lib/router";

function corrColor(v: number | null): string {
  if (v === null) return "transparent";
  const a = Math.min(1, Math.abs(v));
  return v >= 0 ? `rgba(57, 135, 229, ${0.1 + 0.6 * a})` : `rgba(230, 103, 103, ${0.1 + 0.6 * a})`;
}

export function AnalyticsPage() {
  const { search } = useLocation();
  const ids = (search.get("ids") ?? "").split(",").filter(Boolean);
  const runs = useExperiments();
  const cmp = useCompare(ids);
  const [scope, setScope] = useState<"full" | "overlap">("overlap");
  const done = (runs.data ?? []).filter((r) => r.status === "done");
  const toggle = (id: string) => setSearch({ ids: (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]).join(",") || null });
  const color = (id: string) => PALETTE[ids.indexOf(id) % PALETTE.length];
  const name = (id: string) => cmp.data?.runs.find((r) => r.run_id === id)?.name ?? id;
  const lines = useMemo(() => {
    const s = cmp.data?.overlap.series ?? [];
    return {
      eq: s.map((x) => ({ key: x.run_id, name: name(x.run_id), color: color(x.run_id), points: x.points.map(([t, v]) => ({ time: t, value: v - 1 })) })),
      dd: s.map((x) => ({ key: x.run_id, name: name(x.run_id), color: color(x.run_id), points: x.drawdown.map(([t, v]) => ({ time: t, value: v })) })),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cmp.data]);
  return (
    <div className="space-y-3">
      <PageHeader title="Performance Analytics" sub="Select finished runs to compare them on their common sessions. Metric definitions match backtests and forward books." />
      <div className="grid gap-3 xl:grid-cols-[320px_1fr]">
        <Panel title={`Runs (${ids.length} selected)`} bodyClass="p-0">
          {runs.isLoading ? <div className="p-3"><Loading rows={6} /></div> : done.length === 0 ? <div className="p-3"><Empty title="No finished runs" /></div> : (
            <ul className="max-h-[640px] overflow-auto">
              {done.map((r) => (
                <li key={r.run_id} className="flex items-center gap-2 border-b px-3 py-1.5 text-xs last:border-0 hover:bg-muted/50">
                  <Checkbox checked={ids.includes(r.run_id)} onCheckedChange={() => toggle(r.run_id)} aria-label={`select ${r.run_id}`} />
                  <span className="inline-block size-2 rounded-full" style={{ background: ids.includes(r.run_id) ? color(r.run_id) : "transparent" }} />
                  <button className="min-w-0 flex-1 text-left" onClick={() => toggle(r.run_id)}>
                    <div className="truncate">{r.name || r.strategy}</div>
                    <div className="num truncate text-2xs text-muted-foreground">{r.start} → {r.end} · {fmt.pct(r.summary?.total_return, 1)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <div className="space-y-3">
          {ids.length === 0 ? <Empty title="Select runs to compare" /> : cmp.isLoading ? <Loading rows={8} /> : cmp.error ? <ErrorState error={cmp.error} /> : cmp.data && (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs" data-overlap>
                <span className="text-muted-foreground">Common window</span>
                <span className="num font-medium">{cmp.data.overlap.start ?? "none"} → {cmp.data.overlap.end ?? ""}</span>
                {cmp.data.overlap.periods.map((p) => <PeriodBadge key={p} period={p} />)}
                <span className="text-muted-foreground">· {cmp.data.note}</span>
              </div>
              {!cmp.data.overlap.start ? <Empty title="These runs share no sessions" /> : (
                <div className="grid gap-3 2xl:grid-cols-2">
                  <Panel title="Growth on the common window (rebased)"><LineChart name="cmp-eq" lines={lines.eq} unit="pct" height={300} /></Panel>
                  <Panel title="Drawdown on the common window"><LineChart name="cmp-dd" lines={lines.dd} unit="pct" height={300} /></Panel>
                </div>
              )}
              <Panel title="Metrics" actions={<ChoiceSelect className="h-7 w-44" value={scope} onChange={(v) => setScope(v as "full" | "overlap")} aria-label="metric scope"
                options={[{ value: "overlap", label: "Common window only" }, { value: "full", label: "Each run's full window" }]} />}>
                <MetricTable keys={HEADLINE} rows={cmp.data.runs.map((r) => ({ id: r.run_id, name: `${r.name} (${r.start}→${r.end})`, color: color(r.run_id),
                  metrics: scope === "full" ? r.metrics : r.overlap_metrics }))} />
              </Panel>
              {Object.keys(cmp.data.correlation).length > 1 && (
                <Panel title="Daily return correlation (common window)">
                  <table className="text-xs" data-correlation>
                    <thead><tr><th />{ids.map((i) => <th key={i} className="px-2 text-2xs font-normal text-muted-foreground">{name(i).slice(0, 18)}</th>)}</tr></thead>
                    <tbody>
                      {ids.map((a) => (
                        <tr key={a}>
                          <td className="pr-2 text-2xs text-muted-foreground">{name(a).slice(0, 24)}</td>
                          {ids.map((b) => {
                            const v = cmp.data!.correlation[a]?.[b] ?? null;
                            return <td key={b} className="num h-7 min-w-16 rounded-[3px] text-center" style={{ background: corrColor(v) }}>{fmt.num(v, 2)}</td>;
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Panel>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
