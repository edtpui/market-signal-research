// Table columns for the experiment lists: backtest runs, signal studies, entry-exit studies, and the merged list.
import type { RunRow } from "@/api/client";
import type { ExperimentRow as EERow } from "@/api/entryExit";
import type { ExperimentRow as SignalRow } from "@/api/signals";
import type { Col } from "@/components/DataTable";
import { PeriodBadge, StatusBadge } from "@/components/common";
import { ProgressBar } from "@/components/Progress";
import * as fmt from "@/lib/format";
import { KIND_LABEL, isActive, type ExperimentItem } from "@/lib/experiments";
import { eeProgress } from "@/pages/entryexit/common";

const periodsCell = (periods: string[]) => <span className="flex gap-1">{periods.map((p) => <PeriodBadge key={p} period={p} />)}</span>;

export const runCols: Col<RunRow>[] = [
  { id: "created", header: "Created", value: (r) => r.created_at_utc, cell: (r) => <span className="num text-muted-foreground">{fmt.utc(r.created_at_utc)}</span> },
  { id: "name", header: "Name", value: (r) => r.name || r.strategy, cell: (r) => <span className="font-medium">{r.name || r.strategy}</span> },
  { id: "strategy", header: "Strategy", value: (r) => `${r.strategy}@${r.strategy_version}`, cell: (r) => <span className="num">{r.strategy}<span className="text-muted-foreground">@{r.strategy_version}</span></span> },
  { id: "dataset", header: "Dataset", value: (r) => `${r.dataset}@${r.dataset_version}`, cell: (r) => <span className="num text-muted-foreground">{r.dataset}@{fmt.bytesHash(r.dataset_version, 6)}</span>, hidden: true },
  { id: "window", header: "Window", value: (r) => `${r.start}..${r.end}`, cell: (r) => <span className="num">{r.start} → {r.end}</span> },
  { id: "freq", header: "Freq", value: (r) => r.frequency, hidden: true },
  { id: "periods", header: "Periods", value: (r) => r.periods.join(","), cell: (r) => periodsCell(r.periods) },
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

export const signalStudyCols: Col<SignalRow>[] = [
  { id: "created", header: "Created", value: (r) => r.created_at_utc, cell: (r) => <span className="num">{fmt.utc(r.created_at_utc)}</span> },
  { id: "name", header: "Study", value: (r) => r.name || r.experiment_id, cell: (r) => <div><div className="font-medium">{r.name || "unnamed"}</div><div className="num text-2xs text-muted-foreground">{r.experiment_id}</div></div> },
  { id: "kind", header: "Kind", value: (r) => r.kind, cell: (r) => <span className={r.kind === "reproduction" ? "text-primary" : ""}>{r.kind}</span> },
  { id: "status", header: "Status", value: (r) => r.status, cell: (r) => <StatusBadge status={r.status} /> },
  { id: "progress", header: "Signals", align: "right", value: (r) => r.n_signals, cell: (r) => isActive(r.status)
    ? <ProgressBar compact value={r.n_signals ? (r.progress_done + r.progress_failed) / r.n_signals : null} />
    : <span className="num">{r.progress_done}{r.progress_failed ? `+${r.progress_failed}✕` : ""}/{r.n_signals}</span> },
  { id: "data", header: "Data", value: (r) => `${r.dataset} ${r.universe_name}`, cell: (r) => <span className="text-2xs">{r.dataset} · {r.universe_name ?? "custom"} (<span className="num">{r.n_symbols}</span>)</span> },
  { id: "window", header: "Window", value: (r) => r.start, cell: (r) => <span className="num text-2xs">{r.start} … {r.analysis_end}</span> },
  { id: "test", header: "Test period", value: (r) => r.evaluate_test, cell: (r) => (r.evaluate_test ? <span className="text-warn">evaluated</span> : <span className="text-muted-foreground">locked</span>) },
  { id: "supported", header: "Supported", align: "right", value: (r) => Object.values(r.summary?.verdicts ?? {}).reduce((a, v) => a + (v.supported ?? 0), 0) },
  { id: "repro", header: "Reproduction", value: (r) => (r.reproduction ? String(r.reproduction.identical) : ""), cell: (r) => r.reproduction ? <span className={r.reproduction.identical ? "text-pos" : "text-warn"}>{r.reproduction.identical ? "identical" : "differs"}</span> : null },
  { id: "secs", header: "Time", align: "right", value: (r) => r.summary?.seconds ?? null, cell: (r) => <span className="num">{r.summary ? `${r.summary.seconds}s` : fmt.DASH}</span> },
];

export const eeStudyCols: Col<EERow>[] = [
  { id: "created", header: "Created", value: (r) => r.created_at_utc, cell: (r) => <span className="num">{fmt.utc(r.created_at_utc)}</span> },
  { id: "name", header: "Name", value: (r) => r.name || r.experiment_id },
  { id: "status", header: "Status", value: (r) => r.status, cell: (r) => <StatusBadge status={r.status} /> },
  { id: "kind", header: "Kind", value: (r) => r.kind },
  { id: "cells", header: "Entries x exits", value: (r) => r.n_cells, cell: (r) => <span className="num">{r.n_entries} x {r.n_exits} = {r.n_cells}</span>, align: "right" },
  { id: "data", header: "Data", value: (r) => `${r.dataset} ${r.universe_name ?? ""} (${r.n_symbols})` },
  { id: "window", header: "Window", value: (r) => `${r.start}..${r.analysis_end}` },
  { id: "periods", header: "Periods", value: (r) => r.periods.join(","), cell: (r) => periodsCell(r.periods) },
  { id: "progress", header: "Progress", value: (r) => r.progress_done, cell: (r) => isActive(r.status)
    ? <ProgressBar compact {...eeProgress(r)} />
    : <span className="text-2xs text-muted-foreground">{r.status === "failed" ? `stopped at ${r.stage ?? "start"}` : r.status === "done" && r.summary ? `${r.summary.seconds}s` : ""}</span> },
  { id: "test", header: "Test spent", value: (r) => (r.evaluate_test ? "yes" : "no") },
  { id: "repro", header: "Reproduction", value: (r) => (r.reproduction ? `${r.reproduction.identical}/${r.reproduction.cells}` : "") },
];

function progressCell(x: ExperimentItem) {
  if (!isActive(x.status)) return <span className="text-2xs text-muted-foreground">{x.summary}</span>;
  if (x.kind === "entry-exit") return <ProgressBar compact {...eeProgress(x.raw)} />;
  if (x.kind === "signal") return <ProgressBar compact value={x.raw.n_signals ? (x.raw.progress_done + x.raw.progress_failed) / x.raw.n_signals : null} />;
  return <ProgressBar compact value={null} />;
}

export const experimentCols: Col<ExperimentItem>[] = [
  { id: "created", header: "Created", value: (x) => x.created, cell: (x) => <span className="num text-muted-foreground">{fmt.utc(x.created)}</span> },
  { id: "kind", header: "Kind", value: (x) => KIND_LABEL[x.kind], cell: (x) => <span className="text-2xs">{KIND_LABEL[x.kind]}{x.reproduction && <span className="ml-1 text-primary">(reproduction)</span>}</span> },
  { id: "name", header: "Name", value: (x) => x.name, cell: (x) => <div><div className="font-medium">{x.name}</div><div className="num text-2xs text-muted-foreground">{x.id}</div></div> },
  { id: "status", header: "Status", value: (x) => x.status, cell: (x) => <StatusBadge status={x.status} /> },
  { id: "data", header: "Strategy / data", value: (x) => x.data, cell: (x) => <span className="text-2xs">{x.data}</span> },
  { id: "window", header: "Window", value: (x) => x.window, cell: (x) => <span className="num text-2xs">{x.window}</span> },
  { id: "periods", header: "Periods", value: (x) => x.periods.join(","), cell: (x) => periodsCell(x.periods) },
  { id: "summary", header: "Result / progress", value: (x) => x.summary, cell: progressCell },
];
