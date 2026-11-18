// What the ptrade basket host can run (docs/ARCHITECTURE.md 3.4): the UI's early warning; the API checks it again at
// deploy time and fails closed.
import type { StrategyInfo } from "@/api/client";

export function ptradeCompatible(s: Pick<StrategyInfo, "required_fields" | "order_types" | "long_only">): { ok: boolean; why: string } {
  const extra = s.required_fields.filter((f) => !["open", "close"].includes(f));
  const orders = s.order_types.filter((o) => o !== "market");
  if (extra.length) return { ok: false, why: `needs ${extra.join(", ")} (ptrade Frames carry open/close only)` };
  if (orders.length) return { ok: false, why: `uses ${orders.join(", ")} orders (market only on open/close bars)` };
  if (!s.long_only) return { ok: false, why: "may short (ptrade basket books are long only)" };
  return { ok: true, why: "open/close bars, market orders, long only" };
}
