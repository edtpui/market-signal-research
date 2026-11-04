// Market Data: every configured dataset, its cached content versions, its actual coverage (first/last session,
// bars per symbol, missing sessions, invalid bars, gaps), and a per-symbol price view with corporate actions. What
// the data does not cover is shown, never filled.
import { Database, Download } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import type { CoverageRow, Row } from "@/api/client";
import { useBars, useCoverage, useDataset, useDatasets, useIngest } from "@/api/hooks";
import { ChoiceSelect } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { LineChart, PALETTE } from "@/components/LineChart";
import { ChartSkeleton, Empty, ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge, Stat } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { navigate } from "@/lib/router";

const covCols: Col<CoverageRow>[] = [
  { id: "symbol", header: "Symbol", value: (r) => r.symbol, cell: (r) => <span className="num font-medium">{r.symbol}</span> },
  { id: "first", header: "First", value: (r) => r.first_date, cell: (r) => <span className="num">{r.first_date ?? "—"}</span> },
  { id: "last", header: "Last", value: (r) => r.last_date, cell: (r) => <span className="num">{r.last_date ?? "—"}</span> },
  { id: "bars", header: "Bars", align: "right", value: (r) => r.n_bars, cell: (r) => fmt.int(r.n_bars) },
  { id: "expected", header: "Expected", align: "right", value: (r) => r.expected, cell: (r) => fmt.int(r.expected) },
  { id: "missing", header: "Missing", align: "right", value: (r) => r.missing, cell: (r) => <span className={r.missing ? "text-warn" : "text-muted-foreground"}>{fmt.int(r.missing)}</span> },
  { id: "invalid", header: "Invalid", align: "right", value: (r) => r.invalid_bars, cell: (r) => <span className={r.invalid_bars ? "text-neg" : "text-muted-foreground"}>{fmt.int(r.invalid_bars)}</span> },
  { id: "gap", header: "Max gap", align: "right", value: (r) => r.max_gap, cell: (r) => fmt.int(r.max_gap) },
];

function SymbolView({ dataset, symbols }: { dataset: string; symbols: string[] }) {
  const [sym, setSym] = useState(symbols.includes("SPY") ? "SPY" : symbols[0] ?? "");
  const [raw, setRaw] = useState(false);
  const bars = useBars(dataset, sym, raw ? "raw" : "adjusted");
  const lines = useMemo(() => [{
    key: "close", name: `${sym} close (${raw ? "raw" : "adjusted"})`, color: PALETTE[0],
    points: (bars.data?.rows ?? []).filter((r) => typeof r.close === "number").map((r) => ({ time: String(r.date), value: r.close as number })),
  }], [bars.data, sym, raw]);
  const actions = (bars.data?.actions ?? []) as Row[];
  return (
    <Panel title="Symbol" actions={<>
      <ChoiceSelect className="h-7 w-32" value={sym} onChange={setSym} aria-label="symbol" options={symbols.map((s) => ({ value: s, label: s }))} />
      <span className="flex items-center gap-1.5 text-2xs text-muted-foreground"><Switch checked={raw} onCheckedChange={setRaw} aria-label="raw prices" />raw + dividends</span>
    </>}>
      {bars.isLoading ? <ChartSkeleton height={260} /> : bars.error ? <ErrorState error={bars.error} /> : (
        <div className="space-y-2">
          <LineChart name="symbol-close" lines={lines} unit="price" height={260} legend={false} />
          <div className="text-2xs text-muted-foreground">
            {fmt.int(bars.data?.rows.length)} sessions; missing bars are gaps in the line, not interpolated. {String(bars.data?.report.splits ?? "")}.
          </div>
          {raw && (actions.length ? (
            <div className="max-h-40 overflow-auto rounded-md border">
              <table className="w-full text-2xs"><tbody>
                {actions.map((a, i) => <tr key={i} className="border-b last:border-0"><td className="num px-2 py-0.5">{String(a.date).slice(0, 10)}</td><td>{String(a.kind)}</td><td className="num px-2 text-right">{fmt.num(a.value as number, 4)}</td></tr>)}
              </tbody></table>
            </div>
          ) : <div className="text-2xs text-muted-foreground">No corporate actions in this window.</div>)}
        </div>
      )}
    </Panel>
  );
}

function DatasetDetailView({ id }: { id: string }) {
  const list = useDatasets();
  const item = list.data?.find((d) => d.id === id);
  const cached = !!item?.cached;
  const detail = useDataset(id, cached);
  const [onlyGaps, setOnlyGaps] = useState(false);
  const cov = useCoverage(id, onlyGaps, cached);
  const allCov = useCoverage(id, false, cached);
  const ingest = useIngest();
  if (list.isLoading) return <Loading rows={6} />;
  if (!item) return <ErrorState error={`No dataset ${id}`} />;
  const m = detail.data?.meta;
  return (
    <div className="space-y-3" data-dataset={id}>
      <PageHeader title={item.name} sub={<span className="num">{item.id} · {item.provider} · {item.sources.join("; ")}</span>}
        actions={<Button size="sm" variant="outline" disabled={ingest.isPending} onClick={() => ingest.mutate(id, { onSuccess: () => toast.success("Ingested"), onError: (e) => toast.error(e.message) })}>
          <Download className="size-3.5" />{cached ? "Re-check source" : "Ingest"}</Button>} />
      {!cached ? <Empty title="Not cached yet">Ingest builds a versioned Parquet snapshot, clipped to the data policy, and computes coverage.</Empty> : detail.isLoading ? <Loading rows={6} /> : detail.error ? <ErrorState error={detail.error} /> : m && (
        <>
          <div className="grid grid-cols-2 gap-1.5 md:grid-cols-4 xl:grid-cols-8">
            <Stat label="Coverage" value={`${m.first_date} → ${m.last_date}`} />
            <Stat label="Sessions" value={fmt.int(m.n_sessions)} />
            <Stat label="Symbols" value={fmt.int(m.n_symbols)} />
            <Stat label="Bars" value={fmt.int(m.n_rows)} />
            <Stat label="With gaps" value={fmt.int(detail.data!.summary.with_gaps)} tone={detail.data!.summary.with_gaps ? "text-warn" : ""} sub={`${fmt.int(detail.data!.summary.missing_bars)} missing bars`} />
            <Stat label="Invalid bars" value={fmt.int(m.invalid_bars)} tone={m.invalid_bars ? "text-neg" : ""} hint="Non-positive or non-finite prices, or close/open outside [low, high]: treated as missing." />
            <Stat label="Late start / early end" value={`${detail.data!.summary.late_start} / ${detail.data!.summary.early_end}`} hint="Symbols listed after the first session, or ending before the last one." />
            <Stat label="Corporate actions" value={fmt.int(m.n_actions)} hint={String(m.stats.dividends ?? "")} />
          </div>
          <div className="grid gap-3 xl:grid-cols-[1fr_1.4fr]">
            <Panel title="Version and provenance">
              <KV rows={[
                ["Version", <span className="num">{m.version}</span>], ["Ingested", fmt.utc(m.ingested_at_utc)],
                ["Policy", <span className="num">{m.policy.history_floor} → {m.policy.ceiling} (HOLDOUT from {m.policy.holdout_start})</span>],
                ["Periods covered", <span className="flex gap-1">{detail.data!.periods.map((p) => <PeriodBadge key={p} period={p} />)}</span>],
                ["Rows past the ceiling", <span className="num">{String(m.stats.rows_beyond_ceiling)} (counted, never stored)</span>],
                ["Rows before the floor", <span className="num">{String(m.stats.rows_before_floor)} (dropped)</span>],
                ["Splits", String(m.stats.splits ?? "")], ["Dividends", String(m.stats.dividends ?? "")],
                ["Other versions", <span className="num text-2xs">{item.versions.map((v) => v.version).join(", ")}</span>],
              ]} />
              <div className="mt-3">
                <div className="mb-1 text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Sessions per year</div>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(detail.data!.sessions_by_year).map(([y, n]) => <span key={y} className="num rounded border px-1.5 py-0.5 text-2xs">{y}: {n}</span>)}
                </div>
              </div>
            </Panel>
            {allCov.data && <SymbolView dataset={id} symbols={allCov.data.rows.map((r) => r.symbol)} />}
          </div>
          <Panel title="Coverage by symbol" actions={<span className="flex items-center gap-1.5 text-2xs text-muted-foreground"><Switch checked={onlyGaps} onCheckedChange={setOnlyGaps} aria-label="only gaps" />only gaps / invalid</span>}>
            {cov.isLoading ? <Loading rows={6} /> : <DataTable name={`${id}-coverage`} rows={cov.data?.rows ?? []} cols={covCols} maxHeight={480} />}
          </Panel>
        </>
      )}
    </div>
  );
}

export function MarketDataPage({ id }: { id?: string }) {
  const list = useDatasets();
  const ingest = useIngest();
  if (id) return <DatasetDetailView id={id} />;
  return (
    <div className="space-y-3">
      <PageHeader title="Market Data" sub="Datasets configured in config/settings.toml. Bars are clipped to the data policy at ingest; each content version is an immutable snapshot." />
      <Panel title="Datasets">
        {list.isLoading ? <Loading rows={5} /> : list.error ? <ErrorState error={list.error} /> : (
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-xs" data-datasets>
              <thead><tr className="border-b text-2xs text-muted-foreground">
                {["Dataset", "Provider", "Version", "Coverage", "Symbols", "Bars", "Invalid", "Actions", ""].map((h) => <th key={h} className="h-7 px-2 text-left font-medium">{h}</th>)}
              </tr></thead>
              <tbody>
                {list.data!.map((d) => (
                  <tr key={d.id} className="border-b last:border-0 hover:bg-muted/50">
                    <td className="px-2 py-1"><button className="text-left hover:text-primary" onClick={() => navigate(`/data/${d.id}`)}><div className="font-medium">{d.id}</div><div className="text-2xs text-muted-foreground">{d.name}</div></button></td>
                    <td className="px-2">{d.provider}</td>
                    <td className="num px-2">{d.current ? fmt.bytesHash(d.current.version, 10) : "—"}</td>
                    <td className="num px-2">{d.current ? `${d.current.first_date} → ${d.current.last_date}` : "not cached"}</td>
                    <td className="num px-2 text-right">{fmt.int(d.current?.n_symbols)}</td>
                    <td className="num px-2 text-right">{fmt.int(d.current?.n_rows)}</td>
                    <td className="num px-2 text-right">{fmt.int(d.current?.invalid_bars)}</td>
                    <td className="num px-2 text-right">{fmt.int(d.current?.n_actions)}</td>
                    <td className="px-2 text-right">
                      <Button size="xs" variant={d.cached ? "ghost" : "outline"} disabled={ingest.isPending}
                        onClick={() => ingest.mutate(d.id, { onSuccess: () => toast.success(`${d.id} ingested`), onError: (e) => toast.error(e.message) })}>
                        <Database className="size-3" />{d.cached ? "Re-check" : "Ingest"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {ingest.isPending && <div className="mt-2 text-2xs text-muted-foreground">Ingesting: hashing source files and writing the Parquet snapshot (large datasets take a minute)…</div>}
      </Panel>
    </div>
  );
}
