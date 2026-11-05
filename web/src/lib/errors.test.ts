import { describe, expect, it } from "vitest";

import { ApiError, describeError } from "@/api/client";
import { eeProgress } from "@/pages/entryexit/common";
import type { ExperimentRow } from "@/api/entryExit";

describe("describeError", () => {
  it("states a single problem once", () => {
    const e = new ApiError("These settings cannot run: entry 'RSI(14) above 70' (rsi14_above_70) is a bearish signal", 422,
      ["Entry 'RSI(14) above 70' (rsi14_above_70) is a bearish signal"], "choose a bullish signal");
    const d = describeError(e);
    expect(d.problems).toEqual([]);
    expect(d.message).toContain("bearish");
    expect(d.hint).toBe("choose a bullish signal");
  });
  it("lists several problems under a summary line", () => {
    const e = new ApiError("The experiment settings have 2 problems: Dataset is required; Colour is not a setting here", 422,
      ["Dataset is required", "Colour is not a setting here"], null);
    const d = describeError(e);
    expect(d.message).toBe("The experiment settings have 2 problems:");
    expect(d.problems).toHaveLength(2);
  });
  it("separates an unreachable API and server bugs from settings problems", () => {
    expect(describeError(new ApiError("Failed to fetch", 0)).title).toBe("The API is not reachable");
    expect(describeError(new ApiError("boom", 500)).hint).toMatch(/bug/);
    expect(describeError(new Error("x")).title).toBe("Something went wrong");
  });
});

describe("eeProgress", () => {
  const row = (o: Partial<ExperimentRow>) => ({ status: "running", stage: "cells", progress_done: 0, progress_total: 10, n_entries: 1, n_exits: 10, ...o }) as ExperimentRow;
  it("is monotonic across the stages and ends at 100 %", () => {
    const seq = [row({ status: "queued", stage: null }), row({ stage: "prepare" }), row({ progress_done: 0 }), row({ progress_done: 5 }),
      row({ progress_done: 10 }), row({ stage: "portfolio", progress_done: 0, progress_total: 4 }), row({ stage: "portfolio", progress_done: 4, progress_total: 4 }),
      row({ status: "done", stage: "done" })].map((r) => eeProgress(r).value ?? 0);
    for (let i = 1; i < seq.length; i++) expect(seq[i]).toBeGreaterThanOrEqual(seq[i - 1]);
    expect(seq[seq.length - 1]).toBe(1);
    expect(eeProgress(row({ progress_done: 10 }), "none").value).toBeCloseTo(0.97);
  });
});
