// Backtesting: configure an experiment (strategy, version, parameters, universe, window inside the dataset's actual
// coverage, frequency, capital, benchmark, execution assumptions), validate it, save it, launch it, or collect
// variants into a batch of independent runs.
import { CheckCircle2, Layers, Play, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { api, ApiError, type ExecutionConfig, type ExperimentConfig, type RunDetail } from "@/api/client";
import {
  useConfigs, useDatasets, useDeleteConfig, useExperiments, useLaunch, useSaveConfig, useSettings, useStrategies,
  useUniverses, useValidate,
} from "@/api/hooks";
import { ChoiceSelect, ExecutionEditor, Field, NumberInput, ParamsEditor } from "@/components/ConfigForm";
import { Empty, ErrorState, Loading, PageHeader, Panel, PeriodBadge, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { Link, navigate, useLocation } from "@/lib/router";
import { ptradeCompatible } from "@/pages/Strategies";

const FALLBACK_EXEC: ExecutionConfig = {
  fill_timing: "next_open", slippage_bps: 0, commission: { per_share: 0, bps: 0, per_order: 0, minimum: 0 },
  spread: { model: "none", bps: 0, hl_fraction: 0 }, participation_cap: null, cash_policy: "scale", allow_short: false,
  short_borrow_bps_per_year: 0, fractional_shares: false, max_gross_leverage: 1, price_mode: "adjusted",
};

function paramDefaults(schema: Record<string, { default: unknown }>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(schema).map(([k, p]) => [k, p.default]));
}

export function BacktestPage() {
  const { search } = useLocation();
  const settings = useSettings();
  const strategies = useStrategies();
  const datasets = useDatasets();
  const configs = useConfigs();
  const runs = useExperiments();
  const launch = useLaunch();
  const validate = useValidate();
  const save = useSaveConfig();
  const del = useDeleteConfig();
  const [cfg, setCfg] = useState<ExperimentConfig | null>(null);
  const [universeMode, setUniverseMode] = useState<"named" | "custom">("named");
  const [symbols, setSymbols] = useState("");
  const [batch, setBatch] = useState<ExperimentConfig[]>([]);
  const [saveName, setSaveName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const universes = useUniverses(cfg?.dataset);

  // initial config: from ?from=<run>, ?config=<saved id>, or defaults (?strategy=)
  useEffect(() => {
    if (cfg || !settings.data || !strategies.data || !datasets.data) return;
    const from = search.get("from");
    const saved = search.get("config");
    if (from) {
      api.get<RunDetail>(`/experiments/${encodeURIComponent(from)}`).then((r) => {
        load({ ...r.config, dataset_version: null, name: `${r.config.name || r.config.strategy} (copy)` });
      }).catch((e) => setProblem(String(e)));
      return;
    }
    if (saved && configs.data) {
      const c = configs.data.find((x) => x.config_id === saved);
      if (c) return load(c.config);
    }
    const sid = search.get("strategy") ?? strategies.data.strategies[0]?.id;
    const s = strategies.data.strategies.find((x) => x.id === sid) ?? strategies.data.strategies[0];
    const ds = datasets.data.find((d) => d.cached)?.id ?? datasets.data[0]?.id ?? "";
    const last = datasets.data.find((d) => d.id === ds)?.current?.last_date ?? settings.data.policy.ceiling;
    setCfg({
      name: "", strategy: s.id, strategy_version: s.version, params: paramDefaults(s.params), dataset: ds,
      universe_name: settings.data.universes.includes("mega_caps") ? "mega_caps" : settings.data.universes[0] ?? "all",
      universe: null, start: settings.data.policy.backtest_floor, end: last, frequency: "1d",
      initial_capital: 100_000, benchmark: "SPY", execution: settings.data.execution_defaults ?? FALLBACK_EXEC, seed: 0, notes: "",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.data, strategies.data, datasets.data, configs.data]);

  function load(c: ExperimentConfig) {
    setCfg({ ...c, execution: { ...FALLBACK_EXEC, ...c.execution } });
    if (c.universe && c.universe.length) {
      setUniverseMode("custom");
      setSymbols(c.universe.join(", "));
    } else setUniverseMode("named");
  }

  const strat = strategies.data?.strategies.find((s) => s.id === cfg?.strategy);
  const ds = datasets.data?.find((d) => d.id === cfg?.dataset);
  const policy = settings.data?.policy;
  const bounds = useMemo(() => {
    const first = ds?.current?.first_date, last = ds?.current?.last_date;
    const lo = [policy?.backtest_floor, first].filter(Boolean).sort().at(-1);
    const hi = [policy?.ceiling, last].filter(Boolean).sort()[0];
    return { lo, hi, first, last };
  }, [ds, policy]);
  const resolvedUniverse = universes.data?.find((u) => u.name === cfg?.universe_name)?.symbols;

  if (!cfg) {
    return <div className="space-y-3"><PageHeader title="Backtesting" />{problem ? <ErrorState error={problem} /> : <Loading rows={8} />}</div>;
  }
  const set = (patch: Partial<ExperimentConfig>) => setCfg({ ...cfg, ...patch });
  const final = (): ExperimentConfig => {
    const out: ExperimentConfig = { ...cfg };
    if (universeMode === "custom") {
      out.universe = symbols.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      out.universe_name = null;
    } else out.universe = null;
    if (!out.benchmark) out.benchmark = null;
    return out;
  };
  const report = (e: unknown) => {
    const msg = e instanceof ApiError ? e.message : String(e);
    setProblem(msg);
    toast.error(msg);
  };
  const doValidate = () => validate.mutate(final(), {
    onSuccess: (r) => { setProblem(null); toast.success(`Valid: ${r.n_symbols} symbols, warm-up ${r.warmup} bars, periods ${r.periods.join(", ")}`); },
    onError: report,
  });
  const doLaunch = () => launch.mutate([final()], { onSuccess: (ids) => navigate(`/runs/${ids[0]}`), onError: report });
  const doBatch = () => launch.mutate(batch, {
    onSuccess: (ids) => { toast.success(`${ids.length} experiments queued`); setBatch([]); navigate("/experiments"); }, onError: report,
  });
  const compat = strat ? ptradeCompatible(strat) : null;

  return (
    <div className="space-y-3">
      <PageHeader title="Backtesting" sub="Event-driven, deterministic. Orders decided at a close fill at the next session; nothing after the decision session is visible to the strategy."
        actions={<>
          <Button size="sm" variant="outline" onClick={doValidate} disabled={validate.isPending}><CheckCircle2 className="size-3.5" />Validate</Button>
          <Button size="sm" variant="outline" onClick={() => { setBatch((b) => [...b, final()]); toast.info("Added to batch"); }}><Layers className="size-3.5" />Add to batch</Button>
          <Button size="sm" onClick={doLaunch} disabled={launch.isPending} data-action="launch"><Play className="size-3.5" />Launch</Button>
        </>} />
      {problem && <ErrorState error={problem} />}
      <div className="grid gap-3 xl:grid-cols-[1fr_340px]">
        <div className="space-y-3">
          <Panel title="Strategy">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Field label="Strategy" className="md:col-span-2">
                <ChoiceSelect value={cfg.strategy} aria-label="strategy"
                  onChange={(id) => { const s = strategies.data!.strategies.find((x) => x.id === id)!; set({ strategy: id, strategy_version: s.version, params: paramDefaults(s.params) }); }}
                  options={(strategies.data?.strategies ?? []).map((s) => ({ value: s.id, label: `${s.name} (${s.id})` }))} />
              </Field>
              <Field label="Version">
                <ChoiceSelect value={cfg.strategy_version ?? strat?.version ?? ""} onChange={(v) => set({ strategy_version: v })} aria-label="version"
                  options={(strat?.versions ?? []).map((v) => ({ value: v, label: v }))} />
              </Field>
              <Field label="Run name"><Input className="h-8 text-xs" value={cfg.name ?? ""} onChange={(e) => set({ name: e.target.value })} placeholder="optional" aria-label="run name" /></Field>
            </div>
            {strat && <p className="mt-2 text-xs text-muted-foreground">{strat.description} <span className={compat?.ok ? "text-pos" : "text-warn"}>Forward on ptrade: {compat?.ok ? "compatible" : compat?.why}.</span></p>}
            <div className="mt-3">{strat && <ParamsEditor schema={strat.params} value={cfg.params} onChange={(p) => set({ params: p })} />}</div>
          </Panel>
          <Panel title="Data and universe">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Field label="Dataset" className="md:col-span-2">
                <ChoiceSelect value={cfg.dataset} onChange={(v) => set({ dataset: v, dataset_version: null })} aria-label="dataset"
                  options={(datasets.data ?? []).map((d) => ({ value: d.id, label: `${d.id}${d.cached ? "" : " (not cached)"}` }))} />
              </Field>
              <Field label="Frequency" hint="1w and 1mo are aggregated from daily bars (adjusted prices only).">
                <ChoiceSelect value={cfg.frequency} onChange={(v) => set({ frequency: v as ExperimentConfig["frequency"] })} aria-label="frequency"
                  options={[{ value: "1d", label: "Daily" }, { value: "1w", label: "Weekly" }, { value: "1mo", label: "Monthly" }]} />
              </Field>
              <Field label="Benchmark" hint="A symbol in any configured dataset (adjusted close, total return). Empty = none.">
                <Input className="num h-8 text-xs" value={cfg.benchmark ?? ""} onChange={(e) => set({ benchmark: e.target.value.toUpperCase() || null })} aria-label="benchmark" />
              </Field>
              <Field label="Start" hint={`Earliest: ${bounds.lo ?? "?"}`}>
                <Input type="date" className="num h-8 text-xs" value={cfg.start} min={bounds.lo} max={bounds.hi} onChange={(e) => set({ start: e.target.value })} aria-label="start" />
              </Field>
              <Field label="End" hint={`Latest: ${bounds.hi ?? "?"}`}>
                <Input type="date" className="num h-8 text-xs" value={cfg.end} min={bounds.lo} max={bounds.hi} onChange={(e) => set({ end: e.target.value })} aria-label="end" />
              </Field>
              <Field label="Initial capital"><NumberInput value={cfg.initial_capital} min={1} onChange={(v) => set({ initial_capital: v ?? 100000 })} aria-label="capital" /></Field>
              <Field label="Seed" hint="Seeds the strategy's random generator (ctx.rng)."><NumberInput value={cfg.seed} step={1} onChange={(v) => set({ seed: Math.round(v ?? 0) })} aria-label="seed" /></Field>
            </div>
            <div className="mt-2 text-2xs text-muted-foreground" data-coverage>
              {ds?.current
                ? <>Dataset coverage <span className="num text-foreground">{ds.current.first_date} → {ds.current.last_date}</span> ({fmt.int(ds.current.n_symbols)} symbols, version <span className="num">{fmt.bytesHash(ds.current.version, 8)}</span>). Selectable window <span className="num text-foreground">{bounds.lo} → {bounds.hi}</span>; bars before the start are loaded only as warm-up.</>
                : <>Dataset not cached yet: <Link to={`/data/${cfg.dataset}`} className="text-primary underline">ingest it</Link> to see its coverage.</>}
            </div>
            <div className="mt-3 grid gap-2 md:grid-cols-[200px_1fr]">
              <Field label="Universe">
                <ChoiceSelect value={universeMode} onChange={(v) => setUniverseMode(v as "named" | "custom")} aria-label="universe mode"
                  options={[{ value: "named", label: "Named universe" }, { value: "custom", label: "Custom symbols" }]} />
              </Field>
              {universeMode === "named" ? (
                <Field label="Named universe">
                  <ChoiceSelect value={cfg.universe_name ?? "all"} onChange={(v) => set({ universe_name: v })} aria-label="universe"
                    options={(universes.data ?? [{ name: "all" }]).map((u) => ({ value: u.name, label: `${u.name}${u.symbols ? ` (${u.symbols.length})` : ""}` }))} />
                </Field>
              ) : (
                <Field label="Symbols (comma or space separated)">
                  <Textarea className="num min-h-14 text-xs" value={symbols} onChange={(e) => setSymbols(e.target.value)} aria-label="symbols" />
                </Field>
              )}
            </div>
            {universeMode === "named" && resolvedUniverse && (
              <div className="mt-1 truncate text-2xs text-muted-foreground" title={resolvedUniverse.join(", ")}>
                {resolvedUniverse.length} symbols in {cfg.dataset}: {resolvedUniverse.slice(0, 30).join(", ")}{resolvedUniverse.length > 30 ? " …" : ""}
              </div>
            )}
          </Panel>
          <Panel title="Execution assumptions" info="Applied identically in backtests and on the forward host.">
            <ExecutionEditor value={cfg.execution} onChange={(e) => set({ execution: e })} />
          </Panel>
        </div>
        <div className="space-y-3">
          <Panel title={`Batch (${batch.length})`} actions={batch.length > 0 && <Button size="xs" onClick={doBatch} disabled={launch.isPending}><Play className="size-3" />Launch all</Button>}>
            {batch.length === 0 ? <div className="text-xs text-muted-foreground">Add variants with “Add to batch”; each runs as an independent experiment.</div> : (
              <ul className="space-y-1">
                {batch.map((b, i) => (
                  <li key={i} className="flex items-center gap-2 rounded border px-2 py-1 text-xs">
                    <span className="min-w-0 flex-1 truncate">{b.name || b.strategy} <span className="num text-2xs text-muted-foreground">{JSON.stringify(b.params)}</span></span>
                    <Button size="icon-xs" variant="ghost" aria-label="remove" onClick={() => setBatch(batch.filter((_, j) => j !== i))}><Trash2 /></Button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Saved configurations">
            <div className="mb-2 flex gap-1.5">
              <Input className="h-7 text-xs" placeholder="Name" value={saveName} onChange={(e) => setSaveName(e.target.value)} aria-label="config name" />
              <Button size="sm" variant="outline" className="h-7" disabled={!saveName || save.isPending}
                onClick={() => save.mutate({ name: saveName, config: final() }, { onSuccess: () => { toast.success("Saved"); setSaveName(""); }, onError: report })}>
                <Save className="size-3.5" />Save
              </Button>
            </div>
            {configs.isLoading ? <Loading /> : (configs.data ?? []).length === 0 ? <div className="text-xs text-muted-foreground">None saved.</div> : (
              <ul className="space-y-1">
                {configs.data!.map((c) => (
                  <li key={c.config_id} className="flex items-center gap-2 rounded border px-2 py-1 text-xs">
                    <button className="min-w-0 flex-1 truncate text-left hover:text-primary" onClick={() => load(c.config)} title="Load">
                      {c.name} <span className="text-2xs text-muted-foreground">{c.config.strategy}</span>
                    </button>
                    <Button size="icon-xs" variant="ghost" aria-label="delete" onClick={() => del.mutate(c.config_id)}><Trash2 /></Button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Recent runs" actions={<Link to="/experiments" className="text-xs text-primary hover:underline">All</Link>}>
            {(runs.data ?? []).length === 0 ? <Empty title="No runs yet" /> : (
              <ul className="space-y-1">
                {runs.data!.slice(0, 8).map((r) => (
                  <li key={r.run_id}>
                    <Link to={`/runs/${r.run_id}`} className="flex items-center gap-2 rounded px-1 py-0.5 text-xs hover:bg-accent/50">
                      <StatusBadge status={r.status} />
                      <span className="min-w-0 flex-1 truncate">{r.name || r.strategy}</span>
                      {r.periods.map((p) => <PeriodBadge key={p} period={p} />)}
                      <span className={`num w-16 text-right ${fmt.tone(r.summary?.total_return)}`}>{fmt.pct(r.summary?.total_return, 1)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
