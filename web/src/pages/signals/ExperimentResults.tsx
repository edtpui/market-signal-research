// Random Benchmarking for one experiment: progress, methodology, data coverage and research-integrity warnings, then
// every signal's excess return over its matched random controls (forest plot with clustered CIs), actual vs random
// means, random-date and benchmark comparisons, multiple-testing-adjusted significance and the verdict. Click a
// signal for its full results.
import { Ban, Copy, Repeat } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { useCancelSignals, useExperimentResults, useReproduceSignals, useSignalExperiment, type ResultRow } from "@/api/signals";
import { ChoiceSelect, Field } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { BarChart, ChartActions, ForestPlot } from "@/components/ResearchCharts";
import { Empty, ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge, Stat, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import * as fmt from "@/lib/format";
import { navigate } from "@/lib/router";
import { DirectionBadge, ScopeBadge, SubNav, VERDICT_COLOR, VerdictBadge, ci, pct, pval } from "@/pages/signals/common";

export function ExperimentResults({ id }: { id: string }) {
  const exp = useSignalExperiment(id);
  const meta = exp.data?.experiment;
  const cfg = exp.data?.config;
  const running = meta?.status === "queued" || meta?.status === "running";
  const parts = useMemo(() => {
    const p = (exp.data?.study?.parts ?? []).map((x) => x.name);
    return p.length > 1 ? ["combined", ...p] : p;
  }, [exp.data]);
  const [partition, setPartition] = useState<string>("");
  const [horizon, setHorizon] = useState<number | null>(null);
  const part = partition || parts[0] || "";
  const h = horizon ?? cfg?.primary_horizon ?? cfg?.horizons?.[0] ?? 20;
  const res = useExperimentResults(id, part || undefined, h, running);
  const allH = useExperimentResults(id, part || undefined, undefined, running);
  const cancel = useCancelSignals();
  const repro = useReproduceSignals();

  const rows = res.data ?? [];
  const sorted = useMemo(() => [...rows].sort((a, b) => (b.excess ?? -9) * (b.direction === "bullish" ? 1 : -1) - (a.excess ?? -9) * (a.direction === "bullish" ? 1 : -1)), [rows]);
  const byDirection = useMemo(() => {
    const out: Record<string, { n: number; ex: number[] }> = {};
    for (const r of rows) {
      out[r.direction] ??= { n: 0, ex: [] };
      out[r.direction].n += 1;
      if (r.excess !== null) out[r.direction].ex.push(r.excess);
    }
    return out;
  }, [rows]);
  const horizonProfile = useMemo(() => {
    const hs = cfg?.horizons ?? [];
    return hs.map((hh) => {
      const rr = (allH.data ?? []).filter((r) => r.horizon === hh);
      const sig = rr.filter((r) => r.verdict === "supported" || r.verdict === "significant_negligible").length;
      const dir = rr.map((r) => (r.excess ?? 0) * (r.direction === "bullish" ? 1 : -1));
      return { label: `${hh}`, value: dir.length ? dir.reduce((a, b) => a + b, 0) / dir.length : null, n: rr.length, note: `${sig} significant of ${rr.length}` };
    });
  }, [allH.data, cfg]);

  if (exp.isLoading) return <div className="space-y-3"><PageHeader title={id} /><SubNav /><Loading rows={10} /></div>;
  if (exp.error || !exp.data || !meta || !cfg) return <div className="space-y-3"><PageHeader title={id} /><SubNav /><ErrorState error={exp.error ?? "not found"} /></div>;
  const d = exp.data;
  const rep = d.study?.report as Record<string, unknown> | undefined;
  const done = meta.progress_done + meta.progress_failed;

  const cols: Col<ResultRow>[] = [
    { id: "name", header: "Signal", value: (r) => r.name, cell: (r) => <div><div className="font-medium">{r.name}</div><div className="num text-2xs text-muted-foreground">{r.signal_key}</div></div> },
    { id: "family", header: "Family", value: (r) => r.family },
    { id: "dir", header: "Dir", value: (r) => r.direction, cell: (r) => <DirectionBadge d={r.direction} /> },
    { id: "scope", header: "Scope", value: (r) => r.scope, cell: (r) => <ScopeBadge s={r.scope} />, hidden: true },
    { id: "n", header: "Events", align: "right", value: (r) => r.n, cell: (r) => fmt.int(r.n) },
    { id: "dates", header: "Sessions", align: "right", value: (r) => r.n_dates, cell: (r) => fmt.int(r.n_dates), hidden: true },
    { id: "mean", header: `Mean ${h}d`, align: "right", value: (r) => r.mean, cell: (r) => pct(r.mean) },
    { id: "excess", header: "Excess vs matched", align: "right", value: (r) => r.excess, cell: (r) => pct(r.excess) },
    { id: "ci", header: "Clustered 95% CI", value: (r) => r.ci_lo, cell: (r) => ci(r.ci_lo, r.ci_hi) },
    { id: "p", header: "p (rand.)", align: "right", value: (r) => r.p_matched, cell: (r) => pval(r.p_matched) },
    { id: "q", header: "q (BH)", align: "right", value: (r) => r.q_matched, cell: (r) => pval(r.q_matched) },
    { id: "rd", header: "Excess vs random dates", align: "right", value: (r) => r.excess_rd, cell: (r) => pct(r.excess_rd) },
    { id: "prd", header: "q (dates)", align: "right", value: (r) => r.q_rd, cell: (r) => pval(r.q_rd), hidden: true },
    { id: "bench", header: `Excess vs ${cfg.benchmarks[0] ?? "bench"}`, align: "right", value: (r) => r.excess_bench, cell: (r) => pct(r.excess_bench) },
    { id: "win", header: "Win rate", align: "right", value: (r) => r.win_rate, cell: (r) => <span className="num">{fmt.pct(r.win_rate, 1, false)}</span> },
    { id: "ann", header: "Ann. net excess", align: "right", value: (r) => r.ann_net_excess, cell: (r) => pct(r.ann_net_excess, 1) },
    { id: "cons", header: "Years in dir.", align: "right", value: (r) => r.consistency_years, cell: (r) => <span className="num">{fmt.pct(r.consistency_years, 0, false)}</span> },
    { id: "verdict", header: "Verdict", value: (r) => r.verdict, cell: (r) => <VerdictBadge v={r.verdict} /> },
  ];

  return (
    <div className="space-y-3" data-experiment={id}>
      <PageHeader title={<span className="flex items-center gap-2">{meta.name || "Signal experiment"} <StatusBadge status={meta.status} /></span>}
        sub={<span className="num">{id} · {meta.kind}{meta.parent_id ? ` of ${meta.parent_id}` : ""}</span>}
        actions={<>
          {running && <Button size="sm" variant="outline" onClick={() => cancel.mutate(id, { onSuccess: () => toast.info("Cancelling…") })} data-action="cancel"><Ban className="size-3.5" />Cancel</Button>}
          <Button size="sm" variant="outline" onClick={() => navigate(`/signals/new?from=${id}`)} data-action="clone"><Copy className="size-3.5" />Clone</Button>
          <Button size="sm" variant="outline" disabled={meta.status !== "done" || repro.isPending} data-action="reproduce"
            onClick={() => repro.mutate(id, { onSuccess: () => { toast.success("Reproduction started (Research History)"); } })}><Repeat className="size-3.5" />Reproduce</Button>
        </>} />
      <SubNav />
      {meta.error && meta.status !== "cancelled" && <ErrorState error={meta.error} />}
      {running && (
        <div className="rounded-md border px-3 py-2 text-xs" data-progress>
          <div className="mb-1 flex justify-between"><span>Running {meta.n_signals} signals</span><span className="num">{done} / {meta.n_signals}{meta.progress_failed ? ` (${meta.progress_failed} failed)` : ""}</span></div>
          <div className="h-1.5 overflow-hidden rounded bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${(100 * done) / Math.max(1, meta.n_signals)}%` }} /></div>
        </div>
      )}
      <div className="grid grid-cols-2 gap-1.5 md:grid-cols-4 lg:grid-cols-8">
        <Stat label="Signals" value={`${meta.progress_done}${meta.progress_failed ? ` (+${meta.progress_failed} failed)` : ""} / ${meta.n_signals}`} />
        <Stat label="Universe" value={`${meta.n_symbols} symbols`} sub={meta.universe_name ?? ""} />
        <Stat label="Window" value={`${meta.start} … ${meta.analysis_end}`}
          sub={(meta.partitions ?? []).filter((p) => p.locked).map((p) => `test ${p.start} … ${p.end} locked`).join("") || (meta.evaluate_test ? "test period evaluated" : "")} />
        <Stat label="Dataset" value={`${meta.dataset}`} sub={<span className="num">{meta.dataset_version}</span>} />
        <Stat label="Horizons" value={cfg.horizons.join(" · ")} sub={`primary ${cfg.primary_horizon}`} />
        <Stat label="Sampling" value={cfg.sampling.mode} sub={`entry ${cfg.entry.replace("_", " ")}`} />
        <Stat label="Controls" value={`${cfg.permutations} perms`} sub={`vol ${cfg.matching.vol_groups} groups${cfg.matching.sector ? " + sector" : ""}${cfg.matching.liquidity ? " + liquidity" : ""}`} />
        <Stat label="Seed" value={String(cfg.seed)} sub={<span className="num">{meta.methodology_hash.slice(0, 10)}</span>} hint="methodology hash: experiments with the same hash measure signals the same way" />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">{meta.periods.map((p) => <PeriodBadge key={p} period={p} />)}<PeriodBadge period="EXPLORATORY" />
        {meta.reproduction && <span className={meta.reproduction.identical ? "text-xs text-pos" : "text-xs text-warn"}>Reproduction of {meta.reproduction.experiment_id}: {meta.reproduction.identical ? `identical (${meta.reproduction.compared} signals)` : `differs: ${meta.reproduction.differing_signals.join(", ")}`}</span>}
      </div>

      <Warnings d={d} rep={rep} />

      <Panel title="Random benchmarking" actions={
        <div className="flex items-center gap-2">
          <Field label="" className="w-36"><ChoiceSelect className="h-7" value={part} onChange={setPartition} aria-label="partition" options={parts.map((p) => ({ value: p, label: p }))} /></Field>
          <Field label="" className="w-28"><ChoiceSelect className="h-7" value={String(h)} onChange={(v) => setHorizon(Number(v))} aria-label="horizon" options={cfg.horizons.map((x) => ({ value: String(x), label: `${x} sessions` }))} /></Field>
          <ChartActions name={`${id}-${part}-${h}-excess`} config={cfg} header={["signal_key", "partition", "horizon", "n", "excess", "ci_lo", "ci_hi", "p", "q", "verdict"]}
            rows={sorted.map((r) => [r.signal_key, r.partition, r.horizon, r.n, r.excess, r.ci_lo, r.ci_hi, r.p_matched, r.q_matched, r.verdict])} />
        </div>}>
        {res.isLoading ? <Loading rows={6} /> : !rows.length ? <Empty title={running ? "Results appear when the experiment finishes" : "No results"}>{running ? "Signals are written as they finish; the multiple-testing correction runs once all are done." : null}</Empty> : (
          <div className="space-y-3">
            <div className="text-2xs text-muted-foreground">Excess = signal return − exact mean of its matched random controls (same session, same volatility group{cfg.matching.sector ? ", sector" : ""}), per event, averaged. Bars: 95% CI from date-block / ticker cluster bootstraps (the wider). Colour: verdict.</div>
            <ForestPlot name="excess-forest" rows={sorted.map((r) => ({
              key: r.signal_key, label: `${r.direction === "bullish" ? "▲" : "▼"} ${r.name}`, value: r.excess, lo: r.ci_lo, hi: r.ci_hi,
              tone: VERDICT_COLOR[r.verdict], sub: `n=${fmt.int(r.n)} q=${r.q_matched === null ? "—" : r.q_matched.toFixed(3)}`,
              onClick: () => navigate(`/signals/experiments/${id}/${encodeURIComponent(r.signal_key)}`),
            }))} />
            <DataTable name={`${id}-results`} rows={sorted} cols={cols} rowKey={(r) => r.signal_key} maxHeight={560}
              onRowClick={(r) => navigate(`/signals/experiments/${id}/${encodeURIComponent(r.signal_key)}`)} />
          </div>
        )}
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Average directional excess by holding period" actions={<ChartActions name={`${id}-horizon-profile`} config={cfg} header={["horizon", "mean_directional_excess", "tests"]} rows={horizonProfile.map((x) => [x.label, x.value, x.n])} />}>
          <BarChart name="horizon-profile" rows={horizonProfile} />
          <div className="mt-1 text-2xs text-muted-foreground">Mean over signals of the excess in each signal's own direction ({part}); hover for the count of significant tests.</div>
        </Panel>
        <Panel title="Bullish vs bearish signals" actions={<ChartActions name={`${id}-direction`} config={cfg} header={["direction", "signals", "mean_excess"]}
          rows={Object.entries(byDirection).map(([k, v]) => [k, v.n, v.ex.length ? v.ex.reduce((a, b) => a + b, 0) / v.ex.length : null])} />}>
          <BarChart name="direction" rows={Object.entries(byDirection).map(([k, v]) => ({ label: k, value: v.ex.length ? v.ex.reduce((a, b) => a + b, 0) / v.ex.length : null, n: v.n }))} />
          <div className="mt-1 text-2xs text-muted-foreground">Mean raw excess vs matched controls at {h} sessions: a bullish signal works when it is positive, a bearish one when it is negative.</div>
        </Panel>
      </div>

      {d.runs.some((r) => r.status === "failed") && (
        <Panel title="Failed signals (isolated: the others completed)">
          {d.runs.filter((r) => r.status === "failed").map((r) => <div key={r.signal_key} className="text-xs"><span className="num font-medium">{r.signal_key}</span>: <span className="text-neg">{r.error}</span></div>)}
        </Panel>
      )}

      <Panel title="Provenance">
        <KV rows={[
          ["Config hash", <span className="num text-2xs">{meta.config_hash}</span>],
          ["Methodology hash", <span className="num text-2xs">{meta.methodology_hash}</span>],
          ["Code (signals, research, data)", <span className="num text-2xs">{String((d.manifest?.code as Record<string, unknown> | undefined)?.signals_research_data_sha256 ?? "—")}</span>],
          ["Created / finished", <span className="num">{fmt.utc(meta.created_at_utc)} · {fmt.utc(meta.finished_at_utc)}</span>],
          ["Timings", <span className="num">prepare {d.study?.timings?.prepare_s ?? "—"} s · total {meta.summary?.seconds ?? "—"} s</span>],
        ]} />
        <details className="mt-2"><summary className="cursor-pointer text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Resolved configuration</summary>
          <pre className="num mt-1 max-h-[420px] overflow-auto rounded-md border bg-surface-2 p-2 text-[11px] leading-snug">{JSON.stringify(cfg, null, 1)}</pre></details>
      </Panel>
    </div>
  );
}

function Warnings({ d, rep }: { d: NonNullable<ReturnType<typeof useSignalExperiment>["data"]>; rep: Record<string, unknown> | undefined }) {
  const items: { tone: "warn" | "info"; text: string }[] = [];
  if (d.resolution_note) items.push({ tone: "warn", text: d.resolution_note });
  if (d.partial) items.push({ tone: "warn", text: "Partial results: the experiment was cancelled; BH-adjusted q-values cover only the signals that finished." });
  for (const u of d.prior_usage) {
    if (u.experiments_so_far > 1) items.push({ tone: u.role === "test" ? "warn" : "info", text: `The ${u.partition} period (${u.start} … ${u.end}) of this dataset has been used by ${u.experiments_so_far} experiments with ${fmt.int(u.tests_so_far)} tests so far: findings there carry that many chances to be lucky.` });
  }
  const reuse = d.locked.filter((l) => !l.first_use);
  if (reuse.length) items.push({ tone: "warn", text: `${reuse.length} signal(s) were already evaluated on this test period: the locked result is the first evaluation (${reuse[0].locked_experiment}); these are re-evaluations.` });
  if (rep) {
    const ee = (rep.early_end as string[] | undefined) ?? [];
    items.push({ tone: "info", text: `${String(rep.survivorship ?? "")}` });
    if (rep.warmup_note) items.push({ tone: "info", text: String(rep.warmup_note) });
    const never = (rep.never_eligible as string[] | undefined) ?? [];
    if (never.length) items.push({ tone: "info", text: `${never.length} symbols were never eligible (price, liquidity or history filters): ${never.slice(0, 12).join(", ")}${never.length > 12 ? " …" : ""}` });
    const late = (rep.late_start as string[] | undefined) ?? [];
    if (late.length || ee.length) items.push({ tone: "info", text: `Coverage: ${late.length} symbols start after the window start, ${ee.length} end before its end; missing bars are never filled (events without prices are excluded and counted).` });
  }
  if (!items.length) return null;
  return (
    <Panel title="Methodology notes and research-integrity warnings">
      <ul className="space-y-1 text-xs" data-warnings>
        {items.map((x, i) => <li key={i} className={x.tone === "warn" ? "text-warn" : "text-muted-foreground"}>• {x.text}</li>)}
      </ul>
    </Panel>
  );
}
