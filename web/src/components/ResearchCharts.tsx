// Charts for signal research that are not time series: a histogram with markers (randomized null distributions,
// excess-return distributions), signed bars with confidence intervals (by year / sector / regime / horizon), a forest
// plot (estimates with CIs across signals), and a multi-line chart over a numeric x (cumulative return by holding
// day). Plain SVG on the theme's tokens (no extra dependency); every chart has a hover readout, an accessible label
// and a CSV export of exactly the numbers it draws (ChartActions).
import { Download, FileJson } from "lucide-react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/primitives";
import { download, toCsv } from "@/lib/csv";
import * as fmt from "@/lib/format";
import { cn } from "@/lib/utils";

export type Fmt = "pct" | "num" | "int";

export function fmtValue(v: number | null | undefined, unit: Fmt, digits?: number): string {
  if (unit === "pct") return fmt.pct(v, digits ?? (v !== null && v !== undefined && Math.abs(v) < 0.01 ? 2 : 1));
  if (unit === "int") return fmt.int(v);
  return fmt.num(v, digits ?? 3);
}

function useWidth<T extends HTMLElement>(fallback = 600): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const set = () => setW(Math.max(160, el.clientWidth || fallback));
    set();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, w];
}

/** "nice" ticks over [lo, hi] */
export function ticks(lo: number, hi: number, n = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  if (hi === lo) return [lo];
  const raw = (hi - lo) / Math.max(1, n);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => raw <= s) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

export function ChartActions({ name, header, rows, config }: { name: string; header: string[]; rows: unknown[][]; config?: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="xs" className="h-6 gap-1 px-1.5 text-2xs" title="Download the numbers behind this chart"
        onClick={() => download(`${name}.csv`, toCsv(header, rows))} data-action="chart-csv"><Download className="size-3" />Data</Button>
      {config !== undefined && (
        <>
          <Button variant="ghost" size="xs" className="h-6 gap-1 px-1.5 text-2xs" title="The experiment configuration behind this chart" onClick={() => setOpen(true)} data-action="chart-config">
            <FileJson className="size-3" />Config
          </Button>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogContent className="max-w-3xl">
              <DialogTitle>Configuration behind {name}</DialogTitle>
              <DialogDescription>The resolved experiment configuration (dataset version, universe, signal versions, partitions, seed).</DialogDescription>
              <pre className="num max-h-[60vh] overflow-auto rounded-md border bg-surface-2 p-2 text-[11px] leading-snug">{JSON.stringify(config, null, 1)}</pre>
            </DialogContent>
          </Dialog>
        </>
      )}
    </div>
  );
}

function Readout({ x, y, w, children }: { x: number; y: number; w: number; children: ReactNode }) {
  return (
    <div className="pointer-events-none absolute z-10 rounded-md border bg-popover/95 px-2 py-1 text-2xs shadow-lg backdrop-blur"
      style={{ left: Math.min(x + 12, w - 190), top: Math.max(0, y - 8) }}>
      {children}
    </div>
  );
}

// -- histogram ----------------------------------------------------------------------------------------------------------
export interface Marker { value: number | null | undefined; label: string; color: string; dashed?: boolean }

export function Histogram({ name, edges, counts, markers = [], unit = "pct", height = 170, band }: {
  name: string; edges: number[]; counts: number[]; markers?: Marker[]; unit?: Fmt; height?: number;
  band?: [number | null | undefined, number | null | undefined];
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  if (!edges.length || !counts.length) return <div className="py-6 text-center text-xs text-muted-foreground">No distribution</div>;
  const pad = { l: 30, r: 8, t: 8, b: 22 };
  const lo = Math.min(edges[0], ...markers.map((m) => m.value ?? edges[0]));
  const hi = Math.max(edges[edges.length - 1], ...markers.map((m) => m.value ?? edges[0]));
  const span = hi - lo || 1;
  const X = (v: number) => pad.l + ((v - lo) / span) * (w - pad.l - pad.r);
  const ymax = Math.max(1, ...counts);
  const Y = (c: number) => height - pad.b - (c / ymax) * (height - pad.t - pad.b);
  return (
    <div ref={ref} className="relative" data-chart={name}>
      <svg width={w} height={height} role="img" aria-label={`${name} histogram`} onMouseLeave={() => setHover(null)}>
        {band && band[0] !== null && band[0] !== undefined && band[1] !== null && band[1] !== undefined && (
          <rect x={X(band[0])} y={pad.t} width={Math.max(1, X(band[1]) - X(band[0]))} height={height - pad.t - pad.b} fill="var(--band-dev)" />
        )}
        {counts.map((c, i) => {
          const x0 = X(edges[i]), x1 = X(edges[i + 1]);
          return (
            <rect key={i} x={x0 + 0.5} y={Y(c)} width={Math.max(1, x1 - x0 - 1)} height={height - pad.b - Y(c)}
              fill="var(--color-series-1, #3987e5)" opacity={hover?.i === i ? 0.9 : 0.55}
              onMouseMove={(e) => setHover({ i, x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY })} />
          );
        })}
        <line x1={pad.l} x2={w - pad.r} y1={height - pad.b} y2={height - pad.b} stroke="var(--border)" />
        {ticks(lo, hi, Math.max(2, Math.floor(w / 110))).map((t) => (
          <g key={t}>
            <line x1={X(t)} x2={X(t)} y1={height - pad.b} y2={height - pad.b + 3} stroke="var(--muted-foreground)" />
            <text x={X(t)} y={height - 6} fontSize={10} textAnchor="middle" className="num" fill="var(--muted-foreground)">{fmtValue(t, unit)}</text>
          </g>
        ))}
        <text x={4} y={pad.t + 8} fontSize={10} fill="var(--muted-foreground)" className="num">{fmt.int(ymax)}</text>
        {markers.filter((m) => m.value !== null && m.value !== undefined && Number.isFinite(m.value)).map((m, i) => (
          <g key={m.label}>
            <line x1={X(m.value!)} x2={X(m.value!)} y1={pad.t} y2={height - pad.b} stroke={m.color} strokeWidth={2} strokeDasharray={m.dashed ? "4 3" : undefined} />
            <text x={Math.min(X(m.value!) + 4, w - 70)} y={pad.t + 10 + i * 12} fontSize={10} fill={m.color}>{m.label}</text>
          </g>
        ))}
      </svg>
      {hover && (
        <Readout x={hover.x} y={hover.y} w={w}>
          <div className="num">{fmtValue(edges[hover.i], unit)} … {fmtValue(edges[hover.i + 1], unit)}</div>
          <div>count <span className="num">{fmt.int(counts[hover.i])}</span></div>
        </Readout>
      )}
    </div>
  );
}

// -- signed bars with CIs -------------------------------------------------------------------------------------------------
export interface BarRow { label: string; value: number | null; lo?: number | null; hi?: number | null; n?: number; note?: string }

export function BarChart({ name, rows, unit = "pct", height = 180, emphasize }: { name: string; rows: BarRow[]; unit?: Fmt; height?: number; emphasize?: (r: BarRow) => boolean }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const vals = rows.flatMap((r) => [r.value, r.lo, r.hi]).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (!rows.length || !vals.length) return <div className="py-6 text-center text-xs text-muted-foreground">No data</div>;
  const pad = { l: 44, r: 8, t: 8, b: rows.some((r) => r.n !== undefined) ? 32 : 20 };
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const Y = (v: number) => pad.t + ((hi - v) / span) * (height - pad.t - pad.b);
  const bw = (w - pad.l - pad.r) / rows.length;
  return (
    <div ref={ref} className="relative" data-chart={name}>
      <svg width={w} height={height} role="img" aria-label={`${name} bar chart`} onMouseLeave={() => setHover(null)}>
        {ticks(lo, hi, 4).map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={Y(t)} y2={Y(t)} stroke="var(--grid)" />
            <text x={pad.l - 4} y={Y(t) + 3} fontSize={10} textAnchor="end" className="num" fill="var(--muted-foreground)">{fmtValue(t, unit)}</text>
          </g>
        ))}
        <line x1={pad.l} x2={w - pad.r} y1={Y(0)} y2={Y(0)} stroke="var(--muted-foreground)" />
        {rows.map((r, i) => {
          const cx = pad.l + bw * i + bw / 2;
          const v = r.value;
          const col = v === null ? "var(--muted-foreground)" : v >= 0 ? "var(--pos)" : "var(--neg)";
          const em = emphasize ? emphasize(r) : true;
          return (
            <g key={r.label} onMouseMove={(e) => setHover({ i, x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY })}>
              <rect x={pad.l + bw * i} y={pad.t} width={bw} height={height - pad.t - pad.b} fill="transparent" />
              {v !== null && Number.isFinite(v) && (
                <rect x={cx - Math.min(18, bw * 0.35)} width={Math.min(36, bw * 0.7)} y={Math.min(Y(v), Y(0))} height={Math.max(1, Math.abs(Y(v) - Y(0)))}
                  fill={col} opacity={em ? (hover?.i === i ? 0.95 : 0.7) : 0.3} />
              )}
              {typeof r.lo === "number" && typeof r.hi === "number" && Number.isFinite(r.lo) && Number.isFinite(r.hi) && (
                <g stroke="var(--foreground)" strokeWidth={1}>
                  <line x1={cx} x2={cx} y1={Y(r.lo)} y2={Y(r.hi)} />
                  <line x1={cx - 4} x2={cx + 4} y1={Y(r.lo)} y2={Y(r.lo)} />
                  <line x1={cx - 4} x2={cx + 4} y1={Y(r.hi)} y2={Y(r.hi)} />
                </g>
              )}
              <text x={cx} y={height - pad.b + 12} fontSize={10} textAnchor="middle" fill="var(--muted-foreground)">
                {r.label.length > Math.max(4, bw / 6) ? `${r.label.slice(0, Math.max(3, Math.floor(bw / 6)))}…` : r.label}
              </text>
              {r.n !== undefined && <text x={cx} y={height - pad.b + 24} fontSize={9} textAnchor="middle" className="num" fill="var(--muted-foreground)">n={r.n}</text>}
            </g>
          );
        })}
      </svg>
      {hover && rows[hover.i] && (
        <Readout x={hover.x} y={hover.y} w={w}>
          <div className="font-medium">{rows[hover.i].label}</div>
          <div className="num">{fmtValue(rows[hover.i].value, unit)}{typeof rows[hover.i].lo === "number" ? ` [${fmtValue(rows[hover.i].lo, unit)}, ${fmtValue(rows[hover.i].hi, unit)}]` : ""}</div>
          {rows[hover.i].n !== undefined && <div className="num">n = {rows[hover.i].n}</div>}
          {rows[hover.i].note && <div>{rows[hover.i].note}</div>}
        </Readout>
      )}
    </div>
  );
}

// -- forest plot ---------------------------------------------------------------------------------------------------------
export interface ForestRow { key: string; label: string; value: number | null; lo: number | null; hi: number | null; tone?: string; sub?: string; onClick?: () => void }

export function ForestPlot({ name, rows, unit = "pct", rowHeight = 20 }: { name: string; rows: ForestRow[]; unit?: Fmt; rowHeight?: number }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const vals = rows.flatMap((r) => [r.value, r.lo, r.hi]).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (!rows.length || !vals.length) return <div className="py-6 text-center text-xs text-muted-foreground">No estimates</div>;
  const labelW = Math.min(260, Math.max(120, w * 0.32));
  const pad = { l: labelW, r: rows.some((r) => r.sub) ? 124 : 70, t: 6, b: 20 };
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const X = (v: number) => pad.l + ((v - lo) / span) * (w - pad.l - pad.r);
  const height = pad.t + pad.b + rows.length * rowHeight;
  return (
    <div ref={ref} data-chart={name}>
      <svg width={w} height={height} role="img" aria-label={`${name} forest plot`}>
        {ticks(lo, hi, Math.max(2, Math.floor((w - labelW) / 110))).map((t) => (
          <g key={t}>
            <line x1={X(t)} x2={X(t)} y1={pad.t} y2={height - pad.b} stroke="var(--grid)" />
            <text x={X(t)} y={height - 6} fontSize={10} textAnchor="middle" className="num" fill="var(--muted-foreground)">{fmtValue(t, unit)}</text>
          </g>
        ))}
        <line x1={X(0)} x2={X(0)} y1={pad.t} y2={height - pad.b} stroke="var(--muted-foreground)" />
        {rows.map((r, i) => {
          const y = pad.t + i * rowHeight + rowHeight / 2;
          const col = r.tone ?? "var(--foreground)";
          return (
            <g key={r.key} className={cn(r.onClick && "cursor-pointer")} onClick={r.onClick}>
              <title>{`${r.label}: ${fmtValue(r.value, unit)} [${fmtValue(r.lo, unit)}, ${fmtValue(r.hi, unit)}]${r.sub ? ` · ${r.sub}` : ""}`}</title>
              <rect x={0} y={y - rowHeight / 2} width={w} height={rowHeight} fill={i % 2 ? "transparent" : "var(--surface-2)"} opacity={0.5} />
              <text x={4} y={y + 3.5} fontSize={11} fill="var(--foreground)">{r.label.length > labelW / 6.2 ? `${r.label.slice(0, Math.floor(labelW / 6.2))}…` : r.label}</text>
              {typeof r.lo === "number" && typeof r.hi === "number" && <line x1={X(r.lo)} x2={X(r.hi)} y1={y} y2={y} stroke={col} strokeWidth={1.5} />}
              {typeof r.value === "number" && <circle cx={X(r.value)} cy={y} r={3.5} fill={col} />}
              <text x={w - 4} y={y + 3.5} fontSize={10} textAnchor="end" className="num" fill="var(--muted-foreground)">{r.sub ?? fmtValue(r.value, unit)}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// -- lines over a numeric x ------------------------------------------------------------------------------------------------
export interface XYSeries { key: string; name: string; color: string; points: [number, number | null][]; dashed?: boolean }

export function XYChart({ name, series, unit = "pct", height = 220, xLabel = "", markers = [] }: {
  name: string; series: XYSeries[]; unit?: Fmt; height?: number; xLabel?: string; markers?: number[];
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hx, setHx] = useState<number | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const shown = series.filter((s) => !hidden.has(s.key));
  const xs = series.flatMap((s) => s.points.map((p) => p[0]));
  const ys = shown.flatMap((s) => s.points.map((p) => p[1])).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (!xs.length || !ys.length) return <div className="py-6 text-center text-xs text-muted-foreground">No data</div>;
  const pad = { l: 46, r: 10, t: 8, b: 24 };
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  const X = (v: number) => pad.l + ((v - x0) / (x1 - x0 || 1)) * (w - pad.l - pad.r);
  const Y = (v: number) => pad.t + ((y1 - v) / (y1 - y0 || 1)) * (height - pad.t - pad.b);
  const nearest = (px: number) => {
    const target = x0 + ((px - pad.l) / (w - pad.l - pad.r)) * (x1 - x0);
    return xs.reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a), xs[0]);
  };
  return (
    <div ref={ref} className="relative" data-chart={name}>
      <div className="mb-1 flex flex-wrap gap-x-3 gap-y-1 text-2xs">
        {series.map((s) => (
          <button key={s.key} type="button" className={cn("flex items-center gap-1.5 text-muted-foreground hover:text-foreground", hidden.has(s.key) && "opacity-40")}
            onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(s.key)) n.delete(s.key); else n.add(s.key); return n; })}>
            <span className="inline-block h-0.5 w-3 rounded" style={{ background: s.color }} />{s.name}
          </button>
        ))}
      </div>
      <svg width={w} height={height} role="img" aria-label={`${name} line chart`} onMouseMove={(e) => setHx(nearest(e.nativeEvent.offsetX))} onMouseLeave={() => setHx(null)}>
        {ticks(y0, y1, 4).map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={Y(t)} y2={Y(t)} stroke="var(--grid)" />
            <text x={pad.l - 4} y={Y(t) + 3} fontSize={10} textAnchor="end" className="num" fill="var(--muted-foreground)">{fmtValue(t, unit)}</text>
          </g>
        ))}
        <line x1={pad.l} x2={w - pad.r} y1={Y(0)} y2={Y(0)} stroke="var(--muted-foreground)" />
        {ticks(x0, x1, Math.max(2, Math.floor(w / 90))).map((t) => (
          <text key={t} x={X(t)} y={height - 8} fontSize={10} textAnchor="middle" className="num" fill="var(--muted-foreground)">{fmt.int(t)}</text>
        ))}
        {xLabel && <text x={w - pad.r} y={height - 8} fontSize={10} textAnchor="end" fill="var(--muted-foreground)">{xLabel}</text>}
        {markers.map((m) => <line key={m} x1={X(m)} x2={X(m)} y1={pad.t} y2={height - pad.b} stroke="var(--border)" strokeDasharray="2 3" />)}
        {shown.map((s) => {
          const segs: string[] = [];
          let cur = "";
          for (const [x, y] of s.points) {
            if (y === null || !Number.isFinite(y)) { if (cur) segs.push(cur); cur = ""; continue; }
            cur += `${cur ? "L" : "M"}${X(x).toFixed(1)},${Y(y).toFixed(1)}`;
          }
          if (cur) segs.push(cur);
          return segs.map((d, i) => <path key={`${s.key}-${i}`} d={d} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dashed ? "5 4" : undefined} />);
        })}
        {hx !== null && <line x1={X(hx)} x2={X(hx)} y1={pad.t} y2={height - pad.b} stroke="var(--muted-foreground)" />}
      </svg>
      {hx !== null && (
        <Readout x={X(hx)} y={20} w={w}>
          <div className="text-muted-foreground">{xLabel || "x"} = <span className="num">{hx}</span></div>
          {shown.map((s) => {
            const p = s.points.find((q) => q[0] === hx);
            return <div key={s.key} className="flex justify-between gap-3"><span style={{ color: s.color }}>{s.name}</span><span className="num">{fmtValue(p?.[1] ?? null, unit)}</span></div>;
          })}
        </Readout>
      )}
    </div>
  );
}
