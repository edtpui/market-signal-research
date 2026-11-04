// One set of number formats for the whole app (the ptrade terminal's conventions): signed percentages, dollars,
// prices, ratios, dates. Missing values render as an em dash; negatives use a true minus sign so columns align.

export const DASH = "—";
const MINUS = "−";

export function finite(x: number | null | undefined): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

function signed(abs: string, x: number, plus = true): string {
  if (x < 0) return `${MINUS}${abs}`;
  return plus && x > 0 ? `+${abs}` : abs;
}

/** 0.0123 -> "+1.23%" */
export function pct(x: number | null | undefined, digits = 2, plus = true): string {
  if (!finite(x)) return DASH;
  const v = x * 100;
  const abs = Math.abs(v).toFixed(digits);
  return Number(abs) === 0 ? `${(0).toFixed(digits)}%` : `${signed(abs, v, plus)}%`;
}

/** 100000 -> "$100,000"; compact -> "$1.23M" */
export function usd(x: number | null | undefined, opts: { compact?: boolean; cents?: boolean } = {}): string {
  if (!finite(x)) return DASH;
  const abs = Math.abs(x);
  let body: string;
  if (opts.compact && abs >= 1e6) body = `${(abs / 1e6).toFixed(2)}M`;
  else if (opts.compact && abs >= 1e4) body = `${(abs / 1e3).toFixed(1)}k`;
  else body = abs.toLocaleString("en-US", { minimumFractionDigits: opts.cents ? 2 : 0, maximumFractionDigits: opts.cents ? 2 : 0 });
  return x < 0 ? `${MINUS}$${body}` : `$${body}`;
}

export function num(x: number | null | undefined, digits = 2): string {
  if (!finite(x)) return DASH;
  const s = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return x < 0 ? `${MINUS}${s}` : s;
}

export function int(x: number | null | undefined): string {
  return num(x, 0);
}

/** Prices: 2 decimals, 4 below $1. */
export function price(x: number | null | undefined): string {
  if (!finite(x)) return DASH;
  return num(x, Math.abs(x) < 1 ? 4 : 2);
}

/** Share counts: whole numbers when whole, else 2 decimals. */
export function shares(x: number | null | undefined): string {
  if (!finite(x)) return DASH;
  if (Number.isInteger(x)) return num(x, 0);
  return num(x, 2);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2025-11-24" -> "Nov 24" (with the year when `year`). */
export function shortDate(d: string | null | undefined, year = false): string {
  if (!d) return DASH;
  const [y, m, day] = d.slice(0, 10).split("-");
  const base = `${MONTHS[Number(m) - 1]} ${Number(day)}`;
  return year ? `${base}, ${y}` : base;
}

/** "2026-11-03T21:44:03Z" -> "2026-11-03 21:44 UTC" */
export function utc(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

export function bytesHash(h: string | null | undefined, n = 10): string {
  return h ? h.slice(0, n) : DASH;
}

/** Tailwind class for a signed value: positive, negative or muted. */
export function tone(x: number | null | undefined): string {
  if (!finite(x) || Math.abs(x) < 1e-12) return "text-muted-foreground";
  return x > 0 ? "text-pos" : "text-neg";
}
