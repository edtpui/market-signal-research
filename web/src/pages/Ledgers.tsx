// Research ledgers: the append-only research-integrity records of both labs in one place. How often each dataset
// partition has been looked at (the more often, the likelier a finding there is luck), every evaluation of a locked
// test period, and every research result imported into the strategy library.
import { useMemo } from "react";

import { useEEImports, useEEUsage } from "@/api/entryExit";
import { useImports, useUsage } from "@/api/signals";
import { Empty, ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import * as fmt from "@/lib/format";
import { Link } from "@/lib/router";

const th = "text-left font-medium";

export function LedgersPage() {
  const usage = useUsage();
  const sigImports = useImports();
  const eeUsage = useEEUsage();
  const eeImports = useEEImports();

  // ee_data_usage holds one row per (experiment, partition): count experiments and cells per partition window.
  const eeParts = useMemo(() => {
    const by = new Map<string, { dataset: string; partition: string; role: string; start: string | null; end: string | null; experiments: Set<string>; cells: number }>();
    for (const r of eeUsage.data ?? []) {
      const k = [r.dataset, r.partition, r.role, r.start, r.end].join("|");
      const g = by.get(k) ?? { dataset: r.dataset, partition: r.partition, role: r.role, start: r.start, end: r.end, experiments: new Set<string>(), cells: 0 };
      g.experiments.add(r.experiment_id);
      g.cells += r.n_cells;
      by.set(k, g);
    }
    return [...by.values()].sort((a, b) => a.dataset.localeCompare(b.dataset) || String(a.start).localeCompare(String(b.start)));
  }, [eeUsage.data]);

  return (
    <div className="space-y-3">
      <PageHeader title="Research ledgers"
        sub="Append-only records kept by the signal and entry-exit labs. They cannot be edited or deleted: they are how a later reader can tell how much searching produced a result." />
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title="Signal studies: data reuse by partition" info="How many signal studies and tests have looked at each dataset partition.">
          {usage.isLoading ? <Loading rows={3} /> : usage.error ? <ErrorState error={usage.error} retry={usage.refetch} /> : (usage.data?.partitions ?? []).length === 0 ? <Empty title="No usage recorded yet" /> : (
            <table className="w-full text-xs" data-usage="signal">
              <thead><tr className="border-b text-2xs text-muted-foreground"><th className={`py-1 ${th}`}>Dataset</th><th className={th}>Partition</th><th className={th}>Window</th><th className="text-right font-medium">Studies</th><th className="text-right font-medium">Tests</th></tr></thead>
              <tbody>{usage.data!.partitions.map((p, i) => (
                <tr key={i} className="border-b last:border-0"><td className="py-0.5">{p.dataset}</td><td className={p.role === "test" ? "text-warn" : ""}>{p.partition} ({p.role})</td><td className="num text-2xs">{p.start} … {p.end}</td><td className="num text-right">{p.experiments}</td><td className="num text-right">{fmt.int(p.tests)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </Panel>
        <Panel title="Entry-exit studies: data reuse by partition" info="How many entry-exit studies and (entry, exit) cells have looked at each dataset partition.">
          {eeUsage.isLoading ? <Loading rows={3} /> : eeUsage.error ? <ErrorState error={eeUsage.error} retry={eeUsage.refetch} /> : eeParts.length === 0 ? <Empty title="No usage recorded yet" /> : (
            <table className="w-full text-xs" data-usage="entry-exit">
              <thead><tr className="border-b text-2xs text-muted-foreground"><th className={`py-1 ${th}`}>Dataset</th><th className={th}>Partition</th><th className={th}>Window</th><th className="text-right font-medium">Studies</th><th className="text-right font-medium">Cells</th></tr></thead>
              <tbody>{eeParts.map((p, i) => (
                <tr key={i} className="border-b last:border-0"><td className="py-0.5">{p.dataset}</td><td className={p.role === "test" ? "text-warn" : ""}>{p.partition} ({p.role})</td><td className="num text-2xs">{p.start ?? "?"} … {p.end ?? "?"}</td><td className="num text-right">{p.experiments.size}</td><td className="num text-right">{fmt.int(p.cells)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </Panel>
      </div>
      <Panel title="Signal test-period ledger" info="Every evaluation of a locked test period. The first result per signal and methodology is locked; later ones are re-evaluations.">
        {usage.isLoading ? <Loading rows={3} /> : (usage.data?.test_ledger ?? []).length === 0 ? <Empty title="The test period has not been spent">Studies keep it locked unless "Evaluate the locked test period" is on.</Empty> : (
          <>
            <table className="w-full text-xs" data-ledger>
              <thead><tr className="border-b text-2xs text-muted-foreground"><th className={`py-1 ${th}`}>Signal</th><th className={th}>Family</th><th className={th}>Test window</th><th className="text-right font-medium">Evaluations</th><th className="text-right font-medium">Locked</th></tr></thead>
              <tbody>{usage.data!.test_ledger.map((l, i) => (
                <tr key={i} className="border-b last:border-0"><td className="num py-0.5">{l.signal_id}</td><td>{l.family}</td><td className="num text-2xs">{l.test_start} … {l.test_end}</td><td className={l.evaluations > 1 ? "num text-right text-warn" : "num text-right"}>{l.evaluations}</td><td className="num text-right">{l.locked}</td></tr>
              ))}</tbody>
            </table>
            <div className="mt-2 text-2xs text-muted-foreground">By family: {usage.data!.test_by_family.map((f) => `${f.family} ${f.evaluations} evaluations of ${f.configurations} configurations`).join(" · ")}</div>
          </>
        )}
      </Panel>
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title={`Signals imported as strategies (${sigImports.data?.length ?? 0})`}>
          {sigImports.isLoading ? <Loading rows={2} /> : (sigImports.data ?? []).length === 0 ? <Empty title="None yet">Import a signal from its results page; it becomes a strategy with entry, exit and sizing rules that the backtester and the paper host can run.</Empty> : (
            <table className="w-full text-xs">
              <thead><tr className="border-b text-2xs text-muted-foreground"><th className={`py-1 ${th}`}>Strategy</th><th className={th}>Signal</th><th className={th}>Source study</th><th className={th}>Imported</th></tr></thead>
              <tbody>{sigImports.data!.map((i) => (
                <tr key={i.strategy_id} className="border-b last:border-0">
                  <td className="py-0.5"><Link className="num text-primary hover:underline" to={`/library/strategies/${encodeURIComponent(i.strategy_id)}`}>{i.strategy_id}</Link></td>
                  <td className="num text-2xs">{i.signal_key}</td>
                  <td><Link className="num text-2xs text-primary hover:underline" to={`/signals/experiments/${encodeURIComponent(i.experiment_id)}/${encodeURIComponent(i.signal_key)}`}>{i.experiment_id}</Link></td>
                  <td className="num text-2xs">{fmt.utc(i.created_at_utc)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Panel>
        <Panel title={`Entry-exit systems imported as strategies (${eeImports.data?.length ?? 0})`}>
          {eeImports.isLoading ? <Loading rows={2} /> : eeImports.error ? <ErrorState error={eeImports.error} retry={eeImports.refetch} /> : (eeImports.data ?? []).length === 0 ? <Empty title="None yet">Import a cell from its page in an entry-exit study; it needs a finished backtest before it can be deployed forward.</Empty> : (
            <table className="w-full text-xs">
              <thead><tr className="border-b text-2xs text-muted-foreground"><th className={`py-1 ${th}`}>Strategy</th><th className={th}>Source cell</th><th className={th}>Imported</th></tr></thead>
              <tbody>{eeImports.data!.map((i) => (
                <tr key={i.strategy_id} className="border-b last:border-0">
                  <td className="py-0.5"><Link className="num text-primary hover:underline" to={`/library/strategies/${encodeURIComponent(i.strategy_id)}`}>{i.strategy_id}</Link> <span className="num text-2xs text-muted-foreground">v{i.version}</span></td>
                  <td><Link className="num text-2xs text-primary hover:underline" to={`/entry-exit/experiments/${encodeURIComponent(i.experiment_id)}/cells/${encodeURIComponent(i.cell)}`}>{i.experiment_id} / {i.cell}</Link></td>
                  <td className="num text-2xs">{fmt.utc(i.created_at_utc)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}
