// Signal Details: the definition (trigger, parameters, bars, history requirement, version, source), the calculation
// on any symbol and window (indicator series and every trigger), and every experiment that has measured it.
import { FlaskConical } from "lucide-react";
import { useMemo, useState } from "react";

import type { Row } from "@/api/client";
import { useDatasets, useUniverses } from "@/api/hooks";
import { useInspect, useSignalDef } from "@/api/signals";
import { ChoiceSelect, Field } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { LineChart, PALETTE, type Line } from "@/components/LineChart";
import { SignalChart } from "@/components/SignalChart";
import { ChartSkeleton, Empty, ErrorState, KV, Loading, PageHeader, Panel } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { Link, navigate } from "@/lib/router";
import { DirectionBadge, ScopeBadge, VerdictBadge, ci, pct, pval } from "@/pages/signals/common";
import type { Verdict } from "@/api/signals";

const num = (r: Row, k: string) => (typeof r[k] === "number" ? (r[k] as number) : null);

const resultCols: Col<Row>[] = [
  { id: "exp", header: "Experiment", value: (r) => String(r.experiment_id), cell: (r) => <Link className="num text-primary underline-offset-2 hover:underline" to={`/signals/experiments/${r.experiment_id}/${encodeURIComponent(String(r.signal_key))}`} onClick={(e) => e.stopPropagation()}>{String(r.experiment_id)}</Link> },
  { id: "params", header: "Instance", value: (r) => String(r.signal_key), cell: (r) => <span className="num text-2xs text-muted-foreground">{String(r.signal_key)}</span> },
  { id: "data", header: "Data", value: (r) => `${r.dataset} · ${r.n_symbols} sym`, cell: (r) => <span className="text-2xs">{String(r.dataset)} · <span className="num">{String(r.n_symbols)}</span> symbols</span> },
  { id: "part", header: "Partition", value: (r) => String(r.partition) },
  { id: "h", header: "h", align: "right", value: (r) => num(r, "horizon") },
  { id: "n", header: "Events", align: "right", value: (r) => num(r, "n"), cell: (r) => fmt.int(num(r, "n")) },
  { id: "excess", header: "Excess vs matched", align: "right", value: (r) => num(r, "excess"), cell: (r) => pct(num(r, "excess")) },
  { id: "ci", header: "95% CI", value: (r) => num(r, "ci_lo"), cell: (r) => ci(num(r, "ci_lo"), num(r, "ci_hi")) },
  { id: "q", header: "q (BH)", align: "right", value: (r) => num(r, "q_matched"), cell: (r) => pval(num(r, "q_matched")) },
  { id: "verdict", header: "Verdict", value: (r) => String(r.verdict), cell: (r) => <VerdictBadge v={r.verdict as Verdict} /> },
];

export function SignalDetailPage({ id }: { id: string }) {
  const def = useSignalDef(id);
  const datasets = useDatasets();
  const ds = datasets.data?.find((d) => d.cached)?.id ?? datasets.data?.[0]?.id ?? "";
  const [dataset, setDataset] = useState<string>("");
  const [symbol, setSymbol] = useState("AAPL");
  const [universeName, setUniverseName] = useState("sp1500");
  const [start, setStart] = useState("2024-01-02");
  const [end, setEnd] = useState("2024-12-31");          // the default test partition (2025) stays out of casual browsing
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [ask, setAsk] = useState<null | { symbol: string; dataset: string; start: string; end?: string; params: Record<string, unknown>; universe: string }>(null);
  const useDs = dataset || ds;
  const universes = useUniverses(useDs);
  const body = useMemo(() => (ask ? {
    spec: { kind: "signal" as const, id, params: ask.params }, dataset: ask.dataset, symbol: ask.symbol, start: ask.start, end: ask.end || undefined,
    universe_name: def.data?.scope !== "stock" ? ask.universe : null,
  } : null), [ask, id, def.data?.scope]);
  const insp = useInspect(body);
  const oscLines: Line[] = useMemo(() => {
    if (!insp.data) return [];
    const d = insp.data;
    const out: Line[] = Object.entries(d.series.osc).map(([k, vals], i) => ({
      key: k, name: k, color: PALETTE[i % PALETTE.length], points: d.dates.map((t, j) => ({ time: t, value: vals[j] as number })).filter((p) => typeof p.value === "number"),
    }));
    d.series.levels.forEach((lv) => out.push({ key: `lvl${lv}`, name: `level ${lv}`, color: "#9b9a94", dashed: true, width: 1, points: d.dates.map((t) => ({ time: t, value: lv })) }));
    return out;
  }, [insp.data]);

  if (def.isLoading) return <div className="space-y-3"><PageHeader title={id} /><Loading rows={10} /></div>;
  if (def.error || !def.data) return <div className="space-y-3"><PageHeader title={id} /><ErrorState error={def.error ?? "not found"} /></div>;
  const s = def.data;
  const run = () => setAsk({ symbol: symbol.trim().toUpperCase(), dataset: useDs, start, end, params, universe: universeName });

  return (
    <div className="space-y-3" data-signal={id}>
      <PageHeader title={<span className="flex items-center gap-2">{s.name} <DirectionBadge d={s.direction} /> <ScopeBadge s={s.scope} /></span>}
        sub={<span className="num">{s.id} · v{s.version} · {s.family_info.name}</span>}
        actions={<Button size="sm" onClick={() => navigate(`/signals/new?signals=${s.id}`)} data-action="test-signal"><FlaskConical className="size-3.5" />Test this signal</Button>} />
      <div className="grid gap-3 xl:grid-cols-[420px_1fr]">
        <div className="space-y-3">
          <Panel title="Definition">
            <div className="space-y-3">
              <div className="rounded-md border bg-surface-2/60 px-2.5 py-2 text-xs" data-trigger><span className="text-muted-foreground">Trigger at the close of t: </span>{s.trigger}</div>
              {s.doc && <p className="text-xs text-muted-foreground">{s.doc}</p>}
              <KV rows={[
                ["Family", `${s.family_info.name}: ${s.family_info.description}`],
                ["Hypothesis", s.direction === "bullish" ? "higher subsequent returns than comparable stocks" : "lower subsequent returns than comparable stocks"],
                ["Bars read", <span className="num">{s.fields.join(", ")}</span>],
                ["History needed", <span className="num">{s.warmup} sessions before the first possible trigger</span>],
                ["Benchmark", s.needs_benchmark ? "reads the benchmark close (relative strength / regime)" : "not needed"],
                ["Versions", <span className="num">{s.versions.join(", ")}</span>],
                ["Source sha256", <span className="num text-2xs break-all">{s.source_sha256}</span>],
              ]} />
              <div>
                <div className="mb-1 text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Parameters</div>
                {Object.keys(s.params).length === 0 ? <div className="text-xs text-muted-foreground">None: a fixed definition.</div> : (
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(s.params).map(([k, p]) => (
                      <Field key={k} label={`${k} (${p.min ?? "−∞"}…${p.max ?? "∞"})`} hint={p.description}>
                        <Input className="num h-8 text-xs" type="number" step="any" aria-label={`param ${k}`} value={String(params[k] ?? p.default)}
                          onChange={(e) => setParams({ ...params, [k]: p.kind === "int" ? parseInt(e.target.value || "0", 10) : Number(e.target.value) })} />
                      </Field>
                    ))}
                  </div>
                )}
              </div>
              <details>
                <summary className="cursor-pointer text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Calculation source</summary>
                <pre className="num mt-1 max-h-[360px] overflow-auto rounded-md border bg-surface-2 p-2 text-[11px] leading-snug">{s.code || "source not available"}</pre>
              </details>
            </div>
          </Panel>
        </div>
        <div className="space-y-3">
          <Panel title="Calculation on a symbol" actions={<span className="text-2xs text-muted-foreground">Markers: sessions on which the signal fires (evaluated at the close; an event study enters at the next open)</span>}>
            <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-6">
              <Field label="Dataset"><ChoiceSelect value={useDs} onChange={setDataset} aria-label="dataset" options={(datasets.data ?? []).map((d) => ({ value: d.id, label: d.id }))} /></Field>
              <Field label="Symbol"><Input className="num h-8 text-xs" value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="symbol" /></Field>
              <Field label="From"><Input className="num h-8 text-xs" type="date" value={start} onChange={(e) => setStart(e.target.value)} aria-label="from" /></Field>
              <Field label="To" hint="Defaults to the end of the validation period: the 2025 test partition is for final evaluation"><Input className="num h-8 text-xs" type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="to" /></Field>
              {s.scope !== "stock" ? (
                <Field label="Universe (ranks)"><ChoiceSelect value={universeName} onChange={setUniverseName} aria-label="universe"
                  options={(universes.data ?? []).map((u) => ({ value: u.name, label: u.name }))} /></Field>
              ) : <div />}
              <div className="flex items-end"><Button size="sm" className="w-full" onClick={run} data-action="inspect">Calculate</Button></div>
            </div>
            {!ask ? <Empty title="Pick a symbol and window">The chart shows the bars, the signal's indicator series and every trigger.</Empty>
              : insp.isLoading ? <ChartSkeleton height={340} /> : insp.error ? <ErrorState error={insp.error} /> : insp.data ? (
                <div className="space-y-2">
                  <div className="text-2xs text-muted-foreground"><span className="num">{insp.data.triggers.length}</span> triggers on {insp.data.symbol} between <span className="num">{insp.data.dates[0]}</span> and <span className="num">{insp.data.dates.at(-1)}</span> · dataset version <span className="num">{String(insp.data.report.dataset_version)}</span></div>
                  <SignalChart dates={insp.data.dates} bars={insp.data.bars} overlays={insp.data.series.overlay} triggers={insp.data.triggers} direction={s.direction} />
                  {oscLines.length > 0 && <LineChart name="signal-osc" lines={oscLines} unit="ratio" height={160} />}
                  <div className="flex flex-wrap gap-1 text-2xs">{insp.data.triggers.slice(-40).map((t) => <span key={t} className="num rounded border px-1">{t}</span>)}</div>
                </div>
              ) : null}
          </Panel>
          <Panel title={`Research results (${s.results.length})`}>
            {s.results.length === 0 ? <Empty title="Not tested yet">Launch an experiment from the builder; its results appear here.</Empty> :
              <DataTable name={`results-${id}`} rows={s.results} cols={resultCols} maxHeight={360} />}
          </Panel>
        </div>
      </div>
    </div>
  );
}
