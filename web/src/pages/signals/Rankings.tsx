// Signal Rankings: every (signal, partition, horizon) result across experiments, ranked on statistical AND economic
// evidence (not on raw average return), with BH re-applied across every row shown, filters, the forest plot of the
// strongest rows and family-level summaries.
import { useMemo, useState } from "react";

import { useCatalog, useRankings, type ResultRow, type Verdict } from "@/api/signals";
import { ChoiceSelect, Field } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { BarChart, ChartActions, ForestPlot } from "@/components/ResearchCharts";
import { Empty, ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { Input, Switch } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { Link, navigate } from "@/lib/router";
import { DirectionBadge, ScopeBadge, SubNav, VERDICT_COLOR, VerdictBadge, ci, pct, pval } from "@/pages/signals/common";

const ORDER: Record<Verdict, number> = { supported: 0, significant_negligible: 1, not_significant: 2, reversed: 3, insufficient_sample: 4 };

export function Rankings() {
  const cat = useCatalog();
  const [partition, setPartition] = useState("validation");
  const [horizon, setHorizon] = useState("20");
  const [family, setFamily] = useState("all");
  const [direction, setDirection] = useState("all");
  const [verdict, setVerdict] = useState("all");
  const [minN, setMinN] = useState(30);
  const [onlySig, setOnlySig] = useState(false);
  const q = useRankings({ partition: partition === "all" ? undefined : partition, horizon: horizon === "all" ? undefined : Number(horizon),
    family: family === "all" ? undefined : family, direction: direction === "all" ? undefined : direction, verdict: verdict === "all" ? undefined : verdict });
  const rows = useMemo(() => (q.data?.rows ?? []).filter((r) => r.n >= minN && (!onlySig || r.significant))
    .sort((a, b) => ORDER[a.verdict] - ORDER[b.verdict] || (a.q_matched ?? 1) - (b.q_matched ?? 1)
      || (b.ann_net_excess ?? -1) - (a.ann_net_excess ?? -1)), [q.data, minN, onlySig]);
  const top = rows.slice(0, 30);
  const fams = useMemo(() => {
    const m = new Map<string, { n: number; ex: number[]; sig: number }>();
    for (const r of rows) {
      const e = m.get(r.family) ?? { n: 0, ex: [], sig: 0 };
      e.n += 1;
      if (r.excess !== null) e.ex.push(r.excess * (r.direction === "bullish" ? 1 : -1));
      if (r.verdict === "supported" || r.verdict === "significant_negligible") e.sig += 1;
      m.set(r.family, e);
    }
    return [...m.entries()].map(([k, v]) => ({ label: k, value: v.ex.length ? v.ex.reduce((a, b) => a + b, 0) / v.ex.length : null, n: v.n, note: `${v.sig} significant` }))
      .sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
  }, [rows]);

  const cols: Col<ResultRow>[] = [
    { id: "rank", header: "#", align: "right", value: (r) => rows.indexOf(r) + 1 },
    { id: "name", header: "Signal", value: (r) => r.name, cell: (r) => <div><div className="font-medium">{r.name}</div><div className="num text-2xs text-muted-foreground">{r.signal_key}</div></div> },
    { id: "family", header: "Family", value: (r) => r.family },
    { id: "dir", header: "Dir", value: (r) => r.direction, cell: (r) => <DirectionBadge d={r.direction} /> },
    { id: "scope", header: "Scope", value: (r) => r.scope, cell: (r) => <ScopeBadge s={r.scope} />, hidden: true },
    { id: "part", header: "Partition", value: (r) => r.partition },
    { id: "h", header: "h", align: "right", value: (r) => r.horizon },
    { id: "n", header: "Events", align: "right", value: (r) => r.n, cell: (r) => fmt.int(r.n) },
    { id: "excess", header: "Excess vs matched", align: "right", value: (r) => r.excess, cell: (r) => pct(r.excess) },
    { id: "ci", header: "95% CI", value: (r) => r.ci_lo, cell: (r) => ci(r.ci_lo, r.ci_hi) },
    { id: "q", header: "q (experiment)", align: "right", value: (r) => r.q_matched, cell: (r) => pval(r.q_matched) },
    { id: "qall", header: "q (all rows)", align: "right", value: (r) => r.q_across_rows ?? null, cell: (r) => pval(r.q_across_rows) },
    { id: "rd", header: "vs random dates", align: "right", value: (r) => r.excess_rd, cell: (r) => pct(r.excess_rd) },
    { id: "bench", header: "vs benchmark", align: "right", value: (r) => r.excess_bench, cell: (r) => pct(r.excess_bench) },
    { id: "ann", header: "Ann. net excess", align: "right", value: (r) => r.ann_net_excess, cell: (r) => pct(r.ann_net_excess, 1) },
    { id: "cons", header: "Years in dir.", align: "right", value: (r) => r.consistency_years, cell: (r) => <span className="num">{fmt.pct(r.consistency_years, 0, false)}</span> },
    { id: "verdict", header: "Verdict", value: (r) => ORDER[r.verdict], cell: (r) => <VerdictBadge v={r.verdict} /> },
    { id: "exp", header: "Experiment", value: (r) => r.experiment_id, cell: (r) => <Link className="num text-2xs text-primary hover:underline" to={`/signals/experiments/${r.experiment_id}`} onClick={(e) => e.stopPropagation()}>{r.experiment_id}</Link> },
    { id: "data", header: "Data", value: (r) => `${r.dataset} ${r.n_symbols}`, cell: (r) => <span className="text-2xs text-muted-foreground">{r.dataset} · {r.n_symbols}</span>, hidden: true },
  ];

  return (
    <div className="space-y-3">
      <PageHeader title="Signal Rankings" sub="Ranked by verdict (statistical AND economic significance), then BH q, then annualized net excess. A statistically significant but economically negligible signal ranks below a supported one and says so." />
      <SubNav />
      <Panel title="Filters">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-8">
          <Field label="Partition"><ChoiceSelect value={partition} onChange={setPartition} aria-label="partition" options={["all", "train", "validation", "test", "combined", "full"].map((p) => ({ value: p, label: p }))} /></Field>
          <Field label="Holding period"><ChoiceSelect value={horizon} onChange={setHorizon} aria-label="horizon" options={["all", "1", "5", "10", "20", "60"].map((x) => ({ value: x, label: x === "all" ? "all" : `${x} sessions` }))} /></Field>
          <Field label="Family"><ChoiceSelect value={family} onChange={setFamily} aria-label="family" options={[{ value: "all", label: "all" }, { value: "combination", label: "combinations" }, ...(cat.data?.families ?? []).map((f) => ({ value: f.id, label: f.name }))]} /></Field>
          <Field label="Direction"><ChoiceSelect value={direction} onChange={setDirection} aria-label="direction" options={["all", "bullish", "bearish"].map((x) => ({ value: x, label: x }))} /></Field>
          <Field label="Verdict"><ChoiceSelect value={verdict} onChange={setVerdict} aria-label="verdict" options={["all", "supported", "significant_negligible", "not_significant", "reversed", "insufficient_sample"].map((x) => ({ value: x, label: x.replace("_", " ") }))} /></Field>
          <Field label="Min events"><Input className="num h-8 text-xs" type="number" value={minN} onChange={(e) => setMinN(Number(e.target.value) || 0)} aria-label="min events" /></Field>
          <Field label="Significant only"><div className="flex h-8 items-center"><Switch checked={onlySig} onCheckedChange={setOnlySig} aria-label="significant only" /></div></Field>
        </div>
        {q.data && <div className="mt-2 text-2xs text-muted-foreground">{q.data.note}</div>}
      </Panel>
      {q.error && <ErrorState error={q.error} />}
      {q.isLoading ? <Loading rows={8} /> : rows.length === 0 ? <Empty title="No results match">Run experiments in the builder; their tests appear here.</Empty> : (
        <>
          <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
            <Panel title={`Top ${top.length}: excess vs matched random stocks with 95% CI`} actions={<ChartActions name="rankings-top" header={["signal_key", "experiment", "partition", "horizon", "excess", "ci_lo", "ci_hi", "q", "verdict"]}
              rows={top.map((r) => [r.signal_key, r.experiment_id, r.partition, r.horizon, r.excess, r.ci_lo, r.ci_hi, r.q_matched, r.verdict])} />}>
              <ForestPlot name="rankings-forest" rows={top.map((r) => ({ key: `${r.experiment_id}|${r.signal_key}|${r.partition}|${r.horizon}`, label: `${r.direction === "bullish" ? "▲" : "▼"} ${r.name} · ${r.horizon}d`,
                value: r.excess, lo: r.ci_lo, hi: r.ci_hi, tone: VERDICT_COLOR[r.verdict], sub: `n=${fmt.int(r.n)}`, onClick: () => navigate(`/signals/experiments/${r.experiment_id}/${encodeURIComponent(r.signal_key)}`) }))} />
            </Panel>
            <Panel title="By family: mean directional excess" actions={<ChartActions name="rankings-families" header={["family", "mean_directional_excess", "tests"]} rows={fams.map((f) => [f.label, f.value, f.n])} />}>
              <BarChart name="family-excess" rows={fams} height={240} />
            </Panel>
          </div>
          <Panel title={`Ranked results (${rows.length})`}>
            <DataTable name="signal-rankings" rows={rows} cols={cols} maxHeight={640} rowKey={(r) => `${r.experiment_id}|${r.signal_key}|${r.partition}|${r.horizon}`}
              onRowClick={(r) => navigate(`/signals/experiments/${r.experiment_id}/${encodeURIComponent(r.signal_key)}`)} />
          </Panel>
        </>
      )}
    </div>
  );
}
