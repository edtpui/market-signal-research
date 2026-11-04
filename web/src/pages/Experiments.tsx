// Experiment History: every run (never overwritten; reproductions are runs of their own), filterable, with a
// selection that opens Performance Analytics.
import { GitCompare } from "lucide-react";
import { useMemo, useState } from "react";

import type { RunRow } from "@/api/client";
import { useExperiments } from "@/api/hooks";
import { ChoiceSelect } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { Empty, ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/primitives";
import { navigate } from "@/lib/router";
import { runCols } from "@/pages/Overview";

export function ExperimentsPage() {
  const runs = useExperiments();
  const [status, setStatus] = useState("all");
  const [strategy, setStrategy] = useState("all");
  const [selected, setSelected] = useState<string[]>([]);
  const rows = useMemo(() => (runs.data ?? []).filter((r) => (status === "all" || r.status === status) && (strategy === "all" || r.strategy === strategy)), [runs.data, status, strategy]);
  const strategies = [...new Set((runs.data ?? []).map((r) => r.strategy))].sort();
  const select: Col<RunRow> = {
    id: "select", header: "", value: (r) => selected.includes(r.run_id),
    cell: (r) => <span onClick={(e) => e.stopPropagation()}><Checkbox disabled={r.status !== "done"} checked={selected.includes(r.run_id)} aria-label={`select ${r.run_id}`}
      onCheckedChange={(c) => setSelected((s) => (c ? [...s, r.run_id] : s.filter((x) => x !== r.run_id)))} /></span>,
  };
  return (
    <div className="space-y-3">
      <PageHeader title="Experiment History" sub="Every run keeps its resolved config, data version, code hashes and artifacts in a read-only directory under var/experiments."
        actions={<Button size="sm" disabled={selected.length < 1} onClick={() => navigate(`/analytics?ids=${selected.join(",")}`)} data-action="compare"><GitCompare className="size-3.5" />Compare {selected.length || ""}</Button>} />
      <Panel title={`Runs (${rows.length})`}>
        {runs.isLoading ? <Loading rows={8} /> : runs.error ? <ErrorState error={runs.error} retry={runs.refetch} /> : (runs.data ?? []).length === 0 ? <Empty title="No experiments yet" /> : (
          <DataTable name="experiments" rows={rows} cols={[select, ...runCols.map((c) => ({ ...c, hidden: c.id === "id" ? false : c.hidden }))]}
            onRowClick={(r) => navigate(`/runs/${r.run_id}`)} maxHeight={680}
            toolbar={<>
              <ChoiceSelect className="h-7 w-32" value={status} onChange={setStatus} aria-label="status filter"
                options={["all", "done", "running", "queued", "failed"].map((s) => ({ value: s, label: s === "all" ? "All statuses" : s }))} />
              <ChoiceSelect className="h-7 w-44" value={strategy} onChange={setStrategy} aria-label="strategy filter"
                options={[{ value: "all", label: "All strategies" }, ...strategies.map((s) => ({ value: s, label: s }))]} />
            </>} />
        )}
      </Panel>
    </div>
  );
}
