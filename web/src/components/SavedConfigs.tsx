// Saved configurations of a builder: save the current one under a name, load or delete a saved one.
import { Save, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Panel } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import * as fmt from "@/lib/format";

export interface SavedConfig<C> {
  config_id: string;
  name: string;
  created_at_utc: string;
  config: C;
}

export function SavedConfigsPanel<C>({ configs, onLoad, onSave, onDelete, onError, saving, describe }: {
  configs: SavedConfig<C>[] | undefined;
  onLoad: (config: C) => void;
  onSave: (name: string) => Promise<unknown>;
  onDelete: (configId: string) => void;
  onError: (e: unknown) => void;
  saving?: boolean;
  describe?: (config: C) => ReactNode;
}) {
  const [name, setName] = useState("");
  const save = () => {
    const n = name.trim();
    onSave(n).then(() => { toast.success(`Saved "${n}"`); setName(""); }, onError);
  };
  return (
    <Panel title={`Saved configurations (${configs?.length ?? 0})`}>
      <div className="mb-2 flex gap-1.5">
        <Input className="h-7 text-xs" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} aria-label="config name"
          onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) save(); }} />
        <Button size="sm" variant="outline" className="h-7" disabled={!name.trim() || saving} onClick={save} data-action="save"><Save className="size-3.5" />Save</Button>
      </div>
      {(configs ?? []).length === 0 ? <div className="text-xs text-muted-foreground">None saved.</div> : (
        <ul className="space-y-1">
          {configs!.map((c) => (
            <li key={c.config_id} className="flex items-center gap-2 rounded border px-2 py-1 text-xs">
              <button type="button" className="min-w-0 flex-1 truncate text-left hover:text-primary" onClick={() => onLoad(c.config)} title="Load">
                {c.name}{describe && <span className="ml-1 text-2xs text-muted-foreground">{describe(c.config)}</span>}
              </button>
              <span className="num text-2xs text-muted-foreground">{fmt.utc(c.created_at_utc).slice(0, 10)}</span>
              <Button size="icon-xs" variant="ghost" aria-label={`delete ${c.name}`} onClick={() => onDelete(c.config_id)}><Trash2 /></Button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
