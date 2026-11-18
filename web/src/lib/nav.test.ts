import { describe, expect, it } from "vitest";

import { pageTitle, SECTIONS, SECTION_TABS, sectionOf } from "@/lib/nav";
import { match } from "@/lib/router";

// Every path the app has ever linked to, with the section that must light up for it.
const PATHS: [string, string][] = [
  ["/", "overview"],
  ["/experiments", "experiments"],
  ["/backtest", "experiments"],
  ["/signals/new", "experiments"],
  ["/entry-exit/new", "experiments"],
  ["/library/strategies", "experiments"],
  ["/library/strategies/momentum", "experiments"],
  ["/library/signals", "experiments"],
  ["/library/signals/rsi14_below_30", "experiments"],
  ["/library/exits", "experiments"],
  ["/results/compare", "results"],
  ["/results/signal-rankings", "results"],
  ["/results/leaderboards", "results"],
  ["/results/ledgers", "results"],
  ["/runs/r-1", "results"],
  ["/signals/experiments/sr-1", "results"],
  ["/signals/experiments/sr-1/k", "results"],
  ["/entry-exit/experiments/ee-1", "results"],
  ["/entry-exit/experiments/ee-1/cells/e000_x001", "results"],
  ["/forward", "forward"],
  ["/forward/dep-1", "forward"],
  ["/data", "data"],
  ["/data/us_core", "data"],
  ["/settings", "settings"],
  // old paths (aliases)
  ["/strategies", "experiments"],
  ["/strategies/momentum", "experiments"],
  ["/signals", "experiments"],
  ["/signals/library/rsi14_below_30", "experiments"],
  ["/entry-exit", "experiments"],
  ["/signals/history", "experiments"],
  ["/entry-exit/history", "experiments"],
  ["/analytics", "results"],
  ["/signals/rankings", "results"],
  ["/entry-exit/leaderboards", "results"],
];

describe("navigation", () => {
  it("puts every page, old paths included, in exactly one section", () => {
    for (const [path, section] of PATHS) expect([path, sectionOf(match(path))]).toEqual([path, section]);
  });

  it("has six sections and no page outside them except a missing one", () => {
    expect(SECTIONS.map((s) => s.section)).toEqual(["overview", "experiments", "results", "forward", "data", "settings"]);
    expect(sectionOf(match("/nope"))).toBeNull();
    expect(match("/results/nope")).toEqual({ page: "missing", path: "/results/nope" });
  });

  it("highlights exactly one tab on every tabbed list page, and every tab's link opens its own page", () => {
    for (const [section, groups] of Object.entries(SECTION_TABS)) {
      for (const tab of groups!.flat()) {
        const route = match(tab.to);
        expect([tab.to, sectionOf(route)]).toEqual([tab.to, section]);
        const on = groups!.flat().filter((t) => t.active(route)).map((t) => t.to);
        expect([tab.to, on]).toEqual([tab.to, [tab.to]]);
      }
    }
  });

  it("lights the same tab for an old path as for its new one", () => {
    const active = (p: string) => SECTION_TABS[sectionOf(match(p))!]!.flat().filter((t) => t.active(match(p))).map((t) => t.label);
    expect(active("/analytics")).toEqual(active("/results/compare"));
    expect(active("/signals/rankings")).toEqual(["Signal rankings"]);
    expect(active("/entry-exit")).toEqual(["Exits"]);
    expect(active("/strategies/x")).toEqual(["Strategies"]);
    expect(active("/signals/history")).toEqual(["All experiments"]);
  });

  it("gives every page a distinct, descriptive browser title", () => {
    const t = (p: string) => pageTitle(match(p));
    expect(t("/")).toBe("Overview · Market Signal Research");
    expect(t("/backtest")).toBe("New backtest · Market Signal Research");
    expect(t("/results/ledgers")).toBe("Research ledgers · Market Signal Research");
    expect(t("/runs/r-1")).toBe("Run r-1 · Market Signal Research");
    expect(t("/signals/experiments/sr-1")).toBe("Signal study sr-1 · Market Signal Research");
    expect(t("/signals/experiments/sr-1/k")).toBe("k · sr-1 · Market Signal Research");
    expect(t("/entry-exit/experiments/ee-1/cells/e000_x001")).toBe("Cell e000_x001 · ee-1 · Market Signal Research");
    expect(t("/library/strategies/momentum")).toBe("momentum · Strategies · Market Signal Research");
    expect(t("/forward/dep-1")).toBe("dep-1 · Forward testing · Market Signal Research");
    expect(t("/data/us_core")).toBe("us_core · Market data · Market Signal Research");
    expect(t("/nope")).toBe("Not found · Market Signal Research");
    const listPages = PATHS.filter(([p]) => !/\/(r-1|sr-1|ee-1|momentum|rsi14_below_30|dep-1|us_core|x)(\/|$)/.test(p)).map(([p]) => p);
    for (const p of listPages) expect([p, t(p)]).not.toEqual([p, "Market Signal Research"]);
  });
});
