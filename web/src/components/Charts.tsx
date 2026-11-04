// Monthly heatmap (the terminal's diverging year x month grid, value always printed) and the metric grid with
// definitions in tooltips and undefined values shown with their reason.
import { Info } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { METRICS, formatMetric, metricTone, type Metrics } from "@/lib/metrics";
import { cn } from "@/lib/utils";

const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

function shade(v: number, scale: number): string {
  const a = Math.min(1, Math.abs(v) / scale);
  return v >= 0 ? `rgba(57, 135, 229, ${0.12 + 0.55 * a})` : `rgba(230, 103, 103, ${0.12 + 0.55 * a})`;
}

export function MonthlyHeatmap({ rows }: { rows: { year: number; month: number; ret: number | null }[] }) {
  const years = [...new Set(rows.map((r) => r.year))].sort();
  const get = (y: number, m: number) => rows.find((r) => r.year === y && r.month === m + 1)?.ret ?? null;
  const scale = Math.max(0.03, ...rows.map((r) => Math.abs(r.ret ?? 0)));
  const annual = (y: number) => rows.filter((r) => r.year === y).reduce((acc, r) => acc * (1 + (r.ret ?? 0)), 1) - 1;
  if (!rows.length) return <div className="py-6 text-center text-xs text-muted-foreground">No monthly returns</div>;
  return (
    <div className="overflow-x-auto" data-heatmap>
      <table className="w-full border-separate border-spacing-[2px] text-2xs">
        <thead>
          <tr>
            <th className="w-10" />
            {MONTHS.map((m, i) => <th key={i} className="font-normal text-muted-foreground">{m}</th>)}
            <th className="pl-1 text-right font-normal text-muted-foreground">Year</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}>
              <td className="num pr-1 text-muted-foreground">{y}</td>
              {MONTHS.map((_, i) => {
                const v = get(y, i);
                return (
                  <td key={i} className="num h-6 min-w-11 rounded-[3px] text-center" style={{ background: v === null ? "transparent" : shade(v, scale) }}
                    title={v === null ? undefined : `${y}-${String(i + 1).padStart(2, "0")}: ${fmt.pct(v)}`}>
                    {v === null ? "" : fmt.pct(v, 1)}
                  </td>
                );
              })}
              <td className={cn("num pl-1 text-right font-medium", fmt.tone(annual(y)))}>{fmt.pct(annual(y), 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MetricGrid({ metrics, keys, className }: { metrics: Metrics | null | undefined; keys: string[]; className?: string }) {
  return (
    <div className={cn("grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-6", className)} data-metrics>
      {keys.map((k) => {
        const m = metrics?.[k];
        const def = METRICS[k];
        return (
          <Tooltip key={k}>
            <TooltipTrigger asChild>
              <div className="min-w-0 cursor-default rounded-md border bg-surface-2/60 px-2.5 py-1.5" data-metric={k}>
                <div className="flex items-center gap-1 truncate text-2xs text-muted-foreground">
                  {def?.label ?? k}{m && m.value === null && <Info className="size-3 text-warn" />}
                </div>
                <div className={cn("num truncate text-sm font-semibold", metricTone(k, m))}>{formatMetric(k, m)}</div>
              </div>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <div className="font-medium">{def?.label ?? k}</div>
              <div>{def?.def ?? ""}</div>
              {m && m.value === null && <div className="mt-1 text-warn">Undefined: {m.reason}</div>}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}

export function MetricTable({ rows, keys }: { rows: { id: string; name: string; color?: string; metrics: Metrics | null | undefined }[]; keys: string[] }) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-xs" data-metric-table>
        <thead className="bg-card">
          <tr className="border-b">
            <th className="h-7 px-2 text-left text-2xs font-medium text-muted-foreground">Run</th>
            {keys.map((k) => (
              <th key={k} className="h-7 px-2 text-right text-2xs font-medium whitespace-nowrap text-muted-foreground" title={METRICS[k]?.def}>
                {METRICS[k]?.label ?? k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b last:border-0 hover:bg-muted/50">
              <td className="px-2 py-1 whitespace-nowrap">
                <span className="flex items-center gap-1.5">
                  {r.color && <span className="inline-block size-2 rounded-full" style={{ background: r.color }} />}
                  {r.name}
                </span>
              </td>
              {keys.map((k) => {
                const m = r.metrics?.[k];
                return (
                  <td key={k} className={cn("num px-2 py-1 text-right", metricTone(k, m))} title={m && m.value === null ? `Undefined: ${m.reason}` : undefined}>
                    {formatMetric(k, m)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
