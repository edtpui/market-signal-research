// Entry-Exit Experiment Builder: entries (catalog signals, families, combinations with AND / OR / k-of-n / weighted
// logic, parameter grids), exits (catalog exits, families, composed plans with condition logic and partial targets,
// parameter sweeps, protective stops shared by every plan, the baseline exit), the data window and universe, holding
// and risk settings, random-entry controls, the event-driven portfolio settings, costs and the chronological
// validation design. Validate shows the cell count and the multiple-testing load before anything runs.
import { CheckCircle2, Play, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import {
  useEECatalog, useEEConfigs, useEELaunch, useEEOptions, useEESaveConfig, useEEValidate,
  type EEConfig, type ExitComponentSpec, type ExitPlanSpec, type ValidateResult,
} from "@/api/entryExit";
import { useDatasets, useUniverses } from "@/api/hooks";
import type { AnySpec, CombinationSpec } from "@/api/signals";
import { ChoiceSelect, ExecutionEditor, Field, NumberInput, ParamsEditor } from "@/components/ConfigForm";
import { ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input, Switch } from "@/components/ui/primitives";
import { navigate, useLocation } from "@/lib/router";
import { EESubNav } from "@/pages/entryexit/common";

const csvVals = (s: string): unknown[] => s.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean).map((x) => (Number.isFinite(Number(x)) ? Number(x) : x));
const emptyPlan = (): ExitPlanSpec => ({ components: [], condition_mode: "or", min_bars: 0, name: "" });

function ComponentRow({ c, onChange, onRemove, exits, exitSignals, weighted }: {
  c: ExitComponentSpec; onChange: (c: ExitComponentSpec) => void; onRemove: () => void;
  exits: Map<string, { name: string; params: Record<string, import("@/api/client").ParamSpec>; kind: string }>;
  exitSignals: { id: string; name: string }[]; weighted: boolean;
}) {
  const d = exits.get(c.id);
  return (
    <div className="space-y-2 rounded-md border p-2" data-component={c.id}>
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium">{d?.name ?? c.id}</span>
        <span className="text-2xs text-muted-foreground">{c.id} · {d?.kind}</span>
        {weighted && d?.kind === "condition" && (
          <span className="ml-2 flex items-center gap-1 text-2xs">weight<NumberInput className="h-6 w-16" value={c.weight ?? 1} onChange={(v) => onChange({ ...c, weight: v ?? 1 })} /></span>
        )}
        <Button size="icon-xs" variant="ghost" className="ml-auto" onClick={onRemove} aria-label="remove component"><Trash2 /></Button>
      </div>
      {c.id === "signal_exit" && (
        <Field label="Exit signal (bearish catalog signal)">
          <ChoiceSelect value={(c.signal as { id?: string } | null)?.id ?? ""} onChange={(v) => onChange({ ...c, signal: { kind: "signal", id: v, params: {} } })}
            options={exitSignals.map((s) => ({ value: s.id, label: `${s.name} · ${s.id}` }))} aria-label="exit signal" />
        </Field>
      )}
      {d && Object.keys(d.params).length > 0 && <ParamsEditor schema={d.params} value={c.params} onChange={(p) => onChange({ ...c, params: p })} />}
    </div>
  );
}

function PlanEditor({ plan, onChange, exits, exitSignals }: {
  plan: ExitPlanSpec; onChange: (p: ExitPlanSpec) => void; exits: Map<string, { name: string; params: Record<string, import("@/api/client").ParamSpec>; kind: string }>;
  exitSignals: { id: string; name: string }[];
}) {
  const [add, setAdd] = useState("");
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Field label="Plan name"><Input className="h-8 text-xs" value={plan.name ?? ""} onChange={(e) => onChange({ ...plan, name: e.target.value })} /></Field>
        <Field label="Conditions combine" hint="or: any condition; and: all; k_of_n: at least k; weighted: sum of weights >= threshold. Stops always protect (tightest level); targets fill independently.">
          <ChoiceSelect value={plan.condition_mode} onChange={(v) => onChange({ ...plan, condition_mode: v as ExitPlanSpec["condition_mode"] })}
            options={["or", "and", "k_of_n", "weighted"].map((x) => ({ value: x, label: x }))} aria-label="condition mode" />
        </Field>
        {plan.condition_mode === "k_of_n" && <Field label="k"><NumberInput value={plan.k ?? 2} min={1} onChange={(v) => onChange({ ...plan, k: v })} /></Field>}
        {plan.condition_mode === "weighted" && <Field label="Threshold"><NumberInput value={plan.threshold ?? 1} onChange={(v) => onChange({ ...plan, threshold: v })} /></Field>}
        <Field label="Min sessions held" hint="conditions cannot fire before this; stops and targets stay active"><NumberInput value={plan.min_bars} min={0} onChange={(v) => onChange({ ...plan, min_bars: v ?? 0 })} /></Field>
      </div>
      {plan.components.map((c, i) => (
        <ComponentRow key={i} c={c} exits={exits} exitSignals={exitSignals} weighted={plan.condition_mode === "weighted"}
          onChange={(x) => onChange({ ...plan, components: plan.components.map((y, j) => (j === i ? x : y)) })}
          onRemove={() => onChange({ ...plan, components: plan.components.filter((_, j) => j !== i) })} />
      ))}
      <div className="flex items-end gap-2">
        <Field label="Add a component" className="flex-1">
          <ChoiceSelect value={add} onChange={setAdd} aria-label="add component" options={[...exits.entries()].map(([id, d]) => ({ value: id, label: `${d.name} · ${id}` }))} />
        </Field>
        <Button size="sm" variant="outline" disabled={!add} onClick={() => { onChange({ ...plan, components: [...plan.components, { id: add, params: {}, weight: 1 }] }); setAdd(""); }}><Plus className="size-3.5" />Add</Button>
      </div>
    </div>
  );
}

export function Builder() {
  const cat = useEECatalog();
  const opts = useEEOptions();
  const datasets = useDatasets();
  const configs = useEEConfigs();
  const validate = useEEValidate();
  const launch = useEELaunch();
  const save = useEESaveConfig();
  const { search } = useLocation();
  const [cfg, setCfg] = useState<EEConfig | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [check, setCheck] = useState<ValidateResult | null>(null);
  const [universeMode, setUniverseMode] = useState<"named" | "custom">("named");
  const [symbols, setSymbols] = useState("");
  const [addEntry, setAddEntry] = useState("");
  const [addFamily, setAddFamily] = useState("");
  const [addExit, setAddExit] = useState("");
  const [addExitFamily, setAddExitFamily] = useState("");
  const [plan, setPlan] = useState<ExitPlanSpec>(emptyPlan());
  const [grid, setGrid] = useState<{ component: number; param: string; values: string }>({ component: 0, param: "", values: "" });
  const [eGrid, setEGrid] = useState<{ id: string; param: string; values: string }>({ id: "", param: "", values: "" });
  const [protective, setProtective] = useState("");
  const [saveName, setSaveName] = useState("");
  const universes = useUniverses(cfg?.dataset);

  useEffect(() => {
    if (cfg || !opts.data || !datasets.data) return;
    const saved = search.get("config");
    if (saved && configs.data) {
      const c = configs.data.find((x) => x.config_id === saved);
      if (c) { setCfg(c.config); return; }
    }
    const ds = datasets.data.find((d) => d.id === "us_core" && d.cached)?.id ?? datasets.data.find((d) => d.cached)?.id ?? datasets.data[0]?.id ?? "";
    const last = datasets.data.find((d) => d.id === ds)?.current?.last_date ?? opts.data.policy.ceiling;
    const entries = (search.get("entries") ?? "").split(",").filter(Boolean).map((id): AnySpec => ({ kind: "signal", id, params: {} }));
    setCfg({
      ...(opts.data.defaults as EEConfig), name: "", notes: "", entries, entry_families: [], entry_grids: [], exits: [],
      exit_ids: (search.get("exits") ?? "").split(",").filter(Boolean), exit_families: [], exit_grids: [], protective: [],
      dataset: ds, universe_name: "sp1500", universe: null, start: opts.data.policy.backtest_floor, end: last, partitions: [],
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.data, datasets.data, configs.data]);

  const exitMap = useMemo(() => new Map((cat.data?.exits ?? []).map((e) => [e.id, e])), [cat.data]);
  const sigName = useMemo(() => new Map([...(cat.data?.entry_signals ?? []), ...(cat.data?.exit_signals ?? [])].map((s) => [s.id, s.name])), [cat.data]);

  if (!cfg || !opts.data || !cat.data) return <div className="space-y-3"><PageHeader title="Experiment Builder" /><EESubNav />{problem ? <ErrorState error={problem} /> : <Loading rows={10} />}</div>;
  const c = cat.data;
  const set = (patch: Partial<EEConfig>) => { setCfg({ ...cfg, ...patch }); setCheck(null); };
  const final = (): EEConfig => {
    const out = { ...cfg };
    if (universeMode === "custom") { out.universe = symbols.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean); out.universe_name = null; } else out.universe = null;
    if (out.partition_scheme !== "custom") out.partitions = [];
    return out;
  };
  const report = (e: unknown) => { const m = e instanceof ApiError ? e.message : String(e); setProblem(m); toast.error(m); };
  const doValidate = () => validate.mutate(final(), { onSuccess: (r) => { setProblem(null); setCheck(r); toast.success(`${r.n_entries} entries x ${r.n_exits} exits = ${r.n_cells} cells`); }, onError: report });
  const doLaunch = () => launch.mutate(final(), { onSuccess: (r) => navigate(`/entry-exit/experiments/${r.experiment_id}`), onError: report });
  const famCount = (f: string) => c.signal_families.find((x) => x.id === f)?.n_signals ?? 0;
  const nEntries = cfg.entries.length + cfg.entry_families.reduce((a, f) => a + famCount(f), 0) + cfg.entry_grids.reduce((a, g) => a + Object.values(g.grid).reduce((b, v) => b * v.length, 1), 0);
  const nExits = cfg.exits.length + cfg.exit_ids.length + cfg.exit_families.reduce((a, f) => a + (c.exit_families.find((x) => x.id === f)?.n_exits ?? 0), 0)
    + cfg.exit_grids.reduce((a, g) => a + Object.values(g.grid).reduce((b, v) => b * v.length, 1), 0) + (cfg.baseline_exit ? 1 : 0);
  const addCombo = () => set({ entries: [...cfg.entries, { kind: "combination", name: `combo ${cfg.entries.filter((s) => s.kind === "combination").length + 1}`, direction: "bullish", mode: "and", window: 3, edge: true,
    components: [{ id: "rsi14_below_30", params: {}, weight: 1, negate: false }, { id: "macd_signal_cross_up", params: {}, weight: 1, negate: false }] }] });
  const updEntry = (i: number, s: AnySpec) => set({ entries: cfg.entries.map((x, j) => (j === i ? s : x)) });
  const gridComp = plan.components[grid.component];
  const gridParams = gridComp ? Object.keys(exitMap.get(gridComp.id)?.params ?? {}) : [];
  const p = cfg.portfolio;
  const ctl = cfg.controls;

  return (
    <div className="space-y-3">
      <PageHeader title="Entry-Exit Experiment Builder"
        sub="Every entry event is replayed with every exit plan (identical events), against matched random entries under the same exit; the event-driven engine backtests the cells per partition."
        actions={<>
          <Button size="sm" variant="outline" onClick={doValidate} disabled={validate.isPending} data-action="validate"><CheckCircle2 className="size-3.5" />Validate</Button>
          <Button size="sm" onClick={doLaunch} disabled={launch.isPending} data-action="launch"><Play className="size-3.5" />Launch</Button>
        </>} />
      <EESubNav />
      {problem && <ErrorState error={problem} />}
      <div className="grid gap-3 xl:grid-cols-[1fr_380px]">
        <div className="space-y-3">
          <Panel title={`Entry signals (${nEntries})`}>
            <div className="space-y-2">
              <div className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto_1fr_auto_auto]">
                <Field label="Add a bullish signal"><ChoiceSelect value={addEntry} onChange={setAddEntry} aria-label="add entry" options={c.entry_signals.map((s) => ({ value: s.id, label: `${s.name} · ${s.id}` }))} /></Field>
                <div className="flex items-end"><Button size="sm" variant="outline" disabled={!addEntry} onClick={() => { set({ entries: [...cfg.entries, { kind: "signal", id: addEntry, params: {} }] }); setAddEntry(""); }} data-action="add-entry"><Plus className="size-3.5" />Add</Button></div>
                <Field label="Add a signal family (bullish members)"><ChoiceSelect value={addFamily} onChange={setAddFamily} aria-label="add entry family" options={c.signal_families.map((f) => ({ value: f.id, label: f.name }))} /></Field>
                <div className="flex items-end"><Button size="sm" variant="outline" disabled={!addFamily || cfg.entry_families.includes(addFamily)} onClick={() => { set({ entry_families: [...cfg.entry_families, addFamily] }); setAddFamily(""); }}><Plus className="size-3.5" />Add</Button></div>
                <div className="flex items-end"><Button size="sm" variant="outline" onClick={addCombo} data-action="add-combo"><Plus className="size-3.5" />Combination</Button></div>
              </div>
              {cfg.entries.map((s, i) => (
                <div key={i} className="rounded-md border p-2 text-xs" data-entry={s.kind === "signal" ? s.id : s.name}>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{s.kind === "signal" ? sigName.get(s.id) ?? s.id : s.name}</span>
                    <span className="text-2xs text-muted-foreground">{s.kind === "signal" ? s.id : `${s.mode} of ${s.components.length}`}</span>
                    <Button size="icon-xs" variant="ghost" className="ml-auto" onClick={() => set({ entries: cfg.entries.filter((_, j) => j !== i) })} aria-label="remove entry"><Trash2 /></Button>
                  </div>
                  {s.kind === "combination" && <ComboEditor spec={s} onChange={(x) => updEntry(i, x)} signals={c.entry_signals.concat(c.exit_signals)} />}
                </div>
              ))}
              {cfg.entry_families.length > 0 && <div className="flex flex-wrap gap-1 text-2xs">Families: {cfg.entry_families.map((f) => <Button key={f} size="xs" variant="secondary" onClick={() => set({ entry_families: cfg.entry_families.filter((x) => x !== f) })}>{f} ✕</Button>)}</div>}
              <div className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_120px_1fr_auto]">
                <Field label="Entry parameter grid: signal"><ChoiceSelect value={eGrid.id} onChange={(v) => setEGrid({ ...eGrid, id: v })} options={c.entry_signals.map((s) => ({ value: s.id, label: s.id }))} aria-label="grid signal" /></Field>
                <Field label="parameter"><Input className="h-8 text-xs" value={eGrid.param} onChange={(e) => setEGrid({ ...eGrid, param: e.target.value })} placeholder="level" /></Field>
                <Field label="values"><Input className="h-8 text-xs" value={eGrid.values} onChange={(e) => setEGrid({ ...eGrid, values: e.target.value })} placeholder="15, 20, 25, 30, 35, 40" /></Field>
                <div className="flex items-end"><Button size="sm" variant="outline" disabled={!eGrid.id || !eGrid.param || !eGrid.values}
                  onClick={() => { set({ entry_grids: [...cfg.entry_grids, { id: eGrid.id, params: {}, grid: { [eGrid.param]: csvVals(eGrid.values) } }] }); setEGrid({ id: "", param: "", values: "" }); }}><Plus className="size-3.5" />Grid</Button></div>
              </div>
              {cfg.entry_grids.map((g, i) => <div key={i} className="text-2xs text-muted-foreground">grid {g.id}: {JSON.stringify(g.grid)} <Button size="xs" variant="ghost" onClick={() => set({ entry_grids: cfg.entry_grids.filter((_, j) => j !== i) })}>remove</Button></div>)}
            </div>
          </Panel>

          <Panel title={`Exit plans (${nExits})`}>
            <div className="space-y-2">
              <div className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto_1fr_auto]">
                <Field label="Add a catalog exit (one plan)"><ChoiceSelect value={addExit} onChange={setAddExit} aria-label="add exit" options={c.exits.filter((e) => e.id !== "signal_exit").map((e) => ({ value: e.id, label: `${e.name} · ${e.id}` }))} /></Field>
                <div className="flex items-end"><Button size="sm" variant="outline" disabled={!addExit} onClick={() => { set({ exit_ids: [...cfg.exit_ids, addExit] }); setAddExit(""); }} data-action="add-exit"><Plus className="size-3.5" />Add</Button></div>
                <Field label="Add an exit family"><ChoiceSelect value={addExitFamily} onChange={setAddExitFamily} aria-label="add exit family" options={c.exit_families.map((f) => ({ value: f.id, label: `${f.name} (${f.n_exits})` }))} /></Field>
                <div className="flex items-end"><Button size="sm" variant="outline" disabled={!addExitFamily || cfg.exit_families.includes(addExitFamily)} onClick={() => { set({ exit_families: [...cfg.exit_families, addExitFamily] }); setAddExitFamily(""); }}><Plus className="size-3.5" />Add</Button></div>
              </div>
              <div className="flex flex-wrap gap-1 text-2xs">
                {cfg.exit_ids.map((x, i) => <Button key={`${x}${i}`} size="xs" variant="secondary" onClick={() => set({ exit_ids: cfg.exit_ids.filter((_, j) => j !== i) })}>{exitMap.get(x)?.name ?? x} ✕</Button>)}
                {cfg.exit_families.map((f) => <Button key={f} size="xs" variant="secondary" onClick={() => set({ exit_families: cfg.exit_families.filter((x) => x !== f) })}>family {f} ✕</Button>)}
                {cfg.exits.map((x, i) => <Button key={`p${i}`} size="xs" variant="secondary" onClick={() => set({ exits: cfg.exits.filter((_, j) => j !== i) })}>{x.name || x.components.map((y) => y.id).join(" + ")} ✕</Button>)}
              </div>
              <div className="rounded-md border border-dashed p-2">
                <div className="mb-2 text-xs font-medium">Compose a plan (multiple exits, partial targets, stop moves)</div>
                <PlanEditor plan={plan} onChange={setPlan} exits={exitMap} exitSignals={c.exit_signals} />
                <div className="mt-2 flex flex-wrap items-end gap-2">
                  <Button size="sm" variant="outline" disabled={!plan.components.length} onClick={() => { set({ exits: [...cfg.exits, plan] }); setPlan(emptyPlan()); }} data-action="add-plan"><Plus className="size-3.5" />Add plan</Button>
                  <span className="text-2xs text-muted-foreground">or sweep one component's parameter:</span>
                  <Field label="component"><ChoiceSelect value={String(grid.component)} onChange={(v) => setGrid({ ...grid, component: Number(v), param: "" })} options={plan.components.map((x, i) => ({ value: String(i), label: `${i}: ${x.id}` }))} aria-label="grid component" /></Field>
                  <Field label="parameter"><ChoiceSelect value={grid.param} onChange={(v) => setGrid({ ...grid, param: v })} options={gridParams.map((x) => ({ value: x, label: x }))} aria-label="grid parameter" /></Field>
                  <Field label="values"><Input className="h-8 w-48 text-xs" value={grid.values} onChange={(e) => setGrid({ ...grid, values: e.target.value })} placeholder="1, 1.5, 2, 3, 4, 5" /></Field>
                  <Button size="sm" variant="outline" disabled={!plan.components.length || !grid.param || !grid.values}
                    onClick={() => { set({ exit_grids: [...cfg.exit_grids, { plan, component: grid.component, grid: { [grid.param]: csvVals(grid.values) } }] }); setGrid({ component: 0, param: "", values: "" }); }} data-action="add-grid"><Plus className="size-3.5" />Sweep</Button>
                </div>
                {cfg.exit_grids.map((g, i) => <div key={i} className="mt-1 text-2xs text-muted-foreground">sweep {g.plan.components.map((x) => x.id).join(" + ")} · component {g.component} {JSON.stringify(g.grid)} <Button size="xs" variant="ghost" onClick={() => set({ exit_grids: cfg.exit_grids.filter((_, j) => j !== i) })}>remove</Button></div>)}
              </div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto_1fr]">
                <Field label="Protective component added to every plan" hint="e.g. a catastrophic ATR stop, so every exit shares the same worst-case protection">
                  <ChoiceSelect value={protective} onChange={setProtective} aria-label="protective" options={c.exits.filter((e) => e.kind === "stop").map((e) => ({ value: e.id, label: `${e.name} · ${e.id}` }))} />
                </Field>
                <div className="flex items-end"><Button size="sm" variant="outline" disabled={!protective} onClick={() => { set({ protective: [...cfg.protective, { id: protective, params: {} }] }); setProtective(""); }}><Plus className="size-3.5" />Add</Button></div>
                <Field label="Baseline exit (hold-entry comparisons)" hint="every other exit of an entry is compared with this one on identical events (paired)">
                  <ChoiceSelect value={cfg.baseline_exit ?? "none"} onChange={(v) => set({ baseline_exit: v === "none" ? null : v })} aria-label="baseline"
                    options={[{ value: "none", label: "none" }, ...c.exits.filter((e) => e.family === "time").map((e) => ({ value: e.id, label: e.name }))]} />
                </Field>
              </div>
              {cfg.protective.map((x, i) => (
                <ComponentRow key={i} c={x} exits={exitMap} exitSignals={c.exit_signals} weighted={false}
                  onChange={(y) => set({ protective: cfg.protective.map((z, j) => (j === i ? y : z)) })} onRemove={() => set({ protective: cfg.protective.filter((_, j) => j !== i) })} />
              ))}
            </div>
          </Panel>

          <Panel title="Data, holding and risk">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Field label="Dataset"><ChoiceSelect value={cfg.dataset} onChange={(v) => set({ dataset: v })} options={(datasets.data ?? []).map((d) => ({ value: d.id, label: `${d.id}${d.cached ? "" : " (not cached)"}` }))} aria-label="dataset" /></Field>
              <Field label="Universe">
                <ChoiceSelect value={universeMode === "custom" ? "__custom" : cfg.universe_name ?? ""} aria-label="universe"
                  onChange={(v) => { if (v === "__custom") setUniverseMode("custom"); else { setUniverseMode("named"); set({ universe_name: v }); } }}
                  options={[...(universes.data ?? []).map((u) => ({ value: u.name, label: `${u.name}${u.symbols ? ` (${u.symbols.length})` : ""}` })), { value: "__custom", label: "custom list" }]} />
              </Field>
              <Field label="Start"><Input type="date" className="h-8 text-xs" value={cfg.start} onChange={(e) => set({ start: e.target.value })} /></Field>
              <Field label="End"><Input type="date" className="h-8 text-xs" value={cfg.end} max={opts.data.policy.ceiling} onChange={(e) => set({ end: e.target.value })} /></Field>
              {universeMode === "custom" && <Field label="Symbols" className="col-span-full"><Input className="h-8 text-xs" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="AAPL, MSFT, ..." /></Field>}
              <Field label="Event sampling" hint="first: the first session of each run of triggers; all: every trigger; cooldown: skip a symbol's triggers for N sessions">
                <ChoiceSelect value={cfg.sampling.mode} onChange={(v) => set({ sampling: { ...cfg.sampling, mode: v as EEConfig["sampling"]["mode"] } })} options={["first", "all", "cooldown"].map((x) => ({ value: x, label: x }))} aria-label="sampling" />
              </Field>
              {cfg.sampling.mode === "cooldown" && <Field label="Cooldown"><NumberInput value={cfg.sampling.cooldown} min={1} onChange={(v) => set({ sampling: { ...cfg.sampling, cooldown: v ?? 1 } })} /></Field>}
              <Field label="Max holding (sessions)" hint="every plan exits at the open after this many sessions"><NumberInput value={cfg.max_hold} min={1} onChange={(v) => set({ max_hold: v ?? 252 })} /></Field>
              <Field label="Reference ATR (n)" hint="R for plans without a stop = multiple x ATR(n) at the signal close"><NumberInput value={cfg.ref_atr_n} min={2} onChange={(v) => set({ ref_atr_n: v ?? 14 })} /></Field>
              <Field label="Reference ATR multiple"><NumberInput value={cfg.ref_atr_mult} min={0.1} onChange={(v) => set({ ref_atr_mult: v ?? 2 })} /></Field>
              <Field label="Min price ($, raw)"><NumberInput value={cfg.eligibility.min_price} min={0} onChange={(v) => set({ eligibility: { ...cfg.eligibility, min_price: v ?? 0 } })} /></Field>
              <Field label="Min $ volume (median 21d)"><NumberInput value={cfg.eligibility.min_dollar_volume} min={0} onChange={(v) => set({ eligibility: { ...cfg.eligibility, min_dollar_volume: v ?? 0 } })} /></Field>
              <Field label="Min history (bars)"><NumberInput value={cfg.eligibility.min_history} min={1} onChange={(v) => set({ eligibility: { ...cfg.eligibility, min_history: v ?? 126 } })} /></Field>
            </div>
          </Panel>

          <Panel title="Portfolio backtests (event-driven engine) and costs">
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                <Field label="Backtest cells"><ChoiceSelect value={p.run} onChange={(v) => set({ portfolio: { ...p, run: v as EEConfig["portfolio"]["run"] } })} options={[{ value: "all", label: "all cells" }, { value: "top", label: "top by train excess" }, { value: "none", label: "none" }]} aria-label="portfolio run" /></Field>
                {p.run === "top" && <Field label="Top"><NumberInput value={p.top} min={1} onChange={(v) => set({ portfolio: { ...p, top: v ?? 20 } })} /></Field>}
                <Field label="Initial capital"><NumberInput value={p.initial_capital} min={1} onChange={(v) => set({ portfolio: { ...p, initial_capital: v ?? 1e6 } })} /></Field>
                <Field label="Max positions"><NumberInput value={p.max_positions} min={1} onChange={(v) => set({ portfolio: { ...p, max_positions: v ?? 20 } })} /></Field>
                <Field label="Sizing" hint="risk: risk_per_position of equity / initial risk per share (normalizes different stop distances); weight: a fixed share of equity">
                  <ChoiceSelect value={p.sizing.mode} onChange={(v) => set({ portfolio: { ...p, sizing: { ...p.sizing, mode: v as "risk" | "weight" } } })} options={[{ value: "risk", label: "risk" }, { value: "weight", label: "weight" }]} aria-label="sizing" />
                </Field>
                <Field label="Risk per position"><NumberInput value={p.sizing.risk_per_position} min={0} max={0.2} onChange={(v) => set({ portfolio: { ...p, sizing: { ...p.sizing, risk_per_position: v ?? 0.01 } } })} /></Field>
                <Field label="Position weight"><NumberInput value={p.sizing.position_weight} min={0} max={1} onChange={(v) => set({ portfolio: { ...p, sizing: { ...p.sizing, position_weight: v ?? 0.1 } } })} /></Field>
                <Field label="Max position weight"><NumberInput value={p.sizing.max_position_weight} min={0} max={1} onChange={(v) => set({ portfolio: { ...p, sizing: { ...p.sizing, max_position_weight: v ?? 0.25 } } })} /></Field>
                <Field label="Random-entry portfolios per cell"><NumberInput value={p.random_controls} min={0} max={50} onChange={(v) => set({ portfolio: { ...p, random_controls: v ?? 0 } })} /></Field>
                <Field label="Buy-and-hold baseline"><div className="flex h-8 items-center"><Switch checked={p.baseline_buy_and_hold} onCheckedChange={(x) => set({ portfolio: { ...p, baseline_buy_and_hold: x } })} /></div></Field>
                <Field label="Trade notional (replay)" hint="per-trade notional used for per-share and per-order commissions in the trade replay"><NumberInput value={cfg.trade_notional} min={1} onChange={(v) => set({ trade_notional: v ?? 10000 })} /></Field>
              </div>
              <ExecutionEditor value={cfg.execution} onChange={(e) => set({ execution: e })} />
            </div>
          </Panel>

          <Panel title="Random controls, validation and statistics">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Field label="Matched random stocks"><div className="flex h-8 items-center"><Switch checked={ctl.matched} onCheckedChange={(x) => set({ controls: { ...ctl, matched: x } })} /></div></Field>
              <Field label="Draws per event"><NumberInput value={ctl.draws} min={1} max={100} onChange={(v) => set({ controls: { ...ctl, draws: v ?? 10 } })} /></Field>
              <Field label="Random dates (same stock)"><div className="flex h-8 items-center"><Switch checked={ctl.random_dates} onCheckedChange={(x) => set({ controls: { ...ctl, random_dates: x } })} /></div></Field>
              <Field label="Date draws per event"><NumberInput value={ctl.date_draws} min={1} max={50} onChange={(v) => set({ controls: { ...ctl, date_draws: v ?? 5 } })} /></Field>
              <Field label="Permutations"><NumberInput value={ctl.permutations} min={100} max={20000} onChange={(v) => set({ controls: { ...ctl, permutations: v ?? 1000 } })} /></Field>
              <Field label="Volatility groups"><NumberInput value={cfg.matching.vol_groups} min={2} max={50} onChange={(v) => set({ matching: { ...cfg.matching, vol_groups: v ?? 10 } })} /></Field>
              <Field label="Match sector"><div className="flex h-8 items-center"><Switch checked={cfg.matching.sector} onCheckedChange={(x) => set({ matching: { ...cfg.matching, sector: x } })} /></div></Field>
              <Field label="Match liquidity"><div className="flex h-8 items-center"><Switch checked={cfg.matching.liquidity} onCheckedChange={(x) => set({ matching: { ...cfg.matching, liquidity: x } })} /></div></Field>
              <Field label="Min pool"><NumberInput value={cfg.matching.min_pool} min={1} onChange={(v) => set({ matching: { ...cfg.matching, min_pool: v ?? 10 } })} /></Field>
              <Field label="Partitions"><ChoiceSelect value={cfg.partition_scheme} onChange={(v) => set({ partition_scheme: v as EEConfig["partition_scheme"] })} options={[{ value: "default", label: "train 19-22 / validation 23-24 / test 25" }, { value: "none", label: "one exploration window" }]} aria-label="partitions" /></Field>
              <Field label="Evaluate the locked test partition" hint="spends the 2025 test partition; recorded in the append-only test ledger">
                <div className="flex h-8 items-center gap-2"><Switch checked={cfg.evaluate_test} onCheckedChange={(x) => set({ evaluate_test: x })} />{cfg.evaluate_test && <span className="text-2xs text-warn">spends the test period</span>}</div>
              </Field>
              <Field label="Walk-forward objective"><ChoiceSelect value={cfg.walk_forward.objective} onChange={(v) => set({ walk_forward: { ...cfg.walk_forward, objective: v as EEConfig["walk_forward"]["objective"] } })} options={["excess_matched", "mean_ret", "expectancy_r", "ret_per_session"].map((x) => ({ value: x, label: x }))} aria-label="wf objective" /></Field>
              <Field label="First training years"><NumberInput value={cfg.walk_forward.first_train_years} min={1} onChange={(v) => set({ walk_forward: { ...cfg.walk_forward, first_train_years: v ?? 2 } })} /></Field>
              <Field label="Alpha (BH)"><NumberInput value={cfg.stats.alpha} min={0.001} max={0.49} onChange={(v) => set({ stats: { ...cfg.stats, alpha: v ?? 0.05 } })} /></Field>
              <Field label="Min trades"><NumberInput value={cfg.stats.min_trades} min={5} onChange={(v) => set({ stats: { ...cfg.stats, min_trades: v ?? 30 } })} /></Field>
              <Field label="Bootstrap"><NumberInput value={cfg.stats.bootstrap} min={0} onChange={(v) => set({ stats: { ...cfg.stats, bootstrap: v ?? 1000 } })} /></Field>
              <Field label="Seed"><NumberInput value={cfg.seed} min={0} onChange={(v) => set({ seed: v ?? 0 })} /></Field>
            </div>
          </Panel>
        </div>
        <div className="space-y-3">
          <Panel title="Experiment">
            <div className="space-y-2">
              <Field label="Name"><Input className="h-8 text-xs" value={cfg.name ?? ""} onChange={(e) => set({ name: e.target.value })} /></Field>
              <Field label="Notes"><Input className="h-8 text-xs" value={cfg.notes ?? ""} onChange={(e) => set({ notes: e.target.value })} /></Field>
              <KV rows={[["Entries", String(nEntries)], ["Exit plans (approx.)", String(nExits)], ["Cells", String(nEntries * nExits)]]} />
              <div className="flex gap-2">
                <Input className="h-8 text-xs" placeholder="config name" value={saveName} onChange={(e) => setSaveName(e.target.value)} />
                <Button size="sm" variant="outline" disabled={!saveName} onClick={() => save.mutate({ name: saveName, config: final() }, { onSuccess: () => toast.success("saved"), onError: report })}><Save className="size-3.5" />Save</Button>
              </div>
              {(configs.data ?? []).length > 0 && (
                <div className="space-y-0.5 text-2xs">{configs.data!.slice(0, 8).map((x) => <div key={x.config_id}><a className="text-primary underline" href={`/entry-exit/new?config=${x.config_id}`}>{x.name}</a> <span className="text-muted-foreground">{x.created_at_utc.slice(0, 10)}</span></div>)}</div>
              )}
            </div>
          </Panel>
          {check && (
            <Panel title="Validation">
              <div className="space-y-2 text-xs">
                <KV rows={[["Cells", `${check.n_entries} entries x ${check.n_exits} exits = ${check.n_cells}`], ["Symbols", String(check.n_symbols)],
                  ["Dataset version", check.dataset_version], ["Analysis ends", check.analysis_end],
                  ["Periods", <span className="flex gap-1">{check.periods.map((x) => <PeriodBadge key={x} period={x} />)}</span>]]} />
                <div>{check.partitions.map((x) => <div key={x.name} className="text-2xs">{x.name} ({x.role}) {x.start}..{x.end} {x.locked && <span className="text-warn">locked</span>}</div>)}</div>
                <ul className="list-disc pl-4 text-2xs text-muted-foreground">{check.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
                <div className="max-h-64 overflow-auto text-2xs">{check.exits.map((x) => <div key={x.key} className="border-t py-0.5"><span className="font-medium">{x.name}</span> {!x.deployable && <span className="text-warn">intrabar</span>}<div className="text-muted-foreground">{x.trigger}</div></div>)}</div>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

function ComboEditor({ spec, onChange, signals }: { spec: CombinationSpec; onChange: (s: CombinationSpec) => void; signals: { id: string; name: string }[] }) {
  return (
    <div className="mt-2 space-y-2">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Field label="Name"><Input className="h-8 text-xs" value={spec.name} onChange={(e) => onChange({ ...spec, name: e.target.value })} /></Field>
        <Field label="Logic"><ChoiceSelect value={spec.mode} onChange={(v) => onChange({ ...spec, mode: v as CombinationSpec["mode"] })} options={["and", "or", "k_of_n", "weighted"].map((x) => ({ value: x, label: x }))} aria-label="combo mode" /></Field>
        {spec.mode === "k_of_n" && <Field label="k (consensus)"><NumberInput value={spec.k ?? 2} min={1} onChange={(v) => onChange({ ...spec, k: v })} /></Field>}
        {spec.mode === "weighted" && <Field label="Score threshold"><NumberInput value={spec.threshold ?? 1} onChange={(v) => onChange({ ...spec, threshold: v })} /></Field>}
        <Field label="Window (sessions)" hint="a component counts when it fired within the last w sessions"><NumberInput value={spec.window} min={1} max={20} onChange={(v) => onChange({ ...spec, window: v ?? 1 })} /></Field>
      </div>
      {spec.components.map((cc, i) => (
        <div key={i} className="grid grid-cols-[1fr_90px_auto_auto] items-end gap-2">
          <Field label={`component ${i + 1}`}><ChoiceSelect value={cc.id} onChange={(v) => onChange({ ...spec, components: spec.components.map((x, j) => (j === i ? { ...x, id: v, params: {} } : x)) })} options={signals.map((s) => ({ value: s.id, label: `${s.name} · ${s.id}` }))} aria-label="component" /></Field>
          <Field label="weight"><NumberInput value={cc.weight} onChange={(v) => onChange({ ...spec, components: spec.components.map((x, j) => (j === i ? { ...x, weight: v ?? 1 } : x)) })} /></Field>
          <Field label="not"><div className="flex h-8 items-center"><Switch checked={cc.negate} onCheckedChange={(x) => onChange({ ...spec, components: spec.components.map((y, j) => (j === i ? { ...y, negate: x } : y)) })} /></div></Field>
          <Button size="icon-xs" variant="ghost" disabled={spec.components.length <= 2} onClick={() => onChange({ ...spec, components: spec.components.filter((_, j) => j !== i) })} aria-label="remove"><Trash2 /></Button>
        </div>
      ))}
      <Button size="xs" variant="outline" disabled={spec.components.length >= 8} onClick={() => onChange({ ...spec, components: [...spec.components, { id: signals[0]?.id ?? "", params: {}, weight: 1, negate: false }] })}><Plus />Component</Button>
    </div>
  );
}
