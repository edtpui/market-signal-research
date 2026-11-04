// Signal Library: every catalog signal by family, filterable by direction, scope and bar fields; select signals or a
// whole family and send them to the Experiment Builder.
import { FlaskConical } from "lucide-react";
import { useMemo, useState } from "react";

import { useCatalog, type SignalInfo } from "@/api/signals";
import { ChoiceSelect } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/primitives";
import { navigate } from "@/lib/router";
import { cn } from "@/lib/utils";
import { DirectionBadge, ScopeBadge, SubNav } from "@/pages/signals/common";

export function SignalLibrary() {
  const cat = useCatalog();
  const [family, setFamily] = useState<string | null>(null);
  const [direction, setDirection] = useState("all");
  const [scope, setScope] = useState("all");
  const [fields, setFields] = useState("all");
  const [selected, setSelected] = useState<string[]>([]);
  const fams = cat.data?.families ?? [];
  const rows = useMemo(() => (cat.data?.signals ?? []).filter((s) =>
    (!family || s.family === family) && (direction === "all" || s.direction === direction) && (scope === "all" || s.scope === scope)
    && (fields === "all" || (fields === "close" ? s.fields.every((f) => f === "close" || f === "open") : s.fields.includes(fields)))), [cat.data, family, direction, scope, fields]);
  const famName = (id: string) => fams.find((f) => f.id === id)?.name ?? id;

  const cols: Col<SignalInfo>[] = [
    {
      id: "sel", header: "", value: (s) => selected.includes(s.id),
      cell: (s) => <span onClick={(e) => e.stopPropagation()}><Checkbox checked={selected.includes(s.id)} aria-label={`select ${s.id}`}
        onCheckedChange={(c) => setSelected((x) => (c ? [...x, s.id] : x.filter((y) => y !== s.id)))} /></span>,
    },
    { id: "name", header: "Signal", value: (s) => s.name, cell: (s) => <div><div className="font-medium">{s.name}</div><div className="num text-2xs text-muted-foreground">{s.id} · v{s.version}</div></div> },
    { id: "family", header: "Family", value: (s) => famName(s.family) },
    { id: "direction", header: "Direction", value: (s) => s.direction, cell: (s) => <DirectionBadge d={s.direction} /> },
    { id: "scope", header: "Scope", value: (s) => s.scope, cell: (s) => <ScopeBadge s={s.scope} /> },
    { id: "trigger", header: "Trigger", value: (s) => s.trigger, cell: (s) => <span className="block max-w-[520px] truncate text-muted-foreground" title={s.trigger}>{s.trigger}</span> },
    { id: "params", header: "Params", align: "right", value: (s) => Object.keys(s.params).length },
    { id: "fields", header: "Bars", value: (s) => s.fields.join(","), cell: (s) => <span className="num text-2xs">{s.fields.join(", ")}</span> },
    { id: "warmup", header: "History", align: "right", value: (s) => s.warmup, cell: (s) => <span className="num" title="sessions of history needed before the first possible trigger">{s.warmup}</span> },
  ];

  return (
    <div className="space-y-3">
      <PageHeader title="Signal Research Lab" sub="Independently testable signal definitions. Each has an id, family, version, typed parameters, a trigger definition, its bar fields and its history requirement."
        actions={<>
          <Button size="sm" variant="outline" disabled={!family} onClick={() => navigate(`/signals/new?family=${family}`)} data-action="test-family"><FlaskConical className="size-3.5" />Test family</Button>
          <Button size="sm" disabled={!selected.length} onClick={() => navigate(`/signals/new?signals=${selected.join(",")}`)} data-action="test-selected"><FlaskConical className="size-3.5" />Test {selected.length || ""} selected</Button>
        </>} />
      <SubNav />
      {cat.error && <ErrorState error={cat.error} retry={cat.refetch} />}
      {cat.isLoading ? <Loading rows={10} /> : (
        <div className="grid gap-3 lg:grid-cols-[240px_1fr]">
          <Panel title={`Families (${fams.length})`} bodyClass="p-1">
            <button type="button" onClick={() => setFamily(null)} className={cn("flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs hover:bg-accent", !family && "bg-accent font-medium")}>
              All signals <span className="num text-muted-foreground">{cat.data?.n_signals}</span>
            </button>
            <div className="max-h-[640px] overflow-auto" data-families>
              {fams.map((f) => (
                <button key={f.id} type="button" title={f.description} onClick={() => setFamily(f.id)} data-family={f.id}
                  className={cn("flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs hover:bg-accent", family === f.id && "bg-accent font-medium")}>
                  <span className="truncate">{f.name}</span><span className="num text-muted-foreground">{f.n_signals}</span>
                </button>
              ))}
            </div>
          </Panel>
          <Panel title={family ? famName(family) : "All signals"} info={family ? fams.find((f) => f.id === family)?.description : undefined}>
            {family && <p className="mb-2 text-xs text-muted-foreground">{fams.find((f) => f.id === family)?.description}</p>}
            <DataTable name="signals" rows={rows} cols={cols} maxHeight={640} onRowClick={(s) => navigate(`/signals/library/${s.id}`)} rowKey={(s) => s.id}
              toolbar={<>
                <ChoiceSelect className="h-7 w-32" value={direction} onChange={setDirection} aria-label="direction filter"
                  options={[{ value: "all", label: "Both directions" }, { value: "bullish", label: "Bullish" }, { value: "bearish", label: "Bearish" }]} />
                <ChoiceSelect className="h-7 w-36" value={scope} onChange={setScope} aria-label="scope filter"
                  options={[{ value: "all", label: "Any scope" }, { value: "stock", label: "Stock" }, { value: "cross_section", label: "Cross-section" }, { value: "market", label: "Market" }]} />
                <ChoiceSelect className="h-7 w-40" value={fields} onChange={setFields} aria-label="fields filter"
                  options={[{ value: "all", label: "Any bars" }, { value: "close", label: "Open/close only" }, { value: "high", label: "Needs high/low" }, { value: "volume", label: "Needs volume" }]} />
                {selected.length > 0 && <Button size="xs" variant="ghost" onClick={() => setSelected([])}>Clear selection</Button>}
              </>} />
          </Panel>
        </div>
      )}
    </div>
  );
}
