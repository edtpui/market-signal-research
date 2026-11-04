import { describe, expect, it } from "vitest";

import * as fmt from "@/lib/format";
import { formatMetric, metricTone } from "@/lib/metrics";
import { match } from "@/lib/router";

describe("format", () => {
  it("signs percentages with a true minus and a dash for missing values", () => {
    expect(fmt.pct(0.0123)).toBe("+1.23%");
    expect(fmt.pct(-0.0123)).toBe("−1.23%");
    expect(fmt.pct(0)).toBe("0.00%");
    expect(fmt.pct(0.5, 1, false)).toBe("50.0%");
    expect(fmt.pct(null)).toBe(fmt.DASH);
    expect(fmt.pct(Number.NaN)).toBe(fmt.DASH);
  });
  it("formats dollars, prices and shares", () => {
    expect(fmt.usd(100000)).toBe("$100,000");
    expect(fmt.usd(-1234.5, { cents: true })).toBe("−$1,234.50");
    expect(fmt.usd(2_500_000, { compact: true })).toBe("$2.50M");
    expect(fmt.price(0.12345)).toBe("0.1235");
    expect(fmt.shares(28)).toBe("28");
    expect(fmt.shares(28.5)).toBe("28.50");
  });
});

describe("metrics", () => {
  it("shows an undefined metric as a dash, never as zero", () => {
    expect(formatMetric("sharpe", { value: null, reason: "zero return variance" })).toBe(fmt.DASH);
    expect(formatMetric("total_return", { value: 0.25, reason: null })).toBe("+25.00%");
    expect(formatMetric("volatility", { value: 0.2, reason: null })).toBe("20.00%");
    expect(formatMetric("trade_count", { value: 12, reason: null })).toBe("12");
    expect(metricTone("max_drawdown", { value: -0.2, reason: null })).toBe("text-neg");
  });
});

describe("router", () => {
  it("maps every page", () => {
    expect(match("/")).toEqual({ page: "overview" });
    expect(match("/runs/abc")).toEqual({ page: "run", id: "abc" });
    expect(match("/forward/sl-1")).toEqual({ page: "forward", id: "sl-1" });
    expect(match("/forward")).toEqual({ page: "forward", id: undefined });
    expect(match("/data/us_core")).toEqual({ page: "data", id: "us_core" });
    expect(match("/nope").page).toBe("missing");
  });
});
