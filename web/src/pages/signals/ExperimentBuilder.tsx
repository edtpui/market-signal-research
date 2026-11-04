// Experiment Builder: signals (single, several, whole families, parameter comparisons, combinations), data (dataset,
// universe, window inside the dataset's coverage, frequency), the event-study method (horizons, entry timing, event
// sampling), the controls (matched-random stocks, random entry dates, benchmarks), eligibility, chronological
// partitions and the statistics. Validate, save, launch, or clone a previous experiment (?from=) or a saved config.
import { CheckCircle2, Play, Plus, Save, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { api, ApiError } from "@/api/client";
import { useDatasets, useUniverses } from "@/api/hooks";
import {
  useCatalog, useDeleteSignalConfig, useLaunchSignals, useOptions, useSaveSignalConfig, useSignalConfigs, useValidateSignals,
  type AnySpec, type CombinationSpec, type ExperimentDetail, type Partition, type SignalExperimentConfig, type SignalSpec, type ValidateResult,
} from "@/api/signals";
import { ChoiceSelect, Field, NumberInput } from "@/components/ConfigForm";
import { Empty, ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Switch, Textarea } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";
import { navigate, useLocation } from "@/lib/router";
import { DirectionBadge, ScopeBadge, SubNav } from "@/pages/signals/common";

const csvNums = (s: string) => s.split(/[\s,;]+/).map((x) => Number(x)).filter((x) => Number.isFinite(x) && x > 0);

function Toggle({ label, checked, onChange, hint, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string; disabled?: boolean }) {
  return (
    <Field label={label} hint={hint}>
      <div className="flex h-8 items-center gap-2"><Switch checked={checked} disabled={disabled} onCheckedChange={onChange} aria-label={label} />{disabled && hint && <span className="text-2xs text-warn">{hint}</span>}</div>
    </Field>
  );
}

export function ExperimentBuilder() {
  const { search } = useLocation();
  const opts = useOptions();
  const cat = useCatalog();
  const datasets = useDatasets();
  const configs = useSignalConfigs();
  const validate = useValidateSignals();
  const launch = useLaunchSignals();
  const save = useSaveSignalConfig();
  const del = useDeleteSignalConfig();
  const [cfg, setCfg] = useState<SignalExperimentConfig | null>(null);
  const [universeMode, setUniverseMode] = useState<"named" | "custom">("named");
  const [symbols, setSymbols] = useState("");
  const [horizonsText, setHorizonsText] = useState("1, 5, 10, 20, 60");
  const [benchText, setBenchText] = useState("SPY");
  const [addId, setAddId] = useState("");
  const [addFamily, setAddFamily] = useState("");
  const [grid, setGrid] = useState({ id: "", param: "", values: "" });
  const [problem, setProblem] = useState<string | null>(null);
  const [check, setCheck] = useState<ValidateResult | null>(null);
  const [saveName, setSaveName] = useState("");
  const universes = useUniverses(cfg?.dataset);

  useEffect(() => {
    if (cfg || !opts.data || !datasets.data || !cat.data) return;
    const from = search.get("from");
    const saved = search.get("config");
    if (from) {
      api.get<ExperimentDetail>(`/signals/experiments/${encodeURIComponent(from)}`).then((r) => {
        load({ ...r.config, dataset_version: null, resolved_signals: null, analysis_end: null, name: `${r.config.name || from} (copy)` });
      }).catch((e) => setProblem(String(e)));
      return;
    }
    if (saved && configs.data) {
      const c = configs.data.find((x) => x.config_id === saved);
      if (c) return load(c.config);
    }
    const ds = datasets.data.find((d) => d.id === "us_core" && d.cached)?.id ?? datasets.data.find((d) => d.cached)?.id ?? datasets.data[0]?.id ?? "";
    const last = datasets.data.find((d) => d.id === ds)?.current?.last_date ?? opts.data.policy.ceiling;
    const sigs = (search.get("signals") ?? "").split(",").filter(Boolean).map((id): SignalSpec => ({ kind: "signal", id, params: {} }));
    const fam = search.get("family");
    load({
      ...(opts.data.defaults as SignalExperimentConfig), name: "", notes: "", signals: sigs, families: fam ? [fam] : [], grids: [],
      dataset: ds, universe_name: "sp1500", universe: null, start: opts.data.policy.backtest_floor, end: last, partitions: [],
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.data, datasets.data, cat.data, configs.data]);

  function load(c: SignalExperimentConfig) {
    setCfg({ ...c, partitions: c.partition_scheme === "custom" ? c.partitions : [] });
    setHorizonsText(c.horizons.join(", "));
    setBenchText(c.benchmarks.join(", "));
    if (c.universe && c.universe.length) { setUniverseMode("custom"); setSymbols(c.universe.join(", ")); } else setUniverseMode("named");
  }

  const ds = datasets.data?.find((d) => d.id === cfg?.dataset);
  const policy = opts.data?.policy;
  const bounds = useMemo(() => {
    const first = ds?.current?.first_date, last = ds?.current?.last_date;
    return { lo: [policy?.backtest_floor, first].filter(Boolean).sort().at(-1), hi: [policy?.ceiling, last].filter(Boolean).sort()[0], last };
  }, [ds, policy]);
  const sigById = useMemo(() => new Map((cat.data?.signals ?? []).map((s) => [s.id, s])), [cat.data]);

  if (!cfg || !opts.data) return <div className="space-y-3"><PageHeader title="Experiment Builder" /><SubNav />{problem ? <ErrorState error={problem} /> : <Loading rows={10} />}</div>;
  const set = (patch: Partial<SignalExperimentConfig>) => { setCfg({ ...cfg, ...patch }); setCheck(null); };
  const final = (): SignalExperimentConfig => {
    const out: SignalExperimentConfig = { ...cfg, horizons: csvNums(horizonsText), benchmarks: benchText.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean) };
    if (universeMode === "custom") { out.universe = symbols.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean); out.universe_name = null; } else out.universe = null;
    if (out.primary_horizon && !out.horizons.includes(out.primary_horizon)) out.primary_horizon = null;
    if (out.partition_scheme !== "custom") out.partitions = [];
    return out;
  };
  const report = (e: unknown) => { const m = e instanceof ApiError ? e.message : String(e); setProblem(m); toast.error(m); };
  const doValidate = () => validate.mutate(final(), { onSuccess: (r) => { setProblem(null); setCheck(r); toast.success(`${r.n_signals} signals, ${r.n_tests} tests per partition`); }, onError: report });
  const doLaunch = () => launch.mutate(final(), { onSuccess: (r) => navigate(`/signals/experiments/${r.experiment_id}`), onError: report });
  const m = cfg.matching;
  const ref = opts.data.reference;
  const nInstances = cfg.signals.length + cfg.families.reduce((a, f) => a + (cat.data?.families.find((x) => x.id === f)?.n_signals ?? 0), 0)
    + cfg.grids.reduce((a, g) => a + Object.values(g.grid).reduce((b, v) => b * v.length, 1), 0);
  const gridSig = sigById.get(grid.id);

  const updSignal = (i: number, s: AnySpec) => set({ signals: cfg.signals.map((x, j) => (j === i ? s : x)) });
  const addCombo = () => set({ signals: [...cfg.signals, { kind: "combination", name: `combo ${cfg.signals.filter((s) => s.kind === "combination").length + 1}`, direction: "bullish", mode: "and", window: 1, edge: true, components: [{ id: "rsi14_below_30", params: {}, weight: 1, negate: false }, { id: "bb_below_lower", params: {}, weight: 1, negate: false }] }] });

  return (
    <div className="space-y-3">
      <PageHeader title="Experiment Builder" sub="An event study: every trigger is entered at the next open and compared with matched random stocks, random entry dates of the same stock, and benchmarks."
        actions={<>
          <Button size="sm" variant="outline" onClick={doValidate} disabled={validate.isPending} data-action="validate"><CheckCircle2 className="size-3.5" />Validate</Button>
          <Button size="sm" onClick={doLaunch} disabled={launch.isPending} data-action="launch"><Play className="size-3.5" />Launch</Button>
        </>} />
      <SubNav />
      {problem && <ErrorState error={problem} />}
      <div className="grid gap-3 xl:grid-cols-[1fr_360px]">
        <div className="space-y-3">
          <Panel title={`Signals (${nInstances} instances)`}>
            <div className="space-y-3">
              <div className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto_1fr_auto]">
                <Field label="Add a signal">
                  <ChoiceSelect value={addId} onChange={setAddId} aria-label="add signal" options={(cat.data?.signals ?? []).map((s) => ({ value: s.id, label: `${s.name} · ${s.id}` }))} />
                </Field>
                <div className="flex items-end"><Button size="sm" variant="outline" disabled={!addId} onClick={() => { set({ signals: [...cfg.signals, { kind: "signal", id: addId, params: {} }] }); setAddId(""); }} data-action="add-signal"><Plus className="size-3.5" />Add</Button></div>
                <Field label="Add a whole family">
                  <ChoiceSelect value={addFamily} onChange={setAddFamily} aria-label="add family" options={(cat.data?.families ?? []).map((f) => ({ value: f.id, label: `${f.name} (${f.n_signals})` }))} />
                </Field>
                <div className="flex items-end"><Button size="sm" variant="outline" disabled={!addFamily || cfg.families.includes(addFamily)} onClick={() => { set({ families: [...cfg.families, addFamily] }); setAddFamily(""); }} data-action="add-family"><Plus className="size-3.5" />Add</Button></div>
              </div>
              {cfg.families.length > 0 && (
                <div className="flex flex-wrap gap-1.5">{cfg.families.map((f) => (
                  <Badge key={f} variant="outline" className="h-6 gap-1 rounded-sm" data-family-chip={f}>family: {cat.data?.families.find((x) => x.id === f)?.name ?? f}
                    <button type="button" aria-label={`remove ${f}`} onClick={() => set({ families: cfg.families.filter((x) => x !== f) })}><X className="size-3" /></button></Badge>
                ))}</div>
              )}
              {cfg.signals.length === 0 && cfg.families.length === 0 && cfg.grids.length === 0 && <Empty title="No signals yet">Add signals, families, a parameter comparison or a combination.</Empty>}
              <div className="space-y-1.5">
                {cfg.signals.map((s, i) => s.kind === "signal" ? (
                  <div key={i} className="rounded-md border px-2 py-1.5" data-spec={s.id}>
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-medium">{sigById.get(s.id)?.name ?? s.id}</span><span className="num text-2xs text-muted-foreground">{s.id}</span>
                      {sigById.get(s.id) && <><DirectionBadge d={sigById.get(s.id)!.direction} /><ScopeBadge s={sigById.get(s.id)!.scope} /></>}
                      <span className="ml-auto flex items-center gap-1">
                        <ChoiceSelect className="h-6 w-28 text-2xs" value={s.regime?.market_trend ?? "any"} aria-label="market regime filter"
                          onChange={(v) => updSignal(i, { ...s, regime: { market_trend: v as "any", stock_vol: s.regime?.stock_vol ?? "any", vol_window: 63 } })}
                          options={[{ value: "any", label: "any market" }, { value: "up", label: "SPY > SMA200" }, { value: "down", label: "SPY < SMA200" }]} />
                        <ChoiceSelect className="h-6 w-24 text-2xs" value={s.regime?.stock_vol ?? "any"} aria-label="stock volatility filter"
                          onChange={(v) => updSignal(i, { ...s, regime: { market_trend: s.regime?.market_trend ?? "any", stock_vol: v as "any", vol_window: 63 } })}
                          options={[{ value: "any", label: "any vol" }, { value: "low", label: "low vol" }, { value: "mid", label: "mid vol" }, { value: "high", label: "high vol" }]} />
                        <Button size="icon" variant="ghost" className="size-6" aria-label="remove signal" onClick={() => set({ signals: cfg.signals.filter((_, j) => j !== i) })}><Trash2 className="size-3.5" /></Button>
                      </span>
                    </div>
                    {Object.keys(sigById.get(s.id)?.params ?? {}).length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-2">
                        {Object.entries(sigById.get(s.id)!.params).map(([k, p]) => (
                          <label key={k} className="flex items-center gap-1 text-2xs text-muted-foreground" title={p.description}>{k}
                            <Input className="num h-6 w-20 text-2xs" type="number" step="any" value={String(s.params[k] ?? p.default)} aria-label={`${s.id} ${k}`}
                              onChange={(e) => updSignal(i, { ...s, params: { ...s.params, [k]: p.kind === "int" ? parseInt(e.target.value || "0", 10) : Number(e.target.value) } })} />
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                ) : <ComboEditor key={i} spec={s} catalog={cat.data?.signals ?? []} onChange={(c) => updSignal(i, c)} onRemove={() => set({ signals: cfg.signals.filter((_, j) => j !== i) })} />)}
              </div>
              <div className="flex flex-wrap items-end gap-2 border-t pt-2">
                <Field label="Parameter comparison: signal" className="w-64"><ChoiceSelect value={grid.id} onChange={(v) => setGrid({ id: v, param: "", values: "" })} aria-label="grid signal"
                  options={(cat.data?.signals ?? []).filter((s) => Object.keys(s.params).length).map((s) => ({ value: s.id, label: s.id }))} /></Field>
                <Field label="parameter" className="w-36"><ChoiceSelect value={grid.param} onChange={(v) => setGrid({ ...grid, param: v })} aria-label="grid param"
                  options={Object.keys(gridSig?.params ?? {}).map((k) => ({ value: k, label: k }))} /></Field>
                <Field label="values (comma)" className="w-48"><Input className="num h-8 text-xs" value={grid.values} onChange={(e) => setGrid({ ...grid, values: e.target.value })} placeholder="20, 25, 30" aria-label="grid values" /></Field>
                <Button size="sm" variant="outline" disabled={!grid.id || !grid.param || !grid.values.trim()} data-action="add-grid"
                  onClick={() => { set({ grids: [...cfg.grids, { id: grid.id, params: {}, grid: { [grid.param]: grid.values.split(/[\s,;]+/).filter(Boolean).map(Number) } }] }); setGrid({ id: "", param: "", values: "" }); }}><Plus className="size-3.5" />Add comparison</Button>
                <Button size="sm" variant="outline" onClick={addCombo} data-action="add-combo"><Plus className="size-3.5" />Add combination</Button>
              </div>
              {cfg.grids.map((g, i) => (
                <div key={i} className="flex items-center gap-2 text-xs"><Badge variant="outline" className="rounded-sm">comparison</Badge><span className="num">{g.id}: {Object.entries(g.grid).map(([k, v]) => `${k} ∈ {${v.join(", ")}}`).join("; ")}</span>
                  <Button size="icon" variant="ghost" className="size-6" aria-label="remove comparison" onClick={() => set({ grids: cfg.grids.filter((_, j) => j !== i) })}><Trash2 className="size-3.5" /></Button></div>
              ))}
            </div>
          </Panel>

          <Panel title="Data">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Field label="Dataset"><ChoiceSelect value={cfg.dataset} onChange={(v) => set({ dataset: v })} aria-label="dataset" options={(datasets.data ?? []).map((d) => ({ value: d.id, label: `${d.id}${d.cached ? "" : " (not cached)"}` }))} /></Field>
              <Field label="Universe"><ChoiceSelect value={universeMode === "custom" ? "__custom" : cfg.universe_name ?? ""} aria-label="universe"
                onChange={(v) => { if (v === "__custom") setUniverseMode("custom"); else { setUniverseMode("named"); set({ universe_name: v }); } }}
                options={[...(universes.data ?? []).map((u) => ({ value: u.name, label: `${u.name}${u.symbols ? ` (${u.symbols.length})` : ""}` })), { value: "__custom", label: "Custom symbols…" }]} /></Field>
              <Field label={`Start (≥ ${bounds.lo ?? "?"})`}><Input type="date" className="num h-8 text-xs" value={cfg.start} min={bounds.lo} max={bounds.hi} onChange={(e) => set({ start: e.target.value })} aria-label="start" /></Field>
              <Field label={`End (≤ ${bounds.hi ?? "?"}, last ingested)`}><Input type="date" className="num h-8 text-xs" value={cfg.end} min={bounds.lo} max={bounds.hi} onChange={(e) => set({ end: e.target.value })} aria-label="end" /></Field>
              {universeMode === "custom" && <Field label="Symbols" className="col-span-2 md:col-span-4"><Textarea className="num text-xs" value={symbols} onChange={(e) => setSymbols(e.target.value)} aria-label="symbols" /></Field>}
              <Field label="Frequency" hint="Bars; horizons count bars of this frequency"><ChoiceSelect value={cfg.frequency} onChange={(v) => set({ frequency: v as "1d" })} aria-label="frequency" options={[{ value: "1d", label: "Daily" }, { value: "1w", label: "Weekly" }, { value: "1mo", label: "Monthly" }]} /></Field>
              <Field label="Benchmarks" hint="Market benchmarks over the same entry and exit sessions"><Input className="num h-8 text-xs" value={benchText} onChange={(e) => setBenchText(e.target.value)} aria-label="benchmarks" /></Field>
              <Toggle label="Sector-ETF benchmark" checked={cfg.sector_benchmark} onChange={(v) => set({ sector_benchmark: v })} disabled={!ref.sector.available} hint={ref.sector.available ? ref.sector.note : "no sector reference data"} />
            </div>
            <div className="mt-2 text-2xs text-muted-foreground">Coverage of {cfg.dataset}: <span className="num">{ds?.current?.first_date ?? "?"} … {ds?.current?.last_date ?? "?"}</span>. Universe lists are today's: delisted securities are absent (survivorship bias), and every result says so.</div>
          </Panel>

          <Panel title="Event study">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Field label="Horizons (sessions)" hint="Forward-return horizons: exit at the same price field h sessions after entry"><Input className="num h-8 text-xs" value={horizonsText} onChange={(e) => setHorizonsText(e.target.value)} aria-label="horizons" /></Field>
              <Field label="Primary horizon" hint="Ranked and summarized by default"><NumberInput value={cfg.primary_horizon ?? null} allowEmpty onChange={(v) => set({ primary_horizon: v })} aria-label="primary horizon" /></Field>
              <Field label="Entry" hint="The signal is known at the close of t; entry is the next executable price"><ChoiceSelect value={cfg.entry} onChange={(v) => set({ entry: v as "next_open" })} aria-label="entry" options={[{ value: "next_open", label: "Next open (t+1)" }, { value: "next_close", label: "Next close (t+1)" }]} /></Field>
              <Field label="Round-trip cost (bps)" hint="Net figures subtract it from each signal event"><NumberInput value={cfg.costs.round_trip_bps} min={0} onChange={(v) => set({ costs: { round_trip_bps: v ?? 0 } })} aria-label="cost bps" /></Field>
              <Field label="Event sampling" hint="all: every trigger. first: first session of each run. cooldown: skip a symbol's triggers for N sessions. non-overlapping: per horizon, no overlapping holding periods per symbol.">
                <ChoiceSelect value={cfg.sampling.mode} onChange={(v) => set({ sampling: { ...cfg.sampling, mode: v as "all", cooldown: v === "cooldown" ? Math.max(1, cfg.sampling.cooldown) : cfg.sampling.cooldown } })} aria-label="sampling"
                  options={[{ value: "first", label: "First trigger" }, { value: "all", label: "Every trigger" }, { value: "cooldown", label: "Cooldown" }, { value: "non_overlapping", label: "Non-overlapping" }]} />
              </Field>
              {cfg.sampling.mode === "cooldown" && <Field label="Cooldown (sessions)"><NumberInput value={cfg.sampling.cooldown} min={1} onChange={(v) => set({ sampling: { ...cfg.sampling, cooldown: v ?? 1 } })} aria-label="cooldown" /></Field>}
              <Field label="Permutations" hint="Random-control draws per event; the smallest empirical p is 1/(B+1)"><NumberInput value={cfg.permutations} min={100} max={20000} onChange={(v) => set({ permutations: v ?? 1000 })} aria-label="permutations" /></Field>
              <Field label="Seed" hint="Saved with the experiment: draws are reproducible"><NumberInput value={cfg.seed} min={0} onChange={(v) => set({ seed: v ?? 0 })} aria-label="seed" /></Field>
            </div>
          </Panel>

          <Panel title="Random controls">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Toggle label="Match volatility" checked={m.volatility} onChange={(v) => set({ matching: { ...m, volatility: v } })} hint="Same-session, same trailing-volatility group (the video's design)" />
              <Field label="Volatility groups"><NumberInput value={m.vol_groups} min={2} max={50} onChange={(v) => set({ matching: { ...m, vol_groups: v ?? 10 } })} aria-label="vol groups" /></Field>
              <Field label="Volatility window"><NumberInput value={m.vol_window} min={10} max={252} onChange={(v) => set({ matching: { ...m, vol_window: v ?? 63 } })} aria-label="vol window" /></Field>
              <Field label="Minimum pool" hint="Fewer comparable stocks: the event is unmatched (reported, never relaxed)"><NumberInput value={m.min_pool} min={1} onChange={(v) => set({ matching: { ...m, min_pool: v ?? 10 } })} aria-label="min pool" /></Field>
              <Toggle label="Match sector" checked={m.sector} onChange={(v) => set({ matching: { ...m, sector: v } })} disabled={!ref.sector.available} hint={ref.sector.available ? "SIC-derived, current (not point-in-time)" : "no sector data"} />
              <Toggle label="Match liquidity" checked={m.liquidity} onChange={(v) => set({ matching: { ...m, liquidity: v } })} hint="Dollar-volume terciles" />
              <Toggle label="Match price" checked={m.price} onChange={(v) => set({ matching: { ...m, price: v } })} hint="Raw-price terciles" />
              <Toggle label="Match market cap" checked={m.market_cap} onChange={(v) => set({ matching: { ...m, market_cap: v } })} disabled={!ref.market_cap.available} hint={ref.market_cap.available ? "point-in-time file" : "no point-in-time market caps: refused, not approximated"} />
              <Toggle label="Random entry dates" checked={cfg.random_dates.enabled} onChange={(v) => set({ random_dates: { ...cfg.random_dates, enabled: v } })} hint="Same stock, random sessions of the same partition" />
              <Toggle label="Same volatility regime" checked={cfg.random_dates.same_vol_regime} onChange={(v) => set({ random_dates: { ...cfg.random_dates, same_vol_regime: v } })} />
              <Field label="Min random-date pool"><NumberInput value={cfg.random_dates.min_pool} min={1} onChange={(v) => set({ random_dates: { ...cfg.random_dates, min_pool: v ?? 20 } })} aria-label="rd min pool" /></Field>
              <Field label="Controls saved per event"><NumberInput value={cfg.save_controls} min={0} max={50} onChange={(v) => set({ save_controls: v ?? 5 })} aria-label="saved controls" /></Field>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
              <Field label="Min raw price ($)"><NumberInput value={cfg.eligibility.min_price} min={0} onChange={(v) => set({ eligibility: { ...cfg.eligibility, min_price: v ?? 0 } })} aria-label="min price" /></Field>
              <Field label="Min median $ volume (21d)"><NumberInput value={cfg.eligibility.min_dollar_volume} min={0} onChange={(v) => set({ eligibility: { ...cfg.eligibility, min_dollar_volume: v ?? 0 } })} aria-label="min dollar volume" /></Field>
              <Field label="Min history (bars)"><NumberInput value={cfg.eligibility.min_history} min={1} onChange={(v) => set({ eligibility: { ...cfg.eligibility, min_history: v ?? 126 } })} aria-label="min history" /></Field>
              <Field label="Signal workers" hint="Signals run in parallel inside the experiment"><NumberInput value={cfg.workers} min={1} max={16} onChange={(v) => set({ workers: v ?? 2 })} aria-label="workers" /></Field>
            </div>
          </Panel>

          <Panel title="Chronological validation and statistics">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Field label="Partitions" hint="Chronological, never shuffled; holding periods never cross a boundary">
                <ChoiceSelect value={cfg.partition_scheme} onChange={(v) => set({ partition_scheme: v as "default", partitions: v === "custom" ? opts.data!.default_partitions : [] })} aria-label="partitions"
                  options={[{ value: "default", label: "Train / validation / test" }, { value: "none", label: "One window (exploration)" }, { value: "custom", label: "Custom" }]} />
              </Field>
              <Toggle label="Evaluate the locked test period" checked={cfg.evaluate_test} onChange={(v) => set({ evaluate_test: v })} hint="Spends it: recorded in the append-only test ledger; the first result per signal is locked" />
              <Field label="Significance rule"><ChoiceSelect value={cfg.stats.significance} onChange={(v) => set({ stats: { ...cfg.stats, significance: v as "randomization" } })} aria-label="significance"
                options={[{ value: "randomization_and_bootstrap", label: "Randomization q AND clustered CI" }, { value: "randomization", label: "Randomization q" }, { value: "bootstrap", label: "Clustered CI" }]} /></Field>
              <Field label="p-value"><ChoiceSelect value={cfg.stats.p_value} onChange={(v) => set({ stats: { ...cfg.stats, p_value: v as "empirical" } })} aria-label="p value"
                options={[{ value: "empirical", label: "Empirical (permutations)" }, { value: "normal_approx", label: "Normal approx. of the null" }]} /></Field>
              <Field label="FDR level (BH)"><NumberInput value={cfg.stats.alpha} min={0.001} max={0.49} onChange={(v) => set({ stats: { ...cfg.stats, alpha: v ?? 0.05 } })} aria-label="alpha" /></Field>
              <Field label="Economic threshold (annual)" hint="Annualized net excess return a signal must reach to count as economically meaningful"><NumberInput value={cfg.stats.economic_annual_excess} min={0} onChange={(v) => set({ stats: { ...cfg.stats, economic_annual_excess: v ?? 0.02 } })} aria-label="economic threshold" /></Field>
              <Field label="Bootstrap resamples"><NumberInput value={cfg.stats.bootstrap} min={0} onChange={(v) => set({ stats: { ...cfg.stats, bootstrap: v ?? 1000 } })} aria-label="bootstrap" /></Field>
              <Field label="Min events per test"><NumberInput value={cfg.stats.min_events} min={5} onChange={(v) => set({ stats: { ...cfg.stats, min_events: v ?? 30 } })} aria-label="min events" /></Field>
            </div>
            {cfg.evaluate_test && <div className="mt-2 rounded-md border border-warn/50 bg-warn/10 px-2.5 py-1.5 text-xs text-warn">The test period is for a final, pre-decided evaluation. Parameters chosen after looking at it are no longer out-of-sample.</div>}
            {cfg.partition_scheme === "custom" && (
              <div className="mt-2 space-y-1">
                {cfg.partitions.map((p, i) => <PartitionRow key={i} p={p} onChange={(q) => set({ partitions: cfg.partitions.map((x, j) => (j === i ? q : x)) })} onRemove={() => set({ partitions: cfg.partitions.filter((_, j) => j !== i) })} />)}
                <Button size="xs" variant="outline" onClick={() => set({ partitions: [...cfg.partitions, { name: `p${cfg.partitions.length + 1}`, role: "validation", start: cfg.start, end: cfg.end }] })}><Plus className="size-3" />Partition</Button>
              </div>
            )}
          </Panel>
        </div>

        <div className="space-y-3">
          <Panel title="Check">
            {!check ? <div className="text-xs text-muted-foreground">Validate to resolve the signals, the universe, the dataset version and the partitions.</div> : (
              <div className="space-y-2 text-xs" data-validation>
                <KV rows={[
                  ["Signal instances", <span className="num">{check.n_signals}</span>],
                  ["Tests per partition", <span className="num">{check.n_tests} (signals × horizons, BH-adjusted together)</span>],
                  ["Symbols", <span className="num">{check.n_symbols}</span>],
                  ["Dataset version", <span className="num">{check.dataset_version}</span>],
                  ["Analysis ends", <span className="num">{check.analysis_end}</span>],
                  ["Primary horizon", <span className="num">{check.primary_horizon}</span>],
                  ["Periods", <span className="flex gap-1">{check.periods.map((p) => <PeriodBadge key={p} period={p} />)}<PeriodBadge period="EXPLORATORY" /></span>],
                ]} />
                <table className="w-full text-2xs"><tbody>{check.partitions.map((p) => (
                  <tr key={p.name} className="border-b last:border-0"><td className="py-0.5 font-medium">{p.name}</td><td>{p.role}{p.role === "test" && !cfg.evaluate_test ? " (locked)" : ""}</td><td className="num text-right">{p.start} … {p.end}</td></tr>
                ))}</tbody></table>
                {check.notes.map((n) => <div key={n} className="rounded-md border border-warn/40 bg-warn/10 px-2 py-1 text-warn">{n}</div>)}
              </div>
            )}
          </Panel>
          <Panel title="Name and save">
            <div className="space-y-2">
              <Field label="Experiment name"><Input className="h-8 text-xs" value={cfg.name ?? ""} onChange={(e) => set({ name: e.target.value })} aria-label="experiment name" /></Field>
              <Field label="Notes"><Textarea className="text-xs" value={cfg.notes ?? ""} onChange={(e) => set({ notes: e.target.value })} aria-label="notes" /></Field>
              <div className="flex gap-2">
                <Input className="h-8 text-xs" placeholder="configuration name" value={saveName} onChange={(e) => setSaveName(e.target.value)} aria-label="config name" />
                <Button size="sm" variant="outline" disabled={!saveName.trim() || save.isPending} data-action="save"
                  onClick={() => save.mutate({ name: saveName.trim(), config: final() }, { onSuccess: () => { toast.success("Saved"); setSaveName(""); }, onError: report })}><Save className="size-3.5" />Save</Button>
              </div>
            </div>
          </Panel>
          <Panel title={`Saved configurations (${configs.data?.length ?? 0})`} bodyClass="p-1">
            {(configs.data ?? []).length === 0 ? <div className="p-2 text-xs text-muted-foreground">None yet.</div> : (configs.data ?? []).map((c) => (
              <div key={c.config_id} className="flex items-center gap-2 rounded px-2 py-1 text-xs hover:bg-accent">
                <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => load(c.config)} title="Load">{c.name}</button>
                <span className="num text-2xs text-muted-foreground">{fmt.utc(c.created_at_utc).slice(0, 10)}</span>
                <Button size="icon" variant="ghost" className="size-6" aria-label={`delete ${c.name}`} onClick={() => del.mutate(c.config_id)}><Trash2 className="size-3.5" /></Button>
              </div>
            ))}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function PartitionRow({ p, onChange, onRemove }: { p: Partition; onChange: (p: Partition) => void; onRemove: () => void }) {
  return (
    <div className="grid grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2">
      <Input className="h-7 text-xs" value={p.name} onChange={(e) => onChange({ ...p, name: e.target.value })} aria-label="partition name" />
      <ChoiceSelect className="h-7" value={p.role} onChange={(v) => onChange({ ...p, role: v as Partition["role"] })} aria-label="partition role"
        options={["exploration", "train", "validation", "test"].map((r) => ({ value: r, label: r }))} />
      <Input type="date" className="num h-7 text-xs" value={p.start} onChange={(e) => onChange({ ...p, start: e.target.value })} aria-label="partition start" />
      <Input type="date" className="num h-7 text-xs" value={p.end} onChange={(e) => onChange({ ...p, end: e.target.value })} aria-label="partition end" />
      <Button size="icon" variant="ghost" className="size-7" aria-label="remove partition" onClick={onRemove}><Trash2 className="size-3.5" /></Button>
    </div>
  );
}

function ComboEditor({ spec, catalog, onChange, onRemove }: { spec: CombinationSpec; catalog: { id: string; name: string }[]; onChange: (c: CombinationSpec) => void; onRemove: () => void }) {
  const upd = (patch: Partial<CombinationSpec>) => onChange({ ...spec, ...patch });
  return (
    <div className="rounded-md border border-primary/30 px-2 py-2" data-combo={spec.name}>
      <div className="flex flex-wrap items-end gap-2">
        <Badge variant="outline" className="rounded-sm">combination</Badge>
        <Field label="Name" className="w-40"><Input className="h-7 text-xs" value={spec.name} onChange={(e) => upd({ name: e.target.value })} aria-label="combination name" /></Field>
        <Field label="Direction" className="w-28"><ChoiceSelect className="h-7" value={spec.direction} onChange={(v) => upd({ direction: v as "bullish" })} aria-label="combination direction" options={[{ value: "bullish", label: "bullish" }, { value: "bearish", label: "bearish" }]} /></Field>
        <Field label="Mode" className="w-32"><ChoiceSelect className="h-7" value={spec.mode} onChange={(v) => upd({ mode: v as "and", k: v === "k_of_n" ? 2 : null, threshold: v === "weighted" ? 1 : null })} aria-label="combination mode"
          options={[{ value: "and", label: "AND" }, { value: "or", label: "OR" }, { value: "k_of_n", label: "k of n" }, { value: "weighted", label: "weighted score" }]} /></Field>
        {spec.mode === "k_of_n" && <Field label="k" className="w-16"><NumberInput className="h-7" value={spec.k ?? 2} min={1} onChange={(v) => upd({ k: v ?? 2 })} aria-label="k" /></Field>}
        {spec.mode === "weighted" && <Field label="threshold" className="w-20"><NumberInput className="h-7" value={spec.threshold ?? 1} onChange={(v) => upd({ threshold: v ?? 1 })} aria-label="threshold" /></Field>}
        <Field label="Confirm window" className="w-24" ><NumberInput className="h-7" value={spec.window} min={1} max={20} onChange={(v) => upd({ window: v ?? 1 })} aria-label="window" /></Field>
        <Field label="First session only" className="w-28"><div className="flex h-7 items-center"><Switch checked={spec.edge} onCheckedChange={(c) => upd({ edge: c })} aria-label="edge" /></div></Field>
        <Button size="icon" variant="ghost" className="ml-auto size-7" aria-label="remove combination" onClick={onRemove}><Trash2 className="size-3.5" /></Button>
      </div>
      <div className="mt-2 space-y-1">
        {spec.components.map((c, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2 text-xs">
            <ChoiceSelect className="h-7 w-72" value={c.id} onChange={(v) => upd({ components: spec.components.map((x, j) => (j === i ? { ...x, id: v, params: {} } : x)) })} aria-label={`component ${i + 1}`}
              options={catalog.map((s) => ({ value: s.id, label: `${s.name} · ${s.id}` }))} />
            <label className="flex items-center gap-1 text-2xs text-muted-foreground">NOT<Switch checked={c.negate} onCheckedChange={(v) => upd({ components: spec.components.map((x, j) => (j === i ? { ...x, negate: v } : x)) })} aria-label={`negate ${i + 1}`} /></label>
            {spec.mode === "weighted" && <label className="flex items-center gap-1 text-2xs text-muted-foreground">weight<Input className="num h-6 w-16 text-2xs" type="number" step="any" value={c.weight} onChange={(e) => upd({ components: spec.components.map((x, j) => (j === i ? { ...x, weight: Number(e.target.value) } : x)) })} aria-label={`weight ${i + 1}`} /></label>}
            {spec.components.length > 2 && <Button size="icon" variant="ghost" className="size-6" aria-label={`remove component ${i + 1}`} onClick={() => upd({ components: spec.components.filter((_, j) => j !== i) })}><X className="size-3" /></Button>}
          </div>
        ))}
        {spec.components.length < 8 && <Button size="xs" variant="ghost" onClick={() => upd({ components: [...spec.components, { id: catalog[0]?.id ?? "rsi14_below_30", params: {}, weight: 1, negate: false }] })}><Plus className="size-3" />Component</Button>}
      </div>
    </div>
  );
}
