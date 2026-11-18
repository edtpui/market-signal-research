// One entry-exit experiment: progress and provenance, the Entry-Exit Performance Matrix (rows: entries, columns: exit
// plans, cells: the chosen metric on one partition; filters by family, significance and sample size; click a cell for
// its experiment), the controlled comparisons (hold entry constant: every exit against the baseline exit on identical
// events; hold exit constant: entries ranked by excess over random entries), walk-forward selection and parameter
// sensitivity.
import { Ban, Repeat } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import {
  METRIC_LABEL, useComparisons, useEECancel, useEEExperiment, useEEReproduce, useMatrix, useWalkForward, type Matrix,
} from "@/api/entryExit";
import { ChoiceSelect, Field, NumberInput } from "@/components/ConfigForm";
import { XYChart } from "@/components/ResearchCharts";
import { PALETTE } from "@/components/LineChart";
import { Empty, ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge, Stat, StatusBadge } from "@/components/common";
import { ProblemPanel, ProgressBar } from "@/components/Progress";
import { Button } from "@/components/ui/button";
import { Switch, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { navigate } from "@/lib/router";
import { cn } from "@/lib/utils";
import { cellColor, eeFailure, eeProgress, fmtMetric, pctc, qv } from "@/pages/entryexit/common";
import { TimingPanel } from "@/pages/entryexit/TimingPanel";
import { VerdictBadge } from "@/pages/signals/common";

function MatrixGrid({ m, id, filters }: { m: Matrix; id: string; filters: { entryFam: string; exitFam: string; sigOnly: boolean; minN: number } }) {
  const rows = m.entries.filter((e) => !filters.entryFam || e.family === filters.entryFam);
  const cols = m.exits.filter((x) => !filters.exitFam || x.family === filters.exitFam || (filters.exitFam === "deployable" && x.deployable));
  const vals = rows.flatMap((e) => cols.map((x) => m.cells[e.index][x.index].value)).filter(fmt.finite);
  const lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
  if (!rows.length || !cols.length) return <Empty title="No cells match the filters" />;
  return (
    <div className="overflow-auto" data-matrix>
      <table className="border-separate border-spacing-0 text-2xs">
        <thead>
          <tr>
            <th className="sticky left-0 z-10 bg-card p-1 text-left font-medium text-muted-foreground">entry \ exit</th>
            {cols.map((x) => (
              <th key={x.index} className="max-w-[110px] min-w-[84px] p-1 text-left align-bottom font-medium" title={`${x.name} (${x.family})${x.deployable ? "" : " · intrabar orders"}`}>
                <div className="line-clamp-3 leading-tight">{x.name}</div>
                <div className="text-muted-foreground">{x.family}{x.deployable ? "" : " ·ib"}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.index}>
              <th className="sticky left-0 z-10 max-w-[200px] bg-card p-1 text-left font-medium" title={e.key}>
                <div className="truncate">{e.name}</div><div className="text-muted-foreground">{e.family}</div>
              </th>
              {cols.map((x) => {
                const c = m.cells[e.index][x.index];
                const dim = (filters.sigOnly && !(typeof c.q === "number" && c.q < 0.05)) || (c.n ?? 0) < filters.minN;
                return (
                  <td key={x.index} className={cn("cursor-pointer border border-background p-1 text-right hover:outline hover:outline-1 hover:outline-primary", dim && "opacity-30")}
                    style={{ background: dim ? undefined : cellColor(m.metric, c.value, lo, hi) }} data-cell={c.cell}
                    title={`${e.name} -> ${x.name}\n${METRIC_LABEL[m.metric] ?? m.metric}: ${fmtMetric(m.metric, c.value)}\ntrades ${c.n ?? 0} · q ${c.q ?? "—"} · ${c.verdict ?? ""}`}
                    onClick={() => navigate(`/entry-exit/experiments/${id}/cells/${c.cell}`)}>
                    <div className="num font-semibold">{fmtMetric(m.metric, c.value)}</div>
                    <div className="num text-muted-foreground">{c.n ?? 0}{typeof c.q === "number" && c.q < 0.05 ? " *" : ""}</div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1 text-2xs text-muted-foreground">Cell: the metric, then the trade count; * = BH q &lt; 0.05 on the matched-random test. ·ib = intrabar stop/limit orders (not deployable on ptrade). Dimmed: filtered out.</div>
    </div>
  );
}

export function ExperimentView({ id }: { id: string }) {
  const exp = useEEExperiment(id);
  const meta = exp.data?.meta;
  const done = meta?.status === "done" || meta?.status === "cancelled";
  const parts = (exp.data?.study?.parts ?? []).map((p) => p.name);
  const [partition, setPartition] = useState("");
  const [metric, setMetric] = useState("excess_matched");
  const [filters, setFilters] = useState({ entryFam: "", exitFam: "", sigOnly: false, minN: 0 });
  const part = partition || parts[0] || "";
  const matrix = useMatrix(id, part, metric, done);
  const comp = useComparisons(id, done);
  const wf = useWalkForward(id, done);
  const cancel = useEECancel();
  const repro = useEEReproduce();
  const cfg = exp.data?.config;
  const metrics = ["excess_matched", "excess_random_dates", "excess_bench", "mean_ret", "median_ret", "win_rate", "profit_factor", "expectancy_r", "avg_bars",
    "ret_per_session", "trade_sharpe", "mae", "mfe", "stop_rate", "target_rate", "consistency_years", "q", "n",
    ...(cfg?.timing?.enabled ? ["timing_excess", "q_timing", "timing_beat_share"] : []),
    "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "total_return", "exposure", "turnover", "trade_count", "excess_return", "excess_random_portfolio"];
  const entryNames = useMemo(() => (cfg?.resolved_entries ?? []).map((e) => e.name), [cfg]);
  const exitNames = useMemo(() => (cfg?.resolved_exits ?? []).map((e) => e.name), [cfg]);

  if (exp.isLoading) return <div className="space-y-3"><PageHeader title={id} /><Loading rows={10} /></div>;
  if (exp.error || !exp.data || !meta || !cfg) return <div className="space-y-3"><PageHeader title={id} /><ErrorState error={exp.error ?? "not found"} /></div>;
  const running = meta.status === "queued" || meta.status === "running";
  const fams = { entry: [...new Set((cfg.resolved_entries ?? []).map((e) => e.family))], exit: [...new Set((cfg.resolved_exits ?? []).map((e) => e.family))] };
  const base = comp.data?.baseline.index;
  return (
    <div className="space-y-3">
      <PageHeader title={meta.name || id} sub={<span className="flex flex-wrap items-center gap-2"><StatusBadge status={meta.status} />{meta.experiment_id} · {meta.dataset}@{meta.dataset_version} · {meta.n_symbols} symbols · {meta.start}..{meta.analysis_end} {meta.periods.map((p) => <PeriodBadge key={p} period={p} />)}<PeriodBadge period="EXPLORATORY" /></span>}
        actions={<>
          {running && <Button size="sm" variant="outline" onClick={() => cancel.mutate(id)}><Ban className="size-3.5" />Cancel</Button>}
          {meta.status === "done" && <Button size="sm" variant="outline" disabled={repro.isPending} onClick={() => repro.mutate(id, { onSuccess: (r) => toast.success(`reproduction ${r.reproduction}: ${r.identical}/${r.cells} cells identical`), onError: (e) => toast.error(String(e)) })}><Repeat className="size-3.5" />Reproduce</Button>}
          <Button size="sm" variant="outline" onClick={() => navigate(`/entry-exit/new?config=`)}>New</Button>
        </>} />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        <Stat label="Entries x exits" value={`${meta.n_entries} x ${meta.n_exits}`} sub={`${meta.n_cells} cells per partition`} />
        <Stat label="Stage" value={meta.stage ?? "—"} sub={running ? `${meta.progress_done}/${meta.progress_total}` : meta.summary ? `${meta.summary.seconds}s` : ""} />
        <Stat label="Portfolio backtests" value={fmt.int(meta.summary?.portfolio_runs)} hint="event-driven engine runs (cells x partitions, plus random-entry portfolios)" />
        <Stat label="Tests per partition" value={fmt.int(meta.n_cells)} hint="BH controls the FDR across them" />
        <Stat label="Prior experiments on these partitions" value={fmt.int(exp.data.prior_usage.reduce((a, b) => Math.max(a, b.experiments), 0))} hint="earlier entry-exit experiments that used the same partitions (the search is cumulative)" />
        <Stat label="Supported (train / validation)" value={`${meta.summary?.verdicts?.train?.supported ?? 0} / ${meta.summary?.verdicts?.validation?.supported ?? 0}`} />
      </div>
      {running && <ProgressBar {...eeProgress(meta, cfg.portfolio.run)} startedAt={meta.started_at_utc} />}
      {meta.status === "failed" && <ProblemPanel {...eeFailure(meta)} />}
      {meta.status === "cancelled" && <div className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-xs">Cancelled{meta.error ? `: ${meta.error}` : ""}. Results finished before the cancellation are shown; multiple-testing corrections cover only them.</div>}
      {!done ? null : (
        <Tabs defaultValue="matrix">
          <TabsList>
            <TabsTrigger value="matrix">Performance matrix</TabsTrigger>
            <TabsTrigger value="hold-entry">Same entry, different exits</TabsTrigger>
            <TabsTrigger value="hold-exit">Same exit, different entries</TabsTrigger>
            <TabsTrigger value="timing">Exit timing vs random</TabsTrigger>
            <TabsTrigger value="wf">Walk-forward & sensitivity</TabsTrigger>
            <TabsTrigger value="about">Methodology</TabsTrigger>
          </TabsList>
          <TabsContent value="matrix">
            <Panel title="Entry-exit performance matrix" actions={
              <div className="flex flex-wrap items-end gap-2">
                <Field label="Metric"><ChoiceSelect value={metric} onChange={setMetric} className="h-7 w-64 text-xs" options={metrics.map((x) => ({ value: x, label: METRIC_LABEL[x] ?? x }))} aria-label="metric" /></Field>
                <Field label="Partition"><ChoiceSelect value={part} onChange={setPartition} className="h-7 w-32 text-xs" options={parts.map((x) => ({ value: x, label: x }))} aria-label="partition" /></Field>
                <Field label="Entry family"><ChoiceSelect value={filters.entryFam || "all"} onChange={(v) => setFilters({ ...filters, entryFam: v === "all" ? "" : v })} className="h-7 w-32 text-xs" options={[{ value: "all", label: "all" }, ...fams.entry.map((x) => ({ value: x, label: x }))]} aria-label="entry family" /></Field>
                <Field label="Exit family"><ChoiceSelect value={filters.exitFam || "all"} onChange={(v) => setFilters({ ...filters, exitFam: v === "all" ? "" : v })} className="h-7 w-32 text-xs" options={[{ value: "all", label: "all" }, { value: "deployable", label: "deployable only" }, ...fams.exit.map((x) => ({ value: x, label: x }))]} aria-label="exit family" /></Field>
                <Field label="Min trades"><NumberInput className="h-7 w-20" value={filters.minN} min={0} onChange={(v) => setFilters({ ...filters, minN: v ?? 0 })} /></Field>
                <Field label="q < 0.05 only"><div className="flex h-7 items-center"><Switch checked={filters.sigOnly} onCheckedChange={(x) => setFilters({ ...filters, sigOnly: x })} /></div></Field>
              </div>}>
              {matrix.isLoading ? <Loading rows={6} /> : matrix.error ? <ErrorState error={matrix.error} /> : matrix.data ? <MatrixGrid m={matrix.data} id={id} filters={filters} /> : null}
            </Panel>
          </TabsContent>
          <TabsContent value="hold-entry">
            <Panel title={`Exit effect on identical entries: each exit minus the baseline exit (${base !== null && base !== undefined ? exitNames[base] : "none"})`}
              info="paired differences of net trade returns on the same events; CI from a date-block bootstrap; p from sign flips by date block; q: BH across all pairs of the partition">
              {!comp.data?.hold_entry.length ? <Empty title="No baseline exit in this experiment" /> : (
                <div className="overflow-auto">
                  {comp.data.resolution_note && <div className="mb-1 text-2xs text-warn">{comp.data.resolution_note}</div>}
                  <table className="w-full text-xs">
                    <thead><tr className="text-left text-2xs text-muted-foreground"><th>entry</th><th>exit</th><th>partition</th><th className="text-right">trades</th><th className="text-right">mean diff</th><th className="text-right">95% CI</th><th className="text-right">better share</th><th className="text-right">p</th><th className="text-right">q</th><th className="text-right" title="BH q from the normal approximation of the same sign-flip null">q (normal)</th></tr></thead>
                    <tbody>{comp.data.hold_entry.filter((r) => r.partition === part).sort((a, b) => a.entry - b.entry || (b.diff ?? -9) - (a.diff ?? -9)).map((r) => (
                      <tr key={`${r.cell}${r.partition}`} className="cursor-pointer border-t hover:bg-accent/40" onClick={() => navigate(`/entry-exit/experiments/${id}/cells/${r.cell}`)}>
                        <td>{entryNames[r.entry]}</td><td>{exitNames[r.exit]}</td><td>{r.partition}</td><td className="num text-right">{r.n}</td>
                        <td className="text-right">{pctc(r.diff)}</td><td className="num text-right text-muted-foreground">{r.ci ? `[${fmt.pct(r.ci[0])}, ${fmt.pct(r.ci[1])}]` : "—"}</td>
                        <td className="num text-right">{fmt.pct(r.win_share, 0, false)}</td><td className="text-right">{qv(r.p)}</td><td className="text-right">{qv(r.q)}</td><td className="text-right">{qv(r.q_z)}</td>
                      </tr>))}</tbody>
                  </table>
                </div>
              )}
            </Panel>
          </TabsContent>
          <TabsContent value="hold-exit">
            <Panel title="Entry quality under a fixed exit: excess over matched random entries replayed with the same exit" info="the random entries share the session and matching group of each event and are managed by the identical exit plan, so the excess isolates the entry">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {Object.entries(comp.data?.hold_exit ?? {}).map(([pi, byPart]) => (
                  <div key={pi} className="rounded-md border p-2">
                    <div className="mb-1 text-xs font-medium">{exitNames[Number(pi)]}</div>
                    <table className="w-full text-2xs"><tbody>{(byPart[part] ?? []).map((r) => (
                      <tr key={r.cell} className="cursor-pointer border-t hover:bg-accent/40" onClick={() => navigate(`/entry-exit/experiments/${id}/cells/${r.cell}`)}>
                        <td className="truncate">{entryNames[r.entry]}</td><td className="num text-right">{r.n}</td><td className="text-right">{pctc(r.excess)}</td><td className="text-right">{qv(r.q)}</td><td className="text-right"><VerdictBadge v={r.verdict} /></td>
                      </tr>))}</tbody></table>
                  </div>
                ))}
              </div>
            </Panel>
          </TabsContent>
          <TabsContent value="timing">
            <div className="mb-2 flex items-end gap-2"><Field label="Partition"><ChoiceSelect value={part} onChange={setPartition} className="h-7 w-32 text-xs" options={parts.map((x) => ({ value: x, label: x }))} aria-label="timing partition" /></Field></div>
            <TimingPanel id={id} exp={exp.data} part={part} />
          </TabsContent>
          <TabsContent value="wf">
            <WalkForwardPanel id={id} wf={wf.data} entryNames={entryNames} exitNames={exitNames} />
          </TabsContent>
          <TabsContent value="about">
            <Panel title="Methodology and data">
              <KV rows={[
                ["Events", `${cfg.sampling.mode} sampling; entry at the next open; eligibility: price >= ${cfg.eligibility.min_price}, $ volume >= ${fmt.usd(cfg.eligibility.min_dollar_volume, { compact: true })}, history >= ${cfg.eligibility.min_history}`],
                ["Exits", `max holding ${cfg.max_hold} sessions; R reference ${cfg.ref_atr_mult} x ATR(${cfg.ref_atr_n}); stop-first on same-bar stop/target; the entry session is protected after the fill`],
                ["Exit timing", cfg.timing?.enabled ? `each condition exit replayed ${cfg.timing.draws} times with its signals moved to random dates on the same stock${cfg.calibration?.enabled ? "; calibration rules on (placebos and a cheating rule)" : ""}${cfg.random_entries?.enabled ? `; random entries: ${cfg.random_entries.n}` : ""}` : "off"],
                ["Controls", `${cfg.controls.draws} matched random stocks per event (volatility groups ${cfg.matching.vol_groups}${cfg.matching.sector ? ", sector" : ""}) and ${cfg.controls.date_draws} random dates of the same stock, replayed with the same exit; ${cfg.controls.permutations} permutations`],
                ["Portfolio", `event-driven engine per partition; ${cfg.portfolio.sizing.mode} sizing (${cfg.portfolio.sizing.mode === "risk" ? `${fmt.pct(cfg.portfolio.sizing.risk_per_position, 1, false)} risk` : `${fmt.pct(cfg.portfolio.sizing.position_weight, 0, false)} weight`}), max ${cfg.portfolio.max_positions} positions, ${cfg.portfolio.random_controls} random-entry portfolios per cell`],
                ["Costs", `slippage ${cfg.execution.slippage_bps} bps; commission ${cfg.execution.commission.bps} bps + $${cfg.execution.commission.per_share}/sh; spread ${cfg.execution.spread.model}`],
                ["Partitions", (exp.data.study?.parts ?? []).map((x) => `${x.name} (${x.role}) ${x.start}..${x.end}`).join(" · ") + (cfg.evaluate_test ? " · TEST SPENT" : " · test locked")],
                ["Survivorship", "universe lists are today's: delisted stocks are absent (results describe survivors)"],
                ["Entries", (exp.data.entries ?? []).map((e) => `${e.name}: ${e.counts.events} events`).join(" · ")],
              ]} />
            </Panel>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

function WalkForwardPanel({ id, wf, entryNames, exitNames }: { id: string; wf: ReturnType<typeof useWalkForward>["data"]; entryNames: string[]; exitNames: string[] }) {
  if (!wf) return <Loading rows={4} />;
  const groups = Object.entries(wf.walk_forward.groups ?? {});
  const label = (cell: string | null) => (cell ? `${entryNames[Number(cell.slice(1, 4))]} -> ${exitNames[Number(cell.slice(6, 9))]}` : "—");
  return (
    <div className="space-y-3">
      <Panel title={`Walk-forward selection (objective: ${wf.walk_forward.objective})`} info="each fold chooses the best cell of a group on its training window and records that cell's result on the next window; folds never enter the locked test partition">
        {!wf.walk_forward.folds.length ? <Empty title="No walk-forward folds (window too short or disabled)" /> : (
          <div className="overflow-auto">
            <div className="mb-1 text-2xs text-muted-foreground">Folds: {wf.walk_forward.folds.map((f) => `#${f.fold} train ${f.train} → test ${f.test}`).join(" · ")}</div>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-2xs text-muted-foreground"><th>selection group</th><th className="text-right">OOS of selected</th><th className="text-right">OOS mean of all</th><th className="text-right">selection skill</th><th className="text-right">IS/OOS rank corr.</th><th>chosen per fold</th></tr></thead>
              <tbody>{groups.map(([g, v]) => (
                <tr key={g} className="border-t align-top">
                  <td className="py-1">{g.replace(/entry (\d+)/, (_, i) => `entry ${entryNames[Number(i)]}`).replace(/exit (\d+)/, (_, i) => `exit ${exitNames[Number(i)]}`)}</td>
                  <td className="text-right">{pctc(v.oos_mean_selected)}</td><td className="text-right">{pctc(v.oos_mean_all)}</td><td className="text-right">{pctc(v.selection_skill)}</td>
                  <td className="num text-right">{fmt.num(v.spearman_mean, 2)}</td>
                  <td className="text-2xs text-muted-foreground">{v.folds.map((f) => <div key={f.fold}>#{f.fold}: <a className="text-primary underline" href={f.chosen ? `/entry-exit/experiments/${id}/cells/${f.chosen}` : undefined}>{label(f.chosen)}</a> IS {fmt.pct(f.is_value)} → OOS {fmt.pct(f.oos_value)} (rank {f.oos_rank ?? "—"}/{f.n_candidates ?? 0})</div>)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </Panel>
      <Panel title="Parameter sensitivity (exit sweeps)" info="train and validation excess over matched random entries per grid value, and the neighbourhood-smoothed train score: prefer a stable region to an isolated best point">
        {!wf.sensitivity.length ? <Empty title="No exit parameter sweeps in this experiment" /> : (
          <div className="grid gap-3 lg:grid-cols-2">
            {wf.sensitivity.map((s, i) => {
              const xs = s.points.map((p, k) => [typeof p.params[s.params[0]] === "number" ? (p.params[s.params[0]] as number) : k, p] as const);
              return (
                <div key={i} className="rounded-md border p-2">
                  <div className="text-xs font-medium">{entryNames[s.entry]} · {s.component} · {s.params.join(", ")}</div>
                  <div className="text-2xs text-muted-foreground">best on train: {label(s.best_train)} · plateau: {label(s.plateau)} · positive in train {fmt.pct(s.share_positive_train, 0, false)}, in both {fmt.pct(s.share_positive_both, 0, false)} · train/validation rank corr. {fmt.num(s.spearman_train_validation, 2)}</div>
                  <XYChart name={`sensitivity-${i}`} unit="pct" height={180} xLabel={s.params[0]} series={[
                    { key: "train", name: "train", color: PALETTE[0], points: xs.map(([x, p]) => [x, p.train]) },
                    { key: "smoothed", name: "train (smoothed)", color: PALETTE[0], dashed: true, points: xs.map(([x, p]) => [x, p.smoothed_train]) },
                    { key: "validation", name: "validation", color: PALETTE[1], points: xs.map(([x, p]) => [x, p.validation]) },
                  ]} />
                </div>
              );
            })}
          </div>
        )}
      </Panel>
    </div>
  );
}
