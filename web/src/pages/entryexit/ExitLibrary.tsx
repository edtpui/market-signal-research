// The component registry of the Entry-Exit Lab: entry signals, exit signals, protective stops, trailing exits, profit
// targets and position management, with each exit's exact rule, parameters, bar fields and execution kind, plus the
// trigger semantics every experiment uses.
import { useMemo, useState } from "react";

import { useEECatalog, type ExitInfo } from "@/api/entryExit";
import { DataTable, type Col } from "@/components/DataTable";
import { Empty, ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { navigate } from "@/lib/router";
import { cn } from "@/lib/utils";
import { EESubNav } from "@/pages/entryexit/common";

export function ExitLibrary() {
  const cat = useEECatalog();
  const [family, setFamily] = useState<string>("");
  const [sel, setSel] = useState<ExitInfo | null>(null);
  const rows = useMemo(() => (cat.data?.exits ?? []).filter((e) => !family || e.family === family), [cat.data, family]);
  if (cat.isLoading) return <div className="space-y-3"><PageHeader title="Exit Library" /><EESubNav /><Loading rows={10} /></div>;
  if (cat.error || !cat.data) return <div className="space-y-3"><PageHeader title="Exit Library" /><EESubNav /><ErrorState error={cat.error ?? "no catalog"} /></div>;
  const c = cat.data;
  const cols: Col<ExitInfo>[] = [
    { id: "name", header: "Exit", value: (r) => r.name, cell: (r) => <span className="font-medium">{r.name}<span className="ml-1.5 text-2xs text-muted-foreground">{r.id}</span></span> },
    { id: "family", header: "Family", value: (r) => r.family },
    { id: "kind", header: "Kind", value: (r) => r.kind, cell: (r) => <Badge variant="outline" className="h-5 rounded-sm px-1.5 text-2xs">{r.kind}</Badge> },
    { id: "rule", header: "Rule", value: (r) => r.rule, cell: (r) => <span className="text-xs text-muted-foreground">{r.rule}</span> },
    { id: "exec", header: "Execution", value: (r) => (r.intrabar ? "intrabar order" : "next open"),
      cell: (r) => <span className={cn("text-2xs", r.intrabar ? "text-warn" : "text-muted-foreground")} title={r.intrabar ? "resting stop/limit order: needs high/low bars; not deployable on the ptrade host" : "decided at the close, market order at the next open"}>{r.intrabar ? "intrabar order" : "next open"}</span> },
    { id: "params", header: "Parameters", value: (r) => Object.keys(r.params).join(", "),
      cell: (r) => <span className="num text-2xs text-muted-foreground">{Object.entries(r.params).map(([k, p]) => `${k}=${String(p.default)}`).join(" ")}</span> },
  ];
  return (
    <div className="space-y-3">
      <PageHeader title="Exit Library" sub={`${c.n_exits} exit components in ${c.exit_families.length} families · ${c.entry_signals.length} entry signals and ${c.exit_signals.length} bearish exit signals from the signal catalog`}
        actions={<Button size="sm" onClick={() => navigate("/entry-exit/new")}>New experiment</Button>} />
      <EESubNav />
      <div className="grid gap-3 xl:grid-cols-[1fr_380px]">
        <Panel title="Exit components" bodyClass="p-0" actions={
          <div className="flex flex-wrap gap-1">
            <Button size="xs" variant={family ? "ghost" : "secondary"} onClick={() => setFamily("")}>all</Button>
            {c.exit_families.map((f) => (
              <Button key={f.id} size="xs" variant={family === f.id ? "secondary" : "ghost"} onClick={() => setFamily(f.id)} title={f.description}>{f.name} ({f.n_exits})</Button>
            ))}
          </div>}>
          <DataTable rows={rows} cols={cols} name="exit-library" onRowClick={setSel} rowKey={(r) => r.id} maxHeight={640} />
        </Panel>
        <div className="space-y-3">
          <Panel title="Roles">
            <dl className="space-y-1.5 text-xs">
              {c.roles.map((r) => <div key={r.id}><dt className="font-medium">{r.name}</dt><dd className="text-muted-foreground">{r.description}</dd></div>)}
            </dl>
          </Panel>
          <Panel title="Trigger semantics">
            <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
              <li>{c.semantics.timing}</li>
              <li>Same-session priority: {c.semantics.priority.join(" → ")}</li>
              <li>{c.semantics.entry_session}</li>
              <li>{c.semantics.r}</li>
              <li>{c.semantics.trailing}</li>
            </ul>
          </Panel>
          {sel ? (
            <Panel title={sel.name} info={sel.id}>
              <div className="space-y-2 text-xs">
                <div>{sel.rule}</div>
                {sel.doc && <div className="text-muted-foreground">{sel.doc}</div>}
                <div className="text-muted-foreground">Bar fields: {sel.fields.join(", ")} · warm-up {sel.warmup} sessions{sel.needs_benchmark ? " · reads the benchmark" : ""}</div>
                <table className="w-full text-2xs">
                  <tbody>{Object.entries(sel.params).map(([k, p]) => (
                    <tr key={k} className="border-t"><td className="py-0.5 font-medium">{k}</td><td className="num">{String(p.default)}</td>
                      <td className="text-muted-foreground">{p.choices ? p.choices.join(" | ") : `${p.min ?? ""}…${p.max ?? ""}`} {p.description}</td></tr>
                  ))}</tbody>
                </table>
                <Button size="xs" variant="outline" onClick={() => navigate(`/entry-exit/new?exits=${sel.id}`)}>Test this exit</Button>
              </div>
            </Panel>
          ) : <Empty title="Select an exit">Its rule, parameters and execution kind appear here.</Empty>}
        </div>
      </div>
    </div>
  );
}
