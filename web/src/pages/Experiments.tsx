// Experiments: every backtest run, signal study and entry-exit study in one list (never overwritten; a reproduction is
// an experiment of its own). Filter by kind and status; a kind on its own shows that kind's full columns. Finished
// backtests can be selected and compared.
import { GitCompare } from "lucide-react";
import { useMemo, useState } from "react";

import { useEEExperiments } from "@/api/entryExit";
import { useExperiments } from "@/api/hooks";
import { useSignalExperiments } from "@/api/signals";
import { ChoiceSelect } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { Empty, ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { eeStudyCols, experimentCols, runCols, signalStudyCols } from "@/components/experimentColumns";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/primitives";
import { unify, type ExperimentItem } from "@/lib/experiments";
import { Link, navigate, setSearch, useLocation, type ExperimentKind } from "@/lib/router";
import { cn } from "@/lib/utils";

const KINDS: { kind: ExperimentKind; label: string }[] = [
  { kind: "all", label: "All" },
  { kind: "backtest", label: "Backtests" },
  { kind: "signal", label: "Signal studies" },
  { kind: "entry-exit", label: "Entry-exit studies" },
];
const STATUSES = ["all", "done", "running", "queued", "failed", "cancelled"];
const isKind = (v: string | null): v is ExperimentKind => KINDS.some((k) => k.kind === v);

export function ExperimentsPage({ kind: routeKind }: { kind?: ExperimentKind }) {
  const { search } = useLocation();
  const q = search.get("kind");
  const kind: ExperimentKind = isKind(q) ? q : routeKind ?? "all";
  const runs = useExperiments();
  const sig = useSignalExperiments();
  const ee = useEEExperiments();
  const [status, setStatus] = useState("all");
  const [strategy, setStrategy] = useState("all");
  const [selected, setSelected] = useState<string[]>([]);

  const all = useMemo(() => unify(runs.data, sig.data, ee.data), [runs.data, sig.data, ee.data]);
  const counts = useMemo(() => {
    const c: Record<ExperimentKind, number> = { all: all.length, backtest: 0, signal: 0, "entry-exit": 0 };
    all.forEach((x) => { c[x.kind] += 1; });
    return c;
  }, [all]);
  const okStatus = (s: string) => status === "all" || s === status;
  const strategies = [...new Set((runs.data ?? []).map((r) => r.strategy))].sort();

  const toggle = (id: string, on: boolean) => setSelected((s) => (on ? [...s, id] : s.filter((x) => x !== id)));
  const selectBox = (id: string, done: boolean) => (
    <span onClick={(e) => e.stopPropagation()}>
      <Checkbox disabled={!done} checked={selected.includes(id)} aria-label={`select ${id}`} onCheckedChange={(c) => toggle(id, !!c)} />
    </span>
  );
  const selectAny: Col<ExperimentItem> = {
    id: "select", header: "", value: (x) => selected.includes(x.id),
    cell: (x) => (x.kind === "backtest" ? selectBox(x.id, x.status === "done") : null),
  };

  const loading = runs.isLoading || sig.isLoading || ee.isLoading;
  const errors = [runs, sig, ee].filter((x) => x.error);
  const toolbar = (
    <>
      <div className="inline-flex h-7 items-center rounded-md bg-muted p-[2px] text-muted-foreground" role="tablist" aria-label="kind">
        {KINDS.map((k) => (
          <button key={k.kind} type="button" role="tab" aria-selected={k.kind === kind} data-kind={k.kind}
            onClick={() => setSearch({ kind: k.kind === "all" ? null : k.kind })}
            className={cn("rounded px-2 py-0.5 text-xs font-medium whitespace-nowrap hover:text-foreground", k.kind === kind && "bg-background text-foreground shadow-sm")}>
            {k.label} <span className="num text-2xs text-muted-foreground">{counts[k.kind]}</span>
          </button>
        ))}
      </div>
      <ChoiceSelect className="h-7 w-32" value={status} onChange={setStatus} aria-label="status filter"
        options={STATUSES.map((s) => ({ value: s, label: s === "all" ? "All statuses" : s }))} />
      {kind === "backtest" && (
        <ChoiceSelect className="h-7 w-44" value={strategy} onChange={setStrategy} aria-label="strategy filter"
          options={[{ value: "all", label: "All strategies" }, ...strategies.map((s) => ({ value: s, label: s }))]} />
      )}
    </>
  );

  let table;
  if (kind === "backtest") {
    const rows = (runs.data ?? []).filter((r) => okStatus(r.status) && (strategy === "all" || r.strategy === strategy));
    const cols: Col<(typeof rows)[number]>[] = [
      { id: "select", header: "", value: (r) => selected.includes(r.run_id), cell: (r) => selectBox(r.run_id, r.status === "done") },
      ...runCols.map((c) => ({ ...c, hidden: c.id === "id" ? false : c.hidden })),
    ];
    table = <DataTable name="experiments-backtest" rows={rows} cols={cols} rowKey={(r) => r.run_id} maxHeight={680} toolbar={toolbar}
      onRowClick={(r) => navigate(`/runs/${encodeURIComponent(r.run_id)}`)} />;
  } else if (kind === "signal") {
    const rows = (sig.data ?? []).filter((r) => okStatus(r.status));
    table = <DataTable name="experiments-signal" rows={rows} cols={signalStudyCols} rowKey={(r) => r.experiment_id} maxHeight={680} toolbar={toolbar}
      onRowClick={(r) => navigate(`/signals/experiments/${encodeURIComponent(r.experiment_id)}`)} />;
  } else if (kind === "entry-exit") {
    const rows = (ee.data ?? []).filter((r) => okStatus(r.status));
    table = <DataTable name="experiments-entry-exit" rows={rows} cols={eeStudyCols} rowKey={(r) => r.experiment_id} maxHeight={680} toolbar={toolbar}
      onRowClick={(r) => navigate(`/entry-exit/experiments/${encodeURIComponent(r.experiment_id)}`)} />;
  } else {
    const rows = all.filter((x) => okStatus(x.status));
    table = <DataTable name="experiments-all" rows={rows} cols={[selectAny, ...experimentCols]} rowKey={(x) => `${x.kind}:${x.id}`} maxHeight={680} toolbar={toolbar}
      onRowClick={(x) => navigate(x.href)} />;
  }

  return (
    <div className="space-y-3">
      <PageHeader title="Experiments"
        sub="Every backtest, signal study and entry-exit study keeps its resolved config, data version, code hashes and results in a read-only directory under var/. Reproduce one to re-run it and compare."
        actions={<Button size="sm" disabled={selected.length < 1} onClick={() => navigate(`/results/compare?ids=${selected.join(",")}`)} data-action="compare">
          <GitCompare className="size-3.5" />Compare {selected.length ? `${selected.length} run${selected.length > 1 ? "s" : ""}` : "runs"}
        </Button>} />
      {errors.map((x, i) => <ErrorState key={i} error={x.error} retry={x.refetch} />)}
      <Panel title={kind === "all" ? "All experiments" : KINDS.find((k) => k.kind === kind)!.label}>
        {loading ? <Loading rows={8} /> : all.length === 0 ? (
          <Empty title="No experiments yet">
            Start one: <Link to="/backtest" className="text-primary underline">a backtest</Link>, <Link to="/signals/new" className="text-primary underline">a signal study</Link> or <Link to="/entry-exit/new" className="text-primary underline">an entry-exit study</Link>.
          </Empty>
        ) : table}
      </Panel>
    </div>
  );
}
