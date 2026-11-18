// Strategy Library: every registered strategy (built-in and strategies_user/), its versions, typed parameters,
// data and order requirements, ptrade forward compatibility, and its source (whose sha256 every run records).
import { useStrategies, useStrategy } from "@/api/hooks";
import type { SignalProvenance, StrategyInfo } from "@/api/client";
import { DataTable, type Col } from "@/components/DataTable";
import { Empty, ErrorState, KV, Loading, PageHeader, Panel } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import * as fmt from "@/lib/format";
import { ptradeCompatible } from "@/lib/ptrade";
import { Link, navigate } from "@/lib/router";

const cols: Col<StrategyInfo>[] = [
  { id: "name", header: "Strategy", value: (s) => s.name, cell: (s) => <div><div className="font-medium">{s.name}</div><div className="num text-2xs text-muted-foreground">{s.id}</div></div> },
  { id: "category", header: "Category", value: (s) => s.category, cell: (s) => <Badge variant="outline" className="rounded-sm text-2xs">{s.category}</Badge> },
  { id: "versions", header: "Versions", value: (s) => s.versions.join(", "), cell: (s) => <span className="num">{s.versions.join(", ")}</span> },
  { id: "params", header: "Params", align: "right", value: (s) => Object.keys(s.params).length },
  { id: "fields", header: "Bars", value: (s) => s.required_fields.join(","), cell: (s) => <span className="num text-2xs">{s.required_fields.join(", ")}</span> },
  { id: "orders", header: "Orders", value: (s) => s.order_types.join(","), cell: (s) => <span className="num text-2xs">{s.order_types.join(", ")}</span> },
  { id: "ptrade", header: "Forward (ptrade)", value: (s) => ptradeCompatible(s).ok, cell: (s) => { const c = ptradeCompatible(s); return <span className={c.ok ? "text-pos" : "text-muted-foreground"} title={c.why}>{c.ok ? "compatible" : "backtest only"}</span>; } },
  { id: "source", header: "Source", value: (s) => s.source, cell: (s) => <span className="num text-2xs text-muted-foreground">{s.source}</span> },
];

export function StrategiesPage({ id }: { id?: string }) {
  const list = useStrategies();
  const detail = useStrategy(id);
  return (
    <div className="space-y-3">
      <PageHeader title="Strategies" sub="Built-in strategies and user modules in strategies_user/. Every run records the strategy's source sha256." />
      {list.error && <ErrorState error={list.error} retry={list.refetch} />}
      <div className={id ? "grid gap-3 xl:grid-cols-[1fr_1.2fr]" : ""}>
        <Panel title="Strategies">
          {list.isLoading ? <Loading rows={6} /> : <DataTable name="strategies" rows={list.data?.strategies ?? []} cols={cols} onRowClick={(s) => navigate(`/library/strategies/${encodeURIComponent(s.id)}`)} filter />}
          {(list.data?.errors ?? []).map((e) => <div key={e.file} className="mt-2 text-xs text-neg">Failed to load <span className="num">{e.file}</span>: {e.error}</div>)}
        </Panel>
        {id && (
          <Panel title={detail.data?.name ?? id} actions={detail.data && <Button size="xs" onClick={() => navigate(`/backtest?strategy=${encodeURIComponent(id)}`)}>Backtest this</Button>}>
            {detail.isLoading ? <Loading rows={8} /> : detail.error ? <ErrorState error={detail.error} /> : detail.data ? (
              <div className="space-y-3" data-strategy={id}>
                <p className="text-xs leading-relaxed">{detail.data.description || "No description."}</p>
                <KV rows={[
                  ["Id / version", <span className="num">{detail.data.id} v{detail.data.version} (all: {detail.data.versions.join(", ")})</span>],
                  ["Category", detail.data.category],
                  ["Bars needed", <span className="num">{detail.data.required_fields.join(", ")}</span>],
                  ["Order types", <span className="num">{detail.data.order_types.join(", ")}</span>],
                  ["Long only", detail.data.long_only ? "yes" : "no (may short when allowed)"],
                  ["Warm-up (defaults)", <span className="num">{detail.data.warmup_default} bars</span>],
                  ["Forward on ptrade", (() => { const c = ptradeCompatible(detail.data); return <span className={c.ok ? "text-pos" : "text-warn"}>{c.ok ? "compatible" : "not deployable"}: {c.why}</span>; })()],
                  ["Source", <span className="num text-2xs break-all">{detail.data.source_file}</span>],
                  ["sha256", <span className="num text-2xs">{detail.data.source_sha256}</span>],
                ]} />
                <div>
                  <div className="mb-1 text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Parameters</div>
                  {Object.keys(detail.data.params).length === 0 ? <Empty title="No parameters" /> : (
                    <table className="w-full text-xs">
                      <thead><tr className="border-b text-2xs text-muted-foreground"><th className="py-1 text-left font-medium">Name</th><th className="text-left font-medium">Type</th><th className="text-right font-medium">Default</th><th className="text-right font-medium">Range</th><th className="pl-3 text-left font-medium">Meaning</th></tr></thead>
                      <tbody>
                        {Object.entries(detail.data.params).map(([k, p]) => (
                          <tr key={k} className="border-b last:border-0">
                            <td className="num py-1">{k}</td>
                            <td className="text-muted-foreground">{p.kind}</td>
                            <td className="num text-right">{String(p.default)}</td>
                            <td className="num text-right text-muted-foreground">{p.choices ? p.choices.join("|") : p.min !== null || p.max !== null ? `${p.min ?? "−∞"} … ${p.max ?? "∞"}` : fmt.DASH}</td>
                            <td className="pl-3 text-muted-foreground">{p.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
                {detail.data.provenance?.signal && <SignalProvenancePanel p={detail.data.provenance} />}
                {detail.data.category === "entry_exit" && (detail.data.provenance as unknown as EEProvenance | undefined)?.research?.cell &&
                  <EEProvenancePanel p={detail.data.provenance as unknown as EEProvenance} />}
                <details>
                  <summary className="cursor-pointer text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Source</summary>
                  <pre className="num mt-1 max-h-[420px] overflow-auto rounded-md border bg-surface-2 p-2 text-[11px] leading-snug">{detail.data.code}</pre>
                </details>
              </div>
            ) : null}
          </Panel>
        )}
      </div>
    </div>
  );
}

// A strategy imported from Signal Research: the signal it trades, where it was researched and how it validated.
function SignalProvenancePanel({ p }: { p: SignalProvenance }) {
  const r = p.research;
  const rows = r.results.filter((x) => x.horizon === r.primary_horizon);
  return (
    <div className="space-y-2 rounded-md border border-primary/30 p-2" data-provenance>
      <div className="text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Researched signal</div>
      <div className="rounded border border-warn/50 bg-warn/10 px-2 py-1 text-xs text-warn">{p.warning}</div>
      <KV rows={[
        ["Signal", <span className="num">{p.signal.key} · {p.signal.direction}</span>],
        ["Trigger", p.signal.trigger],
        ["Parameters", <span className="num">{JSON.stringify(p.signal.params)}</span>],
        ["Source experiment", <Link className="num text-primary hover:underline" to={`/signals/experiments/${r.experiment_id}/${encodeURIComponent(p.signal.key)}`}>{r.experiment_id}</Link>],
        ["Data", <span className="num">{r.dataset}@{r.dataset_version} · {r.universe_name ?? "custom"} ({r.n_symbols}) · {r.start} … {r.end}</span>],
        ["Method", <span className="num">{r.permutations} permutations · seed {r.seed} · methodology {r.methodology_hash.slice(0, 10)}</span>],
        ["Test period", p.validation.test_evaluated ? (p.validation.test_evaluations.length ? p.validation.test_evaluations.map((t) => (t.first_use ? "locked result (first evaluation)" : `re-evaluation (locked: ${t.locked_experiment})`)).join("; ") : "evaluated") : "locked, not evaluated"],
      ]} />
      <table className="w-full text-xs">
        <thead><tr className="border-b text-2xs text-muted-foreground"><th className="py-1 text-left font-medium">Partition ({r.primary_horizon} sessions)</th><th className="text-right font-medium">n</th><th className="text-right font-medium">Excess vs matched</th><th className="text-right font-medium">q</th><th className="pl-2 text-left font-medium">Verdict</th></tr></thead>
        <tbody>{rows.map((x) => (
          <tr key={x.partition} className="border-b last:border-0"><td className="py-0.5">{x.partition} <span className="text-muted-foreground">({x.role})</span></td><td className="num text-right">{fmt.int(x.n)}</td>
            <td className={`num text-right ${fmt.tone(x.excess_matched)}`}>{fmt.pct(x.excess_matched)}</td><td className="num text-right">{x.q_matched === null ? fmt.DASH : x.q_matched.toFixed(3)}</td><td className="pl-2">{x.verdict.replace("_", " ")}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

interface EEProvenance {
  warning: string;
  research: {
    experiment_id: string; cell: string; entry_name: string; exit_name: string; trigger: string; dataset: string; dataset_version: string;
    universe_name: string | null; n_symbols: number; start: string; end: string; methodology_hash: string; seed: number;
    trade_level: Record<string, { role: string; n: number; mean_ret: number | null; excess_matched: number | null; q: number | null; verdict: string | null; expectancy_r: number | null }>;
    portfolio: Record<string, { role: string; metrics: Record<string, number | null> }>;
  };
  validation: { test_evaluated: boolean };
}

// A system imported from the Entry-Exit Research Lab: its pairing, where it was researched and how it validated.
function EEProvenancePanel({ p }: { p: EEProvenance }) {
  const r = p.research;
  return (
    <div className="space-y-2 rounded-md border border-primary/30 p-2" data-provenance="entry-exit">
      <div className="text-2xs font-semibold tracking-wide text-muted-foreground uppercase">Researched entry-exit system</div>
      <div className="rounded border border-warn/50 bg-warn/10 px-2 py-1 text-xs text-warn">{p.warning}</div>
      <KV rows={[
        ["Entry", r.entry_name], ["Exit", <span>{r.exit_name}<div className="text-2xs text-muted-foreground">{r.trigger}</div></span>],
        ["Source cell", <Link className="num text-primary hover:underline" to={`/entry-exit/experiments/${r.experiment_id}/cells/${r.cell}`}>{r.experiment_id} / {r.cell}</Link>],
        ["Data", <span className="num">{r.dataset}@{r.dataset_version} · {r.universe_name ?? "custom"} ({r.n_symbols}) · {r.start} … {r.end}</span>],
        ["Method", <span className="num">seed {r.seed} · methodology {r.methodology_hash.slice(0, 10)}</span>],
        ["Test period", p.validation.test_evaluated ? "evaluated in the source experiment" : "locked, not evaluated"],
        ["Forward eligibility", "needs a finished backtest of this strategy version (New backtest) before deployment"],
      ]} />
      <table className="w-full text-xs">
        <thead><tr className="border-b text-2xs text-muted-foreground"><th className="py-1 text-left font-medium">Partition</th><th className="text-right font-medium">trades</th><th className="text-right font-medium">mean</th><th className="text-right font-medium">excess vs random</th><th className="text-right font-medium">q</th><th className="text-right font-medium">Sharpe</th><th className="text-right font-medium">CAGR</th><th className="pl-2 text-left font-medium">Verdict</th></tr></thead>
        <tbody>{Object.entries(r.trade_level).map(([k, x]) => (
          <tr key={k} className="border-b last:border-0"><td className="py-0.5">{k} <span className="text-muted-foreground">({x.role})</span></td><td className="num text-right">{fmt.int(x.n)}</td>
            <td className={`num text-right ${fmt.tone(x.mean_ret)}`}>{fmt.pct(x.mean_ret)}</td><td className={`num text-right ${fmt.tone(x.excess_matched)}`}>{fmt.pct(x.excess_matched)}</td>
            <td className="num text-right">{x.q === null || x.q === undefined ? fmt.DASH : x.q.toFixed(3)}</td><td className="num text-right">{fmt.num(r.portfolio[k]?.metrics?.sharpe, 2)}</td>
            <td className={`num text-right ${fmt.tone(r.portfolio[k]?.metrics?.cagr)}`}>{fmt.pct(r.portfolio[k]?.metrics?.cagr)}</td><td className="pl-2">{(x.verdict ?? "").replace("_", " ")}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}
