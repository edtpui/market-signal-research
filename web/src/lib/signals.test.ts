import { describe, expect, it } from "vitest";

import { fmtValue, ticks } from "@/components/ResearchCharts";
import { match } from "@/lib/router";

describe("signal research routes", () => {
  it("maps every Signal Research page", () => {
    expect(match("/signals")).toEqual({ page: "signals", view: "library" });
    expect(match("/signals/new")).toEqual({ page: "signals", view: "new" });
    expect(match("/signals/rankings")).toEqual({ page: "signals", view: "rankings" });
    expect(match("/signals/history")).toEqual({ page: "signals", view: "history" });
    expect(match("/signals/library/rsi14_below_30")).toEqual({ page: "signals", view: "signal", id: "rsi14_below_30" });
    expect(match("/signals/experiments/sr-1")).toEqual({ page: "signals", view: "experiment", id: "sr-1" });
    expect(match(`/signals/experiments/sr-1/${encodeURIComponent("rsi14_below_30@1.0:b34a2ff8")}`))
      .toEqual({ page: "signals", view: "result", id: "sr-1", key: "rsi14_below_30@1.0:b34a2ff8" });
    expect(match("/strategies/x")).toEqual({ page: "strategies", id: "x" });
  });
});

describe("research chart helpers", () => {
  it("produces nice ticks that cover the range", () => {
    const u = ticks(0, 1, 5);
    expect(u.length).toBe(6);
    expect(u[0]).toBe(0);
    expect(u[5]).toBeCloseTo(1, 12);
    const t = ticks(-0.013, 0.021, 4);
    expect(t[0]).toBeGreaterThanOrEqual(-0.013);
    expect(t.at(-1)!).toBeLessThanOrEqual(0.021 + 1e-12);
    expect(t).toContain(0);
    expect(ticks(5, 5)).toEqual([5]);
  });
  it("formats values by unit", () => {
    expect(fmtValue(0.0042, "pct")).toBe("+0.42%");
    expect(fmtValue(0.0123, "pct")).toBe("+1.2%");
    expect(fmtValue(0.123, "pct")).toBe("+12.3%");
    expect(fmtValue(1234, "int")).toBe("1,234");
    expect(fmtValue(null, "pct")).toBe("—");
  });
});
