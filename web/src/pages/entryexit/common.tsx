// Shared pieces of the Entry-Exit Lab pages: sub-navigation, metric formatting and the matrix colour scale.
import type { ReactNode } from "react";

import { LOWER_IS_BETTER, PCT_METRICS, SIGNED_METRICS, type ExperimentRow } from "@/api/entryExit";
import * as fmt from "@/lib/format";
import { Link, useLocation } from "@/lib/router";
import { cn } from "@/lib/utils";

const TABS = [
  { to: "/entry-exit", label: "Exit Library", match: (p: string) => p === "/entry-exit" },
  { to: "/entry-exit/new", label: "Experiment Builder", match: (p: string) => p.startsWith("/entry-exit/new") },
  { to: "/entry-exit/leaderboards", label: "Leaderboards", match: (p: string) => p.startsWith("/entry-exit/leaderboards") },
  { to: "/entry-exit/history", label: "Experiments", match: (p: string) => p.startsWith("/entry-exit/history") || p.startsWith("/entry-exit/experiments") },
];

export function EESubNav({ right }: { right?: ReactNode }) {
  const { path } = useLocation();
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 border-b pb-2" data-subnav="entry-exit">
      <div className="inline-flex h-8 items-center rounded-lg bg-muted p-[3px] text-muted-foreground">
        {TABS.map((t) => (
          <Link key={t.to} to={t.to} className={cn("rounded-md px-2.5 py-1 text-xs font-medium whitespace-nowrap hover:text-foreground",
            t.match(path) && "bg-background text-foreground shadow-sm dark:bg-input/30")}>{t.label}</Link>
        ))}
      </div>
      <span className="text-2xs text-muted-foreground">Entry signals x exit plans on identical events, against random entries under the same exit · EXPLORATORY, data ≤ 2025-12-31</span>
      {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  );
}

export function fmtMetric(metric: string, v: number | null | undefined): string {
  if (!fmt.finite(v)) return fmt.DASH;
  if (PCT_METRICS.has(metric)) return fmt.pct(v, Math.abs(v) < 0.01 ? 2 : 1, SIGNED_METRICS.has(metric));
  if (metric === "n" || metric === "trade_count") return fmt.int(v);
  if (metric === "q" || metric === "q_timing") return v < 0.001 ? v.toExponential(1) : v.toFixed(3);
  if (metric === "avg_bars") return fmt.num(v, 1);
  return fmt.num(v, 2);
}

/** Background for a matrix cell: diverging around 0 for signed metrics, sequential otherwise, scaled by the range. */
export function cellColor(metric: string, v: number | null | undefined, lo: number, hi: number): string | undefined {
  if (!fmt.finite(v)) return undefined;
  if (SIGNED_METRICS.has(metric)) {
    const m = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
    const a = Math.min(1, Math.abs(v) / m);
    return v >= 0 ? `color-mix(in oklab, var(--pos) ${Math.round(a * 55)}%, transparent)` : `color-mix(in oklab, var(--neg) ${Math.round(a * 55)}%, transparent)`;
  }
  const span = hi - lo || 1;
  let a = (v - lo) / span;
  if (LOWER_IS_BETTER.has(metric) || metric === "max_drawdown") a = metric === "max_drawdown" ? a : 1 - a;
  return `color-mix(in oklab, var(--primary) ${Math.round(Math.max(0, Math.min(1, a)) * 50)}%, transparent)`;
}

const STAGE: Record<string, string> = {
  prepare: "Preparing data, indicators and eligibility", cells: "Replaying trades and random controls",
  portfolio: "Backtesting cells in the event-driven engine", done: "Finished",
};

/** Overall progress of an entry-exit experiment from its stage and counters. Weights follow the measured split of the
 * reference run (trade replays ~84 %, portfolio backtests ~16 %); without portfolio runs the replays are ~97 %. */
export function eeProgress(m: ExperimentRow, portfolioRun?: string): { value: number | null; label: string; detail: string } {
  if (m.status === "queued") return { value: 0, label: "Queued", detail: "waiting for the experiment before it to finish" };
  const cellsW = portfolioRun === "none" ? 0.95 : 0.82;
  const f = m.progress_total > 0 ? m.progress_done / m.progress_total : 0;
  if (m.stage === "cells") return { value: 0.02 + cellsW * f, label: STAGE.cells, detail: `cell ${m.progress_done} of ${m.progress_total} (${m.n_entries} entries x ${m.n_exits} exits)` };
  if (m.stage === "portfolio") return { value: 0.02 + cellsW + (0.97 - cellsW) * f, label: STAGE.portfolio, detail: `backtest ${m.progress_done} of ${m.progress_total}` };
  if (m.stage === "done") return { value: 1, label: STAGE.done, detail: "" };
  return { value: 0.01, label: STAGE.prepare, detail: "loading bars and building the shared indicator cache" };
}

/** A failed or interrupted experiment explained: what happened, and what to do. */
export function eeFailure(m: ExperimentRow): { title: string; message: string; problems: string[]; hint: string | null } {
  const err = m.error ?? "unknown error";
  if (/^interrupted/.test(err)) {
    return { title: "The experiment was interrupted", message: "The process running it stopped before it finished (for example the app or the computer was restarted).",
      problems: [], hint: "Launch it again from the Experiment Builder; this record is kept and shows what had finished." };
  }
  const where = STAGE[m.stage ?? ""]?.toLowerCase() ?? "running";
  const clean = err.replace(/^[A-Za-z]+(Error|Exception): /, "");
  return { title: `The experiment failed while ${where}`, message: clean, problems: m.stage === "portfolio" ? ["The trade-level results of every cell were finished and saved before the failure."] : [],
    hint: "Fix the cause named above (often a setting or a data gap), then launch a new experiment: this one stays as a permanent record." };
}

export const pctc = (v: number | null | undefined, d = 2) => <span className={cn("num", fmt.tone(v))}>{fmt.pct(v, d)}</span>;
export const numc = (v: number | null | undefined, d = 2) => <span className={cn("num", fmt.tone(v))}>{fmt.num(v, d)}</span>;
export const qv = (v: number | null | undefined) => (
  <span className={cn("num", typeof v === "number" && v < 0.05 ? "font-semibold text-foreground" : "text-muted-foreground")}>{typeof v === "number" ? (v < 1e-4 ? "< 0.0001" : v < 0.001 ? v.toExponential(1) : v.toFixed(3)) : fmt.DASH}</span>
);
