// The data window every builder shares: a dataset, a named or custom universe, and a start/end inside both the data
// policy and the dataset's actual coverage.
import type { DatasetItem, Policy } from "@/api/client";

export interface UniverseChoice {
  mode: "named" | "custom";
  symbols: string;
}

export interface UniverseFields {
  universe_name?: string | null;
  universe?: string[] | null;
}

export function parseSymbols(text: string): string[] {
  return text.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
}

/** The choice a saved or cloned config implies: a non-empty symbol list means a custom universe. */
export function universeChoiceOf(c: UniverseFields): UniverseChoice {
  return c.universe && c.universe.length ? { mode: "custom", symbols: c.universe.join(", ") } : { mode: "named", symbols: "" };
}

/** The config as it is sent: a custom universe replaces the named one, a named one clears any symbol list. */
export function applyUniverse<T extends UniverseFields>(cfg: T, u: UniverseChoice): T {
  return u.mode === "custom" ? { ...cfg, universe: parseSymbols(u.symbols), universe_name: null } : { ...cfg, universe: null };
}

/** The selectable window: the later of the backtest floor and the first bar, the earlier of the ceiling and the last bar. */
export function windowBounds(policy?: Pick<Policy, "backtest_floor" | "ceiling">, current?: DatasetItem["current"]) {
  const first = current?.first_date, last = current?.last_date;
  const lo = [policy?.backtest_floor, first].filter((x): x is string => !!x).sort().at(-1);
  const hi = [policy?.ceiling, last].filter((x): x is string => !!x).sort()[0];
  return { lo, hi, first, last };
}
