// One signal in one experiment: actual vs random by holding period, the randomized null distributions with the
// actual mean, the per-event excess distribution, CIs by horizon, the cumulative path, performance by year / sector /
// volatility regime / market regime, the event timeline, every test with its BH q and verdict, the events with their
// saved controls, and the import into the Strategy Library.
import { BookPlus, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import type { Row } from "@/api/client";
import { useImportSignal, useSignalEvents, useSignalExperiment, useSignalResult, type Breakdown, type GroupRow, type HorizonStats } from "@/api/signals";
import { ChoiceSelect, Field } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { LineChart } from "@/components/LineChart";
import { BarChart, ChartActions, ForestPlot, Histogram, XYChart } from "@/components/ResearchCharts";
import { Empty, ErrorState, KV, Loading, PageHeader, Panel, Stat } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { Link, navigate } from "@/lib/router";
import { DirectionBadge, ScopeBadge, VERDICT_COLOR, VerdictBadge, ci, pct, pval } from "@/pages/signals/common";

const C = { signal: "#3987e5", matched: "#d95926", rd: "#199e70", bench: "#9b9a94" };

function groupRows(g: GroupRow[] | undefined, key: "excess_matched" | "mean" | "excess_benchmark") {
  return (g ?? []).map((r) => ({ label: r.key, value: r[key], n: key === "excess_matched" ? r.n_matched : r.n }));
}

export function SignalResultPage({ id, signalKey }: { id: string; signalKey: string }) {
  const exp = useSignalExperiment(id);
  const res = useSignalResult(id, signalKey);
  const [partition, setPartition] = useState("");
  const [horizon, setHorizon] = useState<number | null>(null);
  const [tab, setTab] = useState<"matched" | "random_date">("matched");
  const [showEvents, setShowEvents] = useState(false);
  const events = useSignalEvents(id, signalKey, showEvents);
  const imp = useImportSignal();
  const [hold, setHold] = useState<string>("");
  const cfg = exp.data?.config;
  const summ = res.data?.summary;
  const parts = summ ? Object.keys(summ.partitions).sort((a, b) => (a === "combined" ? -1 : b === "combined" ? 1 : 0)) : [];
  const part = partition || parts[0] || "";
  const h = horizon ?? cfg?.primary_horizon ?? cfg?.horizons?.[0] ?? 20;
  const P = summ?.partitions[part];
  const hs: HorizonStats | undefined = P?.horizons[String(h)];
  const bd: Breakdown | null | undefined = P?.breakdowns[String(h)];
  const dir = summ?.signal.direction ?? "bullish";
  const bname = cfg?.benchmarks[0];

  const byHorizon = useMemo(() => {
    if (!P || !cfg) return [];
    return cfg.horizons.map((x) => ({ h: x, s: P.horizons[String(x)] }));
  }, [P, cfg]);

  const evCols: Col<Row>[] = useMemo(() => {
    const cols: Col<Row>[] = [
      { id: "date", header: "Signal date", value: (r) => String(r.date), cell: (r) => <span className="num">{String(r.date)}</span> },
      { id: "symbol", header: "Symbol", value: (r) => String(r.symbol), cell: (r) => <span className="num font-medium">{String(r.symbol)}</span> },
      { id: "part", header: "Partition", value: (r) => String(r.partition) },
      { id: "entry", header: "Entry", value: (r) => String(r.entry_date ?? ""), cell: (r) => <span className="num">{String(r.entry_date ?? "")}</span> },
      { id: "vg", header: "Vol group", align: "right", value: (r) => r.vol_group as number },
      { id: "sector", header: "Sector", value: (r) => String(r.sector ?? ""), hidden: true },
      { id: "matched", header: "Matched", value: (r) => String(r.matched), hidden: true },
      { id: "pool", header: "Pool", align: "right", value: (r) => r.pool_size as number },
      { id: "r", header: `Return ${h}d`, align: "right", value: (r) => r[`r_${h}`] as number, cell: (r) => pct(r[`r_${h}`] as number) },
      { id: "pm", header: "Matched mean", align: "right", value: (r) => r[`pool_${h}`] as number, cell: (r) => pct(r[`pool_${h}`] as number) },
      { id: "rd", header: "Random-date mean", align: "right", value: (r) => r[`rd_${h}`] as number, cell: (r) => pct(r[`rd_${h}`] as number) },
      { id: "b", header: `${bname ?? "bench"}`, align: "right", value: (r) => r[`b_${bname}_${h}`] as number, cell: (r) => pct(r[`b_${bname}_${h}`] as number) },
      { id: "mfe", header: "Max fav.", align: "right", value: (r) => r[`mfe_${h}`] as number, cell: (r) => pct(r[`mfe_${h}`] as number), hidden: true },
      { id: "mae", header: "Max adv.", align: "right", value: (r) => r[`mae_${h}`] as number, cell: (r) => pct(r[`mae_${h}`] as number), hidden: true },
      { id: "ctrl", header: "Saved controls", value: (r) => [1, 2, 3, 4, 5].map((k) => r[`ctrl_${k}`]).filter(Boolean).join(" "), cell: (r) => <span className="num text-2xs text-muted-foreground">{[1, 2, 3, 4, 5].map((k) => r[`ctrl_${k}`]).filter(Boolean).join(" ")}</span> },
      { id: "rdates", header: "Saved random dates", value: (r) => [1, 2, 3].map((k) => r[`rdate_${k}`]).filter(Boolean).join(" "), hidden: true },
    ];
    return cols;
  }, [h, bname]);

  if (res.isLoading || exp.isLoading) return <div className="space-y-3"><PageHeader title={signalKey} /><Loading rows={10} /></div>;
  if (res.error || !res.data) return <div className="space-y-3"><PageHeader title={signalKey} /><ErrorState error={res.error ?? "not found"} /></div>;
  if (!summ) {
    return (
      <div className="space-y-3"><PageHeader title={signalKey} sub={`status ${res.data.run.status}`} />
        {res.data.error ? <pre className="num overflow-auto rounded-md border border-neg/40 bg-neg/5 p-2 text-[11px]">{res.data.error}</pre> : <Empty title="No results for this signal" />}
      </div>
    );
  }
  const s = summ.signal;
  const test = res.data.tests.find((t) => t.partition === part && t.horizon === h);
  const cmp = hs ? (tab === "matched" ? hs.matched : hs.random_date) : null;
  const doImport = () => imp.mutate({ experiment_id: id, signal_key: signalKey, defaults: hold ? { hold_bars: Number(hold) } : {} }, {
    onSuccess: (r) => { toast.success(r.created ? `Imported as ${r.strategy_id}` : `Already imported as ${r.strategy_id}`); navigate(`/library/strategies/${encodeURIComponent(r.strategy_id)}`); },
    onError: (e) => toast.error(String(e)),
  });


  const nullMarkers = cmp ? [
    { value: cmp.signal_mean, label: `signal ${fmt.pct(cmp.signal_mean, 2)}`, color: dir === "bullish" ? "var(--pos)" : "var(--neg)" },
    { value: cmp.null_mean, label: `random ${fmt.pct(cmp.null_mean, 2)}`, color: "var(--muted-foreground)", dashed: true },
  ] : [];

  return (
    <div className="space-y-3" data-signal-result={signalKey}>
      <PageHeader title={<span className="flex items-center gap-2">{s.name} <DirectionBadge d={dir} /> <ScopeBadge s={s.scope} /></span>}
        sub={<span className="num">{signalKey} · <Link className="text-primary hover:underline" to={`/signals/experiments/${id}`}>{id}</Link>{s.kind === "signal" && <> · <Link className="text-primary hover:underline" to={`/library/signals/${encodeURIComponent(s.id)}`}>definition <ExternalLink className="inline size-3" /></Link></>}</span>}
        actions={<div className="flex items-center gap-2">
          <Input className="num h-8 w-28 text-xs" placeholder={`hold ${cfg?.primary_horizon ?? h}`} value={hold} onChange={(e) => setHold(e.target.value)} aria-label="holding sessions for the strategy" title="Holding period of the imported strategy (default: the primary horizon)" />
          <Button size="sm" onClick={doImport} disabled={imp.isPending} data-action="import"><BookPlus className="size-3.5" />Import as a strategy</Button>
        </div>} />
      <div className="rounded-md border bg-surface-2/60 px-2.5 py-1.5 text-xs"><span className="text-muted-foreground">Trigger: </span>{s.trigger}{Object.keys(s.params).length > 0 && s.kind === "signal" && <span className="num text-muted-foreground"> · {Object.entries(s.params).map(([k, v]) => `${k}=${v}`).join(", ")}</span>}</div>
      <div className="grid grid-cols-2 gap-1.5 md:grid-cols-4 lg:grid-cols-8">
        <Stat label="Triggers" value={fmt.int(summ.counts.triggers)} sub={`${fmt.int(summ.counts.ineligible_triggers)} ineligible`} />
        <Stat label="Events" value={fmt.int(summ.counts.events)} sub={`sampling: ${summ.sampling.mode}`} />
        <Stat label={`Events used (${part}, ${h}d)`} value={fmt.int(hs?.n)} sub={`${fmt.int(hs?.n_dates)} sessions · ${fmt.int(hs?.n_symbols)} symbols`} />
        <Stat label="Missing / censored" value={`${summ.counts.missing_forward[String(h)] ?? 0} / ${summ.counts.censored[String(h)] ?? 0}`} hint="missing: no price at entry or exit (never filled); censored: the holding period would cross the partition end" />
        <Stat label="Unmatched" value={fmt.int(summ.counts.unmatched)} sub={`${summ.counts.unmatched_small_pool} small pool · ${summ.counts.unmatched_no_group} no group`} />
        <Stat label="Win rate" value={fmt.pct(hs?.win_rate, 1, false)} sub="in the signal's direction" />
        <Stat label="Max fav. / adv." value={`${fmt.pct(hs?.mfe, 1)} / ${fmt.pct(hs?.mae, 1)}`} hint="mean maximum favorable / adverse excursion over the holding period" />
        <Stat label="Fwd volatility" value={fmt.pct(hs?.fwd_vol, 1, false)} sub="annualized" />
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <Field label="Partition" className="w-40"><ChoiceSelect value={part} onChange={setPartition} aria-label="partition" options={parts.map((p) => ({ value: p, label: p }))} /></Field>
        <Field label="Horizon" className="w-32"><ChoiceSelect value={String(h)} onChange={(v) => setHorizon(Number(v))} aria-label="horizon" options={(cfg?.horizons ?? []).map((x) => ({ value: String(x), label: `${x} sessions` }))} /></Field>
        {test && <div className="flex items-center gap-2 pb-1 text-xs"><VerdictBadge v={test.verdict.category} /><span className="text-muted-foreground">{test.verdict.reason}</span>
          <span className="num text-muted-foreground">· q {test.q_matched?.toFixed(3) ?? "—"} of {test.m_tests} tests</span></div>}
      </div>

      {!hs || hs.n === 0 ? <Empty title="No events in this partition and horizon" /> : (
        <>
          <div className="grid gap-3 lg:grid-cols-3">
            <Panel title="Actual vs random" className="lg:col-span-1">
              <table className="w-full text-xs" data-comparison>
                <thead><tr className="border-b text-2xs text-muted-foreground"><th className="py-1 text-left font-medium">Comparison</th><th className="text-right font-medium">Mean</th><th className="text-right font-medium">Excess</th><th className="text-right font-medium">p / q</th></tr></thead>
                <tbody>
                  <tr className="border-b"><td className="py-1">Signal events</td><td className="text-right">{pct(hs.mean)}</td><td /><td /></tr>
                  <tr className="border-b"><td className="py-1">Matched random stocks</td><td className="text-right">{pct(hs.matched.control_mean)}</td><td className="text-right">{pct(hs.matched.excess)}</td><td className="text-right">{pval(hs.matched.p)} / {pval(test?.q_matched)}</td></tr>
                  {hs.random_date && <tr className="border-b"><td className="py-1">Random entry dates</td><td className="text-right">{pct(hs.random_date.control_mean)}</td><td className="text-right">{pct(hs.random_date.excess)}</td><td className="text-right">{pval(hs.random_date.p)} / {pval(test?.q_random_date)}</td></tr>}
                  {Object.entries(hs.benchmarks).map(([b, v]) => (
                    <tr key={b} className="border-b last:border-0"><td className="py-1">{b === "sector_etf" ? "Sector ETF" : b}</td><td className="text-right">{pct(v.benchmark_mean)}</td><td className="text-right">{pct(v.excess)}</td><td className="text-right">{pval(v.p_boot)}</td></tr>
                  ))}
                </tbody>
              </table>
              <KV className="mt-3" rows={[
                ["Matched excess 95% CI", ci(hs.matched.ci_conservative[0], hs.matched.ci_conservative[1])],
                ["  date-block / ticker se", <span className="num">{fmt.pct(hs.matched.se, 3, false)}</span>],
                ["Net excess (after costs)", pct(hs.matched.excess_net)],
                ["Annualized net excess", pct(test?.verdict.annualized_net_excess, 1)],
                ["z vs null", <span className="num">{fmt.num(hs.matched.z, 2)}</span>],
                ["Mean return 95% CI", ci(hs.mean_boot?.ci[0], hs.mean_boot?.ci[1])],
              ]} />
            </Panel>
            <Panel title="Randomized null distribution" className="lg:col-span-2" actions={<div className="flex items-center gap-2">
              <ChoiceSelect className="h-7 w-40" value={tab} onChange={(v) => setTab(v as "matched")} aria-label="null comparison" options={[{ value: "matched", label: "Matched random stocks" }, ...(hs.random_date ? [{ value: "random_date", label: "Random entry dates" }] : [])]} />
              <ChartActions name={`${signalKey}-${part}-${h}-${tab}-null`} config={cfg} header={["bin_lo", "bin_hi", "count"]} rows={(cmp?.hist?.counts ?? []).map((c, i) => [cmp!.hist!.edges[i], cmp!.hist!.edges[i + 1], c])} />
            </div>}>
              {cmp?.hist ? <Histogram name="null-distribution" edges={cmp.hist.edges} counts={cmp.hist.counts} markers={nullMarkers} band={cmp.null_q} height={210} /> : <Empty title="No null distribution" />}
              <div className="mt-1 text-2xs text-muted-foreground">Each of the {cmp?.permutations ?? cfg?.permutations} permutations draws one random control per event; the histogram is the mean return of those portfolios. Shaded: the central 95%. Empirical p = {pval(cmp?.p)} (direction: {dir}), percentile {fmt.pct(cmp?.percentile ?? null, 1, false)}.</div>
            </Panel>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="By holding period" actions={<ChartActions name={`${signalKey}-${part}-horizons`} config={cfg} header={["horizon", "signal_mean", "matched_mean", "random_date_mean", "benchmark_mean", "excess", "ci_lo", "ci_hi"]}
              rows={byHorizon.map(({ h: x, s: v }) => [x, v?.mean, v?.matched.control_mean, v?.random_date?.control_mean, v?.benchmarks[bname ?? ""]?.benchmark_mean, v?.matched.excess, v?.matched.ci_conservative[0], v?.matched.ci_conservative[1]])} />}>
              <XYChart name="horizon-means" xLabel="sessions held" markers={cfg?.horizons} series={[
                { key: "s", name: "Signal", color: C.signal, points: byHorizon.map(({ h: x, s: v }) => [x, v?.mean ?? null]) },
                { key: "m", name: "Matched random", color: C.matched, points: byHorizon.map(({ h: x, s: v }) => [x, v?.matched.control_mean ?? null]) },
                { key: "r", name: "Random dates", color: C.rd, points: byHorizon.map(({ h: x, s: v }) => [x, v?.random_date?.control_mean ?? null]) },
                ...(bname ? [{ key: "b", name: bname, color: C.bench, dashed: true, points: byHorizon.map(({ h: x, s: v }) => [x, v?.benchmarks[bname]?.benchmark_mean ?? null] as [number, number | null]) }] : []),
              ]} />
              <ForestPlot name="excess-by-horizon" rows={byHorizon.map(({ h: x, s: v }) => ({ key: String(x), label: `${x} sessions`, value: v?.matched.excess ?? null, lo: v?.matched.ci_conservative[0] ?? null, hi: v?.matched.ci_conservative[1] ?? null,
                tone: VERDICT_COLOR[res.data!.tests.find((t) => t.partition === part && t.horizon === x)?.verdict.category ?? "not_significant"], sub: `n=${fmt.int(v?.n)}` }))} />
            </Panel>
            <Panel title="Average cumulative return by holding day" actions={P?.path && <ChartActions name={`${signalKey}-${part}-path`} config={cfg} header={["day", "signal", "matched", ...(bname ? [bname] : [])]}
              rows={P.path.k.map((k, i) => [k, P.path!.signal[i], P.path!.matched[i], ...(bname ? [(P.path![bname] as (number | null)[] | undefined)?.[i] ?? null] : [])])} />}>
              {P?.path ? <>
                <XYChart name="cumulative-path" xLabel="days held" series={[
                  { key: "s", name: "Signal", color: C.signal, points: P.path.k.map((k, i) => [k, P.path!.signal[i]]) },
                  { key: "m", name: "Matched random", color: C.matched, points: P.path.k.map((k, i) => [k, P.path!.matched[i]]) },
                  ...(bname && P.path[bname] ? [{ key: "b", name: bname, color: C.bench, dashed: true, points: P.path.k.map((k, i) => [k, (P.path![bname] as (number | null)[])[i]] as [number, number | null]) }] : []),
                ]} />
                <div className="mt-1 text-2xs text-muted-foreground">One cohort: the {fmt.int(P.path.n)} events whose longest horizon completes inside the partition.</div>
              </> : <Empty title="No complete paths" />}
            </Panel>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="Excess-return distribution (per event)" actions={hs.matched.excess_hist && <ChartActions name={`${signalKey}-${part}-${h}-excess-dist`} config={cfg} header={["bin_lo", "bin_hi", "count"]} rows={hs.matched.excess_hist.counts.map((c, i) => [hs.matched.excess_hist!.edges[i], hs.matched.excess_hist!.edges[i + 1], c])} />}>
              {hs.matched.excess_hist ? <Histogram name="excess-distribution" edges={hs.matched.excess_hist.edges} counts={hs.matched.excess_hist.counts}
                markers={[{ value: 0, label: "0", color: "var(--muted-foreground)", dashed: true }, { value: hs.matched.excess, label: `mean ${fmt.pct(hs.matched.excess, 2)}`, color: "var(--primary)" }]} /> : <Empty title="No matched events" />}
              <div className="mt-1 text-2xs text-muted-foreground">Event return minus its matched pool's mean; clipped to the 0.5–99.5 percentiles for display.</div>
            </Panel>
            <Panel title="Signal return distribution" actions={hs.return_hist && <ChartActions name={`${signalKey}-${part}-${h}-returns`} config={cfg} header={["bin_lo", "bin_hi", "count"]} rows={hs.return_hist.counts.map((c, i) => [hs.return_hist!.edges[i], hs.return_hist!.edges[i + 1], c])} />}>
              {hs.return_hist ? <Histogram name="return-distribution" edges={hs.return_hist.edges} counts={hs.return_hist.counts}
                markers={[{ value: hs.mean, label: `mean ${fmt.pct(hs.mean, 2)}`, color: "var(--primary)" }, { value: hs.median, label: `median ${fmt.pct(hs.median, 2)}`, color: "var(--warn)", dashed: true }]} /> : <Empty title="No returns" />}
            </Panel>
          </div>

          {bd && (
            <>
              <div className="grid gap-3 lg:grid-cols-2">
                <Panel title={`By year · consistency ${fmt.pct(bd.consistency_years.share_in_direction, 0, false)} of ${bd.consistency_years.groups} years in direction`}
                  actions={<ChartActions name={`${signalKey}-${part}-${h}-by-year`} config={cfg} header={["year", "n", "mean", "excess_matched", "n_matched", "excess_benchmark"]} rows={bd.by_year.map((r) => [r.key, r.n, r.mean, r.excess_matched, r.n_matched, r.excess_benchmark])} />}>
                  <BarChart name="by-year" rows={groupRows(bd.by_year, "excess_matched")} />
                  <div className="mt-1 text-2xs text-muted-foreground">Excess vs matched random stocks per calendar year (n = matched events). Years with fewer than 10 do not count toward consistency.</div>
                </Panel>
                {bd.by_sector ? (
                  <Panel title={`By sector · consistency ${fmt.pct(bd.consistency_sectors?.share_in_direction ?? null, 0, false)} of ${bd.consistency_sectors?.groups ?? 0}`}
                    actions={<ChartActions name={`${signalKey}-${part}-${h}-by-sector`} config={cfg} header={["sector", "n", "mean", "excess_matched", "n_matched", "excess_benchmark"]} rows={bd.by_sector.map((r) => [r.key, r.n, r.mean, r.excess_matched, r.n_matched, r.excess_benchmark])} />}>
                    <BarChart name="by-sector" rows={groupRows(bd.by_sector, "excess_matched")} height={200} />
                    <div className="mt-1 text-2xs text-muted-foreground">Sectors from SEC SIC codes (approximate, current, not point-in-time).</div>
                  </Panel>
                ) : <Panel title="By sector"><Empty title="No sector reference data">Configure [signal_research] sector_file.</Empty></Panel>}
              </div>
              <div className="grid gap-3 lg:grid-cols-3">
                <Panel title="By stock volatility regime" actions={<ChartActions name={`${signalKey}-${part}-${h}-by-vol`} config={cfg} header={["regime", "n", "mean", "excess_matched"]} rows={bd.by_vol_regime.map((r) => [r.key, r.n, r.mean, r.excess_matched])} />}>
                  <BarChart name="by-vol-regime" rows={groupRows(bd.by_vol_regime, "excess_matched")} height={160} />
                </Panel>
                <Panel title={`By market trend (${bname ?? "benchmark"})`} actions={<ChartActions name={`${signalKey}-${part}-${h}-by-trend`} config={cfg} header={["regime", "n", "mean", "excess_matched", "excess_benchmark"]} rows={bd.by_market_trend.map((r) => [r.key, r.n, r.mean, r.excess_matched, r.excess_benchmark])} />}>
                  <BarChart name="by-market-trend" rows={groupRows(bd.by_market_trend, "excess_matched")} height={160} />
                </Panel>
                <Panel title="By market volatility" actions={<ChartActions name={`${signalKey}-${part}-${h}-by-mktvol`} config={cfg} header={["regime", "n", "mean", "excess_matched", "excess_benchmark"]} rows={bd.by_market_vol.map((r) => [r.key, r.n, r.mean, r.excess_matched, r.excess_benchmark])} />}>
                  <BarChart name="by-market-vol" rows={groupRows(bd.by_market_vol, "excess_matched")} height={160} />
                </Panel>
              </div>
            </>
          )}
        </>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Event timeline (events per month)" actions={<ChartActions name={`${signalKey}-timeline`} config={cfg} header={["month", "events"]} rows={summ.timeline.map((t) => [t.month, t.n])} />}>
          <LineChart name="timeline" unit="ratio" height={180} legend={false} lines={[{ key: "n", name: "events", color: "#3987e5", area: true, points: summ.timeline.map((t) => ({ time: `${t.month}-01`, value: t.n })) }]} />
          <div className="mt-1 text-2xs text-muted-foreground">First trigger {summ.counts.first_trigger ?? "—"} · by partition: {Object.entries(summ.counts.by_partition).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
        </Panel>
        <Panel title={`Tests (${res.data.tests.length})`}>
          <table className="w-full text-xs" data-tests>
            <thead><tr className="border-b text-2xs text-muted-foreground"><th className="py-1 text-left font-medium">Partition</th><th className="text-right font-medium">h</th><th className="text-right font-medium">n</th><th className="text-right font-medium">Excess</th><th className="text-right font-medium">p</th><th className="text-right font-medium">q</th><th className="text-right font-medium">Ann. net</th><th className="pl-2 text-left font-medium">Verdict</th></tr></thead>
            <tbody>{res.data.tests.map((t) => (
              <tr key={`${t.partition}-${t.horizon}`} className="cursor-pointer border-b last:border-0 hover:bg-muted/50" onClick={() => { setPartition(t.partition); setHorizon(t.horizon); }}>
                <td className="py-0.5">{t.partition}</td><td className="num text-right">{t.horizon}</td><td className="num text-right">{fmt.int(t.n)}</td>
                <td className="text-right">{pct(t.excess)}</td><td className="text-right">{pval(t.p_matched)}</td><td className="text-right">{pval(t.q_matched)}</td>
                <td className="text-right">{pct(t.verdict.annualized_net_excess, 1)}</td><td className="pl-2"><VerdictBadge v={t.verdict.category} /></td>
              </tr>
            ))}</tbody>
          </table>
          {res.data.locked.length > 0 && <div className="mt-2 text-2xs text-warn">Test-period evaluation: {res.data.locked.map((l) => (l.first_use ? `locked result (first of ${l.evaluations} evaluations)` : `re-evaluation; the locked result is ${l.locked_experiment}`)).join("; ")}</div>}
        </Panel>
      </div>

      <Panel title="Events with their saved controls" actions={!showEvents && <Button size="xs" variant="outline" onClick={() => setShowEvents(true)} data-action="load-events">Load events</Button>}>
        {!showEvents ? <div className="text-xs text-muted-foreground">{fmt.int(summ.counts.events)} events with entry dates, returns, matched-pool and random-date means, benchmark returns and the first saved random controls (all draws are reproducible from the saved seed).</div>
          : events.isLoading ? <Loading rows={6} /> : events.error ? <ErrorState error={events.error} /> : (
            <DataTable name={`${signalKey}-events`} rows={events.data?.rows ?? []} cols={evCols} maxHeight={520} />
          )}
      </Panel>
    </div>
  );
}
