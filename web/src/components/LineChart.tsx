// Time-series chart on lightweight-charts with the ptrade terminal's conventions: transparent surface, mono axis
// labels, theme grid colours, crosshair tooltip listing every visible line (sorted), legend toggles. Values are in
// the chart's unit: "pct" (fractions, shown as %), "usd", "ratio" or "price".
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AreaSeries, ColorType, CrosshairMode, LineSeries, createChart, type IChartApi, type ISeriesApi, type LineData,
  type MouseEventParams, type Time,
} from "lightweight-charts";

import { useTheme } from "@/components/theme";
import * as fmt from "@/lib/format";
import { cn } from "@/lib/utils";

export const PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767",
  "#22a6b3", "#b5651d", "#6c8ebf", "#a3a83a"];

export interface Line {
  key: string;
  name: string;
  color: string;
  points: { time: string; value: number }[];
  dashed?: boolean;
  area?: boolean;
  /** area filled between the line and the top (drawdowns: from 0 down to the line) */
  invert?: boolean;
  width?: number;
}

export type Unit = "pct" | "usd" | "ratio" | "price";

export function formatValue(v: number, unit: Unit): string {
  if (unit === "pct") return fmt.pct(v, Math.abs(v) < 0.1 ? 2 : 1);
  if (unit === "usd") return fmt.usd(v, { compact: true });
  if (unit === "price") return fmt.price(v);
  return fmt.num(v, 2);
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

type Hover = { x: number; y: number; time: string; rows: { key: string; name: string; color: string; value: number }[] };

export function LineChart({ name, lines, unit, height = 320, className, legend = true }: {
  name: string; lines: Line[]; unit: Unit; height?: number; className?: string; legend?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef(new Map<string, ISeriesApi<"Line"> | ISeriesApi<"Area">>());
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<Hover | null>(null);
  const { theme } = useTheme();
  const linesRef = useRef(lines);
  linesRef.current = lines;
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;

  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, fontSize: 11, fontFamily: cssVar("--font-mono", "monospace") },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderVisible: false, rightOffset: 1, lockVisibleTimeRangeOnResize: true },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { priceFormatter: (v: number) => formatValue(v, unit) },
      handleScale: { axisPressedMouseMove: { time: true, price: false } },
    });
    chartRef.current = chart;
    const map = seriesRef.current;
    const onMove = (param: MouseEventParams<Time>) => {
      if (!param.point || param.time === undefined) {
        setHover(null);
        return;
      }
      const rows: Hover["rows"] = [];
      for (const l of linesRef.current) {
        if (hiddenRef.current.has(l.key)) continue;
        const s = map.get(l.key);
        const d = s ? (param.seriesData.get(s) as LineData<Time> | undefined) : undefined;
        if (d && typeof d.value === "number") rows.push({ key: l.key, name: l.name, color: l.color, value: d.value });
      }
      rows.sort((a, b) => b.value - a.value);
      setHover(rows.length ? { x: param.point.x, y: param.point.y, time: fmt.shortDate(String(param.time), true), rows } : null);
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
      map.clear();
    };
  }, [unit]);

  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: { textColor: cssVar("--muted-foreground", "#9b9a94") },
      grid: { vertLines: { color: cssVar("--grid", "#212226") }, horzLines: { color: cssVar("--grid", "#212226") } },
      crosshair: {
        vertLine: { color: cssVar("--muted-foreground", "#888"), labelBackgroundColor: cssVar("--popover", "#1c1d20") },
        horzLine: { color: cssVar("--muted-foreground", "#888"), labelBackgroundColor: cssVar("--popover", "#1c1d20") },
      },
    });
  }, [theme, unit]);

  const signature = useMemo(() => lines.map((l) => `${l.key}:${l.points.length}:${l.points.at(-1)?.value ?? ""}`).join("|"), [lines]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const map = seriesRef.current;
    const keep = new Set(lines.map((l) => l.key));
    for (const [k, s] of map) {
      if (!keep.has(k)) {
        chart.removeSeries(s);
        map.delete(k);
      }
    }
    for (const l of lines) {
      let s = map.get(l.key);
      if (!s) {
        s = l.area
          ? chart.addSeries(AreaSeries, { priceLineVisible: false, lastValueVisible: false, lineColor: l.color, lineWidth: 1,
              topColor: l.invert ? `${l.color}10` : `${l.color}55`, bottomColor: l.invert ? `${l.color}66` : `${l.color}08`, invertFilledArea: !!l.invert })
          : chart.addSeries(LineSeries, { priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 3 });
        map.set(l.key, s);
      }
      if (!l.area) s.applyOptions({ color: l.color, lineWidth: (l.width ?? 2) as 1 | 2 | 3 | 4, lineStyle: l.dashed ? 2 : 0 });
      s.applyOptions({ visible: !hidden.has(l.key), lastValueVisible: !hidden.has(l.key) && l.points.length > 0 });
      s.setData(l.points.map((p) => ({ time: p.time as Time, value: p.value })));
    }
    chart.timeScale().fitContent();
  }, [signature, hidden]);

  const hasData = lines.some((l) => l.points.length > 0);
  return (
    <div className={cn("relative", className)} data-chart={name}>
      {legend && lines.length > 1 && (
        <div className="mb-1 flex flex-wrap gap-x-3 gap-y-1 text-2xs" data-legend={name}>
          {lines.map((l) => (
            <button key={l.key} type="button" onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(l.key)) n.delete(l.key); else n.add(l.key); return n; })}
              className={cn("flex items-center gap-1.5 text-muted-foreground hover:text-foreground", hidden.has(l.key) && "opacity-40")}>
              <span className="inline-block h-0.5 w-3 rounded" style={{ background: l.color, opacity: l.dashed ? 0.7 : 1 }} />
              {l.name}
            </button>
          ))}
        </div>
      )}
      <div ref={box} style={{ height }} className="relative" />
      {!hasData && <div className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">No data</div>}
      {hover && (
        <div className="pointer-events-none absolute z-10 min-w-44 rounded-md border bg-popover/95 px-2.5 py-2 shadow-lg backdrop-blur"
          style={{ left: Math.min(hover.x + 14, (box.current?.clientWidth ?? 600) - 210), top: Math.max(4, Math.min(hover.y, height - 40 - hover.rows.length * 18)) }}>
          <div className="mb-1 text-2xs text-muted-foreground">{hover.time}</div>
          {hover.rows.map((r) => (
            <div key={r.key} className="flex items-center justify-between gap-4 text-xs leading-[18px]">
              <span className="flex items-center gap-1.5 truncate">
                <span className="inline-block h-0.5 w-3 rounded" style={{ background: r.color }} />
                <span className="truncate">{r.name}</span>
              </span>
              <span className={cn("num", unit === "pct" ? fmt.tone(r.value) : "")}>{formatValue(r.value, unit)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
