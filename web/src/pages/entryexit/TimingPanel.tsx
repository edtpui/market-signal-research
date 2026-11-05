// The random-timing exit benchmark: every exit against copies of itself whose sell signals were moved to random dates on
// the same stock (same number of signals, same spacing), replayed on the same entries. Calibration rules check the
// benchmark first (placebos must land near zero, the cheating rule must be found); then exits are grouped by style
// (selling into strength vs into weakness) with year-by-year consistency.
import { STYLE_INFO, STYLE_LABEL, useTiming, type ExperimentDetail, type StyleGroup, type TimingRow } from "@/api/entryExit";
import { Empty, ErrorState, Loading, Panel } from "@/components/common";
import * as fmt from "@/lib/format";
import { navigate } from "@/lib/router";
import { cn } from "@/lib/utils";
import { pctc, qv } from "@/pages/entryexit/common";

const ci = (c: [number | null, number | null] | undefined) => (c && fmt.finite(c[0]) && fmt.finite(c[1]) ? `[${fmt.pct(c[0])}, ${fmt.pct(c[1])}]` : fmt.DASH);

function Years({ years }: { years: { group: string | number; n: number; excess?: number | null }[] }) {
  const m = Math.max(...years.map((y) => Math.abs(y.excess ?? 0)), 1e-9);
  return (
    <div className="flex h-6 items-center gap-px" aria-label="excess by year">
      {years.map((y) => {
        const v = y.excess ?? 0;
        const h = Math.max(2, Math.round((Math.abs(v) / m) * 11));
        return (
          <div key={y.group} className="flex h-6 w-2 flex-col justify-center" title={`${y.group}: ${fmt.pct(v, 2)} (${y.n} trades)`}>
            <div className="h-3 flex items-end">{v > 0 && <div className="w-2 rounded-t-[1px] bg-pos" style={{ height: h }} />}</div>
            <div className="h-3">{v < 0 && <div className="w-2 rounded-b-[1px] bg-neg" style={{ height: h }} />}</div>
          </div>
        );
      })}
    </div>
  );
}

function calibrationVerdict(r: TimingRow, oracle: boolean): { ok: boolean; text: string } {
  if (!fmt.finite(r.excess) || r.p === null) return { ok: false, text: "not enough trades" };
  if (oracle) return r.excess > 0 && r.p < 0.01 ? { ok: true, text: "detected (as it must be)" } : { ok: false, text: "NOT detected: benchmark too weak here" };
  return r.p > 0.01 ? { ok: true, text: "indistinguishable from zero (as it must be)" } : { ok: false, text: "flagged: benchmark biased here" };
}

export function TimingPanel({ id, exp, part }: { id: string; exp: ExperimentDetail; part: string }) {
  const q = useTiming(id);
  const cfg = exp.config;
  const entryNames = (cfg.resolved_entries ?? []).map((e) => e.name);
  const exits = cfg.resolved_exits ?? [];
  if (q.isLoading) return <Loading rows={6} />;
  if (q.error) return <ErrorState error={q.error} />;
  const t = q.data ?? {};
  if (!cfg.timing?.enabled || !t.tests) {
    return <Empty title="No random-timing benchmark in this experiment"><span className="text-2xs text-muted-foreground">It was added after this experiment ran, or was switched off. Turn on "Random-timing benchmark" in the Experiment Builder.</span></Empty>;
  }
  const isOracle = (pi: number) => (exits[pi]?.components ?? []).some((c) => (c as { id?: string }).id === "oracle_next_day_drop");
  const tests = t.tests.filter((r) => r.partition === part);
  const calib = (t.calibration ?? []).filter((r) => r.partition === part);
  const groups = (t.style_groups ?? []).filter((g) => g.partition === part);
  const failed = calib.filter((r) => !calibrationVerdict(r, isOracle(r.exit)).ok);
  const byEntry = new Map<number, StyleGroup[]>();
  for (const g of groups) byEntry.set(g.entry, [...(byEntry.get(g.entry) ?? []), g]);
  return (
    <div className="space-y-3">
      <div className="rounded-md border bg-surface-2/40 px-3 py-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">How to read this: </span>each exit is replayed {t.draws} more times on the same entries with its own sell
        signals moved to random dates on the same stock (same number of signals, same spacing). The excess is the real exit&apos;s net trade return minus the
        average of those shifted copies, so it measures the rule&apos;s timing alone: positive means selling on the rule&apos;s days beat selling on random days
        just as often. Partition <span className="font-medium text-foreground">{part}</span>.
      </div>

      <Panel title="1. Calibration: is the benchmark trustworthy here?" info={t.note}>
        {!calib.length ? <Empty title="No calibration rules"><span className="text-2xs text-muted-foreground">Turn on "Calibration rules" in the builder to check the benchmark on this data.</span></Empty> : (
          <>
            <div className={cn("mb-2 rounded-sm px-2 py-1 text-xs", failed.length ? "bg-neg/10" : "bg-pos/10")} data-calibration={failed.length ? "failed" : "passed"}>
              {failed.length
                ? `${failed.length} of ${calib.length} calibration checks failed on ${part}: treat the timing results below with suspicion.`
                : `All ${calib.length} calibration checks passed on ${part}: random-day sells score about zero and the rule that cheats is found.`}
            </div>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-2xs text-muted-foreground"><th>rule</th><th>entry</th><th className="text-right">trades</th><th className="text-right">excess vs random timing</th><th className="text-right">95% CI</th><th className="text-right">p</th><th className="pl-4">check</th></tr></thead>
              <tbody>{calib.map((r) => {
                const v = calibrationVerdict(r, isOracle(r.exit));
                return (
                  <tr key={r.cell} className="border-t">
                    <td>{exits[r.exit]?.name}</td><td className="text-muted-foreground">{entryNames[r.entry]}</td><td className="num text-right">{fmt.int(r.n)}</td>
                    <td className="text-right">{pctc(r.excess)}</td><td className="num text-right text-muted-foreground">{ci(r.ci)}</td><td className="text-right">{qv(r.p)}</td>
                    <td className={cn("pl-4 text-2xs", v.ok ? "text-pos" : "text-neg")}>{v.text}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </>
        )}
      </Panel>

      <Panel title="2. Exit styles: selling into strength vs selling into weakness" info="per entry: each event's timing excess averaged over the exits of a style, then a date-block bootstrap CI; 'exits beating' counts exits whose mean timing excess is positive; bars are the excess by year (green above, red below zero)">
        {!groups.length ? <Empty title="No style groups on this partition" /> : (
          <table className="w-full text-xs">
            <thead><tr className="text-left text-2xs text-muted-foreground"><th>entry</th><th>style</th><th className="text-right">exits</th><th className="text-right">trades</th><th className="text-right">mean timing excess</th><th className="text-right">95% CI</th><th className="text-right">exits beating</th><th className="text-right">years positive</th><th className="pl-3">by year</th></tr></thead>
            <tbody>{[...byEntry.entries()].flatMap(([ei, gs]) => gs.sort((a, b) => (b.excess ?? -9) - (a.excess ?? -9)).map((g) => (
              <tr key={`${ei}${g.style}`} className="border-t" data-style={g.style}>
                <td className="text-muted-foreground">{entryNames[ei]}</td>
                <td title={STYLE_INFO[g.style]}>{STYLE_LABEL[g.style] ?? g.style}</td>
                <td className="num text-right">{g.exits}</td><td className="num text-right">{fmt.int(g.n)}</td>
                <td className="text-right">{pctc(g.excess)}</td><td className="num text-right text-muted-foreground">{ci(g.ci)}</td>
                <td className="num text-right">{g.beat}/{g.of}</td><td className="num text-right">{fmt.pct(g.positive_years, 0, false)}</td>
                <td className="pl-3"><Years years={g.by_year} /></td>
              </tr>
            )))}</tbody>
          </table>
        )}
      </Panel>

      <Panel title="3. Every exit against its own random timing" info={`${t.method}. p: two-sided, from a cluster bootstrap (date blocks or stocks, whichever is wider); q: Benjamini-Hochberg across the ${tests[0]?.m_tests ?? tests.length} real exits of the partition (calibration rules excluded)`}>
        {!tests.length ? <Empty title="No exit with a shiftable condition"><span className="text-2xs text-muted-foreground">Stops, targets and time exits have no signal dates to move; the benchmark covers condition exits.</span></Empty> : (
          <div className="overflow-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-left text-2xs text-muted-foreground"><th>entry</th><th>exit</th><th>style</th><th className="text-right">trades</th><th className="text-right">excess vs random timing</th><th className="text-right">95% CI</th><th className="text-right" title="share of trades where the real exit beat the mean of its shifted copies">trades beating</th><th className="text-right">years positive</th><th className="text-right">p</th><th className="text-right">q</th></tr></thead>
              <tbody>{[...tests].sort((a, b) => a.entry - b.entry || (b.excess ?? -9) - (a.excess ?? -9)).map((r) => (
                <tr key={r.cell} className="cursor-pointer border-t hover:bg-accent/40" onClick={() => navigate(`/entry-exit/experiments/${id}/cells/${r.cell}`)}>
                  <td className="text-muted-foreground">{entryNames[r.entry]}</td><td>{exits[r.exit]?.name}</td>
                  <td className="text-2xs" title={STYLE_INFO[r.style]}>{STYLE_LABEL[r.style] ?? r.style}</td>
                  <td className="num text-right">{fmt.int(r.n)}</td><td className="text-right">{pctc(r.excess)}</td>
                  <td className="num text-right text-muted-foreground">{ci(r.ci)}</td><td className="num text-right">{fmt.pct(r.beat_share, 0, false)}</td>
                  <td className="num text-right">{fmt.pct(r.positive_years, 0, false)}</td><td className="text-right">{qv(r.p)}</td><td className="text-right">{qv(r.q)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
