// Shared building blocks with the ptrade terminal's look: loading/empty/error states, status and research-period
// badges, a titled panel, a key-value list and a stat tile.
import { AlertTriangle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton, Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

export function Loading({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} data-state="loading" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-5 w-full" style={{ opacity: 1 - i * 0.18 }} />)}
    </div>
  );
}

export function ChartSkeleton({ height = 320 }: { height?: number }) {
  return <Skeleton className="w-full" style={{ height }} data-state="loading" />;
}

export function Empty({ title, children, className }: { title: string; children?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1 rounded-md border border-dashed px-4 py-8 text-center", className)} data-state="empty">
      <Inbox className="size-5 text-muted-foreground" />
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="max-w-md text-xs text-muted-foreground">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, retry, className }: { error: unknown; retry?: () => void; className?: string }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className={cn("flex items-start gap-3 rounded-md border border-neg/40 bg-neg/5 px-3 py-2.5", className)} data-state="error" role="alert">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-neg" />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">Could not load this</div>
        <div className="text-xs break-words text-muted-foreground">{msg}</div>
      </div>
      {retry && <Button size="sm" variant="outline" onClick={retry}>Retry</Button>}
    </div>
  );
}

const STATUS: Record<string, string> = {
  done: "border-pos/40 bg-pos/10 text-pos", running: "border-primary/40 bg-primary/10 text-primary",
  queued: "border-warn/40 bg-warn/10 text-warn", failed: "border-neg/40 bg-neg/10 text-neg",
  active: "border-pos/40 bg-pos/10 text-pos", registered: "border-warn/40 bg-warn/10 text-warn",
  waiting: "border-warn/40 bg-warn/10 text-warn", paused: "border-neg/40 bg-neg/10 text-neg",
  exported: "border-dashed border-border text-muted-foreground", retired: "border-border bg-muted text-muted-foreground",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <Badge variant="outline" className={cn("h-5 rounded-sm px-1.5 text-2xs font-medium", STATUS[status], className)} data-status={status}>
      <span className={cn("mr-1 inline-block size-1.5 rounded-full bg-current", status === "running" && "animate-pulse")} />
      {status}
    </Badge>
  );
}

const PERIOD: Record<string, { bg: string; title: string }> = {
  BACK: { bg: "var(--band-holdout)", title: "2010-2018: read-only reference (here only 2018 warm-up bars)" },
  IS: { bg: "var(--band-dev)", title: "In-sample 2019-2022" },
  OOS: { bg: "var(--band-test)", title: "Out-of-sample 2023-2025 (spent for the flag research models)" },
  SIMULATED: { bg: "var(--band-descriptive)", title: "Mock paper host: <= 2025 data replayed as forward" },
  FORWARD: { bg: "var(--band-forward)", title: "Forward paper trading" },
  EXPLORATORY: { bg: "var(--band-posthoc)", title: "Dev workspace result: exploratory, not evidence" },
};

export function PeriodBadge({ period }: { period: string }) {
  const p = PERIOD[period] ?? { bg: "var(--band-holdout)", title: period };
  return (
    <span title={p.title} className="inline-flex h-5 items-center rounded-sm border px-1.5 text-2xs font-semibold tracking-wide" style={{ background: p.bg }} data-period={period}>
      {period}
    </span>
  );
}

export function Panel({ title, actions, children, className, bodyClass, info }: {
  title: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClass?: string; info?: string;
}) {
  return (
    <section className={cn("rounded-lg border bg-card", className)}>
      <header className="flex min-h-9 items-center gap-2 border-b px-3 py-1.5">
        <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase" title={info}>{title}</h2>
        {actions && <div className="ml-auto flex items-center gap-1.5">{actions}</div>}
      </header>
      <div className={cn("p-3", bodyClass)}>{children}</div>
    </section>
  );
}

export function KV({ rows, className }: { rows: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs", className)}>
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Stat({ label, value, tone, hint, sub }: { label: string; value: ReactNode; tone?: string; hint?: string; sub?: ReactNode }) {
  const body = (
    <div className="min-w-0 rounded-md border bg-surface-2/60 px-2.5 py-1.5" data-stat={label}>
      <div className="truncate text-2xs text-muted-foreground">{label}</div>
      <div className={cn("num truncate text-sm font-semibold", tone)}>{value}</div>
      {sub && <div className="truncate text-2xs text-muted-foreground">{sub}</div>}
    </div>
  );
  if (!hint) return body;
  return (
    <Tooltip>
      <TooltipTrigger asChild><div>{body}</div></TooltipTrigger>
      <TooltipContent side="bottom">{hint}</TooltipContent>
    </Tooltip>
  );
}

export function PageHeader({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-end gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      </div>
      {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
