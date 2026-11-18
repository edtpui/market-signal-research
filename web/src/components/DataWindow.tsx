// The data block of every builder (backtest, signal study, entry-exit study): dataset, named or custom universe and
// the window, bounded by the data policy and the dataset's coverage, followed by the builder's own fields.
import type { ReactNode } from "react";

import type { Policy } from "@/api/client";
import { useDatasets, useUniverses } from "@/api/hooks";
import { ChoiceSelect, Field } from "@/components/ConfigForm";
import { Input, Textarea } from "@/components/ui/primitives";
import { windowBounds, type UniverseChoice, type UniverseFields } from "@/lib/dataWindow";
import * as fmt from "@/lib/format";
import { Link } from "@/lib/router";

const CUSTOM = "__custom";

interface DataWindowConfig extends UniverseFields {
  dataset: string;
  start: string;
  end: string;
  dataset_version?: string | null;
}

export function DataWindowFields<C extends DataWindowConfig>({ cfg, set, universe, onUniverse, policy, children, note }: {
  cfg: C;
  set: (patch: Partial<C>) => void;
  universe: UniverseChoice;
  onUniverse: (u: UniverseChoice) => void;
  policy?: Pick<Policy, "backtest_floor" | "ceiling">;
  children?: ReactNode;
  note?: ReactNode;
}) {
  const datasets = useDatasets();
  const universes = useUniverses(cfg.dataset);
  const ds = datasets.data?.find((d) => d.id === cfg.dataset);
  const b = windowBounds(policy, ds?.current);
  const custom = universe.mode === "custom";
  const named = universes.data?.find((u) => u.name === cfg.universe_name)?.symbols;
  const changeDataset = (v: string) => set({ dataset: v, ...("dataset_version" in cfg ? { dataset_version: null } : {}) } as Partial<C>);
  const changeUniverse = (v: string) => {
    if (v === CUSTOM) return onUniverse({ ...universe, mode: "custom" });
    onUniverse({ ...universe, mode: "named" });
    set({ universe_name: v } as Partial<C>);
  };
  return (
    <>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Field label="Dataset">
          <ChoiceSelect value={cfg.dataset} onChange={changeDataset} aria-label="dataset"
            options={(datasets.data ?? []).map((d) => ({ value: d.id, label: `${d.id}${d.cached ? "" : " (not cached)"}` }))} />
        </Field>
        <Field label="Universe">
          <ChoiceSelect value={custom ? CUSTOM : cfg.universe_name ?? ""} onChange={changeUniverse} aria-label="universe"
            options={[...(universes.data ?? []).map((u) => ({ value: u.name, label: `${u.name}${u.symbols ? ` (${u.symbols.length})` : ""}` })),
              { value: CUSTOM, label: "Custom symbols…" }]} />
        </Field>
        <Field label="Start" hint={`Earliest: ${b.lo ?? "?"}`}>
          <Input type="date" className="num h-8 text-xs" value={cfg.start} min={b.lo} max={b.hi} onChange={(e) => set({ start: e.target.value } as Partial<C>)} aria-label="start" />
        </Field>
        <Field label="End" hint={`Latest: ${b.hi ?? "?"}`}>
          <Input type="date" className="num h-8 text-xs" value={cfg.end} min={b.lo} max={b.hi} onChange={(e) => set({ end: e.target.value } as Partial<C>)} aria-label="end" />
        </Field>
        {custom && (
          <Field label="Symbols (comma or space separated)" className="col-span-2 md:col-span-4">
            <Textarea className="num min-h-14 text-xs" value={universe.symbols} onChange={(e) => onUniverse({ ...universe, symbols: e.target.value })} aria-label="symbols" />
          </Field>
        )}
        {children}
      </div>
      <div className="mt-2 space-y-0.5 text-2xs text-muted-foreground" data-coverage>
        <div>
          {ds?.current
            ? <>Dataset coverage <span className="num text-foreground">{ds.current.first_date} → {ds.current.last_date}</span> ({fmt.int(ds.current.n_symbols)} symbols, version <span className="num">{fmt.bytesHash(ds.current.version, 8)}</span>). Selectable window <span className="num text-foreground">{b.lo} → {b.hi}</span>; bars before the start are loaded only as warm-up.</>
            : <>Dataset not cached yet: <Link to={`/data/${encodeURIComponent(cfg.dataset)}`} className="text-primary underline">ingest it</Link> to see its coverage.</>}
        </div>
        {!custom && named && (
          <div className="truncate" title={named.join(", ")}>
            {named.length} symbols in {cfg.dataset}: {named.slice(0, 30).join(", ")}{named.length > 30 ? " …" : ""}
          </div>
        )}
        <div>Universe lists are today's: delisted securities are absent (survivorship bias), and every result says so.{note ? <> {note}</> : null}</div>
      </div>
    </>
  );
}
