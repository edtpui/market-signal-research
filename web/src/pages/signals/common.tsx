// Shared pieces of the Signal Research pages: the sub-navigation, verdict / direction / scope badges, and the
// number cells used by every results table.
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";

import { VERDICT_LABEL, VERDICT_TONE, type Direction, type Verdict } from "@/api/signals";
import { Badge } from "@/components/ui/badge";
import * as fmt from "@/lib/format";
import { Link, useLocation } from "@/lib/router";
import { cn } from "@/lib/utils";

const TABS = [
  { to: "/signals", label: "Signal Library", match: (p: string) => p === "/signals" || p.startsWith("/signals/library") },
  { to: "/signals/new", label: "Experiment Builder", match: (p: string) => p.startsWith("/signals/new") },
  { to: "/signals/rankings", label: "Signal Rankings", match: (p: string) => p.startsWith("/signals/rankings") },
  { to: "/signals/history", label: "Research History", match: (p: string) => p.startsWith("/signals/history") || p.startsWith("/signals/experiments") },
];

export function SubNav({ right }: { right?: ReactNode }) {
  const { path } = useLocation();
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 border-b pb-2" data-subnav="signals">
      <div className="inline-flex h-8 items-center rounded-lg bg-muted p-[3px] text-muted-foreground">
        {TABS.map((t) => (
          <Link key={t.to} to={t.to} className={cn("rounded-md px-2.5 py-1 text-xs font-medium whitespace-nowrap hover:text-foreground",
            t.match(path) && "bg-background text-foreground shadow-sm dark:bg-input/30")}>{t.label}</Link>
        ))}
      </div>
      <span className="text-2xs text-muted-foreground">Event studies against matched-random stocks, random entry dates and benchmarks · EXPLORATORY, data ≤ 2025-12-31</span>
      {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  );
}

export function VerdictBadge({ v, className }: { v: Verdict | null | undefined; className?: string }) {
  if (!v) return <span className="text-muted-foreground">{fmt.DASH}</span>;
  return <Badge variant="outline" className={cn("h-5 rounded-sm px-1.5 text-2xs font-medium whitespace-nowrap", VERDICT_TONE[v], className)} data-verdict={v}>{VERDICT_LABEL[v]}</Badge>;
}

export function DirectionBadge({ d }: { d: Direction }) {
  return (
    <span className={cn("inline-flex items-center gap-0.5 text-2xs font-medium", d === "bullish" ? "text-pos" : "text-neg")} data-direction={d}>
      {d === "bullish" ? <ArrowUpRight className="size-3" /> : <ArrowDownRight className="size-3" />}{d}
    </span>
  );
}

export function ScopeBadge({ s }: { s: string }) {
  const title = s === "market" ? "Fires for every symbol on the same session: the matched-stock control cannot test it (see random dates and benchmarks)"
    : s === "cross_section" ? "Ranks the symbol against the experiment's universe on the session" : "From the symbol's own bars";
  return <Badge variant="outline" className={cn("h-5 rounded-sm px-1.5 text-2xs", s === "market" && "border-warn/50 text-warn")} title={title}>{s.replace("_", "-")}</Badge>;
}

export const pct = (v: number | null | undefined, d?: number) => <span className={cn("num", fmt.tone(v))}>{fmt.pct(v, d ?? 2)}</span>;
export const pval = (v: number | null | undefined) => (
  <span className={cn("num", typeof v === "number" && v < 0.05 ? "font-semibold text-foreground" : "text-muted-foreground")}>{typeof v === "number" ? (v < 0.001 ? v.toExponential(1) : v.toFixed(3)) : fmt.DASH}</span>
);
export const ci = (lo: number | null | undefined, hi: number | null | undefined) => (
  <span className="num text-muted-foreground">{typeof lo === "number" && typeof hi === "number" ? `[${fmt.pct(lo, 2)}, ${fmt.pct(hi, 2)}]` : fmt.DASH}</span>
);

export const VERDICT_COLOR: Record<Verdict, string> = {
  supported: "var(--pos)", significant_negligible: "var(--warn)", reversed: "var(--neg)",
  not_significant: "var(--muted-foreground)", insufficient_sample: "var(--border)",
};
