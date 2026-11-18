// One list of every experiment: backtest runs, signal studies and entry-exit studies, from their three catalogs.
import type { RunRow } from "@/api/client";
import type { ExperimentRow as EERow } from "@/api/entryExit";
import type { ExperimentRow as SignalRow } from "@/api/signals";
import * as fmt from "@/lib/format";
import type { ExperimentKind } from "@/lib/router";

export type StudyKind = Exclude<ExperimentKind, "all">;

export const KIND_LABEL: Record<StudyKind, string> = {
  backtest: "Backtest",
  signal: "Signal study",
  "entry-exit": "Entry-exit study",
};

interface Base {
  id: string;
  name: string;
  created: string;
  status: string;
  reproduction: boolean;
  data: string;
  window: string;
  periods: string[];
  summary: string;
  href: string;
}

export type ExperimentItem =
  | (Base & { kind: "backtest"; raw: RunRow })
  | (Base & { kind: "signal"; raw: SignalRow })
  | (Base & { kind: "entry-exit"; raw: EERow });

export const isActive = (status: string) => status === "running" || status === "queued";

const universe = (name: string | null, n: number) => `${name ?? "custom"} (${n})`;

function sum(v: Record<string, Record<string, number>> | undefined, key: string): number {
  return Object.values(v ?? {}).reduce((a, x) => a + (x[key] ?? 0), 0);
}

export function fromRun(r: RunRow): ExperimentItem {
  const s = r.summary;
  return {
    kind: "backtest", raw: r, id: r.run_id, name: r.name || r.strategy, created: r.created_at_utc, status: r.status,
    reproduction: r.kind === "reproduction", data: `${r.strategy}@${r.strategy_version} · ${r.dataset}`,
    window: `${r.start} → ${r.end}`, periods: r.periods, href: `/runs/${encodeURIComponent(r.run_id)}`,
    summary: r.status === "done" && s ? `${fmt.pct(s.total_return)} total · Sharpe ${fmt.num(s.sharpe)}` : "",
  };
}

export function fromSignal(r: SignalRow): ExperimentItem {
  const done = r.status === "done" && r.summary;
  return {
    kind: "signal", raw: r, id: r.experiment_id, name: r.name || r.experiment_id, created: r.created_at_utc,
    status: r.status, reproduction: r.kind === "reproduction", data: `${r.dataset} · ${universe(r.universe_name, r.n_symbols)}`,
    window: `${r.start} → ${r.analysis_end ?? r.end}`, periods: r.periods ?? [],
    href: `/signals/experiments/${encodeURIComponent(r.experiment_id)}`,
    summary: done ? `${r.progress_done}/${r.n_signals} signals · ${sum(r.summary!.verdicts, "supported")} supported` : "",
  };
}

export function fromEE(r: EERow): ExperimentItem {
  return {
    kind: "entry-exit", raw: r, id: r.experiment_id, name: r.name || r.experiment_id, created: r.created_at_utc,
    status: r.status, reproduction: r.kind === "reproduction", data: `${r.dataset} · ${universe(r.universe_name, r.n_symbols)}`,
    window: `${r.start} → ${r.analysis_end ?? r.end}`, periods: r.periods ?? [],
    href: `/entry-exit/experiments/${encodeURIComponent(r.experiment_id)}`,
    summary: `${r.n_entries} x ${r.n_exits} = ${r.n_cells} cells`,
  };
}

/** Counts for the Overview: totals by kind and by status (failed includes interrupted and cancelled ones). */
export function activity(xs: ExperimentItem[]) {
  const byKind: Record<StudyKind, number> = { backtest: 0, signal: 0, "entry-exit": 0 };
  let running = 0, queued = 0, failed = 0;
  for (const x of xs) {
    byKind[x.kind] += 1;
    if (x.status === "running") running += 1;
    else if (x.status === "queued") queued += 1;
    else if (x.status === "failed" || x.status === "cancelled") failed += 1;
  }
  return { total: xs.length, byKind, running, queued, failed };
}

/** Every experiment, newest first (ties broken by id, so the order is stable). */
export function unify(runs: RunRow[] = [], signals: SignalRow[] = [], ee: EERow[] = []): ExperimentItem[] {
  const all = [...runs.map(fromRun), ...signals.map(fromSignal), ...ee.map(fromEE)];
  return all.sort((a, b) => (a.created === b.created ? a.id.localeCompare(b.id) : a.created < b.created ? 1 : -1));
}
