// Entry-Exit Lab: one entry in the app's navigation; sub-pages for the exit library, the experiment builder, the
// leaderboards, the experiment list, one experiment (matrix and comparisons) and one cell.
import { useEEExperiments } from "@/api/entryExit";
import { DataTable } from "@/components/DataTable";
import { ErrorState, Loading, PageHeader, Panel, PeriodBadge, StatusBadge } from "@/components/common";
import { ProgressBar } from "@/components/Progress";
import { Button } from "@/components/ui/button";
import * as fmt from "@/lib/format";
import { navigate, type EEView } from "@/lib/router";
import { Builder } from "@/pages/entryexit/Builder";
import { CellView } from "@/pages/entryexit/CellView";
import { EESubNav, eeProgress } from "@/pages/entryexit/common";
import { ExitLibrary } from "@/pages/entryexit/ExitLibrary";
import { ExperimentView } from "@/pages/entryexit/ExperimentView";
import { Leaderboards } from "@/pages/entryexit/Leaderboards";

function History() {
  const q = useEEExperiments();
  return (
    <div className="space-y-3">
      <PageHeader title="Entry-exit experiments" actions={<Button size="sm" onClick={() => navigate("/entry-exit/new")}>New experiment</Button>} />
      <EESubNav />
      <Panel title="Experiments" bodyClass="p-0">
        {q.isLoading ? <Loading rows={6} /> : q.error ? <ErrorState error={q.error} /> : (
          <DataTable rows={q.data ?? []} name="entry-exit-experiments" rowKey={(r) => r.experiment_id}
            onRowClick={(r) => navigate(`/entry-exit/experiments/${r.experiment_id}`)} cols={[
              { id: "created", header: "Created", value: (r) => r.created_at_utc, cell: (r) => <span className="num">{fmt.utc(r.created_at_utc)}</span> },
              { id: "name", header: "Name", value: (r) => r.name || r.experiment_id },
              { id: "status", header: "Status", value: (r) => r.status, cell: (r) => <StatusBadge status={r.status} /> },
              { id: "kind", header: "Kind", value: (r) => r.kind },
              { id: "cells", header: "Entries x exits", value: (r) => r.n_cells, cell: (r) => <span className="num">{r.n_entries} x {r.n_exits} = {r.n_cells}</span>, align: "right" },
              { id: "data", header: "Data", value: (r) => `${r.dataset} ${r.universe_name ?? ""} (${r.n_symbols})` },
              { id: "window", header: "Window", value: (r) => `${r.start}..${r.analysis_end}` },
              { id: "periods", header: "Periods", value: (r) => r.periods.join(","), cell: (r) => <span className="flex gap-1">{r.periods.map((p) => <PeriodBadge key={p} period={p} />)}</span> },
              { id: "progress", header: "Progress", value: (r) => r.progress_done, cell: (r) => (r.status === "running" || r.status === "queued")
                ? <ProgressBar compact {...eeProgress(r)} />
                : <span className="text-2xs text-muted-foreground">{r.status === "failed" ? `stopped at ${r.stage ?? "start"}` : r.status === "done" && r.summary ? `${r.summary.seconds}s` : ""}</span> },
              { id: "test", header: "Test spent", value: (r) => (r.evaluate_test ? "yes" : "no") },
              { id: "repro", header: "Reproduction", value: (r) => (r.reproduction ? `${r.reproduction.identical}/${r.reproduction.cells}` : "") },
            ]} />
        )}
      </Panel>
    </div>
  );
}

export function EntryExitPage({ route }: { route: { view: EEView; id?: string; cell?: string } }) {
  switch (route.view) {
    case "new":
      return <Builder />;
    case "leaderboards":
      return <Leaderboards />;
    case "history":
      return <History />;
    case "experiment":
      return <ExperimentView key={route.id} id={route.id!} />;
    case "cell":
      return <CellView key={`${route.id}/${route.cell}`} id={route.id!} cell={route.cell!} />;
    default:
      return <ExitLibrary />;
  }
}
