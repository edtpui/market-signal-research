import { describe, expect, it } from "vitest";

import type { RunRow } from "@/api/client";
import type { ExperimentRow as EERow } from "@/api/entryExit";
import type { ExperimentRow as SignalRow } from "@/api/signals";
import { applyUniverse, parseSymbols, universeChoiceOf, windowBounds } from "@/lib/dataWindow";
import { activity, unify } from "@/lib/experiments";

const run = (id: string, created: string, extra: Partial<RunRow> = {}): RunRow => ({
  run_id: id, name: "", kind: "backtest", parent_run_id: null, strategy: "momentum", strategy_version: "1.0", dataset: "us_core",
  dataset_version: "v", start: "2019-01-02", end: "2025-12-31", frequency: "1d", config_hash: "h", status: "done", error: null,
  created_at_utc: created, started_at_utc: null, finished_at_utc: null, periods: ["IS", "OOS"],
  summary: { total_return: 0.5, sharpe: 1.2 }, reproduction: null, ...extra,
});
const sig = (id: string, created: string): SignalRow => ({
  experiment_id: id, name: "rsi", kind: "study", parent_id: null, dataset: "us_core", dataset_version: "v", universe_name: "sp1500",
  n_symbols: 500, start: "2019-01-02", end: "2025-12-31", analysis_end: "2024-12-31", frequency: "1d", config_hash: "h",
  methodology_hash: "m", status: "running", error: null, created_at_utc: created, started_at_utc: null, finished_at_utc: null,
  n_signals: 4, progress_done: 1, progress_failed: 1, evaluate_test: false, partitions: null, periods: ["IS"], summary: null,
  reproduction: null,
});
const ee = (id: string, created: string): EERow => ({
  experiment_id: id, name: "", kind: "reproduction", parent_id: "ee-0", dataset: "us_core", dataset_version: "v", universe_name: null,
  n_symbols: 15, start: "2019-01-02", end: "2025-12-31", analysis_end: "2024-12-31", status: "done", error: null, created_at_utc: created,
  started_at_utc: null, finished_at_utc: null, n_entries: 3, n_exits: 4, n_cells: 12, progress_done: 12, progress_total: 12, stage: "done",
  evaluate_test: false, partitions: null, periods: ["IS", "OOS"], summary: null, reproduction: null,
});

describe("one experiment list", () => {
  it("merges the three catalogs newest first, each row linking to its own page", () => {
    const xs = unify([run("r-1", "2026-11-03T10:00:00Z")], [sig("sr-1", "2026-11-04T10:00:00Z")], [ee("ee-1", "2026-11-05T10:00:00Z")]);
    expect(xs.map((x) => [x.kind, x.id, x.href])).toEqual([
      ["entry-exit", "ee-1", "/entry-exit/experiments/ee-1"],
      ["signal", "sr-1", "/signals/experiments/sr-1"],
      ["backtest", "r-1", "/runs/r-1"],
    ]);
  });

  it("describes each kind in its own terms and keeps the source row", () => {
    const [e, s, r] = unify([run("r-1", "2026-11-03T10:00:00Z")], [sig("sr-1", "2026-11-04T10:00:00Z")], [ee("ee-1", "2026-11-05T10:00:00Z")]);
    expect(r.summary).toBe("+50.00% total · Sharpe 1.20");
    expect(r.data).toBe("momentum@1.0 · us_core");
    expect(s.summary).toBe("");                    // still running: progress is shown instead
    expect(s.data).toBe("us_core · sp1500 (500)");
    expect(s.window).toBe("2019-01-02 → 2024-12-31");
    expect(e.summary).toBe("3 x 4 = 12 cells");
    expect(e.data).toBe("us_core · custom (15)");
    expect(e.reproduction).toBe(true);
    expect(e.name).toBe("ee-1");
    expect(r.kind === "backtest" && r.raw.run_id).toBe("r-1");
  });

  it("counts activity across the three kinds for the Overview", () => {
    const t = "2026-11-03T10:00:00Z";
    const xs = unify(
      [run("r-1", t), run("r-2", t, { status: "failed" }), run("r-3", t, { status: "queued" })],
      [sig("sr-1", t)],                             // running
      [ee("ee-1", t), { ...ee("ee-2", t), status: "cancelled" as EERow["status"] }],
    );
    expect(activity(xs)).toEqual({ total: 6, byKind: { backtest: 3, signal: 1, "entry-exit": 2 }, running: 1, queued: 1, failed: 2 });
    expect(activity([])).toEqual({ total: 0, byKind: { backtest: 0, signal: 0, "entry-exit": 0 }, running: 0, queued: 0, failed: 0 });
  });

  it("orders ties by id, tolerates missing catalogs and escapes ids in links", () => {
    const t = "2026-11-03T10:00:00Z";
    expect(unify([run("b", t), run("a", t)]).map((x) => x.id)).toEqual(["a", "b"]);
    expect(unify(undefined, undefined, undefined)).toEqual([]);
    expect(unify([run("a/b", t)])[0].href).toBe("/runs/a%2Fb");
  });
});

describe("data window", () => {
  it("parses symbol lists the same way in every builder", () => {
    expect(parseSymbols(" aapl, msft;nvda\n brk-b ")).toEqual(["AAPL", "MSFT", "NVDA", "BRK-B"]);
    expect(parseSymbols("")).toEqual([]);
  });

  it("restores a custom universe from a saved config and sends it back unchanged", () => {
    const saved = { universe_name: null, universe: ["AAPL", "MSFT"] };
    const u = universeChoiceOf(saved);
    expect(u).toEqual({ mode: "custom", symbols: "AAPL, MSFT" });
    expect(applyUniverse({ universe_name: "sp1500", universe: null }, u)).toEqual({ universe_name: null, universe: ["AAPL", "MSFT"] });
    expect(universeChoiceOf({ universe_name: "sp1500", universe: [] })).toEqual({ mode: "named", symbols: "" });
    expect(applyUniverse({ universe_name: "sp1500", universe: ["X"] }, { mode: "named", symbols: "X" })).toEqual({ universe_name: "sp1500", universe: null });
  });

  it("bounds the window by the data policy and the dataset's coverage", () => {
    const policy = { backtest_floor: "2019-01-01", ceiling: "2025-12-31" };
    const cur = { first_date: "2018-01-02", last_date: "2025-06-30" } as Parameters<typeof windowBounds>[1];
    expect(windowBounds(policy, cur)).toMatchObject({ lo: "2019-01-01", hi: "2025-06-30" });
    const late = { first_date: "2020-03-02", last_date: "2026-01-05" } as Parameters<typeof windowBounds>[1];
    expect(windowBounds(policy, late)).toMatchObject({ lo: "2020-03-02", hi: "2025-12-31" });
    expect(windowBounds(policy, null)).toMatchObject({ lo: "2019-01-01", hi: "2025-12-31" });
    expect(windowBounds(undefined, null)).toMatchObject({ lo: undefined, hi: undefined });
  });
});
