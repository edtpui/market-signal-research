// Typed editors for strategy parameters and execution assumptions, generated from the API's schemas.
import type { ExecutionConfig, ParamSpec } from "@/api/client";
import { Input, Label, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("space-y-1", className)}>
      <Label className="text-2xs font-medium text-muted-foreground" title={hint}>{label}</Label>
      {children}
    </div>
  );
}

export function NumberInput({ value, onChange, min, max, step, allowEmpty, className, ...rest }: {
  value: number | null | undefined; onChange: (v: number | null) => void; min?: number | null; max?: number | null; step?: number;
  allowEmpty?: boolean; className?: string; "aria-label"?: string;
}) {
  return (
    <Input type="number" className={cn("num h-8 text-xs", className)} value={value ?? ""} min={min ?? undefined} max={max ?? undefined} step={step ?? "any"}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") return onChange(allowEmpty ? null : 0);
        const v = Number(raw);
        if (Number.isFinite(v)) onChange(v);
      }} {...rest} />
  );
}

export function ChoiceSelect({ value, onChange, options, className, ...rest }: {
  value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; className?: string; "aria-label"?: string;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className={className} {...rest}><SelectValue /></SelectTrigger>
      <SelectContent>{options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
    </Select>
  );
}

export function ParamsEditor({ schema, value, onChange }: { schema: Record<string, ParamSpec>; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }) {
  const entries = Object.entries(schema);
  if (!entries.length) return <div className="text-xs text-muted-foreground">This strategy has no parameters.</div>;
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-3" data-params>
      {entries.map(([k, p]) => {
        const v = k in value ? value[k] : p.default;
        const hint = `${p.description}${p.min !== null || p.max !== null ? ` (${p.min ?? "−∞"} … ${p.max ?? "∞"})` : ""}`;
        return (
          <Field key={k} label={k} hint={hint}>
            {p.kind === "bool" ? (
              <div className="flex h-8 items-center"><Switch checked={Boolean(v)} onCheckedChange={(c) => set(k, c)} aria-label={k} /></div>
            ) : p.choices ? (
              <ChoiceSelect value={String(v)} onChange={(x) => set(k, x)} options={p.choices.map((c) => ({ value: c, label: c }))} aria-label={k} />
            ) : p.kind === "int" || p.kind === "float" ? (
              <NumberInput value={v as number} min={p.min} max={p.max} step={p.kind === "int" ? 1 : undefined} onChange={(x) => set(k, x)} aria-label={k} />
            ) : (
              <Input className="h-8 text-xs" value={Array.isArray(v) ? v.join(",") : String(v ?? "")} onChange={(e) => set(k, e.target.value)} aria-label={k} />
            )}
          </Field>
        );
      })}
    </div>
  );
}

export function ExecutionEditor({ value, onChange }: { value: ExecutionConfig; onChange: (v: ExecutionConfig) => void }) {
  const set = <K extends keyof ExecutionConfig>(k: K, v: ExecutionConfig[K]) => onChange({ ...value, [k]: v });
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4" data-execution>
      <Field label="Fill timing" hint="Orders decided at a session's close fill at the next session's open or close.">
        <ChoiceSelect value={value.fill_timing} onChange={(v) => set("fill_timing", v as ExecutionConfig["fill_timing"])} aria-label="fill timing"
          options={[{ value: "next_open", label: "Next open" }, { value: "next_close", label: "Next close" }]} />
      </Field>
      <Field label="Price mode" hint="adjusted: total-return prices, corporate actions embedded. raw: split-adjusted closes with dividends credited as cash (daily bars only).">
        <ChoiceSelect value={value.price_mode} onChange={(v) => set("price_mode", v as ExecutionConfig["price_mode"])} aria-label="price mode"
          options={[{ value: "adjusted", label: "Adjusted" }, { value: "raw", label: "Raw + dividends" }]} />
      </Field>
      <Field label="Slippage (bps)" hint="Adverse, on every fill, in bps of the reference price.">
        <NumberInput value={value.slippage_bps} min={0} max={1000} onChange={(v) => set("slippage_bps", v ?? 0)} aria-label="slippage" />
      </Field>
      <Field label="Spread model" hint="No bid/ask data: fixed full spread in bps, or a share of the previous bar's high-low range.">
        <ChoiceSelect value={value.spread.model} onChange={(v) => set("spread", { ...value.spread, model: v as ExecutionConfig["spread"]["model"] })} aria-label="spread model"
          options={[{ value: "none", label: "None" }, { value: "fixed_bps", label: "Fixed bps" }, { value: "hl_range", label: "Prior bar range" }]} />
      </Field>
      {value.spread.model === "fixed_bps" && (
        <Field label="Spread (bps, full)"><NumberInput value={value.spread.bps} min={0} onChange={(v) => set("spread", { ...value.spread, bps: v ?? 0 })} aria-label="spread bps" /></Field>
      )}
      {value.spread.model === "hl_range" && (
        <Field label="Share of range"><NumberInput value={value.spread.hl_fraction} min={0} max={1} onChange={(v) => set("spread", { ...value.spread, hl_fraction: v ?? 0 })} aria-label="hl fraction" /></Field>
      )}
      <Field label="Commission $/share"><NumberInput value={value.commission.per_share} min={0} onChange={(v) => set("commission", { ...value.commission, per_share: v ?? 0 })} aria-label="commission per share" /></Field>
      <Field label="Commission (bps)"><NumberInput value={value.commission.bps} min={0} onChange={(v) => set("commission", { ...value.commission, bps: v ?? 0 })} aria-label="commission bps" /></Field>
      <Field label="$ per fill"><NumberInput value={value.commission.per_order} min={0} onChange={(v) => set("commission", { ...value.commission, per_order: v ?? 0 })} aria-label="commission per order" /></Field>
      <Field label="Min $ per fill"><NumberInput value={value.commission.minimum} min={0} onChange={(v) => set("commission", { ...value.commission, minimum: v ?? 0 })} aria-label="commission minimum" /></Field>
      <Field label="Participation cap" hint="Max share of the fill bar's volume; the rest stays open (GTC) or expires (day). Empty = no cap.">
        <NumberInput value={value.participation_cap} min={0} max={1} allowEmpty onChange={(v) => set("participation_cap", v)} aria-label="participation cap" />
      </Field>
      <Field label="Cash shortfall">
        <ChoiceSelect value={value.cash_policy} onChange={(v) => set("cash_policy", v as ExecutionConfig["cash_policy"])} aria-label="cash policy"
          options={[{ value: "scale", label: "Scale down" }, { value: "reject", label: "Reject" }]} />
      </Field>
      <Field label="Max gross leverage"><NumberInput value={value.max_gross_leverage} min={0.05} max={4} onChange={(v) => set("max_gross_leverage", v ?? 1)} aria-label="max gross" /></Field>
      <Field label="Short borrow (bps/yr)"><NumberInput value={value.short_borrow_bps_per_year} min={0} onChange={(v) => set("short_borrow_bps_per_year", v ?? 0)} aria-label="borrow" /></Field>
      <Field label="Allow short"><div className="flex h-8 items-center"><Switch checked={value.allow_short} onCheckedChange={(c) => set("allow_short", c)} aria-label="allow short" /></div></Field>
      <Field label="Fractional shares"><div className="flex h-8 items-center"><Switch checked={value.fractional_shares} onCheckedChange={(c) => set("fractional_shares", c)} aria-label="fractional" /></div></Field>
    </div>
  );
}
