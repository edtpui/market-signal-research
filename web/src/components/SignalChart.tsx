// A signal's calculation on one symbol: candles (or the close) with the signal's overlays and a marker on every
// triggering session, on lightweight-charts with the app's chart conventions. Oscillators go in LineChart below it.
import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, CrosshairMode, LineSeries, createChart, createSeriesMarkers, type IChartApi, type Time,
} from "lightweight-charts";

import { PALETTE } from "@/components/LineChart";
import { useTheme } from "@/components/theme";

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function SignalChart({ dates, bars, overlays, triggers, direction, height = 340 }: {
  dates: string[]; bars: Record<string, (number | null)[]>; overlays: Record<string, (number | null)[]>; triggers: string[];
  direction: "bullish" | "bearish"; height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, fontSize: 11, fontFamily: cssVar("--font-mono", "monospace"),
        textColor: cssVar("--muted-foreground", "#9b9a94") },
      grid: { vertLines: { color: cssVar("--grid", "#212226") }, horzLines: { color: cssVar("--grid", "#212226") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartRef.current = chart;
    const pos = cssVar("--pos", "#2fbf71"), neg = cssVar("--neg", "#f0605d");
    const hasOhlc = ["open", "high", "low", "close"].every((k) => bars[k]);
    let main;
    if (hasOhlc) {
      main = chart.addSeries(CandlestickSeries, { upColor: pos, downColor: neg, wickUpColor: pos, wickDownColor: neg, borderVisible: false, priceLineVisible: false });
      main.setData(dates.map((d, i) => ({ time: d as Time, open: bars.open[i], high: bars.high[i], low: bars.low[i], close: bars.close[i] }))
        .filter((x) => [x.open, x.high, x.low, x.close].every((v) => typeof v === "number")) as never);
    } else {
      main = chart.addSeries(LineSeries, { color: cssVar("--foreground", "#ecebe8"), lineWidth: 2, priceLineVisible: false });
      main.setData(dates.map((d, i) => ({ time: d as Time, value: bars.close[i] })).filter((x) => typeof x.value === "number") as never);
    }
    Object.entries(overlays).forEach(([name, vals], j) => {
      const s = chart.addSeries(LineSeries, { color: PALETTE[(j + 1) % PALETTE.length], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: name });
      s.setData(dates.map((d, i) => ({ time: d as Time, value: vals[i] })).filter((x) => typeof x.value === "number") as never);
    });
    createSeriesMarkers(main, triggers.map((t) => ({
      time: t as Time, position: direction === "bullish" ? "belowBar" : "aboveBar", color: direction === "bullish" ? pos : neg,
      shape: direction === "bullish" ? "arrowUp" : "arrowDown", text: "",
    })));
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [dates, bars, overlays, triggers, direction, theme]);

  return <div ref={box} style={{ height }} data-chart="signal-price" />;
}
