// Research History: every signal experiment (never overwritten; reproductions are experiments of their own) with its
// status, scope and verdicts, and the research-integrity ledgers: how often each dataset partition has been used,
// and every evaluation of a locked test period. Imported signals are listed with their Strategy Library ids.
import { ProgressBar } from "@/components/Progress";
import { useMemo, useState } from "react";

import { useImports, useSignalExperiments, useUsage, type ExperimentRow } from "@/api/signals";
import { ChoiceSelect } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { Empty, ErrorState, Loading, PageHeader, Panel, StatusBadge } from "@/components/common";
import * as fmt from "@/lib/format";
import { Link, navigate } from "@/lib/router";
import { SubNav } from "@/pages/signals/common";

const cols: Col<ExperimentRow>[] = [
  { id: "created", header: "Created", value: (r) => r.created_at_utc, cell: (r) => <span className="num">{fmt.utc(r.created_at_utc)}</span> },
  { id: "name", header: "Experiment", value: (r) => r.name || r.experiment_id, cell: (r) => <div><div className="font-medium">{r.name || "unnamed"}</div><div className="num text-2xs text-muted-foreground">{r.experiment_id}</div></div> },
  { id: "kind", header: "Kind", value: (r) => r.kind, cell: (r) => <span className={r.kind === "reproduction" ? "text-primary" : ""}>{r.kind}</span> },
  { id: "status", header: "Status", value: (r) => r.status, cell: (r) => <StatusBadge status={r.status} /> },
  { id: "progress", header: "Signals", align: "right", value: (r) => r.n_signals, cell: (r) => (r.status === "running" || r.status === "queued")
    ? <ProgressBar compact value={r.n_signals ? (r.progress_done + r.progress_failed) / r.n_signals : null} />
    : <span className="num">{r.progress_done}{r.progress_failed ? `+${r.progress_failed}✕` : ""}/{r.n_signals}</span> },
  { id: "data", header: "Data", value: (r) => `${r.dataset} ${r.universe_name}`, cell: (r) => <span className="text-2xs">{r.dataset} · {r.universe_name ?? "custom"} (<span className="num">{r.n_symbols}</span>)</span> },
  { id: "window", header: "Window", value: (r) => r.start, cell: (r) => <span className="num text-2xs">{r.start} … {r.analysis_end}</span> },
  { id: "test", header: "Test period", value: (r) => r.evaluate_test, cell: (r) => (r.evaluate_test ? <span className="text-warn">evaluated</span> : <span className="text-muted-foreground">locked</span>) },
  { id: "supported", header: "Supported", align: "right", value: (r) => Object.values(r.summary?.verdicts ?? {}).reduce((a, v) => a + (v.supported ?? 0), 0) },
  { id: "repro", header: "Reproduction", value: (r) => (r.reproduction ? String(r.reproduction.identical) : ""), cell: (r) => r.reproduction ? <span className={r.reproduction.identical ? "text-pos" : "text-warn"}>{r.reproduction.identical ? "identical" : "differs"}</span> : null },
  { id: "secs", header: "Time", align: "right", value: (r) => r.summary?.seconds ?? null, cell: (r) => <span className="num">{r.summary ? `${r.summary.seconds}s` : fmt.DASH}</span> },
];

export function ResearchHistory() {
  const exps = useSignalExperiments();
  const usage = useUsage();
  const imports = useImports();
  const [status, setStatus] = useState("all");
  const rows = useMemo(() => (exps.data ?? []).filter((r) => status === "all" || r.status === status), [exps.data, status]);
  return (
    <div className="space-y-3">
      <PageHeader title="Research History" sub="Every experiment keeps its resolved config (dataset version, universe, signal versions, partitions, seed), its controls and its results in a read-only directory under var/signal_research. Reproduce one to re-run and compare content hashes." />
      <SubNav />
      {exps.error && <ErrorState error={exps.error} retry={exps.refetch} />}
      <Panel title={`Experiments (${rows.length})`}>
        {exps.isLoading ? <Loading rows={6} /> : (exps.data ?? []).length === 0 ? <Empty title="No signal experiments yet"><Link className="text-primary underline" to="/signals/new">Open the builder</Link></Empty> : (
          <DataTable name="signal-experiments" rows={rows} cols={cols} rowKey={(r) => r.experiment_id} maxHeight={520} onRowClick={(r) => navigate(`/signals/experiments/${r.experiment_id}`)}
            toolbar={<ChoiceSelect className="h-7 w-32" value={status} onChange={setStatus} aria-label="status filter" options={["all", "done", "running", "queued", "failed", "cancelled"].map((s) => ({ value: s, label: s }))} />} />
        )}
      </Panel>
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title="Data reuse by partition" info="How many experiments and tests have looked at each dataset partition: the more, the higher the chance that a finding there is luck.">
          {(usage.data?.partitions ?? []).length === 0 ? <Empty title="No usage recorded yet" /> : (
            <table className="w-full text-xs" data-usage>
              <thead><tr className="border-b text-2xs text-muted-foreground"><th className="py-1 text-left font-medium">Dataset</th><th className="text-left font-medium">Partition</th><th className="text-left font-medium">Window</th><th className="text-right font-medium">Experiments</th><th className="text-right font-medium">Tests</th></tr></thead>
              <tbody>{usage.data!.partitions.map((p, i) => (
                <tr key={i} className="border-b last:border-0"><td className="py-0.5">{p.dataset}</td><td className={p.role === "test" ? "text-warn" : ""}>{p.partition} ({p.role})</td><td className="num text-2xs">{p.start} … {p.end}</td><td className="num text-right">{p.experiments}</td><td className="num text-right">{fmt.int(p.tests)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </Panel>
        <Panel title="Test-period ledger (append-only)" info="Every evaluation of a locked test period. The first result per signal and methodology is locked; later ones are re-evaluations.">
          {(usage.data?.test_ledger ?? []).length === 0 ? <Empty title="The test period has not been spent">Experiments keep it locked unless "Evaluate the locked test period" is on.</Empty> : (
            <>
              <table className="w-full text-xs" data-ledger>
                <thead><tr className="border-b text-2xs text-muted-foreground"><th className="py-1 text-left font-medium">Signal</th><th className="text-left font-medium">Family</th><th className="text-left font-medium">Test window</th><th className="text-right font-medium">Evaluations</th><th className="text-right font-medium">Locked</th></tr></thead>
                <tbody>{usage.data!.test_ledger.map((l, i) => (
                  <tr key={i} className="border-b last:border-0"><td className="num py-0.5">{l.signal_id}</td><td>{l.family}</td><td className="num text-2xs">{l.test_start} … {l.test_end}</td><td className={l.evaluations > 1 ? "num text-right text-warn" : "num text-right"}>{l.evaluations}</td><td className="num text-right">{l.locked}</td></tr>
                ))}</tbody>
              </table>
              <div className="mt-2 text-2xs text-muted-foreground">By family: {usage.data!.test_by_family.map((f) => `${f.family} ${f.evaluations} evaluations of ${f.configurations} configurations`).join(" · ")}</div>
            </>
          )}
        </Panel>
      </div>
      <Panel title={`Signals imported into the Strategy Library (${imports.data?.length ?? 0})`}>
        {(imports.data ?? []).length === 0 ? <Empty title="None yet">Import a signal from its results page; it becomes a strategy with entry, exit and sizing rules that the backtester and the paper host can run.</Empty> : (
          <table className="w-full text-xs">
            <thead><tr className="border-b text-2xs text-muted-foreground"><th className="py-1 text-left font-medium">Strategy</th><th className="text-left font-medium">Signal</th><th className="text-left font-medium">Source experiment</th><th className="text-left font-medium">Imported</th></tr></thead>
            <tbody>{imports.data!.map((i) => (
              <tr key={i.strategy_id} className="border-b last:border-0">
                <td className="py-0.5"><Link className="num text-primary hover:underline" to={`/strategies/${i.strategy_id}`}>{i.strategy_id}</Link></td>
                <td className="num text-2xs">{i.signal_key}</td>
                <td><Link className="num text-2xs text-primary hover:underline" to={`/signals/experiments/${i.experiment_id}/${encodeURIComponent(i.signal_key)}`}>{i.experiment_id}</Link></td>
                <td className="num text-2xs">{fmt.utc(i.created_at_utc)}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
