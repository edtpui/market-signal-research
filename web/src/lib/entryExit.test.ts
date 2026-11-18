import { describe, expect, it } from "vitest";

import { match } from "@/lib/router";
import { cellColor, fmtMetric } from "@/pages/entryexit/common";

describe("entry-exit routes", () => {
  it("maps every entry-exit page, and the old list paths onto their new places", () => {
    expect(match("/entry-exit/new")).toEqual({ page: "entryexit", view: "new" });
    expect(match("/entry-exit")).toEqual({ page: "library", tab: "exits" });
    expect(match("/entry-exit/leaderboards")).toEqual({ page: "results", view: "leaderboards" });
    expect(match("/entry-exit/history")).toEqual({ page: "experiments", kind: "entry-exit" });
    expect(match("/entry-exit/experiments/ee-1")).toEqual({ page: "entryexit", view: "experiment", id: "ee-1" });
    expect(match("/entry-exit/experiments/ee-1/cells/e000_x003")).toEqual({ page: "entryexit", view: "cell", id: "ee-1", cell: "e000_x003" });
  });
});

describe("matrix helpers", () => {
  it("formats metrics by unit and never shows undefined as zero", () => {
    expect(fmtMetric("excess_matched", 0.0123)).toBe("+1.2%");
    expect(fmtMetric("win_rate", 0.5)).toBe("50.0%");
    expect(fmtMetric("sharpe", 1.234)).toBe("1.23");
    expect(fmtMetric("n", 1234)).toBe("1,234");
    expect(fmtMetric("q", 0.0004)).toBe("4.0e-4");
    expect(fmtMetric("sharpe", null)).toBe("—");
  });
  it("colours signed metrics on a diverging scale and leaves missing cells blank", () => {
    expect(cellColor("excess_matched", 0.02, -0.01, 0.02)).toContain("--pos");
    expect(cellColor("excess_matched", -0.01, -0.01, 0.02)).toContain("--neg");
    expect(cellColor("win_rate", 0.6, 0.4, 0.6)).toContain("--primary");
    expect(cellColor("sharpe", null, 0, 1)).toBeUndefined();
  });
});
