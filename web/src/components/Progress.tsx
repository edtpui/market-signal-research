// A progress bar for long-running jobs: percentage, what is happening now, elapsed time and an estimate of the time
// left (from the elapsed time and the fraction done, once enough is done to estimate). value null = indeterminate
// (the job reports no count), shown as a moving stripe rather than a fake percentage.
import { useEffect, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

function duration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "";
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export function timeText(value: number | null, startedAt?: string | null, now = Date.now()): { elapsed: string; left: string } {
  if (!startedAt) return { elapsed: "", left: "" };
  const el = (now - Date.parse(startedAt)) / 1000;
  const left = value !== null && value >= 0.03 && value < 1 ? (el * (1 - value)) / value : NaN;
  return { elapsed: duration(el), left: Number.isFinite(left) ? `about ${duration(left)} left` : value !== null && value < 1 ? "estimating time left…" : "" };
}

export function ProgressBar({ value, label, detail, startedAt, compact, tone = "primary", className }: {
  value: number | null; label?: ReactNode; detail?: ReactNode; startedAt?: string | null; compact?: boolean;
  tone?: "primary" | "warn"; className?: string;
}) {
  const now = useNow();
  const v = value === null ? null : Math.max(0, Math.min(1, value));
  const pct = v === null ? null : Math.floor(v * 100);
  const t = timeText(v, startedAt, now);
  const bar = (
    <div className={cn("relative h-2 w-full overflow-hidden rounded-full bg-muted", compact && "h-1.5")}
      role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}
      aria-valuetext={pct === null ? "in progress" : `${pct}%`} data-progress={pct ?? "indeterminate"}>
      {v === null ? (
        <div className={cn("absolute inset-y-0 w-1/3 rounded-full progress-indeterminate", tone === "warn" ? "bg-warn" : "bg-primary")} />
      ) : (
        <div className={cn("h-full rounded-full transition-[width] duration-500", tone === "warn" ? "bg-warn" : "bg-primary")} style={{ width: `${Math.max(v * 100, 1)}%` }} />
      )}
    </div>
  );
  if (compact) {
    return (
      <div className={cn("flex min-w-[120px] items-center gap-2", className)} title={typeof label === "string" ? label : undefined}>
        <div className="flex-1">{bar}</div>
        <span className="num w-9 text-right text-2xs text-muted-foreground">{pct === null ? "…" : `${pct}%`}</span>
      </div>
    );
  }
  return (
    <div className={cn("space-y-1.5 rounded-md border bg-surface-2/40 px-3 py-2", className)} data-progress-panel>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs">
        <span className="font-medium">{label}</span>
        <span className="num ml-auto text-base font-semibold">{pct === null ? "" : `${pct}%`}</span>
      </div>
      {bar}
      <div className="flex flex-wrap gap-x-3 text-2xs text-muted-foreground">
        {detail && <span>{detail}</span>}
        {t.elapsed && <span className="num">elapsed {t.elapsed}</span>}
        {t.left && <span className="num">{t.left}</span>}
      </div>
    </div>
  );
}

/** A problem list with the field each line concerns, plus what to change. */
export function ProblemPanel({ title, message, problems, hint, className }: { title: string; message: string; problems: string[]; hint: string | null; className?: string }) {
  return (
    <div className={cn("rounded-md border border-neg/40 bg-neg/5 px-3 py-2.5 text-xs", className)} role="alert" data-state="problems">
      <div className="text-sm font-medium">{title}</div>
      <div className="mt-0.5 break-words text-muted-foreground">{message}</div>
      {problems.length > 0 && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-4">{problems.map((p, i) => <li key={i} className="break-words">{p}</li>)}</ul>
      )}
      {hint && <div className="mt-1.5 rounded-sm bg-background/60 px-2 py-1"><span className="font-medium">How to fix: </span>{hint}</div>}
    </div>
  );
}
